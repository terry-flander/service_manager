"""
routes/bikes.py — Bikes for Sale management.

Handles:
  - /bikes               List of bikes_for_sale records
  - /bikes/<id>          Edit modal JSON (GET/POST)
  - /bikes/<id>/images   Image upload / delete
  - /bikes/<id>/delete   Delete bike record + images
  - /bikes/<id>/transfer Transfer job customer to buyer
  - /bikes-for-sale      Public JSON endpoint for Pista Bikes website
"""
import os
import re
import logging
from datetime import date, datetime, timedelta
from flask import (Blueprint, request, jsonify, render_template,
                   session, abort, send_file)

from models import get_db

bikes_bp = Blueprint('bikes', __name__)
log      = logging.getLogger('app')

ATTACHMENT_BASE  = '/data/attachments'
ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}
MAX_IMAGE_SIZE   = 5 * 1024 * 1024
BIKES_FOR_SALE_EMAIL = 'bikes.for.sale@flyingbike.internal'


# ── Helper ────────────────────────────────────────────────────────────────────

def _bike_image_url(img):
    return f"/bikes/images/{img['id']}"


def _bike_dict(row, images=None):
    d = dict(row)
    d['images'] = images or []
    return d


# ── List ──────────────────────────────────────────────────────────────────────

@bikes_bp.route('/bikes')
def bikes_list():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT b.*, j.reference, j.status as job_status,
                   j.scheduled_date, j.bike_description
            FROM bikes_for_sale b
            JOIN jobs j ON j.id = b.job_id
            ORDER BY b.updated_at DESC
        """).fetchall()
        bikes = []
        for r in rows:
            d = dict(r)
            imgs = conn.execute(
                "SELECT * FROM bike_images WHERE bike_id=? ORDER BY sort_order, id",
                (r['id'],)).fetchall()
            d['images'] = [dict(i) for i in imgs]
            bikes.append(d)
    return render_template('bikes/list.html', bikes=bikes)


# ── Get / create bike record for a job ───────────────────────────────────────

@bikes_bp.route('/bikes/for-job/<int:job_id>')
def bike_for_job(job_id):
    """Return or create a bikes_for_sale record for a sale_bike job."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM bikes_for_sale WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO bikes_for_sale (job_id) VALUES (?)", (job_id,))
            conn.commit()
            row = conn.execute(
                "SELECT * FROM bikes_for_sale WHERE job_id=?", (job_id,)).fetchone()
        imgs = conn.execute(
            "SELECT * FROM bike_images WHERE bike_id=? ORDER BY sort_order, id",
            (row['id'],)).fetchall()
        # Fetch spec templates
        templates = conn.execute(
            "SELECT id, name, body FROM email_templates WHERE grp='bike' ORDER BY name"
        ).fetchall()
    d = dict(row)
    d['images'] = [dict(i) | {'url': _bike_image_url(i)} for i in imgs]
    d['spec_templates'] = [dict(t) for t in templates]
    return jsonify({'ok': True, 'bike': d})


# ── Edit ──────────────────────────────────────────────────────────────────────

@bikes_bp.route('/bikes/<int:bike_id>/short-desc', methods=['POST'])
def bike_update_short_desc(bike_id):
    """Partial update — only short_desc, used by job detail sync."""
    data = request.get_json() or {}
    with get_db() as conn:
        conn.execute(
            "UPDATE bikes_for_sale SET short_desc=?, updated_at=datetime('now') WHERE id=?",
            ((data.get('short_desc') or '').strip(), bike_id))
        conn.commit()
    return jsonify({'ok': True})

