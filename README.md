# personal-api

A self-hosted API over your own data. Scheduled collectors copy data from each service
into one SQLite database you own; the API reads only that local copy, so it is fast,
works offline, and keeps working if a service locks you out.

```
collectors (GitHub, calendar, expenses)  ->  SQLite (records + full-text index)  ->  HTTP API (FastAPI)
                                                                                 ->  MCP server (AI agents)
```

## Quick start

```bash
uv sync
cp config.example.toml config.toml      # then edit: api.token, profile, sources
uv run papi sync                        # pull data into data/personal.db
uv run papi serve                       # http://127.0.0.1:8000  (docs at /docs)

curl -H "Authorization: Bearer $TOKEN" "localhost:8000/activity?since=2026-03-01"
```

Run `papi sync` on a schedule, for example hourly with cron:

```
0 * * * * cd /path/to/personal-api && uv run papi sync >> data/sync.log 2>&1
```

## Endpoints

Every endpoint needs `Authorization: Bearer <api.token>`.

| Endpoint | What it returns |
| --- | --- |
| `GET /me` | Your profile from `config.toml`, plus record counts and last sync status per source |
| `GET /activity?since=&until=&source=&type=&limit=&offset=` | One timeline across all sources, newest first |
| `GET /projects?include_forks=&include_private=&include_archived=` | Your repositories, most recently pushed first |
| `GET /search?q=&source=` | Full-text search over titles and content |
| `GET /spending?since=&until=&group_by=&type=` | Money totals grouped by `category`, `month`, `day` or `account`; `type` is `expense` (default), `income` or `transfer` |

## MCP server

`papi mcp` runs a read-only [MCP](https://modelcontextprotocol.io) server over stdio, so an
AI agent on the same machine can ask about your projects, schedule and spending. It reads the
same SQLite file and exposes five tools: `get_profile`, `get_activity`, `list_projects`,
`search` and `spending_summary`.

Claude Code:

```bash
claude mcp add personal -- uv run --directory /path/to/personal-api papi --config /path/to/personal-api/config.toml mcp
```

Claude Desktop and other clients (`mcpServers` in their config file):

```json
{
  "mcpServers": {
    "personal": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/personal-api", "papi",
               "--config", "/path/to/personal-api/config.toml", "mcp"]
    }
  }
}
```

There is no network listener and so no token: only a program you launch locally can talk
to it. Whatever the agent reads goes to that agent's model provider, so connect it only to
assistants you would hand this data to.

## Sources

| Source | How it syncs | Setup |
| --- | --- | --- |
| `github` | REST API: repos and the last ~90 days of activity | Username; a token is optional and adds private repos |
| `calendar` | iCal feed, recurring events expanded in a rolling window | Google Calendar → Settings → your calendar → *Secret address in iCal format* |
| `expenses` | [L-Edgar](https://github.com/issaczerubbabela/L-Edgar): the Google Sheet, read through the app's Apps Script web app. Also any CSV export or SQLite database | `kind = "ledgar"` and the Apps Script deployment URL |

### L-Edgar

L-Edgar's database lives on the phone, but every transaction is synced to a Google Sheet,
and the Apps Script web app the phone talks to can return that Sheet as JSON. Set
`script_url` to the deployment URL from *More → Settings → Database Setup (Sheets)*.

- Expenses, income and transfers become `expenses/expense`, `expenses/income` and
  `expenses/transfer` records, keyed by the Sheet's permanent Transaction ID, so an edit
  or deletion in the app or the Sheet carries over on the next sync.
- Data is as fresh as the phone's last sync to the Sheet.
- The script's read path fills in a Transaction ID for any row that lacks one, exactly as
  it does when the app imports. Nothing else is written.
- The deployment URL lets anyone who has it read and write the ledger. Keep it out of git.

Syncing is idempotent: records are upserted on `(source, type, external_id)`. One
failing source does not stop the others, and the failure shows up in `/me`.

## Data model

Everything is one row in `records`:

| Column | Meaning |
| --- | --- |
| `source`, `type` | `github`/`repo`, `github`/`event`, `calendar`/`event`, `expenses`/`expense`, `expenses`/`income`, `expenses`/`transfer` |
| `external_id` | Stable id within the source |
| `timestamp` | UTC, `YYYY-MM-DDTHH:MM:SSZ` |
| `title`, `content` | Searchable text |
| `url` | Link back to the original |
| `data` | Source-specific JSON (amount, language, location, ...) |

## Adding a source

Add a module in `personal_api/collectors/` with a `collect(settings) -> CollectResult`
function that returns `Record`s, and register it in `COLLECTORS` in
`personal_api/collectors/__init__.py`. Its settings come from `[sources.<name>]`.

## Security

This is your most sensitive dataset.

- The server binds to `127.0.0.1` by default. To reach it from other devices, put it
  behind Tailscale or a similar private network rather than opening a port.
- `config.toml` and `data/` are gitignored. Never commit them.
- The calendar's secret iCal URL and the GitHub token are credentials; they can also be
  supplied as `CALENDAR_ICS_URL` and `GITHUB_TOKEN`, the L-Edgar URL as `LEDGAR_SCRIPT_URL`,
  and the API token as `PAPI_TOKEN`.
- `/projects` hides private repos unless you pass `include_private=true`, so a website
  can read it without leaking them. `/activity` and `/search` return everything.

## Tests

```bash
uv run pytest
```
