import sqlite3

import pytest

from app.maintenance import backup_database, inspect_database, restore_database
from app.service import Application


def seeded(path):
    app = Application(path)
    app.create_property({"address": "123 Synthetic St", "city": "Fort Wayne", "state": "IN", "zip": "46802"})
    return app


def test_online_backup_is_verified_private_and_restorable(tmp_path):
    live = tmp_path / "live.sqlite3"
    backup = tmp_path / "backups" / "snapshot.sqlite3"
    seeded(live)
    result = backup_database(live, backup)
    assert result["integrity"] == "ok"
    assert result["counts"]["properties"] == 1
    assert backup.stat().st_mode & 0o777 == 0o600

    Application(live).create_property({"address": "456 Later St", "city": "Fort Wayne", "state": "IN"})
    assert inspect_database(live)["counts"]["properties"] == 2
    with pytest.raises(ValueError, match="explicit confirmation"):
        restore_database(backup, live)
    restored = restore_database(backup, live, owner_confirmed_offline=True)
    assert restored["counts"]["properties"] == 1
    assert Application(live).state()["properties"][0]["address"] == "123 Synthetic St"


def test_failed_backup_does_not_replace_existing_destination(tmp_path):
    destination = tmp_path / "backup.sqlite3"
    seeded(destination)
    before = destination.read_bytes()
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(ValueError, match="does not exist"):
        backup_database(missing, destination)
    assert destination.read_bytes() == before


def test_verify_rejects_non_clubsp_and_corrupt_files(tmp_path):
    unrelated = tmp_path / "unrelated.sqlite3"
    with sqlite3.connect(unrelated) as connection:
        connection.execute("CREATE TABLE other(id INTEGER)")
    with pytest.raises(ValueError, match="not a ClubSP"):
        inspect_database(unrelated)

    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not sqlite")
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        inspect_database(corrupt)


def test_source_and_destination_must_differ(tmp_path):
    live = tmp_path / "live.sqlite3"
    seeded(live)
    with pytest.raises(ValueError, match="must differ"):
        backup_database(live, live)
    with pytest.raises(ValueError, match="must differ"):
        restore_database(live, live, owner_confirmed_offline=True)
