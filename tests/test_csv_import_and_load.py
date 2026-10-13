import random

import pytest

from app.adapters.csv_import import PayloadMismatch, run_import
from app.models import Business, SourceRecord

HEADER = "name,category,city,address,website,email,phone\n"


def _csv(rows: list[str]) -> str:
    return HEADER + "\n".join(rows) + "\n"


def test_basic_import_with_provenance(db, connector):
    csv_text = _csv([
        "Bella Spa,massage_wellness,porto,Rua A 1,,bella@spa.pt,+351911111111",
        "Glow Studio,beauty_aesthetics,lisbon,,,glow@x.pt,",
    ])
    run = run_import(db, connector, csv_text)
    assert run.status == "completed"
    assert run.records_ingested == 2
    record = db.query(SourceRecord).first()
    assert record.provenance["source"] == connector.name
    assert record.provenance["permitted_use"] == connector.permitted_use


def test_skips_rows_without_name(db, connector):
    csv_text = _csv([
        ",massage_wellness,porto,,,x@x.pt,",
        "Real Spa,massage_wellness,porto,,,y@y.pt,",
    ])
    run = run_import(db, connector, csv_text)
    assert run.records_ingested == 1
    assert run.records_skipped == 1


def test_failed_run_resumes_from_checkpoint_without_duplicates(db, connector, monkeypatch):
    rows = [f"Spa {i},massage_wellness,porto,,,s{i}@x.pt," for i in range(10)]
    csv_text = _csv(rows)

    import app.adapters.csv_import as csv_mod

    original = csv_mod.ingest_record
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 6:
            raise RuntimeError("provider timeout")
        return original(*args, **kwargs)

    monkeypatch.setattr(csv_mod, "ingest_record", flaky)
    run = run_import(db, connector, csv_text)
    assert run.status == "failed"
    assert run.checkpoint == 5

    monkeypatch.setattr(csv_mod, "ingest_record", original)
    run = run_import(db, connector, csv_text, resume_run=run)
    assert run.status == "completed"
    assert db.query(Business).count() == 10  # no duplicates, nothing lost


def test_resume_rejects_different_file(db, connector, monkeypatch):
    rows = [f"Diff Spa {i},massage_wellness,porto,,,d{i}@x.pt," for i in range(5)]
    csv_text = _csv(rows)

    import app.adapters.csv_import as csv_mod

    original = csv_mod.ingest_record
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return original(*args, **kwargs)

    monkeypatch.setattr(csv_mod, "ingest_record", flaky)
    run = run_import(db, connector, csv_text)
    assert run.status == "failed"

    monkeypatch.setattr(csv_mod, "ingest_record", original)
    other_csv = _csv(["Other Spa,massage_wellness,porto,,,o@x.pt,"])
    with pytest.raises(PayloadMismatch):
        run_import(db, connector, other_csv, resume_run=run)

    # The identical file resumes fine.
    run = run_import(db, connector, csv_text, resume_run=run)
    assert run.status == "completed"
    assert db.query(Business).count() == 5


def test_quota_limits_single_run(db, connector):
    connector.quota_per_run = 3
    db.add(connector)
    db.commit()
    rows = [f"Q Spa {i},massage_wellness,porto,,,q{i}@x.pt," for i in range(5)]
    run = run_import(db, connector, _csv(rows))
    assert run.records_ingested == 3
    assert run.checkpoint == 3


def test_load_1000_mixed_records(db, connector):
    """Technical load test (§4.1): 1,000 mixed records with ~10% duplicates."""
    random.seed(42)
    cities = ["porto", "lisbon", "braga"]
    rows = []
    for i in range(900):
        rows.append(f"Load Spa {i},massage_wellness,{random.choice(cities)},,,l{i}@x.pt,")
    for i in range(100):  # duplicates of the first hundred
        rows.append(f"Load Spa {i} Lda,massage_wellness,{cities[0]},,,l{i}@x.pt,")

    run = run_import(db, connector, _csv(rows))
    assert run.status == "completed"
    assert run.records_ingested == 1000
    unique = db.query(Business).count()
    assert unique <= 900
    assert unique >= 600  # duplicates resolved, real records kept
