"""
routes/job_queries.py — CRUD for saved/named job-filter queries, used by
both the Jobs List and Sales Report query builder dialogs.
"""
import json
from flask import Blueprint, request, jsonify
from models import get_db
from job_queries import query_row_to_dict, get_date_presets, SORTABLE_SQL_COLUMNS

job_queries_bp = Blueprint('job_queries', __name__)

VALID_SORT_FIELDS = set(SORTABLE_SQL_COLUMNS.keys()) | {'gross'}


def _clean_sort_field(value):
    value = (value or '').strip()
    return value if value in VALID_SORT_FIELDS else None


def _clean_sort_dir(value):
    value = (value or 'asc').strip().lower()
    return 'desc' if value == 'desc' else 'asc'


@job_queries_bp.route('/job-queries')
def list_queries():
    """Return all saved queries (id + name only) for populating the
    dropdown selector on Jobs List / Sales Report."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name FROM job_queries ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return jsonify([{'id': r['id'], 'name': r['name']} for r in rows])


@job_queries_bp.route('/job-queries/<int:query_id>')
def get_query(query_id):
    """Return the full definition of one saved query, for pre-filling
    the builder dialog when Edit is clicked."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM job_queries WHERE id=?", (query_id,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    return jsonify({'ok': True, 'query': query_row_to_dict(row)})


@job_queries_bp.route('/job-queries/presets')
def date_presets():
    """Return the current set of date preset options (labels include
    the live-computed fiscal year, e.g. FY2027)."""
    return jsonify([{'value': v, 'label': l} for v, l in get_date_presets()])


@job_queries_bp.route('/job-queries', methods=['POST'])
def create_query():
    """Create a new saved query. Expects JSON body matching the
    query dict shape (job_types/statuses/payment_types as arrays)."""
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'Name is required'}), 400

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM job_queries WHERE name=?", (name,)).fetchone()
        if existing:
            return jsonify({'ok': False, 'error': f'A query named "{name}" already exists'}), 400

        conn.execute("""
            INSERT INTO job_queries
                (name, job_types, statuses, payment_types, search,
                 gross_min, gross_max, date_mode, date_preset, date_from, date_to,
                 sort1_field, sort1_dir, sort2_field, sort2_dir, sort3_field, sort3_dir,
                 column_visibility_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name,
            json.dumps(data.get('job_types') or []),
            json.dumps(data.get('statuses') or []),
            json.dumps(data.get('payment_types') or []),
            (data.get('search') or '').strip() or None,
            data.get('gross_min') or None,
            data.get('gross_max') or None,
            data.get('date_mode') or 'preset',
            data.get('date_preset') or None,
            data.get('date_from') or None,
            data.get('date_to') or None,
            _clean_sort_field(data.get('sort1_field')),
            _clean_sort_dir(data.get('sort1_dir')),
            _clean_sort_field(data.get('sort2_field')),
            _clean_sort_dir(data.get('sort2_dir')),
            _clean_sort_field(data.get('sort3_field')),
            _clean_sort_dir(data.get('sort3_dir')),
            data.get('column_visibility_id') or None,
        ))
        conn.commit()
        new_id = conn.execute(
            "SELECT id FROM job_queries WHERE name=?", (name,)).fetchone()['id']

    return jsonify({'ok': True, 'id': new_id})


@job_queries_bp.route('/job-queries/<int:query_id>', methods=['PUT'])
def update_query(query_id):
    """Update an existing saved query."""
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'Name is required'}), 400

    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM job_queries WHERE id=?", (query_id,)).fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'Not found'}), 404

        dup = conn.execute(
            "SELECT id FROM job_queries WHERE name=? AND id!=?",
            (name, query_id)).fetchone()
        if dup:
            return jsonify({'ok': False, 'error': f'A query named "{name}" already exists'}), 400

        conn.execute("""
            UPDATE job_queries
            SET name=?, job_types=?, statuses=?, payment_types=?, search=?,
                gross_min=?, gross_max=?, date_mode=?, date_preset=?,
                date_from=?, date_to=?,
                sort1_field=?, sort1_dir=?, sort2_field=?, sort2_dir=?,
                sort3_field=?, sort3_dir=?, column_visibility_id=?,
                updated_at=datetime('now')
            WHERE id=?
        """, (
            name,
            json.dumps(data.get('job_types') or []),
            json.dumps(data.get('statuses') or []),
            json.dumps(data.get('payment_types') or []),
            (data.get('search') or '').strip() or None,
            data.get('gross_min') or None,
            data.get('gross_max') or None,
            data.get('date_mode') or 'preset',
            data.get('date_preset') or None,
            data.get('date_from') or None,
            data.get('date_to') or None,
            _clean_sort_field(data.get('sort1_field')),
            _clean_sort_dir(data.get('sort1_dir')),
            _clean_sort_field(data.get('sort2_field')),
            _clean_sort_dir(data.get('sort2_dir')),
            _clean_sort_field(data.get('sort3_field')),
            _clean_sort_dir(data.get('sort3_dir')),
            data.get('column_visibility_id') or None,
            query_id,
        ))
        conn.commit()

    return jsonify({'ok': True})


@job_queries_bp.route('/job-queries/<int:query_id>', methods=['DELETE'])
def delete_query(query_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM job_queries WHERE id=?", (query_id,)).fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'Not found'}), 404
        conn.execute("DELETE FROM job_queries WHERE id=?", (query_id,))
        conn.commit()
    return jsonify({'ok': True})
