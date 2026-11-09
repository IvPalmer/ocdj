# Mac login restoration (#13)

## Terminal login items on the production Mac

Use the registered `OCDJ Instagram Connector.command` and
`OCDJ Playback Connector.command` login items on the Mac where background
LaunchAgents were denied access to the Chrome profile and playback map.
While both connectors are stopped, stage the reviewed runtime sources listed in
`install.SOURCES` under `login-connectors/code/` and use [login.command](login.command)
for both registered documents. Deploy the VPS loopback port before switching the
Mac to this runtime. Keep the existing
durable Python environment and private `config.json`. Keep both entries in
Open at Login and avoid duplicate LaunchAgents.

Each login item starts one worker in the existing GUI login context with no
controlling Terminal or inherited Terminal I/O. The launcher can exit and its
window can close while the worker keeps running. Inspect the corresponding
private `instagram.log` or `playback.log` and verify fresh activity on the
server. To stop a worker, send SIGTERM to the verified worker PID so it can
clean up its owned SSH tunnel. An explicit start or the next login starts a
new worker after exit. To roll back, stop the workers and restore the prior
launcher contents and staged runtime.

## Background LaunchAgent alternative

Opt-in per-user **GUI login** restoration for the existing Instagram resident and
indexed-library audio connector. This is Mac connector tooling; `RUNTIME.md`
continues to prohibit a local OCDJ DB/API. No VPS deployment is needed for this
slice. Parent owns merge, installation, access approvals and real login QA.

`install.py` only stages files. It never reads credentials, calls launchctl,
starts/stops an agent, installs dependencies, changes permissions on existing
files, or probes a real account. Existing agents continue untouched. The new
labels are `dev.grooveops.ocdj-login-instagram` and
`dev.grooveops.ocdj-login-playback`. Each runs **once at the next Aqua login**.
There is no KeepAlive, timer, WatchPaths, login/auth retry, or worker restart.
An exit stays stopped until an explicit invocation or the next login. Sleep/wake
is not a new login. Don't install duplicate legacy login entries for these workers.

## Parent installation after review

Use an existing **durable** Python virtual environment with the existing
Instagram requirements and requests installed. Its Python must stay at that
absolute path; do not select a venv in this temporary checkout. Likewise select
an existing working absolute FFmpeg executable (the known working
imageio-ffmpeg binary is acceptable); Homebrew FFmpeg was previously broken.
The installer preserves venv executable symlinks instead of resolving them to
base Python. It does not test executables or install packages.

From the reviewed checkout, set these path variables privately. Paths below
are placeholders for the existing approved environment, exact library root,
existing local map and working executable. Do not put any token in argv:

```sh
python3 tools/mac_login/install.py \
  --python "$DURABLE_VENV/bin/python" \
  --token-file "$HOME/.config/ocdj/drain.env" \
  --root "$LIBRARY_ROOT" --map "$LOCAL_MAP" --ffmpeg "$WORKING_FFMPEG" \
  --mode playback --mode instagram
```

For audio-only automatic startup, omit `--mode instagram`. The installer refuses
an existing install or selected plist instead of overwriting active code. Stage
all intended modes together. It copies the required reviewed Python sources to
`~/Library/Application Support/OCDJ/login-connectors/code`, writes a private
`config.json` containing **paths only**, and private logs alongside it. Nothing
points back into the temporary checkout. The Python environment, FFmpeg, token
file, map and audio root remain existing external dependencies.

Inspect the generated plists with `plutil -lint` before the next planned logout.
Do **not** bootstrap/kickstart or stop the current workers to test this install.
At planned logout, the existing session ends normally; on the next GUI login,
macOS starts the newly staged jobs. Approve only the existing requested access
if it is both expected and scoped; denial means stop. No Full Disk Access,
Accessibility, Automation, TCC reset/bypass or blanket Keychain trust is needed
or proposed. Missing SSH login access, disk/root/map access or network at login
causes a stopped connector; no daemon storm tries to repair it.

## Instagram access limit and exact proof

**Automatic Instagram access is not proven.** The parent reports that Codex
foreground can read the Chrome Profile 1 SQLite header and run the explicit
`--resident --allow-keychain-prompt` route. Earlier launchd could not read that
header; native no-UI Keychain lookup also failed. These are reported observations,
not new live measurements by this change, and GUI consent does not transfer
between responsible applications. The new login agent deliberately uses
`--resident` semantics with **no Keychain prompt**. It makes one startup attempt;
no permission repair, helper fallback or repeated cookie extraction is added.

