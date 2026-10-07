import os
import re
from markupsafe import Markup
from datetime import date as _date, timedelta
from flask import Flask, session, g, redirect, url_for, request
from models import init_db


def create_app():
    import logging
    log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format='%(asctime)s %(name)s %(levelname)s %(message)s'
    )
    logging.getLogger('email_poller').setLevel(
        getattr(logging, log_level, logging.INFO)
    )
    app = Flask(__name__)
    app.config['SECRET_KEY']          = os.environ.get('SECRET_KEY', 'dev-secret-CHANGE-in-production')
    app.config['GOOGLE_MAPS_API_KEY'] = os.environ.get('GOOGLE_MAPS_API_KEY', '')
    app.config['BASE_URL']            = os.environ.get('BASE_URL', 'https://3.27.91.236')
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)

    # ── Blueprints ────────────────────────────────────────────────────────────
    from routes.auth     import auth_bp
    from routes.jobs     import jobs_bp
    from routes.parts    import parts_bp
    from routes.calendar import calendar_bp
    from routes.invoice  import invoice_bp
    from routes.customers import customers_bp
    from routes.regions  import regions_bp
    from routes.reports     import reports_bp
    from routes.import_jobs   import import_jobs_bp
    from routes.email_replies    import email_replies_bp
    from routes.eftpos import eftpos_bp
    app.register_blueprint(eftpos_bp)
    from routes.import_customers import import_customers_bp
    from routes.job_queries import job_queries_bp
    from routes.column_visibility import column_visibility_bp
    from routes.portal import portal_bp
    from routes.inventory import inventory_bp
    from routes.api import api_bp
    from routes.mechanic import mechanic_bp
    from routes.booking import booking_bp
    from routes.bikes import bikes_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(parts_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(invoice_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(regions_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(import_jobs_bp)
    app.register_blueprint(email_replies_bp)
    app.register_blueprint(import_customers_bp)
    app.register_blueprint(job_queries_bp)
    app.register_blueprint(column_visibility_bp)
    app.register_blueprint(portal_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(mechanic_bp)
    app.register_blueprint(booking_bp)
    app.register_blueprint(bikes_bp)
    from routes.sms import sms_bp
    app.register_blueprint(sms_bp)
    from routes.workshop_booking import workshop_booking_bp
    app.register_blueprint(workshop_booking_bp)

    # ── Global auth gate ──────────────────────────────────────────────────────
    PUBLIC_ENDPOINTS = {'auth.login', 'auth.totp_verify', 'static',
                        'portal.job_portal', 'portal.not_found',
                        'api.create_booking', 'booking.submit',
                        'bikes.public_bikes', 'bikes.bike_image',
                        # Public workshop booking calendar (website form)
                        'workshop_booking.workshop_preflight',
                        'workshop_booking.workshop_available_dates',
                        'workshop_booking.workshop_date_info',
                        'workshop_booking.workshop_request'}

    @app.before_request
    def require_login():
        if request.endpoint in PUBLIC_ENDPOINTS:
            return
        if not session.get('user_id'):
            return redirect(url_for('auth.login', next=request.path))
        # Attach user to g for templates
        from models import get_db
        with get_db() as conn:
            g.user = conn.execute(
                "SELECT * FROM users WHERE id=?",
                (session['user_id'],)).fetchone()
        # Keep theme in session (fast) but always trust DB value
        if g.user:
            session['theme'] = g.user['theme'] or 'dark'

    # ── Jinja globals ─────────────────────────────────────────────────────────
    def _fmt_date(value, fmt='full'):
        if not value:
            return '—'
        if isinstance(value, str):
            try:
                from datetime import datetime
                value = datetime.strptime(value[:10], '%Y-%m-%d').date()
            except ValueError:
                return value
        if fmt == 'dmy':
            return value.strftime('%d/%m/%Y')
        if fmt == 'short':
            return value.strftime('%a %-d %b %Y')
        if fmt == 'weekday_full':
            return value.strftime('%A %-d %B %Y').upper()
        return value.strftime('%A %-d %B %Y')

    def _fmt_datetime_local(value, fmt='%d/%m/%Y %H:%M'):
        """Convert a UTC datetime string to Australia/Melbourne local time."""
        if not value:
            return '—'
        try:
            from datetime import datetime, timezone
            from zoneinfo import ZoneInfo
            if isinstance(value, str):
                dt = datetime.strptime(value[:19], '%Y-%m-%d %H:%M:%S')
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = value.replace(tzinfo=timezone.utc) if not value.tzinfo else value
            local = dt.astimezone(ZoneInfo('Australia/Melbourne'))
            return local.strftime(fmt)
        except Exception:
            return str(value)[:16]

    def _fmt_phone(value):
        """Format a phone number as XXXX XXX XXX for display.
        Strips non-digits, then inserts spaces at positions 4 and 8.
        Returns the original value unchanged if it doesn't look like
        a 10-digit Australian number."""
        if not value:
            return ''
        digits = ''.join(c for c in str(value) if c.isdigit())
        if len(digits) == 10:
            return f"{digits[:4]} {digits[4:7]} {digits[7:]}"
        if len(digits) == 9:  # e.g. landline without area code
            return f"{digits[:4]} {digits[4:7]} {digits[7:]}"
        return str(value)

    app.jinja_env.filters['fmt_date']     = _fmt_date
    app.jinja_env.filters['fmt_datetime'] = _fmt_datetime_local
    app.jinja_env.filters['fmt_phone']    = _fmt_phone

    # ── Job status helpers (see models.get_job_statuses) ─────────────────────
    # {{ job['status']|status_label }}           -> "In Progress" / custom label
    # {% if status_is(job['status'], 'paid', 'invoiced') %}  -> meaning check
    # {{ status_color(code) }}                   -> hex
    # statuses_for_job_type(job_type, current)   -> list for <select> options
    from models import (status_label as _status_label,
                        status_has_meaning as _status_has_meaning,
                        status_colors_map as _status_colors_map,
                        statuses_for_job_type as _statuses_for_job_type,
                        STATUS_FALLBACK_COLOR as _SFC)

    def _status_is(code, *meanings):
        return any(_status_has_meaning(code, m) for m in meanings)

    app.jinja_env.filters['status_label'] = lambda c: _status_label(c)
    app.jinja_env.globals['status_is'] = _status_is
    app.jinja_env.globals['status_color'] = lambda c: _status_colors_map().get(c, _SFC)
    app.jinja_env.globals['statuses_for_job_type'] = \
        lambda jt, current=None: _statuses_for_job_type(jt, current)

    @app.context_processor
    def inject_globals():
        # Load status colours from settings
        from models import (get_db, get_job_statuses, status_colors_map,
                            BUILTIN_STATUS_CODES, STATUS_CODE_RE)
        try:
            with get_db() as _conn:
                status_colors = status_colors_map(_conn)
                job_statuses_all = get_job_statuses(_conn, include_inactive=True)
                job_statuses_active = get_job_statuses(_conn)
                unread_email_count = _conn.execute(
                    "SELECT COUNT(*) FROM email_imports WHERE read=1 OR read IS NULL"
                ).fetchone()[0]
        except Exception:
            status_colors = {}
            job_statuses_all = job_statuses_active = []
            unread_email_count = 0
        # Badge CSS for custom (non built-in) status codes. Built-ins are
        # styled in base.html via the --sc-<code> variables above.
        _css = []
        for _s in job_statuses_all:
            _c, _hex = _s['code'], _s['badge_color']
            if _c in BUILTIN_STATUS_CODES or not STATUS_CODE_RE.match(_c):
                continue
            if not re.match(r'^#[0-9a-fA-F]{6}$', _hex or ''):
                continue
            _css.append(
                f".status-{_c}{{background:{_hex}26;color:{_hex};}}"
                f".job-card.status-{_c}{{border-left-color:{_hex};border-color:{_hex}4d;}}"
                f".status-{_c} .status-icon,.status-{_c} .status-value{{color:{_hex};}}")
        status_css = Markup('<style>' + ''.join(_css) + '</style>') if _css else ''
        from models import get_settings as _get_settings, get_job_types as _get_job_types
        try:
            biz_settings = _get_settings()
        except Exception:
            biz_settings = {}
        try:
            job_types = _get_job_types()
        except Exception:
            job_types = {}
        return {
            'google_maps_api_key': app.config['GOOGLE_MAPS_API_KEY'],
            'current_user': g.get('user'),
            'theme': session.get('theme', 'dark'),
            'status_colors': status_colors,
            'status_css': status_css,
            # [[code, label], ...] for the shared query builder checkboxes
            'job_status_choices': [[s['code'], s['label']] for s in job_statuses_active],
            'paid_status_codes': [s['code'] for s in job_statuses_all if s['code'] == 'paid' or s['special_meaning'] == 'paid'] or ['paid'],
            'job_status_labels': {s['code']: s['label'] for s in job_statuses_all},
            'unread_email_count': unread_email_count,
            'app_version': __import__('version').VERSION,
            'settings': biz_settings,
            'JOB_TYPES': job_types,
        }

    # ── DB init + seed ────────────────────────────────────────────────────────
    with app.app_context():
        init_db()
        _seed_admin()
        from seed import seed_data
        seed_data()

    # ── Start email poller (only if GMAIL credentials are configured) ──────────
    if os.environ.get('GMAIL_USER') and os.environ.get('GMAIL_REFRESH_TOKEN'):
        from email_poller import start_poller
        start_poller(app)
    else:
        import logging
        logging.getLogger('email_poller').warning(
            'GMAIL_USER/GMAIL_APP_PASSWORD not set — email polling disabled')

    return app


def _seed_admin():
    """Create default admin account if none exists — safe to call on every startup."""
    from models import get_db
    from werkzeug.security import generate_password_hash
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
        if not existing:
            conn.execute("""
                INSERT INTO users (name, email, password_hash, role, must_change_pw)
                VALUES (?, ?, ?, 'admin', 1)
            """, ('Admin', 'admin@localhost',
                  generate_password_hash('changeme123')))
            conn.commit()
            print('✓ Default admin created: admin@localhost / changeme123')


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)
