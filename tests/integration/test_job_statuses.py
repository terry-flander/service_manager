"""
Integration tests for configurable job statuses (job_statuses table).

Covers the two modes:
  * empty table  -> built-in statuses, behaviour identical to before
  * seeded table -> custom statuses with special meanings take effect

Each test module gets its own throwaway database.
"""
import os
import runpy
import tempfile

import pytest

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))


@pytest.fixture(scope='module')
def app():
    tmp = tempfile.mkdtemp()
    import models
    models.DB_PATH = os.path.join(tmp, 'field_service.db')
    models._data_dir = tmp
    os.environ.pop('GMAIL_USER', None)
    os.environ.pop('GMAIL_REFRESH_TOKEN', None)

    from app import create_app
    application = create_app()
    application.config['TESTING'] = True
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        runpy.run_path(os.path.join(ROOT, 'migrate.py'))   # full schema
    finally:
        os.chdir(cwd)

    with models.get_db() as conn:
        conn.execute("INSERT INTO regions (name, visit_day) VALUES ('Bayside','Mon')")
        rid = conn.execute("SELECT id FROM regions").fetchone()[0]
        rows = [
            ('FB-1', 'Alice', 'booking', 'pending', '2026-10-05', 'tok1'),
            ('FB-2', 'Bob',   'booking', 'lost',    '2026-10-05', 'tok2'),
            ('FB-3', 'Cara',  'booking', 'paid',    '2026-10-05', 'tok3'),
            ('FB-4', 'Dan',   'booking', 'quote',   '2026-10-06', 'tok4'),
        ]
        for ref, name, jt, st, d, tok in rows:
            conn.execute("""INSERT INTO jobs (reference, customer_name, region_id, job_type,
                            status, scheduled_date, portal_token, paid_date, amount_paid)
                            VALUES (?,?,?,?,?,?,?,?,?)""",
                         (ref, name, rid, jt, st, d, tok,
                          d if st == 'paid' else None, 100 if st == 'paid' else None))
        conn.commit()
    yield application


@pytest.fixture()
def client(app):
    c = app.test_client()
    import models
    with models.get_db() as conn:
        uid = conn.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
    with c.session_transaction() as s:
        s['user_id'] = uid
        s['user_role'] = 'admin'
    return c


def _job_id(ref):
    import models
    with models.get_db() as conn:
        return conn.execute("SELECT id FROM jobs WHERE reference=?", (ref,)).fetchone()[0]


PAGES = ['/?sort=paid', '/settings/job-statuses', '/settings/status-triggers',
         '/calendar/events?start=2026-10-01&end=2026-10-31',
         '/reports/sales', '/reports/unreconciled-eftpos', '/bikes',
         '/mechanic/', '/job/tok1', '/job/tok3']


def test_01_empty_table_uses_builtins(app, client):
    import models
    assert not models.job_statuses_customised()
    codes = [s['code'] for s in models.get_job_statuses()]
    assert codes == list(models.BUILTIN_STATUS_CODES)
    assert models.status_codes_for('lost') == ['lost']
    assert models.status_sql_list('invoiced', 'paid') == "'invoiced','paid'"


def test_02_pages_render_with_builtins(client):
    for url in PAGES + [f"/jobs/{_job_id('FB-1')}", f"/jobs/{_job_id('FB-1')}/edit_legacy",
                        f"/mechanic/job/{_job_id('FB-1')}"]:
        r = client.get(url)
        assert r.status_code == 200, (url, r.status_code, r.headers.get("Location"), r.data[:300])


def test_03_job_list_hides_lost(client):
    html = client.get('/?status=').get_data(as_text=True)
    assert 'Alice' in html and 'Bob' not in html


def test_04_detail_dropdown_has_quote_and_lost(client):
    html = client.get(f"/jobs/{_job_id('FB-1')}").get_data(as_text=True)
    assert 'value="quote"' in html and 'value="lost"' in html


def test_05_old_status_colors_url_redirects(client):
    r = client.get('/settings/status-colors')
    assert r.status_code == 302 and 'job-statuses' in r.headers['Location']


