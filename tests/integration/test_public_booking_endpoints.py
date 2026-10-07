"""
The website booking forms call these endpoints from another domain, without a
login. They must answer directly (no redirect to /login) and carry CORS headers.
"""
import os
import tempfile

import pytest

ORIGIN = 'https://keepcroft.com.au'


@pytest.fixture(scope='module')
def client():
    tmp = tempfile.mkdtemp()
    import models
    models.DB_PATH = os.path.join(tmp, 'field_service.db')
    models._data_dir = tmp
    os.environ.pop('GMAIL_USER', None)
    from app import create_app
    app = create_app()
    app.config['TESTING'] = True
    with models.get_db() as c:
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('pista_cors_origins', ?)", (ORIGIN,))
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('booking_cors_origins', ?)", (ORIGIN,))
        c.commit()
    return app.test_client()     # NOT logged in, like a website visitor


@pytest.mark.parametrize('url', [
    '/workshop/available-dates?from=2026-10-08&weeks=4',
    '/workshop/date-info?date=2026-10-08',
])
def test_workshop_get_is_public_with_cors(client, url):
    r = client.get(url, headers={'Origin': ORIGIN})
    assert r.status_code == 200, (r.status_code, r.headers.get('Location'))
    assert r.headers.get('Access-Control-Allow-Origin') == ORIGIN


@pytest.mark.parametrize('url', ['/workshop/request', '/booking/submit'])
def test_preflight_is_public(client, url):
    r = client.open(url, method='OPTIONS', headers={
        'Origin': ORIGIN, 'Access-Control-Request-Method': 'POST'})
    assert r.status_code in (200, 204), (r.status_code, r.headers.get('Location'))
    assert r.headers.get('Access-Control-Allow-Origin') == ORIGIN


@pytest.mark.parametrize('url', ['/workshop/request', '/booking/submit'])
def test_post_reaches_endpoint(client, url):
    # Wrong secret → the endpoint itself refuses (403), not a login redirect
    r = client.post(url, json={'_secret': 'wrong'}, headers={'Origin': ORIGIN})
    assert r.status_code == 403, (r.status_code, r.headers.get('Location'))
    assert r.headers.get('Access-Control-Allow-Origin') == ORIGIN


def test_rest_of_app_still_needs_login(client):
    assert client.get('/settings/job-statuses').status_code == 302
