# Private foreground connector operation (#13)

This operation is intentionally a single bounded Mac process. Validation alone
cannot keep OCDJ online: the unchanged connector exits after at most ten jobs,
its idle work window expires, and the backend reports offline three minutes
after its last heartbeat. Uncached private media needs a running, authorized Mac
connector. Existing cached playback/recognition has its own VPS lifecycle.

## Observed facts, 2026-11-09 00:57 UTC

Read-only checks against deployed `vps-deploy` commit `1fa67c3`:

| Check | Observation | Limit |
| --- | --- | --- |
| SSH `main-instance`, BatchMode | Works with existing configuration | No keys or access changed |
| Production backend | Existing `ocdj-backend-1`, internal port 8002, no host port published | Internal address can change on recreation; rediscovered per run |
| VPS loopback | Port 8002 unavailable; port 80 requests returned 404 | Do not assume localhost:8002 is a route or add a host publication |
| Mac loopback SSH forward to existing backend | Empty sync: missing/invalid bearer 401; existing bearer 400 with `collection_id`/`posts` serializer errors | No sync, claim, heartbeat or media mutation; tunnel removed afterward |
| Public anonymous workspace GET | 401 | This is not a complete browser/media ingress audit |
| Existing drain config | Present, mode 0600; token available | Value never logged, changed or committed |
| Chrome Profile 1 cookie database | Header-only open raises `PermissionError` in this agent process | No cookie extraction/copy or live Instagram request attempted |
| User TCC database, read-only SQLite | `unable to open database file` | Cannot enumerate grants or prove which app is authorized |
| Wrapper with existing token | Exits 1 with `Chrome-Profile-1-unreadable-in-this-launch-context`, before SSH | Confirms fail-closed behavior, not live Instagram success |

The SSH probe uses a Mac loopback listener forwarding to the existing backend's
private container address over the operator's existing SSH connection. It does
not alter Traefik, publish a port, provision a credential or change any server
service. It grants no browser access. Every run rejects missing tokens,
unexpected/multiple container addresses, failed SSH setup, redirects and an
unexpected validation response. HTTP proxy environment variables are removed
from the connector and disabled for the validation probe.

## Run and stop

Activate the existing Mac-owned Python environment containing
`tools/instagram/requirements.txt`. From the intended source checkout:

```sh
(
  source "$HOME/.config/ocdj/drain.env"
  export DRAIN_TOKEN
  python tools/instagram/foreground.py --seconds 600
)
```

Keep shell tracing disabled. Do not paste a token into the command. The wrapper
uses the active Python interpreter and this checkout's adjacent connector, so
sibling thumbnail changes to `connector.py` are picked up when integrated;
this PR does not edit that file, backend, frontend or workflows.

`private-auth-verified` means only that the private backend accepted the bearer
and rejected an invalid sync payload. It is followed by connector startup,
metadata sync, and the existing ready heartbeat if Instagram access succeeds.
A readable SQLite header does not prove Keychain decryption, valid Instagram
login or an unrestricted session. The existing connector's safe stdout reports
`session-expired`, `restricted` or `rate-limited` where recognized; other failures
are generic. Stderr is suppressed because third-party extractors can print
private details. Inspect the workspace's status without exporting browser data.

| Exit | Meaning and next step |
| --- | --- |
| 0 | Connector completed normally, possibly at ten jobs before the deadline. It is now stopped. Start another explicit run only when wanted. |
| 1 | Access/auth/SSH/connector failure, unsafe lock, or another wrapper running. Resolve the specific prerequisite in the intended launch context; do not automatically retry. |
| 2 | Invalid command arguments. Choose `--seconds` from 1 through 3600. |
| 124 | Deadline or bounded startup timeout. Cleanup completed; a new run is explicit. |
| 130 | Ctrl-C, SIGTERM or terminal hangup. Cleanup completed; no restart. |

On exit, the backend retains its most recent heartbeat/status until it ages out;
the wrapper does not invent a `stopped` heartbeat the backend cannot accept.
Failed jobs interrupted by a hard deadline may retain an existing ten-minute
lease until server expiry. The next authorized run uses the existing fencing
and lease protocol. A stop is not confirmation that a job completed.

Temporary media is under a wrapper-owned temporary directory, removed after
its child groups stop. The empty per-user lock file remains intentionally to
avoid unlink/recreate races; its kernel lock releases at process exit. The
wrapper never kills an unrelated connector or tunnel. SIGKILL, power loss and
OS crashes cannot execute cleanup; do not use `kill -9` as the ordinary stop.
Mac sleep can pause cleanup; no availability during sleep is promised.

## Persistent process feasibility and next gate

An operator-launched Terminal process **may** have existing Chrome/Keychain
access that this background agent lacks. That is unverified here. Do not copy
cookies, grant Full Disk Access/Automation, unlock/export secrets or change TCC
to make this run pass. Use an existing authorized context, or report the blocked
access. If Chrome itself says the session expired, the operator restores the
existing session before another explicit run; do not retry around restrictions.

A terminal kept open for one authorized window is feasible once that context
passes cookie, Keychain, Instagram and private-backend checks. It is not a
persistent connector: the ten-job cap can end it early, session expiry stops it,
and sleep/logout/reboot/tunnel loss interrupts it. A restart loop would repeat
collection reads and could conceal a restricted session. Installing a supervised
persistent worker would require a separately scoped lifecycle/access design and
operator authorization; none is installed or recommended as a bypass here.

The parent/operator may run a temporary foreground connector for current users
from an already-authorized context. Outstanding live proof: successful complete
sync, selected private-media fetch/upload/playback, observed ready-to-offline
transition and an explicit restart. This task does not claim that Instagram is
currently online or that Mac privacy access has been repaired.

## Verification

```sh
python -m unittest tools.instagram.test_foreground tools.instagram.test_connector -v
```

The new DB-free tests exercise argument/target rejection, rejected validation,
missing token and denied cookie access before SSH, real subprocess deadlines,
tunnel loss, successful/failed connector exit, interruption cleanup and proxy
exclusion. Network, cookies and SSH are replaced at test boundaries; real child
processes are used for lifecycle checks. No local DB/API or live Instagram is
started. Run the command explicitly: the existing fixture workflow names
`test_connector` and does not automatically discover `test_foreground`.
