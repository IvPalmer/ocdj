## TraxDB → Pixeldrain sync (local tool)

This folder contains a small Python tool to:

- Scrape TraxDB pages for Pixeldrain **list/folder** links (`https://pixeldrain.com/l/<id>`)
- Compare against your local `traxdb/` library
- Optionally download missing files from Pixeldrain via the official API

Pixeldrain API docs: `https://pixeldrain.com/api`

### Quickstart

From the repo root:

```bash
cd "/Users/palmer/Music/Musicas/Electronic/ID3"
. .venv/bin/activate
python3 traxdb_sync/sync.py --config traxdb_sync/config.json --max-pages 50
bash traxdb_sync/run_download_bg.sh
```

- `sync.py` writes the link report to: `traxdb_sync_report_links.json`
- `run_download_bg.sh` writes logs/progress to: `logs/`

### 1) Install dependencies

From your ID3 folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r traxdb_sync/requirements.txt
```

Or run the helper:

```bash
bash traxdb_sync/setup_venv.sh
```

### 2) Create a Pixeldrain API key

- In Pixeldrain account settings, create an API key.
- Export it as an env var (recommended):

```bash
export PIXELDRAIN_API_KEY="YOUR_KEY_HERE"
```

Pixeldrain uses **HTTP Basic auth** and the API key is the **password**.

Important: do **not** hardcode your API key into `*.py` files. If you already did, rotate/revoke that key in Pixeldrain and use env/config instead.

### 2.1) Optional: config file (no repeated flags)

Create a config file at either:

- `~/.config/traxdb_sync/config.json` (recommended), or
- `traxdb_sync/config.json` (local workspace)

### 3) Provide TraxDB authentication (cookies)

Because TraxDB uses Google social login, this tool does **not** ask for your Google password.
Instead, export a cookie jar from your browser after you’ve logged into TraxDB.

Supported formats:

- **Netscape cookies file**: `cookies.txt`
- **JSON cookie list**: `cookies.json` with items like `{ "name": "...", "value": "...", "domain": "...", "path": "/" }`

Save it somewhere local (example):

- `/Users/palmer/Music/Musicas/Electronic/ID3/traxdb_sync/cookies.txt`

You can also set:

```bash
export TRAXDB_COOKIES="/absolute/path/to/cookies.txt"
export TRAXDB_START_URL="https://traxdb2.blogspot.com/search?updated-max=2025-11-18T08:44:00-03:00"
```

### 4) Run in report-only mode (no downloads)

If you already have `traxdb_sync/config.json` filled out, you can run:

```bash
python3 traxdb_sync/sync.py --config traxdb_sync/config.json --max-pages 50
```

Or override everything via flags:

```bash
python3 traxdb_sync/sync.py \
  --traxdb-root "/Users/palmer/Music/Musicas/Electronic/ID3/traxdb" \
  --traxdb-start-url "https://traxdb2.blogspot.com/search?updated-max=2025-11-18T08:44:00-03:00" \
  --traxdb-cookies "/Users/palmer/Music/Musicas/Electronic/ID3/traxdb_sync/cookies.txt" \
  --max-pages 50
```

This writes `traxdb_sync_report_<timestamp>.json` with:

- Pixeldrain list IDs discovered
- A per-list “download plan”
- Any errors (auth failures, etc.)

### 5) Run with downloads enabled

Add `--download`. If you want a fast dedupe by filename across your whole library, also add `--skip-existing-by-name`.

```bash
python3 traxdb_sync/sync.py \
  --traxdb-root "/Users/palmer/Music/Musicas/Electronic/ID3/traxdb" \
  --traxdb-start-url "https://traxdb2.blogspot.com/search?updated-max=2025-11-18T08:44:00-03:00" \
  --traxdb-cookies "/Users/palmer/Music/Musicas/Electronic/ID3/traxdb_sync/cookies.txt" \
  --max-pages 20 \
  --skip-existing-by-name \
  --download
```

### Output layout

- If the scraper can infer a date (`YYYY-MM-DD`) from the page text, downloads go to `traxdb/<YYYY-MM-DD>/`.
- Otherwise, downloads go to `traxdb/_inbox/<list_id>/` for manual sorting.

The tool also maintains a local state file:

- `traxdb/.pixeldrain_lists_seen.json` (list IDs already processed)

### Download all lists from a saved report (recommended for “complete the lists”)

If you already generated a report with `sync.py` and want to fully download every Pixeldrain list in it:

```bash
python3 traxdb_sync/download_from_report.py \
  --config traxdb_sync/config.json \
  --traxdb-root "/Users/palmer/Music/Musicas/Electronic/ID3/traxdb" \
  --report "/Users/palmer/Music/Musicas/Electronic/ID3/traxdb_sync_report_links.json"
```

If a destination file exists but has a size mismatch, the tool will back it up and (by default) download a side-by-side `*.pixeldrain` file. If you prefer to overwrite mismatches, add `--overwrite-mismatch`.

### Background run + progress (recommended)

This will start a background download and write:

- a `logs/*.log` file you can `tail -f`
- a `logs/*.progress.json` file that updates continuously

```bash
bash traxdb_sync/run_download_bg.sh
```

### Audit (verify local files match Pixeldrain list contents)

```bash
python3 traxdb_sync/audit.py \
  --config traxdb_sync/config.json \
  --traxdb-root "/Users/palmer/Music/Musicas/Electronic/ID3/traxdb" \
  --report "/Users/palmer/Music/Musicas/Electronic/ID3/traxdb_sync_report_links.json" \
  --global-search-by-name \
  --report-path "/Users/palmer/Music/Musicas/Electronic/ID3/logs/traxdb_audit_latest.json"
```


