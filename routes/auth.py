from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, session, g)
from werkzeug.security import generate_password_hash, check_password_hash
from models import get_db
from totp import generate_secret, verify_totp, otp_auth_uri
from functools import wraps

auth_bp = Blueprint('auth', __name__)


# ── Decorators ────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('auth.login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('auth.login', next=request.path))
        if session.get('user_role') != 'admin':
            flash('Administrator access required.', 'danger')
            return redirect(url_for('jobs.index'))
        return f(*args, **kwargs)
    return decorated


def load_user():
    """Call in app context to attach current user to g."""
    user_id = session.get('user_id')
    if user_id:
        with get_db() as conn:
            g.user = conn.execute(
                "SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    else:
        g.user = None


# ── Login / Logout ────────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect(url_for('jobs.index'))

    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        with get_db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE email=? AND active=1",
                (email,)).fetchone()

        if not user or not check_password_hash(user['password_hash'], password):
            flash('Invalid email or password.', 'danger')
            return render_template('auth/login.html')

        # Correct password — check 2FA
        if user['totp_enabled']:
            # Store partial auth in session, redirect to 2FA prompt
            session['pending_user_id']   = user['id']
            session['pending_user_role'] = user['role']
            session['pending_must_change'] = bool(user['must_change_pw'])
            return redirect(url_for('auth.totp_verify'))

        # No 2FA — complete login
        _complete_login(user)
        return _post_login_redirect(user)

    return render_template('auth/login.html')


@auth_bp.route('/login/2fa', methods=['GET', 'POST'])
def totp_verify():
    if not session.get('pending_user_id'):
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        with get_db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE id=?",
                (session['pending_user_id'],)).fetchone()

        if not user or not verify_totp(user['totp_secret'], code):
            flash('Invalid code. Please try again.', 'danger')
            return render_template('auth/totp_verify.html')

        _complete_login(user)
        must_change = session.pop('pending_must_change', False)
        session.pop('pending_user_id', None)
        session.pop('pending_user_role', None)

        if must_change:
            return redirect(url_for('auth.change_password'))
        return redirect(url_for('jobs.index'))

    return render_template('auth/totp_verify.html')


@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('auth.login'))


# ── 2FA Setup ─────────────────────────────────────────────────────────────────

@auth_bp.route('/setup-2fa', methods=['GET', 'POST'])
@login_required
def setup_2fa():
    user_id = session['user_id']
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

    if request.method == 'POST':
        code   = request.form.get('code', '').strip()
        secret = request.form.get('secret', '').strip()

        if not verify_totp(secret, code):
            flash('Code did not match. Please scan the QR code again and try.', 'danger')
            uri = otp_auth_uri(secret, user['email'])
            return render_template('auth/setup_2fa.html', uri=uri, secret=secret)

        with get_db() as conn:
            conn.execute(
                "UPDATE users SET totp_secret=?, totp_enabled=1 WHERE id=?",
                (secret, user_id))
            conn.commit()
        session['totp_enabled'] = True
        flash('Two-factor authentication enabled.', 'success')
        return redirect(url_for('auth.change_password')
                        if user['must_change_pw']
                        else url_for('jobs.index'))

    # Generate fresh secret for setup
    secret = generate_secret()
    uri    = otp_auth_uri(secret, user['email'])
    return render_template('auth/setup_2fa.html', uri=uri, secret=secret)


# ── Change Password ───────────────────────────────────────────────────────────

@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current  = request.form.get('current_password', '')
        new_pw   = request.form.get('new_password', '')
        confirm  = request.form.get('confirm_password', '')

        with get_db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE id=?",
                (session['user_id'],)).fetchone()

        if not check_password_hash(user['password_hash'], current):
            flash('Current password is incorrect.', 'danger')
            return render_template('auth/change_password.html')

        if len(new_pw) < 8:
            flash('New password must be at least 8 characters.', 'danger')
            return render_template('auth/change_password.html')

        if new_pw != confirm:
            flash('Passwords do not match.', 'danger')
            return render_template('auth/change_password.html')

        with get_db() as conn:
            conn.execute(
                "UPDATE users SET password_hash=?, must_change_pw=0 WHERE id=?",
                (generate_password_hash(new_pw), session['user_id']))
            conn.commit()

        flash('Password updated successfully.', 'success')
        return redirect(url_for('jobs.index'))

    return render_template('auth/change_password.html')


