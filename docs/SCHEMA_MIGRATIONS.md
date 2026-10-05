# Database schema evolution

ClubSP uses an explicit component schema registry stored in the SQLite database as `clubsp_schema_versions`.

Current schema-owning components are:

- `core`
- `relationships`
- `gmail_inbox`
- `discovery`
- `funding`

Each component registers its supported version after its baseline tables/indexes exist. On startup, the code checks any existing version **before** running that component's current DDL. If the database says a component was created by a newer ClubSP build, the older build refuses to continue instead of guessing that the schema is compatible.

## Future changes

Do not change an existing table shape and leave the component version unchanged.

For a schema change:

1. increment that component's value in `COMPONENT_VERSIONS`;
2. add an explicit migration function from the previous version;
3. run it through `ensure_component(..., migrations={...})`;
4. test upgrade from a real copy of the previous schema;
5. back up the production database before deployment;
6. verify readiness and application workflows after migration.

The migration registry updates the component version only after the migration function completes inside the caller's database transaction. A missing migration is a hard error.

## Rollback

A code rollback is safe only when the older code supports the database component versions now on disk. If a newer migration is not backward-compatible, restore the pre-deployment verified backup rather than forcing the older binary to open a newer schema.

This registry is intentionally separate from Alembic. ClubSP is currently a single-owner SQLite workspace; if the persistence layer moves to a server database, the migration mechanism should be revisited rather than layering two competing migration systems on the same schema.
