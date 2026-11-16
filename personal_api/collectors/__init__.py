"""Collectors: one module per source. Each exposes collect(settings) -> CollectResult.

To add a source, write a module with a `collect` function and register it in COLLECTORS.
"""

from __future__ import annotations

import sqlite3
import traceback
from dataclasses import dataclass
from typing import Any, Callable

from ..db import Record, now_utc, upsert


@dataclass
class CollectResult:
    records: list[Record]
    # Optional (source, type, start, end): this run is the complete truth for that window,
    # so records in it that were not returned (e.g. deleted calendar events) are removed.
    replace_window: tuple[str, str, str, str] | None = None


from . import calendar, expenses, github  # noqa: E402

COLLECTORS: dict[str, Callable[[dict[str, Any]], CollectResult]] = {
    "github": github.collect,
    "calendar": calendar.collect,
    "expenses": expenses.collect,
}


def sync(conn: sqlite3.Connection, config: dict[str, Any], only: list[str] | None = None) -> dict[str, Any]:
    """Run enabled collectors. One failing source never blocks the others."""
    summary: dict[str, Any] = {}
    for name, collect in COLLECTORS.items():
        settings = config["sources"].get(name, {})
        if only and name not in only:
            continue
        if not only and not settings.get("enabled"):
            continue
        started = now_utc()
        try:
            result = collect(settings)
            with conn:
                if result.replace_window:
                    source, type_, start, end = result.replace_window
                    keep = {r.external_id for r in result.records if r.type == type_}
                    stale = [
                        row["id"]
                        for row in conn.execute(
                            "SELECT id, external_id FROM records WHERE source = ? AND type = ?"
                            " AND timestamp >= ? AND timestamp < ?",
                            (source, type_, start, end),
                        )
                        if row["external_id"] not in keep
                    ]
                    conn.executemany("DELETE FROM records WHERE id = ?", [(i,) for i in stale])
                count = upsert(conn, result.records)
                conn.execute(
                    "INSERT INTO sync_runs (source, started_at, finished_at, ok, records)"
                    " VALUES (?, ?, ?, 1, ?)",
                    (name, started, now_utc(), count),
                )
            summary[name] = {"ok": True, "records": count}
        except Exception as exc:  # noqa: BLE001 - report and move on to the next source
            error = f"{type(exc).__name__}: {exc}"
            with conn:
                conn.execute(
                    "INSERT INTO sync_runs (source, started_at, finished_at, ok, error)"
                    " VALUES (?, ?, ?, 0, ?)",
                    (name, started, now_utc(), error),
                )
            summary[name] = {"ok": False, "error": error, "trace": traceback.format_exc()}
    return summary
