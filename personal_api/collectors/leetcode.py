"""LeetCode via its public (unofficial) GraphQL endpoint. No login; the profile must be public.

type=stats  one snapshot: solved counts, ranking, streak, contest rating
type=solved recent accepted submissions (LeetCode exposes only the latest 20 publicly)
type=day    one record per active day with its submission count, for every active year
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from ..db import Record, now_utc, to_utc
from . import CollectResult

URL = "https://leetcode.com/graphql"

PROFILE = """query($u: String!) {
  matchedUser(username: $u) {
    username
    profile { ranking }
    submitStatsGlobal { acSubmissionNum { difficulty count } }
  }
  recentAcSubmissionList(username: $u, limit: 20) { id title titleSlug timestamp lang }
  userContestRanking(username: $u) { rating attendedContestsCount globalRanking topPercentage }
}"""

CALENDAR = """query($u: String!, $y: Int) {
  matchedUser(username: $u) {
    userCalendar(year: $y) { streak totalActiveDays activeYears submissionCalendar }
  }
}"""


def _query(client: httpx.Client, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    response = client.post(URL, json={"query": query, "variables": variables})
    response.raise_for_status()
    return response.json()


def collect(settings: dict[str, Any], ctx: Any = None, *,
            transport: httpx.BaseTransport | None = None) -> CollectResult:
    username = settings.get("username")
    if not username:
        raise ValueError("sources.leetcode.username is not set")
    profile_url = f"https://leetcode.com/u/{username}/"
    headers = {"Referer": "https://leetcode.com", "User-Agent": "personal-api"}
    records: list[Record] = []

    with httpx.Client(headers=headers, timeout=30, transport=transport) as client:
        body = _query(client, PROFILE, {"u": username})
        data = body.get("data") or {}
        user = data.get("matchedUser")
        if not user:
            raise ValueError(f"LeetCode user {username!r} not found: {body.get('errors') or ''}")

        for sub in data.get("recentAcSubmissionList") or []:
            records.append(Record(
                source="leetcode", type="solved", external_id=str(sub["id"]),
                timestamp=to_utc(datetime.fromtimestamp(int(sub["timestamp"]), timezone.utc)),
                title=sub["title"], content=sub.get("lang") or "",
                url=f"https://leetcode.com/problems/{sub['titleSlug']}/",
                data={"lang": sub.get("lang"), "slug": sub["titleSlug"]},
            ))

        # The calendar can be hidden in privacy settings; everything else still syncs.
        streak = total_active_days = None
        days: dict[str, int] = {}
        years: list[int | None] = [None]
        seen_years: set[int | None] = set()
        while years:
            year = years.pop()
            seen_years.add(year)
            calendar = ((_query(client, CALENDAR, {"u": username, "y": year}).get("data") or {})
                        .get("matchedUser") or {}).get("userCalendar")
            if not calendar:
                break
            if year is None:
                streak, total_active_days = calendar.get("streak"), calendar.get("totalActiveDays")
                years.extend(y for y in calendar.get("activeYears") or [] if y not in seen_years)
            for stamp, count in json.loads(calendar.get("submissionCalendar") or "{}").items():
                day = datetime.fromtimestamp(int(stamp), timezone.utc).strftime("%Y-%m-%d")
                days[day] = int(count)

    for day, count in days.items():
        records.append(Record(
            source="leetcode", type="day", external_id=day, timestamp=to_utc(day),
            title=f"{count} LeetCode submission{'s' if count != 1 else ''}",
            url=profile_url, data={"submissions": count},
        ))

    solved = {row["difficulty"].lower(): row["count"]
              for row in user["submitStatsGlobal"]["acSubmissionNum"]}
    contest = data.get("userContestRanking") or {}
    records.append(Record(
        source="leetcode", type="stats", external_id="profile", timestamp=now_utc(),
        title=(f"LeetCode: {solved.get('all', 0)} solved ({solved.get('easy', 0)} easy, "
               f"{solved.get('medium', 0)} medium, {solved.get('hard', 0)} hard)"),
        url=profile_url,
        data={
            "username": user["username"],
            "solved": solved,
            "ranking": (user.get("profile") or {}).get("ranking"),
            "streak": streak,
            "total_active_days": total_active_days,
            "contest_rating": round(contest["rating"]) if contest.get("rating") else None,
            "contests_attended": contest.get("attendedContestsCount"),
            "contest_top_percentage": contest.get("topPercentage"),
        },
    ))
    return CollectResult(records)
