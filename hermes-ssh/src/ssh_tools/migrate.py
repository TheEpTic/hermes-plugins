#!/usr/bin/env python3
"""Migrate hermes-ssh data from plaintext to encrypted storage.

Checks for plaintext machines.json in the data directory, encrypts it
in-place, and deletes the old file. Safe to run multiple times.

Usage:
    python -m ssh_tools.migrate                    # default data dir
    python -m ssh_tools.migrate /path/to/data/dir  # custom dir
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Default data dir
DEFAULT_DATA_DIR = Path.home() / ".hermes" / "ssh-tools"


def migrate(data_dir: Path) -> None:
    """Migrate plaintext files to encrypted storage."""
    from .storage import EncryptedStore

    store = EncryptedStore(data_dir)

    files_to_migrate = ["machines.json"]
    migrated = 0

    for filename in files_to_migrate:
        path = data_dir / filename
        if not path.exists():
            print(f"  {filename}: not found, skipping")
            continue

        raw = path.read_text()
        if not raw.strip():
            print(f"  {filename}: empty, skipping")
            continue

        if raw.strip().startswith("gAAAAA"):
            print(f"  {filename}: already encrypted")
            continue

        # It's plaintext — migrate
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f"  {filename}: corrupt JSON, skipping")
            continue

        store.write(filename, data)
        path.unlink()
        print(f"  {filename}: migrated and deleted plaintext")
        migrated += 1

    # Also check old data dir locations
    old_dirs = [
        Path(__file__).parent.parent.parent / "data",  # plugin source data/
        Path.home() / "projects" / "hermes-ssh" / "src" / "ssh_tools" / "data",
    ]

    for old_dir in old_dirs:
        if not old_dir.exists():
            continue
        print(f"\nFound old data directory: {old_dir}")
        for filename in files_to_migrate:
            old_file = old_dir / filename
            if not old_file.exists():
                continue
            try:
                data = json.loads(old_file.read_text())
            except json.JSONDecodeError:
                print(f"  {filename}: corrupt, skipping")
                continue

            # Migrate to new encrypted store
            store.write(filename, data)
            old_file.unlink()
            print(f"  {filename}: migrated from old dir, deleted")

            # Also migrate command log if it exists
            old_log = old_dir / "command_log.jsonl"
            if old_log.exists():
                new_log = data_dir / "command_log.jsonl"
                new_log.parent.mkdir(parents=True, exist_ok=True)
                new_log.write_text(old_log.read_text())
                old_log.unlink()
                print(f"  command_log.jsonl: copied and deleted from old dir")

            migrated += 1

    print(f"\nMigration complete. {migrated} file(s) migrated.")
    print(f"Data dir: {data_dir}")
    print(f"Key file: {data_dir / '.key'}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        target = DEFAULT_DATA_DIR

    print(f"Migrating hermes-ssh data to encrypted storage")
    print(f"Target dir: {target}\n")
    migrate(target)
