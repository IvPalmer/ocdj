"""Opt-in per-user login entry point. No access fallback or worker restart."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import stat
import subprocess
import sys
import time

import requests

from tools.instagram import foreground as transport
from tools.mac_library import helper


def event(state):
    print(datetime.now(timezone.utc).isoformat(timespec='seconds') + ' ' + state, flush=True)


def private_text(path):
    fd = helper.open_absolute(str(path))
    with os.fdopen(fd, 'r') as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 65536):
            raise helper.Refused()
        return handle.read(65537)


def token_from_file(path):
    # Parse the one existing assignment; never execute/source a shell file.
    values = []
    for line in private_text(path).splitlines():
        line = line.strip()
        if line.startswith('export '):
            line = line[7:].lstrip()
        if line.startswith('DRAIN_TOKEN='):
            words = shlex.split(line[len('DRAIN_TOKEN='):], comments=True)
            if len(words) != 1 or not words[0] or any(c in words[0] for c in '\r\n\0'):
                raise helper.Refused()
            values.append(words[0])
    if len(values) != 1:
        raise helper.Refused()
    return values[0]


def detach(command, log_path):
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(log_path, flags, 0o600), 'ab') as log:
        info = os.fstat(log.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise helper.Refused()
        subprocess.Popen(command, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=subprocess.STDOUT, close_fds=True)


def playback(config):
    # Same singleton as manual helper: no second worker even across launch contexts.
    with helper.singleton():
        data = helper.load_map(Path(config['map']), config['root'])
        fd = helper.open_absolute(config['root'], directory=True)
        os.close(fd)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        tunnel = None
        try:
            tunnel = transport.start_tunnel(port)
            transport.wait_tunnel(tunnel, port, time.monotonic() + 15)
            token = os.environ['DRAIN_TOKEN']
            transport.wait_backend(port, token)
            api = helper.API(f'http://127.0.0.1:{port}', token)
            observed = 0
            while True:
                if tunnel.poll() is not None:
                    raise helper.Refused()
                try:
                    helper.serve_one(api, data, ffmpeg=config['ffmpeg'])
                except (requests.ConnectionError, requests.Timeout):
                    event('playback-waiting-for-backend')
                    # The prior attempt is over. The server skips live leases
                    # and the next iteration starts a fresh claim.
                    transport.wait_backend(port, token)
                    continue
                if time.monotonic() >= observed:
                    event('playback-poll-completed')
                    observed = time.monotonic() + 60
                time.sleep(2)
        finally:
            transport.stop(tunnel)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('instagram', 'playback'))
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--detach', action='store_true', help='Release the login Terminal session')
    parser.add_argument('--allow-keychain-prompt', action='store_true',
                        help='Permit the existing Instagram Keychain prompt in the GUI login context')
    args = parser.parse_args()
    if args.allow_keychain_prompt and args.mode != 'instagram':
        parser.error('--allow-keychain-prompt requires instagram')
    if args.detach:
        command = [sys.executable, '-u', '-m', 'tools.mac_login.restore',
                   args.mode, '--config', str(args.config)]
        if args.allow_keychain_prompt:
            command.append('--allow-keychain-prompt')
        try:
            detach(command, args.config.with_name(args.mode + '.log'))
        except (OSError, helper.Refused):
            event(args.mode + '-launcher-failed; check private log and runtime')
            return 1
        return 0
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, transport.interrupted)
    event(args.mode + '-starting')
    try:
        config = json.loads(private_text(args.config))
        os.environ['DRAIN_TOKEN'] = token_from_file(config['token_file'])
        if args.mode == 'instagram':
            code = transport.run(600, resident=True, allow_keychain_prompt=args.allow_keychain_prompt)
        else:
            playback(config)
            code = 0
    except transport.RunError as exc:
        # Transport emits fixed reason codes, never raw subprocess/HTTP details.
        event(args.mode + '-stopped: ' + str(exc) + '; no-retry')
        return 1
    except KeyboardInterrupt:
        code = 130
    except Exception:
        # Paths, request errors, tokens and helper details never enter health logs.
        event(args.mode + '-stopped-access-config-or-transport-required; no-retry')
        return 1
    event(args.mode + '-exited-' + str(code) + '; no-retry')
    return code


if __name__ == '__main__':
    sys.exit(main())
