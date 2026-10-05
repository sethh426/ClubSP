# Backup and recovery

ClubSP is a single-owner SQLite workspace. A plain file copy while the application
is writing can produce an inconsistent snapshot, so production backups should use
the SQLite backup API.

Create and verify an online snapshot:

```sh
python -m app.maintenance backup --db data/clubsp.sqlite3 --out backups/clubsp-$(date +%F).sqlite3
python -m app.maintenance verify backups/clubsp-2026-10-05.sqlite3
```

The backup command writes a temporary database in the destination directory,
uses SQLite's online backup mechanism, runs `PRAGMA integrity_check`, sets the
snapshot to owner-only permissions, fsyncs it, and atomically replaces the named
destination. It does not copy Gmail token files or other secrets.

Restores are intentionally offline. Stop ClubSP and its service/reverse proxy
worker first, keep the current live database as a separate rollback copy, then:

```sh
python -m app.maintenance restore --backup backups/clubsp-2026-10-05.sqlite3 \
  --db data/clubsp.sqlite3 --confirm-offline
```

The restore command refuses to run without the explicit offline confirmation,
verifies the source is an intact ClubSP database, restores into a temporary file,
checks integrity again, and atomically replaces the destination. Restart ClubSP
and verify `/api/health`, the Revenue Command Center, Relationship Desk, and
Funding Desk before deleting the rollback copy.

A backup is not complete disaster recovery until a restore has been tested on a
separate machine or isolated directory. Production operations should schedule
regular snapshots, retain multiple generations off the server, encrypt the
storage location, and periodically rehearse restoration.
