"""Read queries over the store, shared by the HTTP API and the MCP server.

Bad arguments raise ValueError.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .db import row_to_dict, to_utc


def _time(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    try:
        return to_utc(value)
    except ValueError:
        raise ValueError(f"{name} must be an ISO date or datetime, e.g. 2026-03-01") from None


def profile(conn: sqlite3.Connection, config: dict[str, Any]) -> dict[str, Any]:
    sources: dict[str, Any] = {}
    for row in conn.execute(
        "SELECT source, COUNT(*) AS records, MIN(timestamp) AS earliest,"
        " MAX(timestamp) AS latest FROM records GROUP BY source"
    ):
        sources[row["source"]] = {"records": row["records"], "earliest": row["earliest"],
                                  "latest": row["latest"], "last_sync": None}
    for row in conn.execute(
        "SELECT source, finished_at, ok, error FROM sync_runs"
        " WHERE id IN (SELECT MAX(id) FROM sync_runs GROUP BY source)"
    ):
        sources.setdefault(row["source"], {"records": 0})["last_sync"] = {
            "at": row["finished_at"], "ok": bool(row["ok"]), "error": row["error"],
        }
    return {"profile": config["profile"], "sources": sources}


def activity(conn: sqlite3.Connection, since: str | None = None, until: str | None = None,
             source: str | None = None, type: str | None = None,
             limit: int = 100, offset: int = 0) -> dict[str, Any]:
    where, params = ["1 = 1"], []
    for clause, value in (
        ("timestamp >= ?", _time(since, "since")),
        ("timestamp < ?", _time(until, "until")),
        ("source = ?", source),
        ("type = ?", type),
    ):
        if value is not None:
            where.append(clause)
            params.append(value)
    if type is None:
        where.append("NOT (source = 'github' AND type = 'repo')")  # repos live in projects
    sql = " FROM records WHERE " + " AND ".join(where)
    total = conn.execute("SELECT COUNT(*)" + sql, params).fetchone()[0]
    rows = conn.execute(
        "SELECT *" + sql + " ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    return {"total": total, "items": [row_to_dict(r) for r in rows]}


def projects(conn: sqlite3.Connection, include_forks: bool = False,
             include_private: bool = False, include_archived: bool = True) -> dict[str, Any]:
    items = []
    for row in conn.execute(
        "SELECT * FROM records WHERE source = 'github' AND type = 'repo' ORDER BY timestamp DESC"
    ):
        repo = row_to_dict(row)
        d = repo["data"]
        if (d.get("fork") and not include_forks) or (d.get("private") and not include_private) \
                or (d.get("archived") and not include_archived):
            continue
        items.append({"name": repo["title"], "description": repo["content"], "url": repo["url"],
                      "last_pushed": repo["timestamp"], **d})
    return {"total": len(items), "items": items}


def search(conn: sqlite3.Connection, q: str, source: str | None = None,
           limit: int = 50) -> dict[str, Any]:
    terms = [t.replace('"', '""') for t in q.split()]
    if not terms:
        raise ValueError("q must not be empty")
    sql = ("SELECT records.* FROM records_fts JOIN records ON records.id = records_fts.rowid"
           " WHERE records_fts MATCH ?")
    params: list[Any] = [" ".join(f'"{t}"*' for t in terms)]
    if source:
        sql += " AND records.source = ?"
        params.append(source)
    rows = conn.execute(sql + " ORDER BY bm25(records_fts), timestamp DESC LIMIT ?",
                        [*params, limit])
    items = [row_to_dict(r) for r in rows]
    return {"total": len(items), "items": items}


SPENDING_GROUPS = {
    "category": "COALESCE(json_extract(data, '$.category'), 'Uncategorised')",
    "month": "substr(timestamp, 1, 7)",
    "day": "substr(timestamp, 1, 10)",
    "account": "COALESCE(json_extract(data, '$.account'), 'Unknown')",
}


def spending(conn: sqlite3.Connection, since: str | None = None, until: str | None = None,
             group_by: str = "category", type: str = "expense") -> dict[str, Any]:
    if group_by not in SPENDING_GROUPS:
        raise ValueError(f"group_by must be one of: {', '.join(SPENDING_GROUPS)}")
    where, params = ["source = 'expenses'", "type = ?"], [type]
    for clause, value in (("timestamp >= ?", _time(since, "since")),
                          ("timestamp < ?", _time(until, "until"))):
        if value is not None:
            where.append(clause)
            params.append(value)
    key = SPENDING_GROUPS[group_by]
    rows = conn.execute(
        f"SELECT {key} AS key, ROUND(SUM(json_extract(data, '$.amount')), 2) AS total,"
        f" COUNT(*) AS count FROM records WHERE {' AND '.join(where)} GROUP BY key"
        + (" ORDER BY key" if group_by in ("month", "day") else " ORDER BY total DESC"),
        params,
    ).fetchall()
    return {
        "type": type,
        "group_by": group_by,
        "total": round(sum(r["total"] or 0 for r in rows), 2),
        "count": sum(r["count"] for r in rows),
        "groups": [dict(r) for r in rows],
    }
