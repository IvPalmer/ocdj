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
from reveal_map import default_map_path, write_reveal_map


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--export', metavar='NEW_PRIVATE_JSON')
    parser.add_argument('--collection-id', help='Stable UUID for this root and exclusion scope')
    parser.add_argument('--key-file', help='Existing private file containing exactly 32 random bytes')
    parser.add_argument('--local-reveal-map', action='store_true',
                        help='Atomically refresh the private map in OCDJ Helper’s sandbox; no server export required')
    parser.add_argument('--include-ocdj-review', action='store_true',
                        help='Include marked OCDJ archives from ID3/_review only')
    args = parser.parse_args()
    try:
        if (args.export or args.local_reveal_map) and not (args.collection_id and args.key_file):
            raise SnapshotError('Export or local map requires a stable collection UUID and private key file.')
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
        local_paths = {} if args.local_reveal_map else None
        payload, counts = scan(args.root, args.collection_id or '00000000-0000-0000-0000-000000000000', key,
                               local_paths=local_paths, include_ocdj_review=args.include_ocdj_review)
        print(json.dumps(dict(mode='export' if args.export else ('local_map' if args.local_reveal_map else 'dry_run'), complete=payload['complete'], **counts), sort_keys=True))
        if args.local_reveal_map:
            destination = default_map_path()
            if not destination.parents[3].is_dir():
                raise SnapshotError('Launch the installed helper once to initialize its sandbox before creating a local map.')
            write_reveal_map(destination, payload, args.root, local_paths)
        if args.export:
            write_snapshot(args.export, payload)
        return 0 if payload['complete'] else 2
    except (SnapshotError, OSError, ValueError):
        print('Snapshot refused: check root, identity, key, completeness and private destinations (launch the helper once before creating its local map).', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
