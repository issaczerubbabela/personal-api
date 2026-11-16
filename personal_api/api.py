"""The API. Reads only the local SQLite copy; never calls upstream services."""

from __future__ import annotations

import secrets
import sqlite3
from typing import Any, Iterator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import load_config
from .db import connect, row_to_dict, to_utc

bearer = HTTPBearer(auto_error=False)


def create_app(config: dict[str, Any] | None = None) -> FastAPI:
    config = config or load_config()
    token = config["api"].get("token")
    if not token:
        raise RuntimeError("Set api.token in config.toml (or PAPI_TOKEN) before serving.")
    db_path = config["database"]["path"]

    def auth(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if not credentials or not secrets.compare_digest(credentials.credentials, token):
            raise HTTPException(401, "Invalid or missing bearer token",
                                headers={"WWW-Authenticate": "Bearer"})

    def db() -> Iterator[sqlite3.Connection]:
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def parse_time(value: str | None, name: str) -> str | None:
        if value is None:
            return None
        try:
            return to_utc(value)
        except ValueError:
            raise HTTPException(422, f"{name} must be an ISO date or datetime, e.g. 2026-03-01")

    app = FastAPI(title="Personal API", version="0.1.0", dependencies=[Depends(auth)])

    @app.get("/me")
    def me(conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        """Your profile plus what is in the store and how fresh it is."""
        sources = {}
        for row in conn.execute(
            "SELECT source, COUNT(*) AS records, MIN(timestamp) AS earliest,"
            " MAX(timestamp) AS latest FROM records GROUP BY source"
        ):
            sources[row["source"]] = dict(row) | {"last_sync": None}
            del sources[row["source"]]["source"]
        for row in conn.execute(
            "SELECT source, finished_at, ok, error FROM sync_runs"
            " WHERE id IN (SELECT MAX(id) FROM sync_runs GROUP BY source)"
        ):
            sources.setdefault(row["source"], {"records": 0})["last_sync"] = {
                "at": row["finished_at"], "ok": bool(row["ok"]), "error": row["error"],
            }
        return {"profile": config["profile"], "sources": sources}

    @app.get("/activity")
    def activity(
        since: str | None = Query(None, description="ISO date/datetime, inclusive"),
        until: str | None = Query(None, description="ISO date/datetime, exclusive"),
        source: str | None = None,
        type: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        conn: sqlite3.Connection = Depends(db),
    ) -> dict[str, Any]:
        """One timeline across every source, newest first."""
        where, params = ["1 = 1"], []
        for clause, value in (
            ("timestamp >= ?", parse_time(since, "since")),
            ("timestamp < ?", parse_time(until, "until")),
            ("source = ?", source),
            ("type = ?", type),
        ):
            if value is not None:
                where.append(clause)
                params.append(value)
        if type is None:
            where.append("NOT (source = 'github' AND type = 'repo')")  # repos live in /projects
        sql = " FROM records WHERE " + " AND ".join(where)
        total = conn.execute("SELECT COUNT(*)" + sql, params).fetchone()[0]
        rows = conn.execute(
            "SELECT *" + sql + " ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        return {"total": total, "items": [row_to_dict(r) for r in rows]}

    @app.get("/projects")
    def projects(
        include_forks: bool = False,
        include_private: bool = False,
        include_archived: bool = True,
        conn: sqlite3.Connection = Depends(db),
    ) -> dict[str, Any]:
        """Your repositories, most recently pushed first. Private repos are opt-in."""
        items = []
        for row in conn.execute(
            "SELECT * FROM records WHERE source = 'github' AND type = 'repo'"
            " ORDER BY timestamp DESC"
        ):
            repo = row_to_dict(row)
            d = repo["data"]
            if (d.get("fork") and not include_forks) or (d.get("private") and not include_private) \
                    or (d.get("archived") and not include_archived):
                continue
            items.append({
                "name": repo["title"], "description": repo["content"], "url": repo["url"],
                "last_pushed": repo["timestamp"], **d,
            })
        return {"total": len(items), "items": items}

    @app.get("/search")
    def search(
        q: str = Query(..., min_length=1),
        source: str | None = None,
        limit: int = Query(50, ge=1, le=500),
        conn: sqlite3.Connection = Depends(db),
    ) -> dict[str, Any]:
        """Full-text search over titles and content. Every word must match (prefix allowed)."""
        terms = [t.replace('"', '""') for t in q.split()]
        match = " ".join(f'"{t}"*' for t in terms)
        sql = ("SELECT records.* FROM records_fts JOIN records ON records.id = records_fts.rowid"
               " WHERE records_fts MATCH ?")
        params: list[Any] = [match]
        if source:
            sql += " AND records.source = ?"
            params.append(source)
        rows = conn.execute(sql + " ORDER BY bm25(records_fts), timestamp DESC LIMIT ?",
                            [*params, limit])
        items = [row_to_dict(r) for r in rows]
        return {"total": len(items), "items": items}

    return app
