"""
Shared fixtures for the service_manager test suite.
Unit tests (tests/unit/) need none of these — they test pure functions directly.
Integration tests (tests/integration/) use app, client, and auth_client.
"""
import os
import tempfile
import pytest


@pytest.fixture(scope='session')
def app():
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    os.environ['DATA_DIR'] = os.path.dirname(db_path)
    # Prevent the background email-poller thread from starting
    os.environ.pop('GMAIL_USER', None)
    os.environ.pop('GMAIL_REFRESH_TOKEN', None)

    from app import create_app
    application = create_app()
    application.config['TESTING'] = True
    application.config['WTF_CSRF_ENABLED'] = False

    yield application

    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_client(client):
    """Flask test client pre-authenticated as the default admin."""
    client.post('/login', data={
        'email':    'admin@flyingbike.com.au',
        'password': 'changeme123',
    }, follow_redirects=True)
    return client
