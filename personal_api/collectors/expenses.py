"""Money transactions.

kind = "ledgar": the L-Edgar app's Google Sheet, read through its Apps Script web app.
kind = "csv" / "sqlite": any other tracker, from an export file or its database.
"""

from __future__ import annotations

import csv
import glob
import hashlib
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx

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


def _collect_ledgar(settings: dict[str, Any], transport: httpx.BaseTransport | None) -> CollectResult:
    """L-Edgar keeps every transaction in a Google Sheet; its Apps Script web app returns
    them as JSON with a permanent Transaction ID, so edits and deletions carry over."""
    url = settings.get("script_url")
    if not url:
        raise ValueError("sources.expenses.script_url is not set")
    # Apps Script answers with a redirect to script.googleusercontent.com.
    with httpx.Client(timeout=120, follow_redirects=True, transport=transport) as client:
        response = client.get(url, params={"target": "transactions"})
    response.raise_for_status()
    try:
        body = response.json()
    except ValueError:
        raise ValueError("Apps Script did not return JSON; check the deployment URL and that "
                         "access is set to 'Anyone'") from None
    if body.get("status") != "ok":
        raise ValueError(f"Apps Script error: {body.get('message') or body.get('status')}")

    currency = settings.get("currency", "INR")
    records: list[Record] = []
    seen: Counter[str] = Counter()
    for row in body.get("data") or []:
        kind = str(row.get("type") or "").strip().lower()
        if kind not in ("expense", "income", "transfer") or not row.get("date"):
            continue
        amount = float(row.get("amount") or 0)
        description = str(row.get("description") or "").strip()
        remarks = str(row.get("remarks") or "").strip()
        category = str(row.get("incCategory" if kind == "income" else "expCategory") or "").strip()
        account = str(row.get("accountName") or "").strip()
        from_account = str(row.get("fromAccountName") or "").strip()
        to_account = str(row.get("toAccountName") or "").strip()
        timestamp = _parse_date(row["date"], None)  # yyyy-MM-dd, a calendar day
        external_id = str(row.get("id") or "").strip()
        if not external_id:  # scripts older than version 2 have no Transaction ID
            key = f"{timestamp}|{kind}|{amount}|{description}|{category}"
            seen[key] += 1
            external_id = hashlib.sha1(f"{key}#{seen[key]}".encode()).hexdigest()[:16]
        if kind == "transfer":
            title = description or f"Transfer {from_account} -> {to_account}".strip()
        else:
            title = description or category or kind.capitalize()
        records.append(
            Record(
                source="expenses",
                type=kind,
                external_id=external_id,
                timestamp=timestamp,
                title=title,
                content=" | ".join(p for p in (category, remarks, account, from_account, to_account) if p),
                data={
                    "amount": amount,
                    "currency": currency,
                    "category": category or None,
                    "account": account or None,
                    "from_account": from_account or None,
                    "to_account": to_account or None,
                    "remarks": remarks or None,
                    "bookmarked": bool(row.get("isBookmarked")),
                    "logged_at": row.get("timestamp") or None,
                },
            )
        )
    # The Sheet is the whole ledger, so anything it no longer has was deleted. An empty
    # answer is more likely a wrong tab or deployment than a wiped ledger: keep what we have.
    return CollectResult(records, replace_window=("0000", "9999") if records else None)


def collect(settings: dict[str, Any], transport: httpx.BaseTransport | None = None) -> CollectResult:
    if settings.get("kind") == "ledgar":
        return _collect_ledgar(settings, transport)
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