# ── User Management (admin only) ──────────────────────────────────────────────

@auth_bp.route('/users')
@admin_required
def users():
    with get_db() as conn:
        all_users = conn.execute(
            "SELECT * FROM users ORDER BY name").fetchall()
    return render_template('auth/users.html', users=all_users)


@auth_bp.route('/users/new', methods=['GET', 'POST'])
@admin_required
def new_user():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        name  = request.form.get('name', '').strip()
        if not email or not name:
            flash('Name and email are required.', 'danger')
            return render_template('auth/user_form.html', user=None)

        with get_db() as conn:
            if conn.execute(
                    "SELECT id FROM users WHERE email=?", (email,)).fetchone():
                flash(f'Email {email} is already registered.', 'danger')
                return render_template('auth/user_form.html', user=request.form)

            # Temporary password — user must change on first login
            temp_pw = request.form.get('password', '').strip()
            if len(temp_pw) < 8:
                flash('Password must be at least 8 characters.', 'danger')
                return render_template('auth/user_form.html', user=request.form)

            conn.execute("""
                INSERT INTO users (name, email, password_hash, phone, role, must_change_pw)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (name, email,
                  generate_password_hash(temp_pw),
                  request.form.get('phone', '').strip(),
                  request.form.get('role', 'mechanic')))
            conn.commit()

        flash(f'User {name} created. They must change their password on first login.', 'success')
        return redirect(url_for('auth.users'))

    return render_template('auth/user_form.html', user=None)


@auth_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        return "User not found", 404

    if request.method == 'POST':
        name  = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()

        with get_db() as conn:
            clash = conn.execute(
                "SELECT id FROM users WHERE email=? AND id!=?",
                (email, user_id)).fetchone()
            if clash:
                flash(f'Email {email} already in use.', 'danger')
                return render_template('auth/user_form.html', user=user)

            conn.execute("""
                UPDATE users SET name=?, email=?, phone=?, role=?, active=?
                WHERE id=?
            """, (name, email,
                  request.form.get('phone', '').strip(),
                  request.form.get('role', 'mechanic'),
                  1 if 'active' in request.form else 0,
                  user_id))

            # Optional password reset
            new_pw = request.form.get('reset_password', '').strip()
            if new_pw:
                if len(new_pw) < 8:
                    flash('Password must be at least 8 characters.', 'danger')
                    return render_template('auth/user_form.html', user=user)
                conn.execute("""
                    UPDATE users SET password_hash=?, must_change_pw=1 WHERE id=?
                """, (generate_password_hash(new_pw), user_id))

            # Optional 2FA reset
            if 'reset_2fa' in request.form:
                conn.execute("""
                    UPDATE users SET totp_secret=NULL, totp_enabled=0 WHERE id=?
                """, (user_id,))
                flash('2FA reset — user must re-enrol on next login.', 'success')

            conn.commit()

        flash('User updated.', 'success')
        return redirect(url_for('auth.users'))

    return render_template('auth/user_form.html', user=user)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _complete_login(user):
    session.clear()
    session['user_id']       = user['id']
    session['user_name']     = user['name']
    session['user_role']     = user['role']
    session['totp_enabled']  = bool(user['totp_enabled'])
    session['theme']         = user['theme'] or 'dark'
    session.permanent        = True


def _post_login_redirect(user):
    if user['must_change_pw']:
        return redirect(url_for('auth.change_password'))
    if not user['totp_enabled']:
        return redirect(url_for('auth.setup_2fa'))
    next_url = request.args.get('next') or url_for('jobs.index')
    return redirect(next_url)


@auth_bp.route('/set-theme/<theme>')
def set_theme(theme):
    if theme not in ('light', 'dark'):
        theme = 'dark'
    if session.get('user_id'):
        with get_db() as conn:
            conn.execute(
                "UPDATE users SET theme=? WHERE id=?",
                (theme, session['user_id']))
            conn.commit()
        session['theme'] = theme
    from flask import request as req
    return redirect(req.referrer or url_for('jobs.index'))
