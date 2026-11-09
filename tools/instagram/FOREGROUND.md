# Mac login-session connector (#13)

`foreground.py --resident` runs one connector continuously in the explicitly
chosen GUI login session, without the default deadline or ten-job exit. It is
an opt-in process, not an installed LaunchAgent. It uses the existing drain
bearer, Chrome Profile 1 and private SSH forward; there is no new inbound
service, public ingress, cookie export, permission change or worker relaunch.
Backend recognition is owned separately; this change only operates the Mac
connector and its existing protocol.

## Access gate and exact operator step

The 2026-11-09 header-only preflight in the agent's background launch context
returns exit 1, `Chrome-Profile-1-unreadable-in-this-launch-context`:
`open(.../Google/Chrome/Profile 1/Cookies, 'rb').read(16)` raises
`PermissionError` (`Operation not permitted`). No cookie contents, Keychain item,
TCC database or credential configuration were read in this check. The latest
operator handoff reports that the existing foreground context works; that is
not proof this background process or the new resident mode has that authority.

Use the **same already-authorized GUI application and Python environment that
ran the successful foreground connector**. If that was Apple Terminal, open
`/System/Applications/Utilities/Terminal.app` from Finder in the operator's
logged-in desktop, open a normal tab, change to the reviewed source checkout,
and activate that existing environment. Do not invoke through launchd, SSH,
`sudo`, another terminal app, or an agent process and assume authority transfers.
Run this first; it needs no token and cannot sync or claim work:

```sh
python tools/instagram/foreground.py --preflight
```

Only `Profile-1-header-readable` proves file access **in that process context**.
It does not prove a TCC grant, valid cookies, Keychain decryption, Instagram login,
or private API access. It reads exactly the SQLite header, never dumps it or
queries the database. If it fails, stop and report that context. Do not grant
Full Disk Access, Automation, Accessibility, change file ownership/ACLs, copy
cookies, or reset TCC. There is no validated permission repair in this PR.

The Mac extraction adapter temporarily disables Keychain interaction **inside
the connector process**, reads only the existing `Chrome Safe Storage`/`Chrome`
item via Security.framework, restores the previous interaction policy and frees
the native buffer. It replaces yt-dlp's prompting `security` helper only during
extraction; it never falls back to that helper. Only Instagram cookies enter the
in-memory request session. Missing prior item access, a locked Keychain, missing
API or changed yt-dlp helper fail closed. No prompt, unlock or ACL modification
is attempted. Native Python has its own Keychain caller identity: a previously
working `/usr/bin/security` subprocess does **not** establish Python trust.
The adapter's native success remains unverified; denial is a real blocker,
not a reason to broaden trust to all Python programs.

If the known GUI application still fails either gate, continuous operation is
blocked pending a separately reviewed stable, signed app identity with access
scoped to Chrome's existing item and profile. This PR does not install such an
app or claim that macOS offers a per-file TCC grant for this Python process.
The concrete least-privilege step available now is the existing authorized GUI
context plus the preflight above; there is no permission-free bypass to invent.

## Start, observe and stop (parent/operator live QA only)

After review/integration, in that same successful GUI context, with shell
tracing off:

```sh
(
  source "$HOME/.config/ocdj/drain.env"
  export DRAIN_TOKEN
  python tools/instagram/foreground.py --resident
)
```

Keep the terminal tab open. Do not run another raw connector, old LaunchAgent
or wrapper alongside it. The existing private per-user singleton lock covers
both wrapper modes. The bearer remains in memory/environment, never argv or
logs; proxy environment variables are stripped. `private-auth-verified` proves
only the rejected empty-sync validation over the existing loopback SSH forward.
It does not claim a successful Instagram sync. No production run was started
for this PR.

Resident behavior:

- No wall-clock cutoff or job count cap; one job at a time with existing leases,
  fencing, per-file bounds and temporary-media cleanup. Idle/job cadence is 15s
  (thumbnail steps can interleave earlier), with the existing heartbeat protocol.
- Initial complete collection sync, then one refresh per hour. A scheduling gap
  over 180s, including wake, refreshes once before the next claim. It never
  catches up every missed hour. Refresh preserves backend human state through
  the existing sync contract. No eager video download. Cookies are extracted
  once per run; login expiry requires an explicit operator repair/restart.
