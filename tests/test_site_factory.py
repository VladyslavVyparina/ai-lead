import pytest

from app.core.schemas import Provenance, SourceRecordIn
from app.models import ContactPoint, Evidence
from app.services.ingestion import ingest_record
from app.services.proposal import PricebookError, build_proposal
from app.services.site_factory import FactValidationError, build_and_deploy_preview, build_site_config


def _business_with_facts(db, connector, confidence=1.0):
    record = SourceRecordIn(
        business_name="Kaapora Wellness",
        category="massage_wellness",
        city="porto",
        provenance=Provenance(source="test", permitted_use="test"),
    )
    business, _ = ingest_record(db, connector, record)
    db.add(ContactPoint(business_id=business.id, channel="phone", value="+351911222333", verified=True))
    db.add(Evidence(business_id=business.id, fact_key="address", fact_value="Rua B 2, Porto",
                    source_url="https://kaapora.pt", confidence=confidence))
    db.add(Evidence(business_id=business.id, fact_key="services",
                    fact_value=[{"name": "Massagem relax", "price": "EUR 45"}],
                    source_url="https://kaapora.pt", confidence=confidence))
    db.flush()
    return business


def test_site_config_built_only_from_evidence(db, connector):
    business = _business_with_facts(db, connector)
    config = build_site_config(db, business)
    assert config.business_name.value == "Kaapora Wellness"
    assert config.address.value == "Rua B 2, Porto"
    assert config.services[0].price.value == "EUR 45"
    # No evidence for these -> omitted, never invented
    assert config.hours is None
    assert config.description is None
    assert config.noindex is True


def test_low_confidence_facts_block_build(db, connector):
    business = _business_with_facts(db, connector, confidence=0.4)
    with pytest.raises(FactValidationError) as exc:
        build_and_deploy_preview(db, business, actor="test")
    assert "address" in exc.value.flagged


def test_preview_deploys_with_stub_and_passes_qa(db, connector):
    business = _business_with_facts(db, connector)
    build = build_and_deploy_preview(db, business, actor="test")
    db.commit()
    assert build.status == "deployed"
    assert build.preview_url.startswith("https://preview-")
    assert build.qa_report["links_ok"] is True
    assert build.expires_at is not None


def test_proposal_uses_versioned_pricebook(db, connector):
    business = _business_with_facts(db, connector)
    build_and_deploy_preview(db, business, actor="test")
    proposal = build_proposal(db, business, offer_package="local_pro", actor="test")
    db.commit()
    assert proposal.pricebook_version == "v1"
    assert proposal.content["offer"]["price_from_eur"] == 500
    assert proposal.content["preview_url"].startswith("https://preview-")
    assert proposal.content["evidence"]


def test_unknown_package_rejected(db, connector):
    business = _business_with_facts(db, connector)
    with pytest.raises(PricebookError):
        build_proposal(db, business, offer_package="enterprise", actor="test")
