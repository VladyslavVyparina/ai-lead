import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import SourceConnector


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def connector(db):
    c = SourceConnector(
        name="test-csv",
        adapter="csv_import",
        owner="owner",
        permitted_use="pilot research",
        quota_per_run=5000,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c