@bikes_bp.route('/bikes/<int:bike_id>', methods=['GET', 'POST'])
def bike_edit(bike_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM bikes_for_sale WHERE id=?", (bike_id,)).fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'Not found'}), 404

        if request.method == 'GET':
            imgs = conn.execute(
                "SELECT * FROM bike_images WHERE bike_id=? ORDER BY sort_order, id",
                (bike_id,)).fetchall()
            templates = conn.execute(
                "SELECT id, name, body FROM email_templates WHERE grp='bike' ORDER BY name"
            ).fetchall()
            d = dict(row)
            d['images'] = [dict(i) | {'url': _bike_image_url(i)} for i in imgs]
            d['spec_templates'] = [dict(t) for t in templates]
            return jsonify({'ok': True, 'bike': d})

        data = request.get_json() or {}
        conn.execute("""
            UPDATE bikes_for_sale
            SET short_desc=?, description_html=?,
                condition_grade=?, frame_size=?, colour=?,
                year_est=?, asking_price=?, min_price=?, status=?,
                updated_at=datetime('now')
            WHERE id=?
        """, (
            (data.get('short_desc') or '').strip(),
            (data.get('description_html') or '').strip(),
            (data.get('condition_grade') or '').strip(),
            (data.get('frame_size') or '').strip(),
            (data.get('colour') or '').strip(),
            data.get('year_est') or None,
            data.get('asking_price') or None,
            data.get('min_price') or None,
            (data.get('status') or 'preparing').strip(),
            bike_id,
        ))
        conn.commit()
    return jsonify({'ok': True})


# ── Image upload ──────────────────────────────────────────────────────────────

@bikes_bp.route('/bikes/<int:bike_id>/images', methods=['POST'])
def bike_image_upload(bike_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM bikes_for_sale WHERE id=?", (bike_id,)).fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'Not found'}), 404

        f = request.files.get('image')
        if not f:
            return jsonify({'ok': False, 'error': 'No file'}), 400
        if f.content_type not in ALLOWED_IMAGE_TYPES:
            return jsonify({'ok': False, 'error': 'Only images allowed'}), 400
        data = f.read()
        if len(data) > MAX_IMAGE_SIZE:
            return jsonify({'ok': False, 'error': 'Image too large (max 5MB)'}), 400

        import hashlib
        content_hash = hashlib.md5(data).hexdigest()[:8]
        safe_name    = re.sub(r'[^\w.\-]', '_', f.filename or 'image')
        base, ext    = os.path.splitext(safe_name)
        filename     = f"{base}_{content_hash}{ext or '.jpg'}"

        dir_path = os.path.join(ATTACHMENT_BASE, f'bike_{bike_id}')
        os.makedirs(dir_path, exist_ok=True)
        filepath = os.path.join(dir_path, filename)
        with open(filepath, 'wb') as fp:
            fp.write(data)

        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order),0) FROM bike_images WHERE bike_id=?",
            (bike_id,)).fetchone()[0]
        conn.execute("""
            INSERT INTO bike_images (bike_id, filename, filepath, sort_order)
            VALUES (?, ?, ?, ?)
        """, (bike_id, filename, filepath, max_order + 1))
        conn.commit()
        img_id = conn.execute(
            "SELECT id FROM bike_images WHERE filepath=?", (filepath,)).fetchone()['id']

    return jsonify({'ok': True, 'image': {'id': img_id, 'filename': filename,
                                           'url': _bike_image_url({'id': img_id})}})


@bikes_bp.route('/bikes/images/<int:img_id>', methods=['GET', 'DELETE'])
def bike_image(img_id):
    with get_db() as conn:
        img = conn.execute(
            "SELECT * FROM bike_images WHERE id=?", (img_id,)).fetchone()
        if not img:
            abort(404)
        if request.method == 'DELETE':
            if os.path.exists(img['filepath']):
                os.remove(img['filepath'])
            conn.execute("DELETE FROM bike_images WHERE id=?", (img_id,))
            conn.commit()
            return jsonify({'ok': True})
    if not os.path.exists(img['filepath']):
        abort(404)
    return send_file(img['filepath'], mimetype='image/jpeg')


# ── Delete bike record ────────────────────────────────────────────────────────

