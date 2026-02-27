## soulseek_sync (slskd-powered)

This module drives **slskd** (a headless Soulseek client) to **search + enqueue downloads** from a plain text list.

- slskd project: `https://github.com/slskd/slskd`
- Background runner: `soulseek_sync/run_bg.sh`

### Quickstart

1) Ensure slskd is running and connected.

2) Create Soulseek config (one-time):

```bash
cp soulseek_sync/config.example.json soulseek_sync/config.json
```

Edit `soulseek_sync/config.json` and set `slskd_api_key`.

3) Run:

```bash
soulseek_sync/run_now.sh
```

### What this does

- Reads a list of queries from `wanted.txt` (one per line)
- Searches slskd for each query
- Picks the best match using your quality policy:
  - **AIFF** (preferred)
  - then **FLAC**
  - then **WAV**
  - then **MP3 @ 320kbps only**
  - rejects anything below those requirements
- Enqueues the chosen result for download
- Supports downloading a *full release folder* (see input format below)
- Writes:
  - live progress JSON
  - final report JSON

### 1) Run slskd

You can run slskd via Docker (recommended) or by cloning/building it.
See the slskd docs: `https://github.com/slskd/slskd`

You must configure in slskd:
- Soulseek username/password
- API key/token (for programmatic access)
- Download directory

**Important**: set slskd's download directory to point at this repo's `soulseek/` folder (or a folder you choose).

### 2) Python deps

If you're using the same `.venv` as other tools in this repo:

```bash
cd "/Users/palmer/Music/Musicas/Electronic/ID3"
. .venv/bin/activate
pip install -r soulseek_sync/requirements.txt
```

### 3) Config

Create `soulseek_sync/config.json` (copy from `soulseek_sync/config.example.json`) or set env vars:

- `slskd_base_url`: e.g. `http://localhost:5030`
- `slskd_api_key`: slskd API key/token
- `soulseek_root`: where downloads should go (default: `<repo>/soulseek`)

Env var equivalents:
- `SLSKD_BASE_URL`
- `SLSKD_API_KEY`
- `SOULSEEK_ROOT`

### 4) Run

```bash
python3 -m soulseek_sync.run --wanted soulseek_sync/wanted.txt
```

### 5) Run in background

```bash
bash soulseek_sync/run_bg.sh
```

Logs and JSON outputs go in `logs/`.

### Helper scripts

- Foreground run: `soulseek_sync/run_now.sh`
- Background run: `soulseek_sync/run_bg.sh`
- Dry-run: `soulseek_sync/run_dry.sh`
- Queue/status: `soulseek_sync/status.sh`

### wanted.txt format

- **Track**:
  - `track: Artist - Track Title`

- **Release folder** (downloads the best matching folder for a user, enqueuing all qualifying files inside it):
  - `release: Artist - Album Title`

For release folders:
- audio formats are filtered by policy (**AIFF > FLAC > WAV > MP3@320 only**)
- common extras (`.cue`, `.log`, `.jpg`, etc) are allowed

### Search behavior

The runner performs **exactly one slskd search per line**, using the text you provide (after trimming whitespace).


