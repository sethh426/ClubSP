import sqlite3

import pytest

from app.funding import FundingBook
from app.relationships import RelationshipBook
from app.schema import (
    COMPONENT_VERSIONS,
    assert_component_compatible,
    component_version,
    ensure_component,
    schema_state,
)
from app.service import Application


def test_all_runtime_schema_components_register_and_survive_restart(tmp_path):
    path = tmp_path / "clubsp.db"
    app = Application(path)
    RelationshipBook(app)
    FundingBook(app.database)
    with app.database.session() as (connection, _):
        state = schema_state(connection)
    assert {name: item["version"] for name, item in state.items()} == COMPONENT_VERSIONS

    restarted = Application(path)
    RelationshipBook(restarted)
    FundingBook(restarted.database)
    with restarted.database.session() as (connection, _):
        again = schema_state(connection)
    assert {name: item["version"] for name, item in again.items()} == COMPONENT_VERSIONS


def test_newer_core_schema_is_refused_before_initialization(tmp_path):
    path = tmp_path / "clubsp.db"
    app = Application(path)
    with app.database.session(write=True) as (connection, _):
        connection.execute(
            "UPDATE clubsp_schema_versions SET version=99 WHERE component='core'"
        )
    with pytest.raises(RuntimeError, match="supports only"):
        Application(path)


def test_newer_feature_schema_is_refused(tmp_path):
    path = tmp_path / "clubsp.db"
    app = Application(path)
    book = RelationshipBook(app)
    with app.database.session(write=True) as (connection, _):
        connection.execute(
            "UPDATE clubsp_schema_versions SET version=99 WHERE component='relationships'"
        )
    with pytest.raises(RuntimeError, match="relationships"):
        RelationshipBook(book.application)


def test_explicit_migration_path_is_transactional_and_versioned():
    connection = sqlite3.connect(":memory:")
    ensure_component(connection, "core")
    connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY)")
    connection.commit()

    def migrate_v1_to_v2(db):
        db.execute("ALTER TABLE sample ADD COLUMN note TEXT NOT NULL DEFAULT ''")

    assert ensure_component(connection, "core", target=2, migrations={1: migrate_v1_to_v2}) == 2
    assert component_version(connection, "core") == 2
    columns = [row[1] for row in connection.execute("PRAGMA table_info(sample)")]
    assert columns == ["id", "note"]


def test_missing_migration_refuses_version_change():
    connection = sqlite3.connect(":memory:")
    ensure_component(connection, "core")
    with pytest.raises(RuntimeError, match="Missing migration"):
        ensure_component(connection, "core", target=2)
    assert component_version(connection, "core") == 1


def test_unknown_component_rejected():
    connection = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="Unknown"):
        assert_component_compatible(connection, "not-a-component")
