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
                        lambda s, ctx: real_github(s, transport=github_transport()))
    monkeypatch.setitem(collectors.COLLECTORS, "calendar", lambda s, ctx: real_calendar(s, now=NOW))
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


# --- L-Edgar (Apps Script) ---------------------------------------------------------------

LEDGAR_ROWS = [
    {"id": "tx-1", "revision": "a", "timestamp": "3/3/2026 19:02:11", "date": "2026-03-03",
     "type": "Expense", "expCategory": "Food", "incCategory": "", "description": "Biryani",
     "amount": 320, "accountName": "HDFC", "fromAccountName": "", "toAccountName": "",
     "remarks": "with friends", "isBookmarked": False},
    {"id": "tx-2", "date": "2026-03-01", "type": "Income", "expCategory": "", "incCategory": "Salary",
     "description": "March salary", "amount": 50000, "accountName": "HDFC", "remarks": ""},
    {"id": "tx-3", "date": "2026-03-02", "type": "Transfer", "expCategory": "", "incCategory": "",
     "description": "", "amount": 5000, "accountName": "", "fromAccountName": "HDFC",
     "toAccountName": "Wallet", "remarks": ""},
    {"id": "tx-4", "date": "2026-02-10", "type": "Expense", "expCategory": "Travel",
     "description": "Train", "amount": 480.5, "accountName": "Wallet", "remarks": ""},
]


def ledgar_transport(rows):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["target"] == "transactions"
        return httpx.Response(200, json={"status": "ok", "count": len(rows), "data": rows,
                                         "scriptVersion": 2})
    return httpx.MockTransport(handler)


@pytest.fixture
def ledgar(tmp_path, monkeypatch):
    from personal_api import collectors
    rows = list(LEDGAR_ROWS)
    monkeypatch.setitem(collectors.COLLECTORS, "expenses",
                        lambda s, ctx: expenses.collect(s, transport=ledgar_transport(rows)))
    config = {
        "api": {"token": "secret"}, "profile": {"name": "Test"},
        "database": {"path": str(tmp_path / "db.sqlite")},
        "sources": {"expenses": {"enabled": True, "kind": "ledgar",
                                 "script_url": "https://script.example/exec"}},
    }
    conn = connect(config["database"]["path"])
    assert sync(conn, config)["expenses"] == {"ok": True, "records": 4}
    return config, conn, rows


def test_ledgar_sync_and_spending(ledgar):
    config, conn, rows = ledgar
    client = TestClient(create_app(config), headers={"Authorization": "Bearer secret"})

    transfer = client.get("/activity", params={"type": "transfer"}).json()["items"][0]
    assert transfer["title"] == "Transfer HDFC -> Wallet"
    assert transfer["data"]["to_account"] == "Wallet"
    assert client.get("/search", params={"q": "friends"}).json()["items"][0]["title"] == "Biryani"

    by_category = client.get("/spending").json()
    assert by_category["total"] == 800.5  # income and transfers are not spending
    assert by_category["groups"] == [{"key": "Travel", "total": 480.5, "count": 1},
                                     {"key": "Food", "total": 320.0, "count": 1}]
    march = client.get("/spending", params={"since": "2026-03-01", "group_by": "month"}).json()
    assert march["groups"] == [{"key": "2026-03", "total": 320.0, "count": 1}]
    assert client.get("/spending", params={"type": "income"}).json()["total"] == 50000
    assert client.get("/spending", params={"group_by": "nope"}).status_code == 422

    # Edited and deleted in the Sheet -> same after the next sync.
    rows[0] = rows[0] | {"amount": 400}
    del rows[3]
    sync(conn, config)
    assert client.get("/spending").json()["groups"] == [{"key": "Food", "total": 400.0, "count": 1}]


def test_ledgar_script_error_is_reported(tmp_path):
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"status": "error", "message": "boom", "data": []}))
    with pytest.raises(ValueError, match="boom"):
        expenses.collect({"kind": "ledgar", "script_url": "https://script.example/exec"},
                         transport=transport)


# --- MCP ---------------------------------------------------------------------------------

def test_mcp_tools(ledgar):
    import anyio
    from mcp import Client

    from personal_api.mcp_server import create_server

    config, conn, _ = ledgar

    async def run():
        async with Client(create_server(config)) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            assert tools == {"get_profile", "get_activity", "list_projects", "search", "get_record", "get_stats",
                             "spending_summary"}
            profile = await client.call_tool("get_profile")
            assert profile.structured_content["sources"]["expenses"]["records"] == 4
            summary = await client.call_tool(
                "spending_summary", {"since": "2026-03-01", "until": "2026-04-01"})
            assert summary.structured_content["total"] == 320.0
            found = await client.call_tool("search", {"query": "salary"})
            assert found.structured_content["items"][0]["type"] == "income"
            bad = await client.call_tool("get_activity", {"since": "last tuesday"})
            assert bad.is_error

    anyio.run(run)