Parent proof after a planned real login:

1. Inspect `instagram.log`. The exact safe header failure is
   `Chrome-Profile-1-unreadable-in-this-launch-context`. A header success prints
   `Profile-1-header-readable; Keychain/session not tested`. A worker failure
   after private auth is **not** proof of a valid Instagram session; correlate
   the existing backend heartbeat, and retain sanitized reason/status evidence.
2. If access is unavailable, keep that login job stopped. In the **same existing
   authorized GUI application/environment as the successful foreground run**,
   execute the staged header-only check (no token, SSH or cookie extraction):

   ```sh
   "$DURABLE_VENV/bin/python" \
     "$HOME/Library/Application Support/OCDJ/login-connectors/code/tools/instagram/foreground.py" \
     --preflight
   ```

3. Once the login attempt is confirmed exited, use the existing supervised
   foreground command from [FOREGROUND.md](../instagram/FOREGROUND.md), substituting
   the staged `foreground.py` path. Explicit `--resident --allow-keychain-prompt`
   may ask once for the existing Chrome item. Denial/cancellation stops; never
   select Always Allow or alter trust settings. If only Codex has existing file
   access, Terminal is not an equivalent context. Record that scoped GUI access
   remains necessary; do not present this as unattended Instagram restoration.
4. Real success needs complete sync, existing heartbeat, selected private media,
   and sleep/wake QA. Process-alive log lines alone prove none of these.

The Instagram singleton now holds a stable home lock across TMPDIR contexts and
also the current context's legacy temp lock. A pre-upgrade raw worker/old wrapper
in a *different* temp context cannot be discovered by these locks; installation
therefore waits for the next login and never starts alongside active workers.
Manual/raw connectors must still not be run concurrently. Lock files stay in
place after exit to avoid unlink races.

## Audio transport, health and rollback

Audio startup acquires the exact existing playback singleton **before SSH**,
loads the exact private map/root, and opens an ephemeral Mac loopback forward
to `127.0.0.1:18002` on the VPS using the existing BatchMode SSH access.
The VPS Compose backend publishes that port on host loopback only.
It reuses `serve_one`, including staging, source validation and fenced upload.
Each claim and completion is sent once. After a connection failure or timeout,
the connector abandons the attempt and waits up to 90 seconds for the existing
authenticated probe. The next poll uses a fresh claim and the server skips
live leases. Authentication rejection, an exited SSH tunnel or an inaccessible
map ends the run and cleans up its SSH group.

`playback.log` records UTC start/exit and a `playback-poll-completed` observation
at most once per minute, after a successful claim response. This proves a poll,
not successful audio playback. `instagram.log` has start/exit, sanitized access
reasons, and resident process liveness; backend heartbeat/sync is separate.
Logs/config are mode 0600 beneath a mode-0700 install directory. No credentials,
cookies, private tags, library filenames or raw error bodies are logged. The
existing token assignment is parsed in memory without shell execution; the
installer never opens it. Private token/map ownership and modes are enforced
at runtime. Logs append; parent may archive them while stopped if needed.

Parent real QA: verify one PID per connector and one owned forward, no duplicate
legacy jobs, successful audio prepare/upload and browser MP3 decode/seek, planned
logout/login restoration, denied/missing access stop, and no restart after
failure. Successful synthetic tests do not establish TCC, Keychain, SSH agent,
real audio-root access, mounted volume readiness or login ordering.

To reverse a staged install **before first login**, remove only its two named
plist files and its `login-connectors` directory. After activation, parent first
unloads **only these labels** with `launchctl bootout gui/$(id -u)/LABEL`, verifies
the owned connector/SSH children exited, then removes those files. Never unload
other OCDJ agents or unlink live singleton files. Reinstall/update only while
these owned jobs are stopped; installation refuses overwrite. No server data,
permissions or existing credentials need rollback. Resolve uncertain leases
under the existing protocol before any explicit restart.

## Synthetic verification

```sh
TEST_FFMPEG="$WORKING_FFMPEG" python3 -m unittest \
  tools.mac_login.test_restore tools.instagram.test_connector \
  tools.instagram.test_foreground tools.instagram.test_resident \
  tools.mac_library.test_helper -v
```

Fixtures use temporary homes/config/maps, generated audio, fake SSH and real
throwaway subprocess cleanup. They never install real agents, call launchctl,
read browser data/Keychain, access secrets, or connect to production. CI runs the
same checks on Linux and macOS. Self-review is not independent review.
