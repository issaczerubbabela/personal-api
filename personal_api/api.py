"""The HTTP API. Reads only the local SQLite copy; never calls upstream services."""

from __future__ import annotations

import secrets
import sqlite3
from typing import Any, Iterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import queries
from .config import load_config
from .db import connect

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

    app = FastAPI(title="Personal API", version="0.2.0", dependencies=[Depends(auth)])

    @app.exception_handler(ValueError)
    def bad_argument(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/me")
    def me(conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        """Your profile plus what is in the store and how fresh it is."""
        return queries.profile(conn, config)

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
        return queries.activity(conn, since, until, source, type, limit, offset)

    @app.get("/projects")
    def projects(
        include_forks: bool = False,
        include_private: bool = False,
        include_archived: bool = True,
        conn: sqlite3.Connection = Depends(db),
    ) -> dict[str, Any]:
        """Your repositories, most recently pushed first. Private repos are opt-in."""
        return queries.projects(conn, include_forks, include_private, include_archived)

    @app.get("/search")
    def search(
        q: str = Query(..., min_length=1),
        source: str | None = None,
        limit: int = Query(50, ge=1, le=500),
        conn: sqlite3.Connection = Depends(db),
    ) -> dict[str, Any]:
        """Full-text search over titles and content. Every word must match (prefix allowed)."""
        return queries.search(conn, q, source, limit)

    @app.get("/spending")
    def spending(
        since: str | None = None,
        until: str | None = None,
        group_by: str = Query("category", description="category, month, day or account"),
        type: str = Query("expense", description="expense, income or transfer"),
        conn: sqlite3.Connection = Depends(db),
    ) -> dict[str, Any]:
        """Totals for a period, grouped by category, month, day or account."""
        return queries.spending(conn, since, until, group_by, type)

    return app