# --- LeetCode, Monkeytype, Obsidian, browser --------------------------------------------

def leetcode_transport(calendar_visible=True):
    def handler(request: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(request.content)
        if "userCalendar" in body["query"]:
            if not calendar_visible:
                return httpx.Response(200, json={"errors": [{"message": "no permission"}],
                                                 "data": {"matchedUser": None}})
            year = body["variables"]["y"]
            days = {"1772323200": 4} if year is None else {"1740787200": 2}  # 2026-03-01, 2025-03-01
            return httpx.Response(200, json={"data": {"matchedUser": {"userCalendar": {
                "streak": 3, "totalActiveDays": 2, "activeYears": [2025, 2026],
                "submissionCalendar": json.dumps(days)}}}})
        return httpx.Response(200, json={"data": {
            "matchedUser": {"username": "me", "profile": {"ranking": 1234},
                            "submitStatsGlobal": {"acSubmissionNum": [
                                {"difficulty": "All", "count": 10}, {"difficulty": "Easy", "count": 6},
                                {"difficulty": "Medium", "count": 3}, {"difficulty": "Hard", "count": 1}]}},
            "recentAcSubmissionList": [{"id": "9", "title": "Two Sum", "titleSlug": "two-sum",
                                        "timestamp": "1772359200", "lang": "python3"}],
            "userContestRanking": None}})
    return httpx.MockTransport(handler)


def test_leetcode():
    from personal_api.collectors import leetcode
    records = leetcode.collect({"username": "me"}, transport=leetcode_transport()).records
    by_type = {}
    for r in records:
        by_type.setdefault(r.type, []).append(r)
    assert by_type["solved"][0].url == "https://leetcode.com/problems/two-sum/"
    assert {r.external_id: r.data["submissions"] for r in by_type["day"]} == {"2026-03-01": 4, "2025-03-01": 2}
    stats = by_type["stats"][0]
    assert stats.title == "LeetCode: 10 solved (6 easy, 3 medium, 1 hard)"
    assert stats.data["streak"] == 3 and stats.data["contest_rating"] is None

    hidden = leetcode.collect({"username": "me"}, transport=leetcode_transport(False)).records
    assert {r.type for r in hidden} == {"solved", "stats"}  # private calendar is not fatal


def test_monkeytype_is_incremental(tmp_path):
    from personal_api import collectors
    from personal_api.collectors import monkeytype
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/results":
            assert request.headers["Authorization"] == "ApeKey k"
            calls.append(dict(request.url.params))
            return httpx.Response(200, json={"message": "ok", "data": [
                {"_id": "r1", "wpm": 92.5, "rawWpm": 95, "acc": 97.1, "mode": "time", "mode2": "30",
                 "timestamp": 1772359200000, "testDuration": 30, "isPb": True}]})
        return httpx.Response(200, json={"message": "ok", "data": {
            "name": "Me", "streak": 2, "maxStreak": 9,
            "typingStats": {"completedTests": 40, "startedTests": 90, "timeTyping": 7200},
            "personalBests": {"time": {"30": [{"wpm": 92.5}, {"wpm": 80}], "60": []},
                              "words": {"10": [{"wpm": 110}]}}}})

    config = {"database": {"path": str(tmp_path / "db.sqlite")},
              "sources": {"monkeytype": {"enabled": True, "username": "me", "ape_key": "k"}}}
    conn = connect(config["database"]["path"])
    original = collectors.COLLECTORS["monkeytype"]
    collectors.COLLECTORS["monkeytype"] = lambda s, ctx: monkeytype.collect(
        s, ctx, transport=httpx.MockTransport(handler))
    try:
        assert sync(conn, config)["monkeytype"] == {"ok": True, "records": 2}
        sync(conn, config)
    finally:
        collectors.COLLECTORS["monkeytype"] = original
    assert "onOrAfterTimestamp" not in calls[0]
    assert calls[1]["onOrAfterTimestamp"] == "1772359200000"

    from personal_api import queries
    stats = queries.stats(conn)["monkeytype"]
    assert stats["best_wpm"] == {"time 30": 92.5, "words 10": 110}
    assert stats["hours_typing"] == 2.0
    test = queries.activity(conn, source="monkeytype", type="test")["items"][0]
    assert test["title"] == "92.5 wpm, 97.1% accuracy (time 30)"
    assert queries.activity(conn)["total"] == 1  # the stats snapshot is not timeline activity


def test_obsidian(tmp_path):
    from personal_api import queries
    vault = tmp_path / "Vault"
    (vault / "Projects").mkdir(parents=True)
    (vault / "Private").mkdir()
    (vault / ".obsidian").mkdir()
    (vault / "Projects" / "Personal API.md").write_text(
        "---\ntags: [project, api]\n---\nCollectors sync into SQLite. See [[Ideas|my ideas]] #python\n"
        + "filler " * 200)
    (vault / "Ideas.md").write_text("---\ntags:\n  - inbox\n---\nBuild a life log.")
    (vault / "Private" / "Diary.md").write_text("secret")
    (vault / ".obsidian" / "x.md").write_text("config")
    config = {"database": {"path": str(tmp_path / "db.sqlite")},
              "sources": {"obsidian": {"enabled": True, "vault": str(vault), "exclude": ["Private"]}}}
    conn = connect(config["database"]["path"])
    assert sync(conn, config)["obsidian"] == {"ok": True, "records": 2}

    hit = queries.search(conn, "collectors sqlite")["items"][0]
    assert hit["title"] == "Personal API" and hit["content_truncated"]
    assert hit["data"]["tags"] == ["api", "project", "python"] and hit["data"]["links"] == ["Ideas"]
    assert queries.record(conn, hit["id"])["content"].endswith("filler")
    assert queries.search(conn, "secret")["total"] == 0

    (vault / "Ideas.md").unlink()
    sync(conn, config)
    assert [i["title"] for i in queries.activity(conn, source="obsidian")["items"]] == ["Personal API"]


def test_browser_history(tmp_path):
    import sqlite3
    from datetime import timedelta
    from personal_api import queries
    from personal_api.collectors import browser

    when = datetime(2026, 3, 4, 10, 0, tzinfo=timezone.utc)

    def make(name, schema, rows):
        db = sqlite3.connect(tmp_path / name)
        db.executescript(schema)
        for table, values in rows:
            db.execute(f"INSERT INTO {table} VALUES ({','.join('?' * len(values))})", values)
        db.commit()
        db.close()
        return str(tmp_path / name)

    safari = make("History.db",
                  "CREATE TABLE history_items (id INTEGER PRIMARY KEY, url TEXT);"
                  "CREATE TABLE history_visits (id INTEGER PRIMARY KEY, history_item INTEGER, visit_time REAL, title TEXT);",
                  [("history_items", (1, "https://docs.python.org/3/")),
                   ("history_visits", (1, 1, (when - browser.MAC_EPOCH).total_seconds(), "Python docs"))])
    micro = lambda epoch: int((when - epoch) / timedelta(microseconds=1))
    chromium = make("History",
                    "CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT);"
                    "CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER);",
                    [("urls", (1, "https://www.github.com/x", "GitHub")),
                     ("urls", (2, "https://netbanking.hdfcbank.com/login", "Bank")),
                     ("urls", (3, "chrome://settings", "Settings")),
                     ("visits", (1, 1, micro(browser.WEBKIT_EPOCH))),
                     ("visits", (2, 2, micro(browser.WEBKIT_EPOCH))),
                     ("visits", (3, 3, micro(browser.WEBKIT_EPOCH)))])
    firefox = make("places.sqlite",
                   "CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT);"
                   "CREATE TABLE moz_historyvisits (id INTEGER PRIMARY KEY, place_id INTEGER, visit_date INTEGER);",
                   [("moz_places", (1, "https://monkeytype.com/", None)),
                    ("moz_historyvisits", (1, 1, micro(browser.UNIX_EPOCH)))])

    config = {"database": {"path": str(tmp_path / "db.sqlite")}, "sources": {"browser": {
        "enabled": True, "exclude_domains": ["hdfcbank.com"],
        "profiles": [{"kind": "safari", "path": safari},
                     {"kind": "chromium", "name": "helium", "path": chromium},
                     {"kind": "firefox", "name": "zen", "path": firefox}]}}}
    conn = connect(config["database"]["path"])
    assert sync(conn, config)["browser"] == {"ok": True, "records": 3}
    assert sync(conn, config)["browser"] == {"ok": True, "records": 0}  # nothing new

    visits = queries.activity(conn, source="browser")["items"]
    assert {v["timestamp"] for v in visits} == {"2026-03-04T10:00:00Z"}
    assert {(v["data"]["browser"], v["data"]["domain"], v["title"]) for v in visits} == {
        ("safari", "docs.python.org", "Python docs"), ("helium", "github.com", "GitHub"),
        ("zen", "monkeytype.com", "monkeytype.com")}
    assert queries.activity(conn)["total"] == 0  # hidden from the default timeline