- Only Instagram JSON GETs and idempotent heartbeat status writes retry network
  connection/timeouts or HTTP 502/503/504, at 5/15/30s, maximum four attempts.
  Other HTTP errors, login redirects, 401/403/429 and Instagram restrictions stop
  without retry. Existing per-image errors remain bounded to that sync attempt.
- An exited SSH tunnel is rebuilt on the same Mac loopback port, rediscovering
  the private container address and revalidating auth. Setup uses at most four
  attempts at 5/15/30s; three reconnect episodes in 180s exhaust the flap budget.
  Only recognized SSH network failures/timeouts retry; authentication, command,
  target or validation rejection stops immediately. This reuses the existing
  forwarding socket and never exposes an inbound service or new port on VPS.
- Claims, sync, uploads, thumbnail batches and completions are sent **once**.
  An ambiguous response can stop the process even if the tunnel recovers. No
  worker is relaunched and no job is replayed to hide that uncertainty. Resolve
  the cause and inspect server lease/state before any new explicit invocation.
- Sleep is offline time. On wake, the same process can recover a surviving or
  rebuilt tunnel during a safe heartbeat/read. If a mutation was interrupted,
  retry budgets exhaust, or the worker exited, it stays stopped. This is not
  an always-awake or reboot-persistent service; no sleep inhibition is added.

Ctrl-C, SIGTERM or terminal hangup stops the wrapper, its worker/ffmpeg and SSH
process groups and removes the owned temporary directory. No restart occurs.
SIGKILL/power loss cannot run cleanup. Interrupted jobs can retain their existing
10-minute server lease. The backend ages the last heartbeat offline after three
minutes; the wrapper does not invent a server `stopped` status.

Exit 0 means normal connector completion (bounded mode); 1 means an access,
auth, transport, unstable tunnel or worker error; 2 is an argument error; 124 is
a bounded-mode deadline/setup timeout; 130 is interrupt. All require an explicit
new invocation. To retain the old one-shot window, use `--seconds 600` without
`--resident`; its hard deadline and ten-job cap remain. `--seconds` is ignored
in resident mode.

Long selected-media downloads/recognition still occupy the single worker and
can exceed the three-minute heartbeat age. During such a job, online status can
briefly age out even though the process remains alive. This PR does not add a
second heartbeat writer or change recognition timing/backend status semantics.

## Verification, release and rollback

```sh
python -m unittest tools.instagram.test_connector tools.instagram.test_foreground tools.instagram.test_resident -v
```

61 DB-free synthetic tests cover the existing parser/thumbnail contracts,
more than ten jobs, idle residency, hourly and wake refresh, restriction stop,
single-attempt ambiguous mutations, read/heartbeat retry budgets, same-worker
transport recovery, auth failure, flap budget, native no-UI denial/restore/free
with fake C functions, and process-group/temp cleanup. Existing real subprocess
cleanup fixtures remain. No local Django/DB/API or real secrets/network are used.
The Instagram Actions workflow runs these and existing PostgreSQL/Chromium
integration fixtures on GitHub. Local system Python emits a pre-existing
requests dependency-version warning; isolated Actions dependencies are separate.

Parent owns review, merge, deployment and live QA. Still required: same-context
header success, no-UI native Keychain/session success, complete collection sync,
selected private playback/recognition, more than ten jobs, hourly refresh,
sleep/wake with no ambiguous mutation, restriction stop and manual restart,
plus the existing private browser/media boundary checks. Native Keychain behavior
and continuous production availability are **not proven by synthetic CI**.

Rollback: Ctrl-C the owned resident process, verify it and its SSH/ffmpeg children
are gone, and use the prior reviewed bounded wrapper from the prior release if
needed. To remove the code, revert this PR through the release branch. No DB
migration, server configuration, permission or installed service needs undoing.
No data or queued state is reset; let uncertain leases expire under the existing
protocol. Rollback is not authorization to relaunch a worker.

Implementation references: [yt-dlp cookie helper](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/cookies.py),
[Apple Keychain no-UI setting](https://developer.apple.com/documentation/security/seckeychainsetuserinteractionallowed(_:)),
[Apple item lookup](https://developer.apple.com/documentation/security/seckeychainfindgenericpassword(_:_:_:_:_:_:_:_:)).
The legacy SecKeychain APIs are deprecated; this adapter deliberately fails closed
if unavailable instead of adding a dependency or prompting fallback.
