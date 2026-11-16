from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from personal_api.api import create_app
from personal_api.collectors import calendar, expenses, github, sync
from personal_api.db import connect

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:standup
DTSTART:20260302T090000Z
DTEND:20260302T091500Z
RRULE:FREQ=DAILY;COUNT=3
SUMMARY:Standup
END:VEVENT
BEGIN:VEVENT
UID:holiday
DTSTART;VALUE=DATE:20260310
DTEND;VALUE=DATE:20260311
SUMMARY:Day off
END:VEVENT
END:VCALENDAR
"""
NOW = datetime(2026, 3, 5, tzinfo=timezone.utc)


def github_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/repos"):
            return httpx.Response(200, json=[
                {"id": 1, "name": "personal-api", "full_name": "me/personal-api",
                 "description": "API over my own data", "html_url": "https://github.com/me/personal-api",
                 "pushed_at": "2026-03-04T10:00:00Z", "created_at": "2026-01-01T00:00:00Z",
                 "language": "Python", "stargazers_count": 3},
                {"id": 2, "name": "a-fork", "full_name": "me/a-fork", "description": None,
                 "html_url": "https://github.com/me/a-fork", "pushed_at": "2026-02-01T00:00:00Z",
                 "created_at": "2026-02-01T00:00:00Z", "fork": True},
            ])
        return httpx.Response(200, json=[
            {"id": "100", "type": "PushEvent", "created_at": "2026-03-04T10:00:00Z",
             "repo": {"name": "me/personal-api"}, "payload": {"ref": "refs/heads/main"}},
        ])
    return httpx.MockTransport(handler)


@pytest.fixture
def config(tmp_path):
    ics = tmp_path / "cal.ics"
    ics.write_text(ICS)
    csv = tmp_path / "expenses.csv"
    csv.write_text("Date,Amount,Note,Category\n"
                   "03/03/2026,\"1,250.50\",Groceries at market,Food\n"
                   "04/03/2026,80,Coffee,Food\n"
                   "04/03/2026,80,Coffee,Food\n")
    return {
        "api": {"token": "secret"},
        "profile": {"name": "Test"},
        "database": {"path": str(tmp_path / "db.sqlite")},
        "sources": {
            "github": {"enabled": True, "username": "me"},
            "calendar": {"enabled": True, "ics_url": str(ics), "past_days": 30, "future_days": 30},
            "expenses": {"enabled": True, "path": str(csv), "currency": "INR",
                         "columns": {"date": "Date", "amount": "Amount", "description": "Note",
                                     "category": "Category"}},
        },
    }


@pytest.fixture
def client(config, monkeypatch):
    real_github, real_calendar = github.collect, calendar.collect
    from personal_api import collectors
    monkeypatch.setitem(collectors.COLLECTORS, "github",
                        lambda s: real_github(s, transport=github_transport()))
    monkeypatch.setitem(collectors.COLLECTORS, "calendar", lambda s: real_calendar(s, now=NOW))
    conn = connect(config["database"]["path"])
    for _ in range(2):  # twice: syncing must be idempotent
        summary = sync(conn, config)
        assert all(r["ok"] for r in summary.values()), summary
    conn.close()
    return TestClient(create_app(config), headers={"Authorization": "Bearer secret"})


def test_auth_required(client):
    assert client.get("/me", headers={"Authorization": ""}).status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_me(client):
    body = client.get("/me").json()
    assert body["profile"] == {"name": "Test"}
    assert body["sources"]["github"]["records"] == 3
    assert body["sources"]["calendar"]["records"] == 4  # 3 standups + 1 all-day
    assert body["sources"]["expenses"]["records"] == 3  # identical rows kept distinct
    assert body["sources"]["expenses"]["last_sync"]["ok"] is True


def test_activity_filters(client):
    everything = client.get("/activity").json()
    assert everything["total"] == 8  # repos excluded from the timeline
    timestamps = [i["timestamp"] for i in everything["items"]]
    assert timestamps == sorted(timestamps, reverse=True)

    recent = client.get("/activity", params={"since": "2026-03-04", "until": "2026-03-05"}).json()
    assert {i["title"] for i in recent["items"]} == {"Pushed to me/personal-api (main)", "Standup", "Coffee"}

    spend = client.get("/activity", params={"source": "expenses"}).json()["items"]
    assert sorted(i["data"]["amount"] for i in spend) == [80.0, 80.0, 1250.5]
    assert spend[-1]["timestamp"] == "2026-03-03T00:00:00Z"  # dd/mm/yyyy parsed

    assert client.get("/activity", params={"since": "nope"}).status_code == 422


def test_projects_hides_forks(client):
    assert [p["name"] for p in client.get("/projects").json()["items"]] == ["personal-api"]
    assert client.get("/projects", params={"include_forks": True}).json()["total"] == 2


def test_search(client):
    assert [i["title"] for i in client.get("/search", params={"q": "grocer"}).json()["items"]] \
        == ["Groceries at market"]
    assert client.get("/search", params={"q": "my data"}).json()["items"][0]["title"] == "personal-api"
    assert client.get("/search", params={"q": 'weird "AND ( query'}).status_code == 200


def test_deleted_calendar_events_are_removed(config, client):
    config["sources"]["calendar"]["ics_url"] = config["sources"]["calendar"]["ics_url"]
    path = config["sources"]["calendar"]["ics_url"]
    text = open(path).read()
    start = text.index("BEGIN:VEVENT\nUID:holiday")
    open(path, "w").write(text[:start] + "END:VCALENDAR\n")
    conn = connect(config["database"]["path"])
    sync(conn, config, ["calendar"])
    conn.close()
    titles = {i["title"] for i in client.get("/activity", params={"source": "calendar"}).json()["items"]}
    assert titles == {"Standup"}


def test_expenses_from_sqlite(tmp_path):
    import sqlite3
    path = tmp_path / "tracker.db"
    src = sqlite3.connect(path)
    src.execute("CREATE TABLE tx (id INTEGER PRIMARY KEY, created_at TEXT, amt REAL, note TEXT, cat TEXT)")
    src.execute("INSERT INTO tx VALUES (1, '2026-03-01T12:00:00+05:30', 499, 'Book', 'Reading')")
    src.commit()
    src.close()
    result = expenses.collect({
        "kind": "sqlite", "path": str(path),
        "query": "SELECT id, created_at AS date, amt AS amount, note AS description, cat AS category FROM tx",
    })
    assert result.records[0].timestamp == "2026-03-01T06:30:00Z"
    assert result.records[0].data["amount"] == 499.0
