#!/usr/bin/env python3
"""One explicit private Mac connector run; no restart, installation or service."""
import argparse
import fcntl
import ipaddress
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

COOKIE_DB = Path.home() / 'Library/Application Support/Google/Chrome/Profile 1/Cookies'
SSH = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
       '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
       '-o', 'ForkAfterAuthentication=no', '-o', 'RemoteCommand=none',
       '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2']


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


def backend_address(deadline=None):
    value = subprocess.check_output(
        SSH + ['main-instance', 'docker inspect ocdj-backend-1 --format '
               '"{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"'],
        text=True, stderr=subprocess.DEVNULL, env=clean_env(),
        timeout=remaining(deadline, 15) if deadline else 15).strip()
    # Fail closed on multiple networks or unexpected targets; never guess.
    try:
        address = ipaddress.IPv4Address(value)
    except ValueError:
        raise RunError('backend-target-unavailable') from None
    if not any(address in ipaddress.ip_network(net) for net in
               ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
        raise RunError('backend-target-unavailable')
    return str(address)


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


def wait_tunnel(tunnel, port, deadline):
    end = min(deadline, time.monotonic() + 10)
    while time.monotonic() < end:
        if tunnel.poll() is not None:
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


def supervise(tunnel, child, deadline):
    while True:
        if tunnel.poll() is not None:
            print('stopped: tunnel-lost; no retry', flush=True)
            return 1
        code = child.poll()
        if code is not None:
            print('stopped: connector-finished' if code == 0 else
                  'stopped: connector-failed; check session/access in this launch context', flush=True)
            return 0 if code == 0 else 1
        if time.monotonic() >= deadline:
            print('stopped: deadline-expired; no restart', flush=True)
            return 124
        time.sleep(min(.1, max(0, deadline - time.monotonic())))


def run(duration):
    deadline = time.monotonic() + duration
    token = os.environ.get('DRAIN_TOKEN', '')
    if not token:
        raise RunError('existing-DRAIN_TOKEN-required')
    try:
        with COOKIE_DB.open('rb') as cookie_file:
            cookie_file.read(16)  # Only access/header check; no cookie extraction.
    except OSError:
        raise RunError('Chrome-Profile-1-unreadable-in-this-launch-context') from None
    lock_path = Path(tempfile.gettempdir()) / f'ocdj-instagram-foreground-{os.getuid()}.lock'
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        info = os.fstat(lock.fileno())
        if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise RunError('unsafe-local-lock')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunError('another-foreground-wrapper-is-running') from None
        address = backend_address(deadline)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        tunnel = child = None
        # Own TMPDIR so even a forced connector termination removes raw media.
        with tempfile.TemporaryDirectory(prefix='ocdj-instagram-run-') as directory:
            try:
                tunnel = subprocess.Popen(
                    SSH + ['-N', '-o', 'ExitOnForwardFailure=yes', '-L',
                           f'127.0.0.1:{port}:{address}:8002', 'main-instance'],
                    env=clean_env(), start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                wait_tunnel(tunnel, port, deadline)
                probe(port, token, remaining(deadline))
                print(f'private-auth-verified; starting Profile 1 connector; limit={duration}s; Ctrl-C stops', flush=True)
                env = clean_env(token=True)
                env['TMPDIR'] = directory
                child = subprocess.Popen(
                    [sys.executable, str(Path(__file__).with_name('connector.py')),
                     '--api', f'http://127.0.0.1:{port}', '--profile', 'Profile 1',
                     '--work-seconds', str(duration)],
                    env=env, start_new_session=True, stderr=subprocess.DEVNULL)
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
    parser.add_argument('--seconds', type=seconds, default=600,
                        help='hard run deadline including setup, 1–3600 (default 600)')
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    try:
        return run(args.seconds)
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
