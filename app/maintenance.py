"""Verified SQLite backup and recovery helpers for the single-owner ClubSP workspace."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from datetime import datetime, timezone


def _integrity(connection):
    row = connection.execute("PRAGMA integrity_check").fetchone()
    if not row or row[0] != "ok":
        raise ValueError("SQLite integrity check failed")


def inspect_database(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError("Database file does not exist")
    uri = path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        _integrity(connection)
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        required = {"properties", "deals", "memory"}
        missing = sorted(required - tables)
        if missing:
            raise ValueError("Backup is not a ClubSP database; missing tables: " + ", ".join(missing))
        counts = {}
        for table in ("properties", "deals", "buyers", "memory"):
            if table in tables:
                counts[table] = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        return {"path": str(path.resolve()), "bytes": path.stat().st_size,
                "tables": len(tables), "counts": counts, "integrity": "ok"}


def backup_database(source, destination):
    source, destination = Path(source), Path(destination)
    if not source.is_file():
        raise ValueError("Source database does not exist")
    if source.resolve() == destination.resolve():
        raise ValueError("Backup destination must differ from the live database")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".clubsp-backup-", suffix=".sqlite3", dir=destination.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        with sqlite3.connect(source) as live, sqlite3.connect(temp) as backup:
            live.backup(backup)
            _integrity(backup)
        os.chmod(temp, 0o600)
        with temp.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temp, destination)
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
        result = inspect_database(destination)
        result.update(created_at=datetime.now(timezone.utc).isoformat(),
                      source=str(source.resolve()), destination=str(destination.resolve()))
        return result
    finally:
        temp.unlink(missing_ok=True)


def restore_database(backup, destination, *, owner_confirmed_offline=False):
    """Restore a verified backup. Caller must stop ClubSP first."""
    if owner_confirmed_offline is not True:
        raise ValueError("Restore requires explicit confirmation that ClubSP is stopped")
    backup, destination = Path(backup), Path(destination)
    inspect_database(backup)
    if backup.resolve() == destination.resolve():
        raise ValueError("Backup and live database paths must differ")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".clubsp-restore-", suffix=".sqlite3", dir=destination.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        with sqlite3.connect(backup.resolve().as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(temp) as target:
            source.backup(target)
            _integrity(target)
        os.chmod(temp, 0o600)
        with temp.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temp, destination)
        return inspect_database(destination)
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="ClubSP database backup and recovery")
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--db", default="data/clubsp.sqlite3")
    backup.add_argument("--out", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("path")
    restore = sub.add_parser("restore")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--db", default="data/clubsp.sqlite3")
    restore.add_argument("--confirm-offline", action="store_true")
    args = parser.parse_args()
    if args.command == "backup":
        result = backup_database(args.db, args.out)
    elif args.command == "verify":
        result = inspect_database(args.path)
    else:
        result = restore_database(args.backup, args.db, owner_confirmed_offline=args.confirm_offline)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
