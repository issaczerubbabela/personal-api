"""Expenses from a CSV export or directly from your expense tracker's SQLite database."""

from __future__ import annotations

import csv
import glob
import hashlib
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..db import Record, to_utc
from . import CollectResult

DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d %b %Y", "%Y/%m/%d")


def _parse_date(value: Any, date_format: str | None) -> str:
    if isinstance(value, (int, float)):  # unix seconds or milliseconds
        return to_utc(datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc))
    text = str(value).strip()
    if date_format:
        return to_utc(datetime.strptime(text, date_format))
    try:
        return to_utc(text)
    except ValueError:
        pass
    for fmt in DATE_FORMATS:
        try:
            return to_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {text!r}; set sources.expenses.date_format")


def _parse_amount(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = "".join(ch for ch in str(value) if ch.isdigit() or ch in ".-")
    return float(text)


def _csv_rows(settings: dict[str, Any]) -> Iterator[dict[str, Any]]:
    columns = {"date": "date", "amount": "amount", "description": "description",
               "category": "category", **settings.get("columns", {})}
    pattern = str(Path(settings["path"]).expanduser())
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise FileNotFoundError(f"no files match {pattern}")
    for path in paths:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if not (row.get(columns["date"]) or "").strip():
                    continue
                yield {key: row.get(header, "") for key, header in columns.items()}


def _sqlite_rows(settings: dict[str, Any]) -> Iterator[dict[str, Any]]:
    path = Path(settings["path"]).expanduser().resolve()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        for row in conn.execute(settings["query"]):
            yield dict(row)
    finally:
        conn.close()


def collect(settings: dict[str, Any]) -> CollectResult:
    if not settings.get("path"):
        raise ValueError("sources.expenses.path is not set")
    kind = settings.get("kind", "csv")
    if kind == "sqlite" and not settings.get("query"):
        raise ValueError("sources.expenses.query is required when kind = 'sqlite'")
    rows = _sqlite_rows(settings) if kind == "sqlite" else _csv_rows(settings)
    currency = settings.get("currency", "")

    records: list[Record] = []
    seen: Counter[str] = Counter()
    for row in rows:
        timestamp = _parse_date(row["date"], settings.get("date_format"))
        amount = _parse_amount(row["amount"])
        description = str(row.get("description") or "").strip()
        category = str(row.get("category") or "").strip()
        # Rows have no id of their own, so derive one from the contents; the counter
        # keeps genuinely identical rows (two coffees, same day) distinct.
        key = row.get("id") or f"{timestamp}|{amount}|{description}|{category}"
        seen[str(key)] += 1
        digest = hashlib.sha1(f"{key}#{seen[str(key)]}".encode()).hexdigest()[:16]
        records.append(
            Record(
                source="expenses",
                type="expense",
                external_id=digest,
                timestamp=timestamp,
                title=description or category or "Expense",
                content=category,
                data={"amount": amount, "currency": currency, "category": category or None},
            )
        )
    return CollectResult(records)
