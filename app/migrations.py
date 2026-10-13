"""Small forward-only SQLite migrations for the MVP.

`create_all` creates new tables but cannot add columns to an existing database.
These idempotent migrations preserve pilot data during server upgrades.
Move to Alembic before switching to PostgreSQL or running multiple releases.
"""

from sqlalchemy import Engine, inspect, text


def _add_column_if_missing(inspector, statements: list[str], table: str, column: str, ddl: str) -> None:
    if table not in set(inspector.get_table_names()):
        return
    columns = {c["name"] for c in inspector.get_columns(table)}
    if column not in columns:
        statements.append(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def migrate_sqlite(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    statements: list[str] = []

    _add_column_if_missing(
        inspector, statements, "contact_points", "normalized_value",
        "normalized_value VARCHAR(512) NOT NULL DEFAULT ''",
    )
    _add_column_if_missing(
        inspector, statements, "source_runs", "payload_sha256",
        "payload_sha256 VARCHAR(64) NOT NULL DEFAULT ''",
    )
    _add_column_if_missing(
        inspector, statements, "source_runs", "error_count",
        "error_count INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        inspector, statements, "source_runs", "cursor_after",
        "cursor_after VARCHAR(255) NOT NULL DEFAULT ''",
    )

    for col, ddl in [
        ("category", "category VARCHAR(128) NOT NULL DEFAULT ''"),
        ("catalog_ref", "catalog_ref VARCHAR(255) NOT NULL DEFAULT ''"),
        ("cursor", "cursor VARCHAR(255) NOT NULL DEFAULT ''"),
        ("record_count", "record_count INTEGER NOT NULL DEFAULT 0"),
        ("error_count", "error_count INTEGER NOT NULL DEFAULT 0"),
        ("last_run_at", "last_run_at DATETIME"),
        ("next_run_at", "next_run_at DATETIME"),
    ]:
        _add_column_if_missing(inspector, statements, "source_connectors", col, ddl)

    _add_column_if_missing(
        inspector, statements, "evidence", "status",
        "status VARCHAR(32) NOT NULL DEFAULT 'confirmed'",
    )
    _add_column_if_missing(
        inspector, statements, "qualifications", "breakdown",
        "breakdown JSON",
    )
    _add_column_if_missing(
        inspector, statements, "qualifications", "reasons",
        "reasons JSON",
    )
    _add_column_if_missing(
        inspector, statements, "website_builds", "content_pack",
        "content_pack JSON",
    )
    _add_column_if_missing(
        inspector, statements, "website_builds", "pipeline_stage",
        "pipeline_stage VARCHAR(64) NOT NULL DEFAULT 'pending'",
    )
    _add_column_if_missing(
        inspector, statements, "website_builds", "cost_units",
        "cost_units FLOAT NOT NULL DEFAULT 0.0",
    )
    _add_column_if_missing(
        inspector, statements, "preview_submissions", "dedupe_key",
        "dedupe_key VARCHAR(255) NOT NULL DEFAULT ''",
    )
    _add_column_if_missing(
        inspector, statements, "preview_submissions", "attribution",
        "attribution JSON",
    )
    _add_column_if_missing(
        inspector, statements, "messages", "research_snapshot",
        "research_snapshot JSON",
    )

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        if "contact_points" in set(inspector.get_table_names()):
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_contact_points_normalized_value "
                    "ON contact_points (normalized_value)"
                )
            )
        if "preview_submissions" in set(inspector.get_table_names()):
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_preview_submissions_dedupe_key "
                    "ON preview_submissions (dedupe_key)"
                )
            )


def backfill_normalized_contacts(engine: Engine) -> None:
    """Backfill canonical values after the column migration."""
    if engine.dialect.name != "sqlite":
        return
    from app.services.dedupe import normalize_contact

    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT id, channel, value FROM contact_points "
                "WHERE normalized_value = '' OR normalized_value IS NULL"
            )
        ).mappings()
        for row in rows:
            connection.execute(
                text(
                    "UPDATE contact_points "
                    "SET normalized_value = :normalized WHERE id = :id"
                ),
                {
                    "id": row["id"],
                    "normalized": normalize_contact(row["channel"], row["value"]),
                },
            )
