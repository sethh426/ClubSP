"""Component schema version registry for explicit ClubSP database evolution."""
from __future__ import annotations

from datetime import datetime, timezone


COMPONENT_VERSIONS = {
    "core": 1,
    "relationships": 1,
    "gmail_inbox": 1,
    "discovery": 1,
    "funding": 1,
}


def _initialize_registry(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS clubsp_schema_versions (
            component TEXT PRIMARY KEY,
            version INTEGER NOT NULL CHECK(version > 0),
            updated_at TEXT NOT NULL
        )
    """)


def component_version(connection, component):
    _initialize_registry(connection)
    row = connection.execute(
        "SELECT version FROM clubsp_schema_versions WHERE component=?", (component,)
    ).fetchone()
    return int(row[0]) if row else 0


def ensure_component(connection, component, target=None, migrations=None):
    """Register/upgrade one schema component inside the caller's transaction.

    Version 1 is the baseline for an already-initialized component. Later version
    changes require an explicit migration function keyed by the source version.
    """
    if component not in COMPONENT_VERSIONS:
        raise ValueError("Unknown schema component")
    target = COMPONENT_VERSIONS[component] if target is None else target
    if type(target) is not int or target < 1:
        raise ValueError("Schema target must be a positive integer")
    current = component_version(connection, component)
    if current > target:
        raise RuntimeError(
            f"Database schema component {component} is version {current}; "
            f"this ClubSP build supports only {target}"
        )
    if current == 0:
        if target != 1:
            raise RuntimeError(f"Cannot bootstrap {component} directly to schema version {target}")
        connection.execute(
            "INSERT INTO clubsp_schema_versions(component,version,updated_at) VALUES(?,?,?)",
            (component, 1, datetime.now(timezone.utc).isoformat()),
        )
        return 1
    migrations = migrations or {}
    while current < target:
        migrate = migrations.get(current)
        if migrate is None:
            raise RuntimeError(
                f"Missing migration for {component} schema version {current} to {current + 1}"
            )
        migrate(connection)
        current += 1
        connection.execute(
            "UPDATE clubsp_schema_versions SET version=?,updated_at=? WHERE component=?",
            (current, datetime.now(timezone.utc).isoformat(), component),
        )
    return current


def schema_state(connection):
    _initialize_registry(connection)
    rows = connection.execute(
        "SELECT component,version,updated_at FROM clubsp_schema_versions ORDER BY component"
    ).fetchall()
    return {row[0]: {"version": int(row[1]), "updated_at": row[2]} for row in rows}
