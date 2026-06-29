"""
routes/column_visibility.py — CRUD for named, reusable Column Visibility
sets (which fields show on the Jobs List, per layout: desktop/landscape/
portrait), used by the shared query builder dialog. Sales Report
deliberately does not use this — its column set stays fixed.
"""
import json
from flask import Blueprint, request, jsonify
from models import get_db
from job_queries import column_visibility_row_to_dict, COLUMN_CATALOG

column_visibility_bp = Blueprint('column_visibility', __name__)


def _clean_column_list(value):
    """Validate a submitted column list against the catalog, dropping
    anything unrecognised rather than erroring — keeps saves robust
    even if the catalog changes later."""
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str) and v in COLUMN_CATALOG]


@column_visibility_bp.route('/column-visibility')
def list_sets():
    """Return all saved sets (id + name) for the dropdown in the query
    builder's Column Visibility picker."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name FROM column_visibility_sets ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return jsonify([{'id': r['id'], 'name': r['name']} for r in rows])


@column_visibility_bp.route('/column-visibility/catalog')
def catalog():
    """Return the full column catalog (key, label, align), for
    populating the builder's column checklist."""
    return jsonify([
        {'key': k, 'label': v['label'], 'align': v['align']}
        for k, v in COLUMN_CATALOG.items()
    ])


@column_visibility_bp.route('/column-visibility/<int:set_id>')
def get_set(set_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM column_visibility_sets WHERE id=?", (set_id,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    return jsonify({'ok': True, 'set': column_visibility_row_to_dict(row)})


@column_visibility_bp.route('/column-visibility', methods=['POST'])
def create_set():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'Name is required'}), 400

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM column_visibility_sets WHERE name=?", (name,)).fetchone()
        if existing:
            return jsonify({'ok': False, 'error': f'A set named "{name}" already exists'}), 400

        conn.execute("""
            INSERT INTO column_visibility_sets (name, page, desktop, landscape, portrait)
            VALUES (?, 'jobs', ?, ?, ?)
        """, (
            name,
            json.dumps(_clean_column_list(data.get('desktop'))),
            json.dumps(_clean_column_list(data.get('landscape'))),
            json.dumps(_clean_column_list(data.get('portrait'))),
        ))
        conn.commit()
        new_id = conn.execute(
            "SELECT id FROM column_visibility_sets WHERE name=?", (name,)).fetchone()['id']

    return jsonify({'ok': True, 'id': new_id})


@column_visibility_bp.route('/column-visibility/<int:set_id>', methods=['PUT'])
def update_set(set_id):
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'Name is required'}), 400

    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM column_visibility_sets WHERE id=?", (set_id,)).fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'Not found'}), 404

        dup = conn.execute(
            "SELECT id FROM column_visibility_sets WHERE name=? AND id!=?",
            (name, set_id)).fetchone()
        if dup:
            return jsonify({'ok': False, 'error': f'A set named "{name}" already exists'}), 400

        conn.execute("""
            UPDATE column_visibility_sets
            SET name=?, desktop=?, landscape=?, portrait=?, updated_at=datetime('now')
            WHERE id=?
        """, (
            name,
            json.dumps(_clean_column_list(data.get('desktop'))),
            json.dumps(_clean_column_list(data.get('landscape'))),
            json.dumps(_clean_column_list(data.get('portrait'))),
            set_id,
        ))
        conn.commit()

    return jsonify({'ok': True})


@column_visibility_bp.route('/column-visibility/<int:set_id>', methods=['DELETE'])
def delete_set(set_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM column_visibility_sets WHERE id=?", (set_id,)).fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'Not found'}), 404
        # Detach any queries pointing at this set before deleting it, so
        # they cleanly fall back to the default rather than dangling.
        conn.execute(
            "UPDATE job_queries SET column_visibility_id=NULL WHERE column_visibility_id=?",
            (set_id,))
        conn.execute("DELETE FROM column_visibility_sets WHERE id=?", (set_id,))
        conn.commit()
    return jsonify({'ok': True})
