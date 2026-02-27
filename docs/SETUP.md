### Setup (current state: Python tools + wrappers)

This repo uses:
- **ID3**: downloads only (music folders/files)
- **Repo**: logs/reports/progress JSON (default: `./logs/`)

#### 1) Bootstrap

From repo root:

```bash
bash bootstrap.sh
```

This creates:
- `./.venv/` (repo-wide venv)
- `./djtools_config.json` (shared config; edit it)
- `./logs/` (artifacts)

#### 2) Configure paths + secrets

Edit `djtools_config.json` and set at least:
- `pixeldrain_api_key`
- `traxdb_cookies`
- `traxdb_start_url`
- `slskd_base_url`
- `slskd_api_key`
- `soulseek_root` (should point into your ID3 folder)

Spotify (for later app work):
- `spotify_client_id`
- `spotify_redirect_uri` (keep `djtools://oauth/spotify`)
- `spotify_playlist_public_default` (set false for private by default)

Telegram (for later app work):
- `telegram_bot_token`
- `telegram_chat_id` (pair it using the steps below)

Optional env vars:
- `DJTOOLS_ID3_ROOT`: overrides the default ID3 path for wrappers
- `DJTOOLS_ARTIFACTS_ROOT`: overrides where logs/reports are written (default: repo root)

#### 3) Run TraxDB

- Generate report:

```bash
bash tools/traxdb_sync/run_sync.sh --max-pages 50
```

- Download from report (background):

```bash
bash tools/traxdb_sync/run_download_bg.sh
```

- Audit:

```bash
bash tools/traxdb_sync/run_audit.sh
```

#### 4) Run Soulseek

- Start in background:

```bash
bash tools/soulseek_sync/run_bg.sh
```

- Show status:

```bash
bash tools/soulseek_sync/status.sh
```

#### 5) Telegram pairing (get your `chat_id`)

1) Create a bot:
- In Telegram, open `@BotFather`
- Run `/newbot`
- Choose a name + username
- Copy the bot token (looks like `123456:ABC...`)

2) Put the token in `djtools_config.json` under `telegram_bot_token`.

3) In Telegram, open your bot and send `/start`.

4) Run the pairing helper (writes logs to stdout):

```bash
source .venv/bin/activate
python3 tools/telegram_pair.py --watch --write
```

This will write `telegram_chat_id` into `djtools_config.json`.

5) Send a test message:

```bash
source .venv/bin/activate
python3 tools/telegram_send_test.py --message "hello from dj-tools"
```

#### 6) YouTube / yt-dlp / ffmpeg (recognition pipeline)

For the MVP, **we do not need a YouTube API key**. We’ll download audio using `yt-dlp` and normalize with `ffmpeg`.

Bundled MVP (recommended for our repo workflow):

```bash
source .venv/bin/activate
bash tools/setup_media_tools.sh
```

This will:
- download `yt-dlp` into `tools/bin/yt-dlp` (or copy it from your PATH)
- install/copy `ffmpeg` + `ffprobe` into `tools/bin/` (on macOS, prefers static builds to avoid missing dylibs; falls back to Homebrew if needed)

Tuning (optional):
- `recognize_concurrent_fragments` (default: 8). Some sites use HLS/DASH where media is split into many “fragments”. This setting controls how many fragments are downloaded in parallel; **the fragment count won’t change**, but wall-clock time usually improves.

If you hit YouTube throttling/captcha, we’ll address that later (cookies, rate limiting, or an API-backed metadata fetch), but we can start without any Google/YouTube API setup.


