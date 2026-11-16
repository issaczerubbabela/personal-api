"""Monkeytype via its official API.

type=stats  one snapshot from the public profile: tests, time typing, streak, personal bests
            (needs `username`; no key)
type=test   every typing test (needs `ape_key`; only new tests are fetched after the first run)

ApeKeys may call /results 30 times a day, so do not sync this source more than hourly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ..db import Record, now_utc, to_utc
from . import CollectResult

API = "https://api.monkeytype.com"


def _ms(value: Any) -> str:
    return to_utc(datetime.fromtimestamp(int(value) / 1000, timezone.utc))


def collect(settings: dict[str, Any], ctx: Any = None, *,
            transport: httpx.BaseTransport | None = None) -> CollectResult:
    username, ape_key = settings.get("username"), settings.get("ape_key")
    if not username and not ape_key:
        raise ValueError("set sources.monkeytype.username and/or ape_key")
    records: list[Record] = []

    with httpx.Client(base_url=API, timeout=30, transport=transport) as client:
        if username:
            response = client.get(f"/users/{username}/profile")
            response.raise_for_status()
            profile = response.json()["data"]
            bests: dict[str, float] = {}
            for mode, lengths in (profile.get("personalBests") or {}).items():
                for length, entries in lengths.items():
                    if entries:
                        bests[f"{mode} {length}"] = max(e["wpm"] for e in entries)
            stats = profile.get("typingStats") or {}
            top = max(bests.values(), default=None)
            records.append(Record(
                source="monkeytype", type="stats", external_id="profile", timestamp=now_utc(),
                title=f"Monkeytype: {stats.get('completedTests', 0)} tests"
                      + (f", best {top:g} wpm" if top else ""),
                url=f"https://monkeytype.com/profile/{profile.get('name', username)}",
                data={
                    "username": profile.get("name", username),
                    "completed_tests": stats.get("completedTests"),
                    "started_tests": stats.get("startedTests"),
                    "hours_typing": round((stats.get("timeTyping") or 0) / 3600, 1),
                    "streak": profile.get("streak"),
                    "max_streak": profile.get("maxStreak"),
                    "best_wpm": bests,
                },
            ))

        if ape_key:
            params: dict[str, Any] = {"limit": 1000}
            latest = ctx.latest("test") if ctx else None
            if latest:
                params["onOrAfterTimestamp"] = int(
                    datetime.fromisoformat(latest.replace("Z", "+00:00")).timestamp() * 1000)
            response = client.get("/results", params=params,
                                  headers={"Authorization": f"ApeKey {ape_key}"})
            response.raise_for_status()
            for result in response.json().get("data") or []:
                mode = f"{result.get('mode', '')} {result.get('mode2', '')}".strip()
                records.append(Record(
                    source="monkeytype", type="test", external_id=str(result["_id"]),
                    timestamp=_ms(result["timestamp"]),
                    title=f"{result['wpm']:g} wpm, {result['acc']:g}% accuracy ({mode})",
                    content=result.get("language") or "english",
                    data={
                        "wpm": result["wpm"], "raw_wpm": result.get("rawWpm"),
                        "accuracy": result["acc"], "consistency": result.get("consistency"),
                        "mode": mode, "duration": result.get("testDuration"),
                        "language": result.get("language") or "english",
                        "personal_best": bool(result.get("isPb")),
                    },
                ))
    return CollectResult(records)
