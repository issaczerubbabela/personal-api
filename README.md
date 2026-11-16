# personal-api

A self-hosted API over your own data. Scheduled collectors copy data from each service
into one SQLite database you own; the API reads only that local copy, so it is fast,
works offline, and keeps working if a service locks you out.

```
collectors (GitHub, calendar, expenses)  ->  SQLite (records + full-text index)  ->  FastAPI
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

## Sources

| Source | How it syncs | Setup |
| --- | --- | --- |
| `github` | REST API: repos and the last ~90 days of activity | Username; a token is optional and adds private repos |
| `calendar` | iCal feed, recurring events expanded in a rolling window | Google Calendar → Settings → your calendar → *Secret address in iCal format* |
| `expenses` | A CSV export (file or glob), or your tracker's SQLite database read-only via a query | Map your column names in `config.toml` |

Syncing is idempotent: records are upserted on `(source, type, external_id)`. One
failing source does not stop the others, and the failure shows up in `/me`.

## Data model

Everything is one row in `records`:

| Column | Meaning |
| --- | --- |
| `source`, `type` | e.g. `github`/`repo`, `github`/`event`, `calendar`/`event`, `expenses`/`expense` |
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
  supplied as `CALENDAR_ICS_URL` and `GITHUB_TOKEN`, and the API token as `PAPI_TOKEN`.
- `/projects` hides private repos unless you pass `include_private=true`, so a website
  can read it without leaking them. `/activity` and `/search` return everything.

## Tests

```bash
uv run pytest
```
