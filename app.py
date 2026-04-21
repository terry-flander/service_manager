import os
from datetime import date as _date, timedelta
from flask import Flask, session, g, redirect, url_for, request
from models import init_db


def create_app():
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(name)s %(levelname)s %(message)s'
    )
    app = Flask(__name__)
    app.config['SECRET_KEY']          = os.environ.get('SECRET_KEY', 'dev-secret-CHANGE-in-production')
    app.config['GOOGLE_MAPS_API_KEY'] = os.environ.get('GOOGLE_MAPS_API_KEY', '')
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
    from routes.email_replies import email_replies_bp

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

    # ── Global auth gate ──────────────────────────────────────────────────────
    PUBLIC_ENDPOINTS = {'auth.login', 'auth.totp_verify', 'static'}

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
        if fmt == 'short':
            return value.strftime('%a %-d %b %Y')
        return value.strftime('%A %-d %B %Y')

    app.jinja_env.filters['fmt_date'] = _fmt_date

    @app.context_processor
    def inject_globals():
        # Load status colours from settings
        status_colors = {}
        _defaults = {
            'pending':'#f59e0b','scheduled':'#3b82f6','in_progress':'#8b5cf6',
            'complete':'#10b981','invoiced':'#6b7280','paid':'#10b981','void':'#ef4444',
        }
        try:
            from models import get_db
            with get_db() as _conn:
                for _s, _d in _defaults.items():
                    _row = _conn.execute(
                        "SELECT value FROM settings WHERE key=?",
                        (f'status_color_{_s}',)).fetchone()
                    status_colors[_s] = _row['value'] if _row else _d
        except Exception:
            status_colors = dict(_defaults)
        return {
            'google_maps_api_key': app.config['GOOGLE_MAPS_API_KEY'],
            'current_user': g.get('user'),
            'theme': session.get('theme', 'dark'),
            'status_colors': status_colors,
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
        existing = conn.execute(
            "SELECT id FROM users WHERE email='admin@flyingbike.com.au'").fetchone()
        if not existing:
            conn.execute("""
                INSERT INTO users (name, email, password_hash, role, must_change_pw)
                VALUES (?, ?, ?, 'admin', 1)
            """, ('Admin', 'admin@flyingbike.com.au',
                  generate_password_hash('changeme123')))
            conn.commit()
            print('✓ Default admin created: admin@flyingbike.com.au / changeme123')


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)
