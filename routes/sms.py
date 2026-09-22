"""
routes/sms.py — SMS compose, send and log endpoints.
"""
from flask import Blueprint, request, jsonify, g
from models import get_db

sms_bp = Blueprint('sms', __name__)


@sms_bp.route('/sms/templates')
def sms_templates():
    """Return all SMS templates for the compose modal."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, body FROM email_templates WHERE grp='sms' ORDER BY name"
        ).fetchall()
    return jsonify([{'id': r['id'], 'name': r['name'], 'body': r['body']} for r in rows])


@sms_bp.route('/sms/render', methods=['POST'])
def sms_render():
    """Render a template body with job variable substitution for preview."""
    from sms_sender import render_sms_body
    data   = request.get_json() or {}
    job_id = data.get('job_id')
    body   = data.get('body', '')
    with get_db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404
    return jsonify({'ok': True, 'body': render_sms_body(body, dict(job))})


@sms_bp.route('/sms/send', methods=['POST'])
def sms_send():
    """Send an SMS. Body: {job_id, body, to_number (optional override)}."""
    from sms_sender import send_sms, is_sendable_mobile, get_sms_enabled
    if not get_sms_enabled():
        return jsonify({'ok': False, 'error': 'SMS is not enabled'}), 400

    data   = request.get_json() or {}
    job_id = data.get('job_id')
    body   = (data.get('body') or '').strip()
    to_raw = (data.get('to_number') or '').strip()

    if not job_id or not body:
        return jsonify({'ok': False, 'error': 'job_id and body required'}), 400

    with get_db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404

    if not to_raw:
        to_raw = job['customer_phone'] or ''

    if not is_sendable_mobile(to_raw):
        return jsonify({'ok': False, 'error': f'No valid mobile: {to_raw!r}'}), 400

    sent_by = g.user['id'] if g.get('user') else None
    ok, sid, err = send_sms(to_raw, body, job_id=job_id, sent_by=sent_by)
    if ok:
        return jsonify({'ok': True, 'sid': sid})
    return jsonify({'ok': False, 'error': err}), 500


@sms_bp.route('/sms/log/<int:job_id>')
def sms_log(job_id):
    """Return SMS history for a job."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.id, s.to_number, s.body, s.status, s.twilio_sid,
                   s.error_msg, s.sent_at, u.name as sent_by_name
            FROM sms_log s
            LEFT JOIN users u ON u.id = s.sent_by
            WHERE s.job_id=? ORDER BY s.sent_at DESC
        """, (job_id,)).fetchall()
    return jsonify([dict(r) for r in rows])