def test_06_first_save_seeds_and_relabels(client):
    import models
    r = client.post('/settings/job-statuses', data={
        'action': 'save', 'label_in_progress': 'On the Stand',
        'sort_in_progress': '3', 'color_in_progress': '#8e24aa',
        'label_lost': 'Lost', 'sort_lost': '8', 'color_lost': '#d50000',
        'active_lost': '1',
    })
    assert r.status_code == 302
    assert models.job_statuses_customised()
    labels = models.status_labels_map()
    assert labels['in_progress'] == 'On the Stand'
    assert labels['pending'] == 'Pending'       # untouched rows keep defaults
    assert models.status_colors_map()['lost'] == '#d50000'


def test_07_add_custom_lost_status(client):
    import models
    r = client.post('/settings/job-statuses', data={
        'action': 'add', 'code': 'no_show', 'label': 'No Show',
        'special_meaning': 'lost', 'badge_color': '#616161',
        'job_types': ['booking'],
    })
    assert r.status_code == 302
    assert set(models.status_codes_for('lost')) == {'lost', 'no_show'}
    # move Alice to no_show
    with models.get_db() as conn:
        conn.execute("UPDATE jobs SET status='no_show' WHERE reference='FB-1'")
        conn.commit()
    html = client.get('/?status=').get_data(as_text=True)
    assert 'Alice' not in html                    # hidden like 'lost'
    ev = client.get('/calendar/events?start=2026-10-01&end=2026-10-31').get_json()
    assert not any('Alice' in (e.get('title') or '') for e in ev)


def test_08_custom_status_renders_everywhere(client):
    for url in PAGES + [f"/jobs/{_job_id('FB-1')}", f"/jobs/{_job_id('FB-1')}/edit_legacy",
                        f"/mechanic/job/{_job_id('FB-1')}"]:
        r = client.get(url)
        assert r.status_code == 200, (url, r.status_code, r.headers.get("Location"), r.data[:300])
    html = client.get(f"/jobs/{_job_id('FB-1')}").get_data(as_text=True)
    assert 'No Show' in html                                 # label in badge
    assert '.status-no_show{background:#61616126' in html    # generated CSS
    assert 'On the Stand' in html                            # relabelled built-in


def test_09_custom_paid_status_counts_as_paid(client):
    import models
    client.post('/settings/job-statuses', data={
        'action': 'add', 'code': 'paid_deposit', 'label': 'Paid – Deposit',
        'special_meaning': 'paid', 'badge_color': '#0b8043'})
    with models.get_db() as conn:
        conn.execute("UPDATE jobs SET status='paid_deposit' WHERE reference='FB-3'")
        conn.commit()
    assert models.status_has_meaning('paid_deposit', 'paid')
    html = client.get('/job/tok3').get_data(as_text=True)      # portal
    assert 'Invoice' in html


def test_10_bad_code_rejected(client):
    import models
    client.post('/settings/job-statuses', data={
        'action': 'add', 'code': "x'); DROP TABLE jobs;--", 'label': 'Evil'})
    assert "x');" not in [s['code'] for s in models.get_job_statuses(include_inactive=True)]


def test_11_system_status_cannot_be_deactivated(client):
    import models
    client.post('/settings/job-statuses', data={
        'action': 'save', 'label_paid': 'Paid', 'sort_paid': '7', 'color_paid': '#33b679'})
    paid = [s for s in models.get_job_statuses(include_inactive=True) if s['code'] == 'paid'][0]
    assert paid['active'] == 1


def test_12_revert_blocked_while_custom_codes_in_use(client):
    import models
    client.post('/settings/job-statuses', data={'action': 'reset'})
    assert models.job_statuses_customised()
    with models.get_db() as conn:
        conn.execute("UPDATE jobs SET status='pending' WHERE status NOT IN "
                     "('pending','scheduled','in_progress','quote','complete','invoiced','paid','lost')")
        conn.commit()
    client.post('/settings/job-statuses', data={'action': 'reset'})
    assert not models.job_statuses_customised()
