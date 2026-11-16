"""GitHub: repositories (type=repo) and recent activity (type=event, ~last 90 days)."""

from __future__ import annotations

from typing import Any, Iterator

import httpx

from ..db import Record, to_utc
from . import CollectResult

API = "https://api.github.com"


def _paginate(client: httpx.Client, url: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
    next_url: str | None = url
    while next_url:
        response = client.get(next_url, params=params)
        response.raise_for_status()
        yield from response.json()
        next_url = response.links.get("next", {}).get("url")
        params = {}  # the next link already carries them


def _summarize(event: dict[str, Any]) -> str:
    kind = event["type"]
    repo = event["repo"]["name"]
    p = event.get("payload", {})
    if kind == "PushEvent":
        branch = (p.get("ref") or "").removeprefix("refs/heads/")
        return f"Pushed to {repo}" + (f" ({branch})" if branch else "")
    if kind == "CreateEvent":
        return f"Created {p.get('ref_type', 'ref')} {p.get('ref') or ''} in {repo}".replace("  ", " ")
    if kind == "PullRequestEvent":
        return f"{p.get('action', 'updated').capitalize()} PR #{p.get('number', '?')} in {repo}"
    if kind == "IssuesEvent":
        return f"{p.get('action', 'updated').capitalize()} issue #{p.get('issue', {}).get('number', '?')} in {repo}"
    if kind == "IssueCommentEvent":
        return f"Commented on #{p.get('issue', {}).get('number', '?')} in {repo}"
    if kind == "WatchEvent":
        return f"Starred {repo}"
    if kind == "ForkEvent":
        return f"Forked {repo}"
    if kind == "ReleaseEvent":
        return f"Released {p.get('release', {}).get('tag_name', '')} in {repo}"
    return f"{kind.removesuffix('Event')} in {repo}"


def collect(settings: dict[str, Any], ctx: Any = None, *,
            transport: httpx.BaseTransport | None = None) -> CollectResult:
    username = settings.get("username")
    if not username:
        raise ValueError("sources.github.username is not set")
    token = settings.get("token")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    records: list[Record] = []
    with httpx.Client(headers=headers, timeout=30, transport=transport) as client:
        # With a token, /user/repos also returns private repos you own.
        repos_url = f"{API}/user/repos" if token else f"{API}/users/{username}/repos"
        repo_params = {"per_page": 100, "sort": "pushed"}
        if token:
            repo_params["affiliation"] = "owner"
        for repo in _paginate(client, repos_url, repo_params):
            records.append(
                Record(
                    source="github",
                    type="repo",
                    external_id=str(repo["id"]),
                    timestamp=to_utc(repo.get("pushed_at") or repo["created_at"]),
                    title=repo["name"],
                    content=repo.get("description") or "",
                    url=repo["html_url"],
                    data={
                        "full_name": repo["full_name"],
                        "language": repo.get("language"),
                        "topics": repo.get("topics", []),
                        "stars": repo.get("stargazers_count", 0),
                        "forks": repo.get("forks_count", 0),
                        "fork": repo.get("fork", False),
                        "private": repo.get("private", False),
                        "archived": repo.get("archived", False),
                        "homepage": repo.get("homepage") or None,
                        "created_at": repo["created_at"],
                    },
                )
            )

        for event in _paginate(client, f"{API}/users/{username}/events", {"per_page": 100}):
            repo_name = event["repo"]["name"]
            records.append(
                Record(
                    source="github",
                    type="event",
                    external_id=str(event["id"]),
                    timestamp=to_utc(event["created_at"]),
                    title=_summarize(event),
                    url=f"https://github.com/{repo_name}",
                    data={"event_type": event["type"], "repo": repo_name, "public": event.get("public", True)},
                )
            )
    return CollectResult(records)