@bikes_bp.route('/bikes/<int:bike_id>/delete', methods=['POST'])
def bike_delete(bike_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM bikes_for_sale WHERE id=?", (bike_id,)).fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'Not found'}), 404
        # Delete image files
        imgs = conn.execute(
            "SELECT filepath FROM bike_images WHERE bike_id=?", (bike_id,)).fetchall()
        for img in imgs:
            if os.path.exists(img['filepath']):
                os.remove(img['filepath'])
        conn.execute("DELETE FROM bikes_for_sale WHERE id=?", (bike_id,))
        conn.commit()
    return jsonify({'ok': True})


# ── Transfer customer ─────────────────────────────────────────────────────────

@bikes_bp.route('/bikes/<int:bike_id>/transfer', methods=['POST'])
def bike_transfer(bike_id):
    """Transfer the bike job's customer from Bikes for Sale to the buyer."""
    data = request.get_json() or {}
    with get_db() as conn:
        bike = conn.execute(
            "SELECT * FROM bikes_for_sale WHERE id=?", (bike_id,)).fetchone()
        if not bike:
            return jsonify({'ok': False, 'error': 'Not found'}), 404

        job = conn.execute(
            "SELECT * FROM jobs WHERE id=?", (bike['job_id'],)).fetchone()
        if not job:
            return jsonify({'ok': False, 'error': 'Job not found'}), 404

        # Get or create buyer customer
        buyer_id   = data.get('customer_id')
        buyer_name = (data.get('name') or '').strip()
        buyer_email= (data.get('email') or '').strip()
        buyer_phone= (data.get('phone') or '').strip()

        if buyer_id:
            cust = conn.execute(
                "SELECT * FROM customers WHERE id=?", (buyer_id,)).fetchone()
            if not cust:
                return jsonify({'ok': False, 'error': 'Customer not found'}), 404
            buyer_name  = cust['name']
            buyer_email = cust['email']
            buyer_phone = cust['phone']
        else:
            if not buyer_name or not buyer_email:
                return jsonify({'ok': False,
                    'error': 'Name and email required for new customer'}), 400
            from routes.jobs import upsert_customer
            buyer_id, _ = upsert_customer(
                conn, buyer_name, buyer_email, buyer_phone, '', '')

        # Update job customer
        conn.execute("""
            UPDATE jobs
            SET customer_id=?, customer_name=?, customer_email=?, customer_phone=?
            WHERE id=?
        """, (buyer_id, buyer_name, buyer_email, buyer_phone, job['id']))
        # Mark bike as sold
        conn.execute(
            "UPDATE bikes_for_sale SET status='sold', updated_at=datetime('now') WHERE id=?",
            (bike_id,))
        conn.commit()
    log.info(f"Bike {bike_id} transferred to customer {buyer_id} ({buyer_name})")
    return jsonify({'ok': True, 'job_id': job['id']})


# ── Public endpoint ───────────────────────────────────────────────────────────

@bikes_bp.route('/bikes-for-sale')
def public_bikes():
    """Public JSON feed for Pista Bikes website. No auth required."""
    with get_db() as conn:
        days_row = conn.execute(
            "SELECT value FROM settings WHERE key='bikes_sold_days'"
        ).fetchone()
        sold_days = int(days_row['value']) if days_row else 30
        cutoff    = (date.today() - timedelta(days=sold_days)).isoformat()

        rows = conn.execute("""
            SELECT b.id, b.short_desc, b.description_html,
                   b.year_est, b.asking_price, b.status, b.updated_at,
                   b.condition_grade, b.frame_size, b.colour,
                   j.status as job_status, j.bike_description
            FROM bikes_for_sale b
            JOIN jobs j ON j.id = b.job_id
            WHERE j.status = 'complete'
               OR (b.status = 'sold' AND b.updated_at >= ?)
            ORDER BY b.updated_at DESC
        """, (cutoff,)).fetchall()

        result = []
        for r in rows:
            imgs = conn.execute(
                "SELECT id, filename FROM bike_images WHERE bike_id=? ORDER BY sort_order, id",
                (r['id'],)).fetchall()
            result.append({
                'id':               r['id'],
                'short_desc':       r['short_desc'],
                'description_html': r['description_html'] or '',
                'year_est':         r['year_est'],
                'asking_price':     r['asking_price'],
                'condition_grade':  r['condition_grade'] or '',
                'frame_size':       r['frame_size'] or '',
                'colour':           r['colour'] or '',
                'status':           'sold' if r['status'] == 'sold' else 'available',
                'bike_description': r['bike_description'],
                'images': [
                    {'id': i['id'], 'url': f"{_public_base()}/bikes/images/{i['id']}"}
                    for i in imgs
                ],
            })

    resp = jsonify(result)
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp


def _public_base():
    return os.environ.get('BIKES_FOR_SALE_URL',
                          'https://app.theflyingbike.com.au').rstrip('/')
