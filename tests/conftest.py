import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from myapp import create_app, db  # type: ignore[import-not-found]
    from myapp.models import Lead  # type: ignore[import-not-found]
except ModuleNotFoundError:
    create_app = None
    db = None
    Lead = None


@pytest.fixture(scope='session')
def app():
    if create_app is None or db is None:
        pytest.skip("legacy myapp test fixtures are unavailable in this repo layout")
    app = create_app('testing')
    with app.app_context():
        db.create_all()  # Create the database
        yield app  # This is where the testing happens
        db.drop_all()  # Cleanup after tests


@pytest.fixture(scope='session')
def client(app):
    return app.test_client()


@pytest.fixture(scope='session')
def scoring_engine():
    # Create and return a mock scoring engine
    class MockScoringEngine:
        def score(self, lead):
            return 1  # Simulated scoring logic
    return MockScoringEngine()


@pytest.fixture(scope='session')
def mock_leads_data():
    return [
        {'name': 'Lead 1', 'email': 'lead1@example.com'},
        {'name': 'Lead 2', 'email': 'lead2@example.com'},
        {'name': 'Lead 3', 'email': 'lead3@example.com'}
    ]


@pytest.fixture(scope='function', autouse=bool(db))
def db_session(app):
    if db is None:
        pytest.skip("legacy myapp database fixture is unavailable in this repo layout")
    # Use a new database session for each test
    connection = db.engine.connect()
    transaction = connection.begin()
    db.session.remove()
    db.session.bind = connection
    yield db.session  # This is where the testing happens
    transaction.rollback()
    connection.close()
