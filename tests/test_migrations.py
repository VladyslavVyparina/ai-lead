from sqlalchemy import create_engine, inspect, text

from app.migrations import backfill_normalized_contacts, migrate_sqlite


def test_existing_sqlite_database_migrates_without_data_loss(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE contact_points ("
                "id INTEGER PRIMARY KEY, business_id INTEGER NOT NULL, "
                "channel VARCHAR(32) NOT NULL, value VARCHAR(512) NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO contact_points "
                "(id, business_id, channel, value) "
                "VALUES (1, 1, 'phone', '+351 912 345 678')"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE source_runs ("
                "id INTEGER PRIMARY KEY, status VARCHAR(32), checkpoint INTEGER)"
            )
        )

    migrate_sqlite(engine)
    backfill_normalized_contacts(engine)
    # Idempotency: a second startup must remain safe.
    migrate_sqlite(engine)
    backfill_normalized_contacts(engine)

    inspector = inspect(engine)
    contact_columns = {c["name"] for c in inspector.get_columns("contact_points")}
    run_columns = {c["name"] for c in inspector.get_columns("source_runs")}
    assert "normalized_value" in contact_columns
    assert "payload_sha256" in run_columns

    with engine.connect() as connection:
        value = connection.execute(
            text("SELECT normalized_value FROM contact_points WHERE id = 1")
        ).scalar_one()
    assert value == "912345678"
