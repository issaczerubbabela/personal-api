# To do

What you have to do yourself, grouped by application. The code for everything under
"Built" is already in this repo; these are the steps only you can do (accounts,
credentials, your own machine).

## Built

### Personal API (the core)

- [ ] Clone the repo on the machine that will run it and run `uv sync`.
- [ ] `cp config.example.toml config.toml`.
- [ ] Generate an API token and put it in `[api] token`:
      `python -c "import secrets; print(secrets.token_urlsafe(32))"`
- [ ] Fill in `[profile]` (name, bio, location, links). This is what `/me` returns.
- [ ] Run `uv run papi sync`, then `uv run papi serve`, and open `http://127.0.0.1:8000/docs`.
- [ ] Schedule the sync (cron, hourly):
      `0 * * * * cd /path/to/personal-api && uv run papi sync >> data/sync.log 2>&1`
- [ ] Decide how to reach it from other devices. Use Tailscale or similar; do not open a port.
- [ ] Set up a backup of `data/personal.db` (encrypted if it leaves the machine).
- [ ] Merge branch `ccr-a073b827-vgpwhv` into `main` once you are happy with it.

### GitHub

- [ ] Set `[sources.github] username` in `config.toml`.
- [ ] Optional: create a fine-grained personal access token (read-only: Metadata, Contents)
      and set it as `token` or `GITHUB_TOKEN`. Without it you get public repos only and
      60 requests an hour.
- [ ] Run `uv run papi sync github` and check `/projects`. This collector has only been
      tested against mocked responses, so tell me if the first real run fails.

### Google Calendar

- [ ] In Google Calendar: Settings → your calendar → Integrate calendar → copy
      **Secret address in iCal format**.
- [ ] Set it as `[sources.calendar] ics_url` (or `CALENDAR_ICS_URL`) and set `enabled = true`.
- [ ] Repeat later for other calendars if you want them (one URL per calendar; today the
      collector takes one).
- [ ] Run `uv run papi sync calendar` and check `/activity?source=calendar`.

### L-Edgar (expense tracker)

- [ ] Open L-Edgar → More → Settings → Database Setup (Sheets) and copy the Apps Script
      deployment URL.
- [ ] Set `[sources.expenses] kind = "ledgar"`, `script_url = "<that URL>"`, `enabled = true`
      (or use `LEDGAR_SCRIPT_URL`).
- [ ] Make sure the deployed script is version 2 (the one the current app requires), so
      rows carry a Transaction ID.
- [ ] Run `uv run papi sync expenses`, then check `/spending?group_by=month` against the
      app's Insights screen for one month.
- [ ] Remember that every Apps Script redeploy changes the URL: update `config.toml` when
      you update the app.
- [ ] Consider adding a shared secret to the script. Today anyone holding the URL can read
      and write the ledger.

### MCP server (AI agents)

- [ ] Register it with Claude Code:
      `claude mcp add personal -- uv run --directory /path/to/personal-api papi --config /path/to/personal-api/config.toml mcp`
- [ ] Or add the JSON block from the README to Claude Desktop's config.
- [ ] Test with a real question, e.g. "what did I spend on food last month?" and
      "what was I working on in the first week of November?"
- [ ] Decide which assistants you are comfortable giving this data to; whatever a tool
      returns is sent to that assistant's provider.

## Candidates: applications to add next

Ordered by value for effort. Nothing here is built yet. Each needs a collector module
(about 50–100 lines) plus the setup steps listed.

### Everything MD (your notes)

Why: the best source for "an agent that knows you", and search becomes far more useful.
Effort: low. The notes are files you already own.

- [ ] Tell me where the notes live (a folder of `.md` files? a database? a repo?).
- [ ] Decide which folders are in scope and which are private and stay out.

### LeetCode

Why: coding streaks and a "problems solved" figure for the portfolio.
Effort: low. Unofficial public GraphQL endpoint, no login for public profiles.

- [ ] Give me your LeetCode username and make sure the profile is public.
- [ ] Accept that it is unofficial and can break without notice.

### Notion

Why: project notes, reading lists and any databases you keep there.
Effort: low to medium. Official API.

- [ ] Create an internal integration at notion.so/my-integrations and copy its token.
- [ ] Share the specific pages and databases you want synced with that integration.
- [ ] Tell me which databases matter (tasks, reading list, journal, ...).

### Spotify

Why: listening history for the life log.
Effort: medium. Official API, but OAuth, and it only returns the last 50 plays, so the
sync must run at least hourly to avoid gaps.

- [ ] Create an app at developer.spotify.com and note the client ID and secret.
- [ ] Run a one-time login in the browser to get a refresh token (I will add a
      `papi auth spotify` command for this).
- [ ] Optional: request your extended streaming history from Spotify's privacy page to
      backfill older years.

### Browser history

Why: fills in "what was I reading or researching that day".
Effort: low. Chrome and Firefox keep it in a local SQLite file.

- [ ] Run the API on the same machine as the browser, or copy the history file to it.
- [ ] Decide on a domain blocklist (banking, health, anything you do not want indexed).

### Gmail

Why: receipts, bookings and "who was I talking to about X".
Effort: medium to high. OAuth with a sensitive scope, and it is the riskiest dataset here.

- [ ] Create a Google Cloud project, enable the Gmail API, and create OAuth credentials.
- [ ] Decide the scope: I recommend metadata only (sender, subject, date, labels) or a
      single label such as `Receipts`, not full message bodies.

### Google Takeout (YouTube history, location, Chrome)

Why: years of history in one go.
Effort: medium. Export files only; no API.

- [ ] Request a Takeout export for YouTube history and (if enabled) Timeline, in JSON.
- [ ] Download and unzip it into a folder the collector reads.
- [ ] Repeat every few months, or schedule Takeout's recurring export to Drive.

### Health and fitness (Google Fit / Health Connect / Apple Health)

Why: steps, sleep and workouts for the dashboard.
Effort: medium. Mostly export-file based on the phone.

- [ ] Tell me which phone and which app holds the data.
- [ ] Set up a periodic export to a folder (Health Connect export, or Tasker on Android).

### Bank statements

Why: a check on L-Edgar, catching transactions you never logged.
Effort: medium. No personal API; statement CSVs only. L-Edgar already parses HDFC, Axis
and CUB notifications, so this is lower priority.

- [ ] Download monthly statement CSVs per bank into a folder.
- [ ] Send me one statement's header row per bank (no real rows needed).

### WhatsApp, Instagram, LinkedIn, X

Why: completeness. Effort: high for the value; all are manual export files with formats
that change. I would leave these until you have a concrete use for them.

- [ ] Request each platform's data export when you want it.

## Not sources, but worth doing with what exists

- [ ] Point your portfolio website at `/projects` and `/me` (needs the API reachable from
      the site's build or server, never from the browser with the token in it).
- [ ] A small dashboard page over `/spending` and `/activity`.
- [ ] An automation: "when a new repo appears in `/projects`, add it to the portfolio".
