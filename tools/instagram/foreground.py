#!/usr/bin/env python3
"""Private Mac connector: bounded run or explicit login-session residency."""
import argparse
from contextlib import contextmanager, ExitStack
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

LOGIN_LOCK = Path.home() / '.ocdj-instagram-resident.lock'
COOKIE_DB = Path.home() / 'Library/Application Support/Google/Chrome/Profile 1/Cookies'
SSH = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
       '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
       '-o', 'ForkAfterAuthentication=no', '-o', 'RemoteCommand=none',
       '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2']

VPS_CONNECTOR_TARGET = '127.0.0.1:18002'


class RunError(Exception):
    pass


def seconds(value):
    value = int(value)
    if not 1 <= value <= 3600:
        raise ValueError('seconds must be 1–3600')
    return value


def remaining(deadline, limit=10):
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError
    return min(limit, left)


def clean_env(token=False):
    # requests otherwise honors proxy variables, even for a loopback bearer.
    return {k: v for k, v in os.environ.items()
            if not k.lower().endswith('_proxy') and (token or k != 'DRAIN_TOKEN')}


def network_failure(detail):
    # Classify in memory only; never print SSH stderr, target names or details.
    return any(message in (detail or '').lower() for message in (
        'connection timed out', 'operation timed out', 'connection refused',
        'no route to host', 'network is unreachable', 'could not resolve hostname',
        'connection reset by peer', 'connection closed by remote host', 'broken pipe'))


def start_tunnel(port):
    return subprocess.Popen(
        SSH + ['-N', '-o', 'ExitOnForwardFailure=yes', '-L',
               f'127.0.0.1:{port}:{VPS_CONNECTOR_TARGET}', 'main-instance'],
        env=clean_env(), start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def probe(port, token, timeout=10):
    # Empty sync is rejected before any database mutation. Never probe claim or
    # heartbeat: both change state. A 400 proves auth AND deployed validation.
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}/api/instagram/connector/sync/', data=b'{}',
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=timeout):
            pass
    except urllib.error.HTTPError as exc:
        with exc:
            if exc.code == 400:
                try:
                    errors = json.loads(exc.read(4096))
                    if isinstance(errors, dict) and set(errors) == {'collection_id', 'posts'}:
                        return
                except (ValueError, UnicodeError):
                    pass
    raise RunError('private-auth-validation-failed')


def wait_backend(port, token):
    """Wait through one bounded outage using only the existing auth probe."""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            probe(port, token, remaining(deadline, 5))
            return
        except OSError:
            time.sleep(min(3, max(0, deadline - time.monotonic())))
    raise RunError('backend-recovery-timeout')


def wait_tunnel(tunnel, port, deadline):
    end = min(deadline, time.monotonic() + 10)
    while time.monotonic() < end:
        if tunnel.poll() is not None:
            detail = tunnel.stderr.read(4096).decode('utf-8', 'replace') if tunnel.stderr else ''
            if network_failure(detail):
                raise ConnectionError('ssh-network-unavailable')
            raise RunError('tunnel-start-failed')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.1):
                return
        except OSError:
            time.sleep(.05)
    raise TimeoutError


def stop(process):
    if process is None:
        return
    # Each child owns a new process group. Kill descendants too, including an
    # ffmpeg child left behind after the connector exits or times out.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)
        if process.stderr is not None:
            process.stderr.close()


def supervise(tunnel, child, deadline, reconnect=None):
    reconnects = []
    observed = time.monotonic() + 60
    while True:
        code = child.poll()
        if code is not None:
            print('stopped: connector-finished' if code == 0 else
                  'stopped: connector-failed; check session/access in this launch context', flush=True)
            return 0 if code == 0 else 1
        if tunnel.poll() is not None:
            if reconnect is None:
                print('stopped: tunnel-lost; no retry', flush=True)
                return 1
            now = time.monotonic()
            reconnects = [t for t in reconnects if now - t < 180]
            if len(reconnects) >= 3:
                raise RunError('tunnel-unstable; explicit-restart-required')
            reconnects.append(now)
            reconnect()  # Transport only; never relaunch the worker.
        if time.monotonic() >= observed:
            print('connector-process-alive; session/sync health requires backend heartbeat', flush=True)
            observed = time.monotonic() + 60
        if time.monotonic() >= deadline:
            print('stopped: deadline-expired; no restart', flush=True)
            return 124
        time.sleep(min(.1, max(0, deadline - time.monotonic())))


