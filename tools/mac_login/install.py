"""Stage opt-in login agents without loading, starting or stopping any process."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import sys

LABELS = {mode: 'dev.grooveops.ocdj-login-' + mode for mode in ('instagram', 'playback')}
SOURCES = ('tools/instagram/foreground.py', 'tools/instagram/connector.py',
           'tools/instagram/mac_access.py', 'tools/mac_library/helper.py',
           'tools/mac_library/reveal_map.py', 'tools/mac_library/register_review.py',
           'backend/library/snapshots.py', 'tools/mac_login/restore.py',
           'backend/instagram/recognition.py')


def install(home, source, python, token_file, root, local_map, ffmpeg, modes):
    # Keep venv executable spelling: resolving a symlink can silently select base Python.
    for value in (python, token_file, root, local_map, ffmpeg):
        if not Path(value).is_absolute() or '\0' in str(value):
            raise ValueError('absolute-existing-paths-required')
    if any(source in Path(value).absolute().parents for value in (python, ffmpeg)):
        raise ValueError('executables-must-live-outside-checkout')
    if not os.access(python, os.X_OK) or not os.access(ffmpeg, os.X_OK):
        raise ValueError('existing-executables-required')
    base = home / 'Library/Application Support/OCDJ/login-connectors'
    agents = home / 'Library/LaunchAgents'
    # No overwrites/upgrades: existing installs require explicit removal after logout.
    if base.exists() or base.is_symlink() or any(
            (agents / (LABELS[m] + '.plist')).exists() or
            (agents / (LABELS[m] + '.plist')).is_symlink() for m in modes):
        raise ValueError('installation-exists; leave-active-workers-unchanged')
    agents.mkdir(parents=True, exist_ok=True)
    base.mkdir(parents=True, mode=0o700)
    written = []
    try:
        code = base / 'code'
        for relative in SOURCES:
            target = code / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)
        config = base / 'config.json'
        with open(config, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as handle:
            json.dump(dict(token_file=str(token_file), root=str(root), map=str(local_map),
                           ffmpeg=str(ffmpeg)), handle)
        for mode in modes:
            log = base / (mode + '.log')
            log.touch(mode=0o600, exist_ok=False)
            data = dict(Label=LABELS[mode], ProgramArguments=[str(python), '-u', '-m',
                        'tools.mac_login.restore', mode, '--config', str(config)],
                        WorkingDirectory=str(code), RunAtLoad=True,
                        LimitLoadToSessionType='Aqua', ProcessType='Background',
                        StandardOutPath=str(log), StandardErrorPath=str(log),
                        EnvironmentVariables={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'})
            path = agents / (LABELS[mode] + '.plist')
            with open(path, 'xb', opener=lambda p, f: os.open(p, f, 0o600)) as handle:
                written.append(path)
                plistlib.dump(data, handle)
    except BaseException:
        for path in written:
            path.unlink()
        shutil.rmtree(base)
        raise
    return base


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', required=True, type=Path, help='Existing durable venv Python; do not use this checkout')
    parser.add_argument('--token-file', required=True, type=Path, help='Existing private drain.env; never read by installer')
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--map', required=True, type=Path)
    parser.add_argument('--ffmpeg', required=True, type=Path)
    parser.add_argument('--mode', choices=tuple(LABELS), action='append', required=True)
    args = parser.parse_args()
    try:
        if sys.platform != 'darwin':
            raise ValueError('mac-only')
        install(Path.home(), Path(__file__).resolve().parents[2], args.python,
                args.token_file, args.root, args.map, args.ffmpeg, set(args.mode))
    except Exception:
        print('Not installed: verify durable absolute paths and absence of an existing install. No process changed.', file=sys.stderr)
        return 1
    print('Staged for next GUI login only. No launchctl, worker restart, access probe or credential read performed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
