#!/usr/bin/env python3
"""Manual, aggregate-only dry run by default. No Django, network or audio writes."""
import argparse
import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from library.snapshots import SnapshotError, scan, write_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--export', metavar='NEW_PRIVATE_JSON')
    parser.add_argument('--collection-id', help='Stable UUID for this root and exclusion scope')
    parser.add_argument('--key-file', help='Existing private file containing exactly 32 random bytes')
    args = parser.parse_args()
    try:
        if args.export and not (args.collection_id and args.key_file):
            raise SnapshotError('Export requires a stable collection UUID and private key file.')
        key = b'\0' * 32  # Ephemeral dry run only; IDs are never emitted.
        if args.key_file:
            fd = os.open(args.key_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                    raise SnapshotError('Key must be a private regular file owned by this user.')
                key = handle.read(33)
                if len(key) != 32:
                    raise SnapshotError('Key must contain exactly 32 bytes.')
        payload, counts = scan(args.root, args.collection_id or '00000000-0000-0000-0000-000000000000', key)
        print(json.dumps(dict(mode='export' if args.export else 'dry_run', complete=payload['complete'], **counts), sort_keys=True))
        if args.export:
            write_snapshot(args.export, payload)
        return 0 if payload['complete'] else 2
    except (SnapshotError, OSError, ValueError):
        print('Snapshot refused: check root, identity, key, completeness and new private destination.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
