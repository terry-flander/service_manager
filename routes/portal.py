"""
routes/portal.py — Public customer-facing job status portal.

Accessible via a secure token link with no login required.
The token is unguessable (secrets.token_hex(32)) so possession
of the URL is proof of identity.
"""
from flask import Blueprint, render_template
from models import get_db

portal_bp = Blueprint('portal', __name__)


@portal_bp.route('/job/<token>')
def job_portal(token):
    """Public job status page — no auth, token is the credential."""
    with get_db() as conn:
        job = conn.execute("""
            SELECT j.*, r.name as region_name
            FROM jobs j
            LEFT JOIN regions r ON r.id = j.region_id
            WHERE j.portal_token = ?
        """, (token,)).fetchone()

        if not job:
            return render_template('portal/not_found.html'), 404

        job_parts = conn.execute(
            "SELECT * FROM job_parts WHERE job_id=? ORDER BY id",
            (job['id'],)).fetchall()

    return render_template('portal/job.html',
                           job=dict(job),
                           job_parts=[dict(p) for p in job_parts],
                           token=token)
