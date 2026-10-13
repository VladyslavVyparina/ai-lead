from app.core.schemas import InboundLeadIn, Provenance, SourceRecordIn
from app.models import Business, SourceRecord
from app.services.dedupe import normalize_name, normalize_phone
from app.services.ingestion import ingest_inbound, ingest_record


def _record(name, city="porto", **kwargs) -> SourceRecordIn:
    return SourceRecordIn(
        business_name=name,
        category="beauty_aesthetics",
        city=city,
        provenance=Provenance(source="test", permitted_use="test"),
        **kwargs,
    )


def test_normalize_name_strips_accents_and_legal_suffixes():
    assert normalize_name("Estética Bela, Lda.") == normalize_name("estetica bela")


def test_normalize_phone_matches_formats():
    assert normalize_phone("+351 912 345 678") == normalize_phone("912345678")


def test_same_name_city_dedupes(db, connector):
    b1, created1 = ingest_record(db, connector, _record("Clínica Sorriso"))
    db.commit()
    b2, created2 = ingest_record(db, connector, _record("Clinica Sorriso LDA"))
    db.commit()
    assert created1 and not created2
    assert b1.id == b2.id
    assert db.query(Business).count() == 1


def test_phone_match_dedupes_across_names(db, connector):
    ingest_record(db, connector, _record("Spa Um", phone="+351 912 345 678"))
    db.commit()
    b2, created = ingest_record(db, connector, _record("Spa One Rebrand", phone="912345678"))
    db.commit()
    assert not created
    assert db.query(Business).count() == 1


def test_replay_same_record_is_idempotent(db, connector):
    record = _record("Studio Glow", email="hi@glow.pt")
    ingest_record(db, connector, record)
    db.commit()
    ingest_record(db, connector, record)
    db.commit()
    assert db.query(SourceRecord).count() == 1
    assert db.query(Business).count() == 1


def test_different_cities_stay_separate(db, connector):
    ingest_record(db, connector, _record("Bella Spa", city="porto"))
    ingest_record(db, connector, _record("Bella Spa", city="braga"))
    db.commit()
    assert db.query(Business).count() == 2


def test_contact_points_store_normalized_value(db, connector):
    b, _ = ingest_record(db, connector, _record("Norm Spa", phone="+351 912 345 678", email="Info@Spa.PT"))
    db.commit()
    from app.models import ContactPoint

    contacts = {c.channel: c for c in db.query(ContactPoint).filter(ContactPoint.business_id == b.id)}
    assert contacts["phone"].normalized_value == "912345678"
    assert contacts["email"].normalized_value == "info@spa.pt"


def test_orphaned_source_record_repaired_on_replay(db, connector):
    record = _record("Orphan Spa", email="orphan@spa.pt")
    from app.services.ingestion import _idempotency_key

    # Simulate a failed earlier attempt: record persisted without business link.
    db.add(
        SourceRecord(
            business_id=None,
            connector_id=connector.id,
            raw=record.model_dump(mode="json"),
            provenance=record.provenance.model_dump(mode="json"),
            idempotency_key=_idempotency_key(connector.id, record),
        )
    )
    db.commit()

    business, created = ingest_record(db, connector, record)
    db.commit()
    assert created
    orphan = db.query(SourceRecord).one()
    assert orphan.business_id == business.id


def test_config_loader_returns_isolated_copies():
    from app.services.config_loader import load

    first = load("scoring")
    first["weights"]["digital_gap"] = 999
    second = load("scoring")
    assert second["weights"]["digital_gap"] == 30


def test_inbound_routes_to_mql_not_sql(db):
    from app.models import SourceConnector

    connector = SourceConnector(
        name="inbound", adapter="inbound_webhook", owner="o", permitted_use="inbound"
    )
    db.add(connector)
    db.commit()

    lead = InboundLeadIn(
        campaign="pilot-forms",
        business_name="Massagens Rio",
        email="rio@example.pt",
        city="lisbon",
        idempotency_key="inb-1",
    )
    business, created = ingest_inbound(db, connector, lead)
    db.commit()
    assert created
    assert business.state == "mql"
    assert business.route == "inbound"
