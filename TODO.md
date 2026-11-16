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

### LeetCode

- [ ] Set `[sources.leetcode] username` and `enabled = true`.
- [ ] Make sure the profile is public, and that "Display my submission history" is on in
      LeetCode's privacy settings if you want the per-day history.
- [ ] Run `uv run papi sync leetcode` and check `/stats`.
- [ ] Know the limit: LeetCode only exposes your latest 20 accepted submissions publicly, so
      individual problems build up from now on. Counts and per-day history go back in full.

### Monkeytype

- [ ] Set `[sources.monkeytype] username` and `enabled = true`. That alone gives totals,
      streak and personal bests.
- [ ] Optional, for every individual test: Monkeytype → Account settings → Ape keys →
      generate one and activate it, then set `ape_key` (or `MONKEYTYPE_APE_KEY`).
- [ ] Keep this source to an hourly sync at most; an ApeKey may fetch results 30 times a day.

### Obsidian

- [ ] Set `[sources.obsidian] vault` to the vault folder and `enabled = true`.
- [ ] List folders to keep out in `exclude` (work notes, journal, anything you would not
      want an AI assistant to read).
- [ ] Run the sync on the Mac that has the vault. If the vault is in iCloud, the path is
      under `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/`.
- [ ] Run `uv run papi sync obsidian` and try `/search?q=<something from a note>`.

### Browser history (Safari, Helium, and the older Firefox, Zen, Chrome)

- [ ] Give your terminal Full Disk Access (System Settings → Privacy & Security), which
      Safari's history needs. Do the same for whatever runs the scheduled sync.
- [ ] Find Helium's history file (a file named `History` inside its profile folder under
      `~/Library/Application Support/`) and add it as a `chromium` profile. The path in
      `config.example.toml` is my best guess; check it.
- [ ] If the old Firefox, Zen and Chrome profiles are still on disk, add them once to
      import their history (`places.sqlite` for Firefox and Zen, `History` for Chrome).
- [ ] Fill in `exclude_domains` before the first sync: banking, health, work, anything
      private. Excluded domains are never stored.
- [ ] Run `uv run papi sync browser` and check `/activity?source=browser&limit=5`.
- [ ] This collector is tested against databases I built to match each browser's schema,
      not real history files. Tell me if a real one fails.

## Candidates: applications to add next

Nothing here is built yet. Both need files only you can get.

### YouTube Music

Why: your listening history. There is no official API, so the route is Google Takeout,
where YouTube Music plays appear inside YouTube's watch history.

- [ ] Go to takeout.google.com, select only **YouTube and YouTube Music**, and under
      "All YouTube data included" choose **history** with format **JSON**.
- [ ] Download and unzip it, and tell me the folder. I will build the collector against
      the real `watch-history.json`.
- [ ] Set Takeout to repeat every two months if you want it kept current.

### HDFC bank statements

Why: catches transactions that never made it into L-Edgar. Lower priority, since L-Edgar
already captures HDFC notifications.

- [ ] In HDFC NetBanking, download an account statement in delimited (CSV) format.
- [ ] Send me the header row and one or two rows with the values changed. I will add an
      importer that matches them against L-Edgar entries.

### Old Spotify and Apple Music history (one-time backfill)

Why: only if you want past years in the life log. Both are one-off data requests.

- [ ] Spotify: Account → Privacy → request **Extended streaming history**.
- [ ] Apple Music: privacy.apple.com → request a copy of **Apple Media Services** data.

### Ruled out

- **Everything MD**: private to your organisation, so it stays out.
- **Notion, Spotify (current)**: you do not use them.

## Not sources, but worth doing with what exists

- [ ] Point your portfolio website at `/projects` and `/me` (needs the API reachable from
      the site's build or server, never from the browser with the token in it).
- [ ] Show `/stats` (LeetCode solved, typing speed) on the portfolio.
- [ ] A small dashboard page over `/spending`, `/stats` and `/activity`.
- [ ] An automation: "when a new repo appears in `/projects`, add it to the portfolio".
