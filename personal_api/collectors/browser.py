"""Browser history, read from each browser's own local SQLite file.

kind = "safari"    ~/Library/Safari/History.db (the terminal needs Full Disk Access)
kind = "chromium"  Chrome, Helium, Brave, Arc, Edge: the profile's `History` file
kind = "firefox"   Firefox, Zen: the profile's `places.sqlite`

The file is copied before reading because a running browser keeps it locked. After the
first run only new visits are read. type=visit.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..db import Record, to_utc
from . import CollectResult

MAC_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)      # Safari: seconds since 2001
WEBKIT_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)   # Chromium: microseconds since 1601
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)     # Firefox: microseconds since 1970

# kind -> (query returning id, url, title, raw time; epoch; raw units per second)
KINDS = {
    "safari": ("SELECT v.id, i.url, v.title, v.visit_time FROM history_visits v"
               " JOIN history_items i ON i.id = v.history_item WHERE v.visit_time > ?",
               MAC_EPOCH, 1),
    "chromium": ("SELECT v.id, u.url, u.title, v.visit_time FROM visits v"
                 " JOIN urls u ON u.id = v.url WHERE v.visit_time > ?",
                 WEBKIT_EPOCH, 1_000_000),
    "firefox": ("SELECT v.id, p.url, p.title, v.visit_date FROM moz_historyvisits v"
                " JOIN moz_places p ON p.id = v.place_id WHERE v.visit_date > ?",
                UNIX_EPOCH, 1_000_000),
}
DEFAULT_PATHS = {"safari": "~/Library/Safari/History.db"}


def _blocked(host: str, exclude: list[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in exclude)


def collect(settings: dict[str, Any], ctx: Any = None) -> CollectResult:
    profiles = settings.get("profiles") or []
    if not profiles:
        raise ValueError("add at least one [[sources.browser.profiles]] entry")
    exclude = [d.lower().removeprefix("www.") for d in settings.get("exclude_domains", [])]

    records: list[Record] = []
    for profile in profiles:
        kind = profile.get("kind")
        if kind not in KINDS:
            raise ValueError(f"browser profile kind must be one of: {', '.join(KINDS)}")
        name = profile.get("name") or kind
        raw_path = profile.get("path") or DEFAULT_PATHS.get(kind)
        if not raw_path:
            raise ValueError(f"browser profile {name!r} needs a path")
        path = Path(raw_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"{name}: history file not found: {path}")
        query, epoch, per_second = KINDS[kind]

        latest = ctx.latest("visit", f"{name}:") if ctx else None
        after = 0.0
        if latest:
            after = (datetime.fromisoformat(latest.replace("Z", "+00:00")) - epoch).total_seconds() * per_second

        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "history.db"
            shutil.copy2(path, copy)
            for suffix in ("-wal", "-shm"):  # recent visits may still be in the write-ahead log
                side = Path(str(path) + suffix)
                if side.exists():
                    shutil.copy2(side, str(copy) + suffix)
            conn = sqlite3.connect(copy)
            try:
                rows = conn.execute(query, (after,)).fetchall()
            finally:
                conn.close()

        for visit_id, url, title, raw_time in rows:
            parts = urlsplit(url or "")
            host = (parts.hostname or "").lower().removeprefix("www.")
            if parts.scheme not in ("http", "https") or _blocked(host, exclude):
                continue
            records.append(Record(
                source="browser", type="visit", external_id=f"{name}:{visit_id}",
                timestamp=to_utc(epoch + timedelta(seconds=raw_time / per_second)),
                title=title or host, content=host, url=url,
                data={"browser": name, "domain": host},
            ))
    return CollectResult(records)
