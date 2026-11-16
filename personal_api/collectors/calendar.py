"""Calendar via an iCal feed (Google Calendar's "secret address in iCal format") or .ics file.

No OAuth needed. Recurring events are expanded inside a rolling window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import icalendar
import recurring_ical_events

from ..db import Record, to_utc
from . import CollectResult


def collect(settings: dict[str, Any], ctx: Any = None, *, now: datetime | None = None) -> CollectResult:
    location = settings.get("ics_url")
    if not location:
        raise ValueError("sources.calendar.ics_url is not set")
    if location.startswith(("http://", "https://")):
        response = httpx.get(location, timeout=30, follow_redirects=True)
        response.raise_for_status()
        raw = response.content
    else:
        raw = Path(location).expanduser().read_bytes()

    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=int(settings.get("past_days", 90)))
    end = now + timedelta(days=int(settings.get("future_days", 60)))

    cal = icalendar.Calendar.from_ical(raw)
    records: list[Record] = []
    for event in recurring_ical_events.of(cal).between(start, end):
        begins = event["DTSTART"].dt
        ends = event["DTEND"].dt if "DTEND" in event else None
        all_day = not isinstance(begins, datetime)
        timestamp = to_utc(begins)
        if not (to_utc(start) <= timestamp < to_utc(end)):
            continue  # overlaps the window but starts outside it
        records.append(
            Record(
                source="calendar",
                type="event",
                external_id=f"{event.get('UID', '')}@{timestamp}",
                timestamp=timestamp,
                title=str(event.get("SUMMARY", "")),
                content=str(event.get("DESCRIPTION", "")),
                url=str(event["URL"]) if "URL" in event else None,
                data={
                    "end": to_utc(ends) if ends else None,
                    "all_day": all_day,
                    "location": str(event.get("LOCATION", "")) or None,
                    "status": str(event.get("STATUS", "")) or None,
                },
            )
        )
    return CollectResult(records, replace_window=(to_utc(start), to_utc(end)))