def preflight():
    try:
        with COOKIE_DB.open('rb', buffering=0) as cookie_file:
            header = cookie_file.read(16)
    except OSError:
        raise RunError('Chrome-Profile-1-unreadable-in-this-launch-context') from None
    if header != b'SQLite format 3\x00':
        raise RunError('Chrome-Profile-1-unexpected-header')
    print('Profile-1-header-readable; Keychain/session not tested', flush=True)


@contextmanager
def singleton():
    # Stable across TMPDIR/GUI/launchd contexts. Also hold the legacy lock so a
    # still-running pre-upgrade foreground wrapper in this context wins.
    legacy = Path(tempfile.gettempdir()) / f'ocdj-instagram-foreground-{os.getuid()}.lock'
    with ExitStack() as stack:
        for path in (LOGIN_LOCK, legacy):
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            lock = stack.enter_context(os.fdopen(fd, 'r+'))
            info = os.fstat(lock.fileno())
            if (info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode)
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise RunError('unsafe-local-lock')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RunError('another-foreground-wrapper-is-running') from None
        yield


def run(duration, resident=False, allow_keychain_prompt=False):
    if allow_keychain_prompt and not resident:
        raise RunError('keychain-prompt-requires-resident')
    deadline = float('inf') if resident else time.monotonic() + duration
    token = os.environ.get('DRAIN_TOKEN', '')
    if not token:
        raise RunError('existing-DRAIN_TOKEN-required')
    preflight()
    with singleton():
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        tunnel = child = None
        # Own TMPDIR so even a forced connector termination removes raw media.
        with tempfile.TemporaryDirectory(prefix='ocdj-instagram-run-') as directory:
            try:
                def connect():
                    nonlocal tunnel
                    stop(tunnel)
                    # Discovery and validation are read/rejected-write only.
                    # Worker POSTs are never replayed by reconnecting transport.
                    for attempt, delay in enumerate((0, 5, 15, 30)):
                        if child is not None and child.poll() is not None:
                            raise RunError('connector-stopped; no-relaunch')
                        if delay: time.sleep(delay)
                        try:
                            tunnel = start_tunnel(port)
                            wait_tunnel(tunnel, port, deadline)
                            probe(port, token, remaining(deadline))
                            return
                        except (TimeoutError, OSError, subprocess.TimeoutExpired):
                            stop(tunnel)
                            if not resident or attempt == 3: raise
                        except RunError:
                            stop(tunnel)
                            # Auth/validation rejection cannot be retried.
                            raise
                connect()
                mode = 'resident' if resident else str(duration) + 's'
                print(f'private-auth-verified; starting Profile 1 connector; mode={mode}; Ctrl-C stops', flush=True)
                env = clean_env(token=True)
                env['TMPDIR'] = directory
                child = subprocess.Popen(
                    [sys.executable, str(Path(__file__).with_name('connector.py')),
                     '--api', f'http://127.0.0.1:{port}', '--profile', 'Profile 1',
                     '--work-seconds', str(duration)] + (['--resident'] if resident else [])
                    + (['--allow-keychain-prompt'] if allow_keychain_prompt else []),
                    env=env, start_new_session=True, stderr=subprocess.DEVNULL)
                if resident:
                    # connect() updates the owned tunnel; supervision uses a
                    # lightweight handle so it always observes the current one.
                    class Tunnel:
                        def poll(self): return tunnel.poll()
                    return supervise(Tunnel(), child, deadline, connect)
                return supervise(tunnel, child, deadline)
            finally:
                try:
                    stop(child)
                finally:
                    stop(tunnel)
    # Keep the empty lock file: unlinking after unlock creates a singleton race.


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--resident', action='store_true', help='explicit login-session resident; no install/relaunch')
    modes.add_argument('--preflight', action='store_true', help='header-only access check; no token, SSH or extraction')
    parser.add_argument('--allow-keychain-prompt', action='store_true',
                        help='resident startup only: permit existing yt-dlp macOS Keychain helper')
    parser.add_argument('--seconds', type=seconds, default=600,
                        help='hard run deadline including setup, 1–3600 (default 600)')
    args = parser.parse_args()
    if args.allow_keychain_prompt and not args.resident:
        parser.error('--allow-keychain-prompt requires --resident')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    try:
        if args.preflight:
            preflight()
            return 0
        return run(args.seconds, resident=args.resident, allow_keychain_prompt=args.allow_keychain_prompt)
    except KeyboardInterrupt:
        print('stopped: operator-interrupt; no restart', flush=True)
        return 130
    except (TimeoutError, subprocess.TimeoutExpired):
        print('stopped: deadline-or-startup-timeout; no restart', flush=True)
        return 124
    except Exception as exc:
        print('stopped: ' + (str(exc) if isinstance(exc, RunError) else 'startup-or-connection-failed'), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
