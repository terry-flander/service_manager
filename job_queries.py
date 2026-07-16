"""
job_queries.py — Saved, named job-filter queries shared by the Jobs List
and Sales Report pages.

A "job query" is a named, reusable set of filter criteria (job types,
statuses, payment types, customer search text, gross min/max, and a
date range — either a relative preset or a custom From/To). Date
presets are always resolved relative to *today* at the moment a query
is used, never frozen at save time, so "This Month" always means the
actual current month.
"""
import json
from datetime import date, timedelta
import calendar as _calendar


# ── Date preset resolution ──────────────────────────────────────────────────

def _fiscal_year_label(end_year):
    """AU fiscal year ending 30 June of `end_year` is labelled FY{end_year}."""
    return f"FY{end_year}"


def get_date_presets():
    """Return the list of (value, label) preset options, with fiscal year
    labels computed for the actual current/previous fiscal year."""
    today = date.today()
    # AU fiscal year: 1 July -> 30 June. If we're on/after 1 July, the
    # current fiscal year ends next calendar year; otherwise it ends
    # this calendar year.
    current_fy_end_year = today.year + 1 if today.month >= 7 else today.year
    previous_fy_end_year = current_fy_end_year - 1

    return [
        ('this_month',     'This Month'),
        ('last_month',     'Last Month'),
        ('this_quarter',   'This Quarter'),
        ('this_year',      'This Year'),
        ('last_year',      'Last Year'),
        ('last_7_days',    'Last 7 Days'),
        ('last_30_days',   'Last 30 Days'),
        ('current_fy',     _fiscal_year_label(current_fy_end_year)),
        ('previous_fy',    _fiscal_year_label(previous_fy_end_year)),
        ('all_time',       'All Time'),
        ('custom',         'Custom'),
    ]


def resolve_date_preset(preset, custom_from=None, custom_to=None):
    """Resolve a preset name to a concrete (date_from, date_to) pair of
    ISO date strings. Returns (None, None) for 'all_time' (no constraint).
    For 'custom', just echoes back the given custom_from/custom_to.
    """
    today = date.today()

    if preset == 'custom':
        return custom_from or None, custom_to or None

    if preset == 'all_time' or not preset:
        return None, None

    if preset == 'this_month':
        start = today.replace(day=1)
        return start.isoformat(), today.isoformat()

    if preset == 'last_month':
        first_of_this_month = today.replace(day=1)
        last_month_end = first_of_this_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return last_month_start.isoformat(), last_month_end.isoformat()

    if preset == 'this_quarter':
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        start = today.replace(month=q_start_month, day=1)
        return start.isoformat(), today.isoformat()

    if preset == 'this_year':
        start = today.replace(month=1, day=1)
        return start.isoformat(), today.isoformat()

    if preset == 'last_year':
        start = date(today.year - 1, 1, 1)
        end = date(today.year - 1, 12, 31)
        return start.isoformat(), end.isoformat()

    if preset == 'last_7_days':
        start = today - timedelta(days=6)  # inclusive of today = 7 days
        return start.isoformat(), today.isoformat()

    if preset == 'last_30_days':
        start = today - timedelta(days=29)
        return start.isoformat(), today.isoformat()

    if preset == 'current_fy':
        fy_start_year = today.year if today.month >= 7 else today.year - 1
        start = date(fy_start_year, 7, 1)
        return start.isoformat(), today.isoformat()

    if preset == 'previous_fy':
        fy_start_year = (today.year if today.month >= 7 else today.year - 1) - 1
        start = date(fy_start_year, 7, 1)
        end = date(fy_start_year + 1, 6, 30)
        return start.isoformat(), end.isoformat()

    # Unknown preset — no constraint rather than erroring
    return None, None


# ── Query (de)serialisation helpers ─────────────────────────────────────────

def _loads_list(raw):
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except Exception:
        return []


def query_row_to_dict(row):
    """Convert a job_queries DB row into a plain dict with lists decoded,
    ready for rendering in the builder dialog or passing to resolve_query_filters."""
    if row is None:
        return None
    keys = row.keys()
    def _safe(col, default=None):
        return row[col] if col in keys else default
    return {
        'id':            row['id'],
        'name':          row['name'],
        'job_types':     _loads_list(row['job_types']),
        'statuses':      _loads_list(row['statuses']),
        'payment_types': _loads_list(row['payment_types']),
        'search':        row['search'] or '',
        'gross_min':     row['gross_min'],
        'gross_max':     row['gross_max'],
        'date_mode':     row['date_mode'] or 'preset',
        'date_preset':   row['date_preset'] or '',
        'date_from':     row['date_from'] or '',
        'date_to':       row['date_to'] or '',
        'date_field':    _safe('date_field') or 'scheduled',
        'sort1_field':   _safe('sort1_field') or '',
        'sort1_dir':     _safe('sort1_dir') or 'asc',
        'sort2_field':   _safe('sort2_field') or '',
        'sort2_dir':     _safe('sort2_dir') or 'asc',
        'sort3_field':   _safe('sort3_field') or '',
        'sort3_dir':     _safe('sort3_dir') or 'asc',
        'column_visibility_id': _safe('column_visibility_id'),
    }


# ── SQL building — the single source of truth used by both Jobs List and
#    Sales Report ──────────────────────────────────────────────────────────

def resolve_query_filters(query, table_alias='j', date_column='scheduled_date'):
    """Build a WHERE-clause fragment (no leading 'WHERE'/'AND') and its
    params list from a query dict (as returned by query_row_to_dict, or
    an equivalent dict built directly from form data).

    `date_column` lets callers point the date range at whichever column
    makes sense for that report (Jobs List uses scheduled_date; Sales
    Report uses coalesce(paid_date, scheduled_date) — pass the raw SQL
    expression as date_column if it's not a simple column name).

    Returns (sql_fragment, params) — sql_fragment is '' if the query has
    no constraints at all (caller should skip adding 'AND' in that case).
    """
    if not query:
        return '', []

    clauses = []
    params = []
    a = table_alias

    job_types = query.get('job_types') or []
    if job_types:
        placeholders = ','.join('?' * len(job_types))
        clauses.append(f"{a}.job_type IN ({placeholders})")
        params.extend(job_types)

    statuses = query.get('statuses') or []
    if statuses:
        placeholders = ','.join('?' * len(statuses))
        clauses.append(f"{a}.status IN ({placeholders})")
        params.extend(statuses)

    payment_types = query.get('payment_types') or []
    if payment_types:
        placeholders = ','.join('?' * len(payment_types))
        clauses.append(f"{a}.payment_type IN ({placeholders})")
        params.extend(payment_types)

    search = (query.get('search') or '').strip()
    if search:
        clauses.append(f"({a}.customer_name LIKE ? OR {a}.customer_phone LIKE ?)")
        params.extend([f'%{search}%', f'%{search}%'])

    gross_min = query.get('gross_min')
    if gross_min not in (None, ''):
        clauses.append(f"{a}.total >= ?")
        params.append(float(gross_min))

    gross_max = query.get('gross_max')
    if gross_max not in (None, ''):
        clauses.append(f"{a}.total <= ?")
        params.append(float(gross_max))

    date_mode = query.get('date_mode', 'preset')
    if date_mode == 'custom':
        date_from = query.get('date_from') or None
        date_to   = query.get('date_to') or None
    else:
        date_from, date_to = resolve_date_preset(query.get('date_preset'))

    # date_field from the query overrides the caller-supplied date_column default.
    # 'scheduled' -> j.scheduled_date, 'paid' -> j.paid_date.
    # If date_field is missing/unknown, fall back to the caller's default.
    date_field = (query.get('date_field') or '').strip()
    if date_field == 'paid':
        resolved_date_col = f"{table_alias}.paid_date"
    elif date_field == 'scheduled':
        resolved_date_col = f"{table_alias}.scheduled_date"
    else:
        resolved_date_col = date_column  # caller-supplied fallback

    if date_from:
        clauses.append(f"{resolved_date_col} >= ?")
        params.append(date_from)
    if date_to:
        clauses.append(f"{resolved_date_col} <= ?")
        params.append(date_to)

    return ' AND '.join(clauses), params


def get_resolved_date_range(query):
    """Convenience: return the actual (date_from, date_to) a query
    resolves to right now, for display purposes (e.g. showing the
    concrete dates a preset currently means)."""
    if not query:
        return None, None
    if query.get('date_mode') == 'custom':
        return query.get('date_from') or None, query.get('date_to') or None
    return resolve_date_preset(query.get('date_preset'))


# ── Sorting — up to 3 fields, each with its own direction ───────────────────

# Allow-list of sortable fields and their real SQL column. "gross" has no
# real column — it's a calculated value (via calc_totals()), so whenever
# it appears in any of the 3 slots the ENTIRE sort is done in Python
# (using a composite key across all 3 requested fields) rather than
# splitting the work between SQL and Python, which would not correctly
# honour multi-column precedence when gross isn't the first key.
SORTABLE_SQL_COLUMNS = {
    'reference':      'j.reference',
    'customer_name':  'j.customer_name',
    'customer_phone': 'j.customer_phone',
    'job_type':       'j.job_type',
    'status':         'j.status',
    'payment_type':   'j.payment_type',
    'scheduled_date': 'j.scheduled_date',
    'paid_date':      'j.paid_date',
    'invoice_number': 'j.invoice_number',
    'amount_paid':    'j.amount_paid',
    'total':          'j.total',
}


def get_sort_fields():
    """(value, label) pairs for the sort dropdowns in the query builder."""
    return [
        ('reference',      'Reference'),
        ('customer_name',  'Customer Name'),
        ('customer_phone', 'Customer Phone'),
        ('job_type',       'Job Type'),
        ('status',         'Status'),
        ('payment_type',   'Payment Type'),
        ('scheduled_date', 'Scheduled Date'),
        ('paid_date',      'Paid Date'),
        ('invoice_number', 'Invoice Number'),
        ('amount_paid',    'Amount Paid'),
        ('total',          'Total'),
        ('gross',          'Gross'),
    ]


def get_sort_spec(query):
    """Return the query's sort fields/directions as a list of
    (field, direction) tuples, e.g. [('status','asc'), ('gross','desc')].
    Empty list if the query has no sort fields set."""
    if not query:
        return []
    spec = []
    for i in (1, 2, 3):
        field = (query.get(f'sort{i}_field') or '').strip()
        if not field:
            continue
        direction = (query.get(f'sort{i}_dir') or 'asc').strip().lower()
        direction = 'desc' if direction == 'desc' else 'asc'
        spec.append((field, direction))
    return spec


def resolve_sort_clause(query, table_alias='j'):
    """Build an ORDER BY fragment (no leading 'ORDER BY') from a query's
    sort spec, validated against the allow-list so nothing user-supplied
    is ever interpolated directly as a column name.

    Returns (sql_fragment, needs_python_sort):
      - If 'gross' is NOT among the requested sort fields, sql_fragment
        is a complete, ready-to-use ORDER BY fragment and
        needs_python_sort is False — the caller can skip any Python-side
        sorting entirely.
      - If 'gross' IS among the requested fields (in any of the 3 slots),
        sql_fragment is '' and needs_python_sort is True — the caller
        must sort the fully-fetched, fully-computed rows in Python using
        apply_python_sort() below, since gross has no SQL column and
        splitting the sort across SQL+Python can't correctly honour
        multi-column precedence unless gross happens to be the only key.
    """
    spec = get_sort_spec(query)
    if not spec:
        return '', False

    if any(field == 'gross' for field, _ in spec):
        return '', True

    clauses = []
    for field, direction in spec:
        col = SORTABLE_SQL_COLUMNS.get(field)
        if not col:
            continue
        clauses.append(f"{col} {'DESC' if direction == 'desc' else 'ASC'}")
    return ', '.join(clauses), False


def apply_python_sort(items, query, gross_key=lambda item: item[1]):
    """Sort a list of items in Python using the query's full sort spec
    (all 3 fields together, correct multi-column precedence), for the
    case where 'gross' is one of the requested fields.

    `items` — list of whatever the caller has (e.g. (job_row, total)
    tuples for Jobs List, or plain dicts for Sales Report).
    `gross_key` — function extracting the gross value from one item;
    defaults to item[1] (matching Jobs List's (job, total) tuple shape).
    Callers with a different item shape (e.g. dicts with a 'gross' key)
    should pass gross_key=lambda item: item['gross'].

    Other fields are read via item[0][field] if items are (row, total)
    tuples, or item[field] if items are dict-like — detected by trying
    the dict-like path first via duck typing.
    """
    spec = get_sort_spec(query)
    if not spec:
        return items

    def _field_value(item, field):
        if field == 'gross':
            return gross_key(item) or 0
        # (row, total) tuple shape — Jobs List
        if isinstance(item, tuple):
            row = item[0]
            try:
                return row[field] or ''
            except Exception:
                return ''
        # Plain dict shape — Sales Report
        return item.get(field, '') or ''

    # Python's sort is stable but only supports one direction per call,
    # so apply the LEAST significant key first, most significant last —
    # each pass re-sorts (stably) within groups already ordered by the
    # more significant keys that follow.
    for field, direction in reversed(spec):
        reverse = (direction == 'desc')
        items = sorted(items, key=lambda it: _field_value(it, field), reverse=reverse)
    return items


def get_report_sort_by(query):
    """Sales Report only understands Sort By: Date Paid vs Scheduled —
    it groups rows by month/day rather than offering a full multi-column
    sort. If a saved query has sort fields set, this looks for the first
    one that's either 'paid_date' or 'scheduled_date' and returns 'paid'
    or 'scheduled' accordingly (first match wins if both appear). Returns
    None if the query has no sort fields, or none of them are date
    fields — callers should fall back to their own page-level default
    in that case (the existing per-user display preference toggle)."""
    spec = get_sort_spec(query)
    for field, _direction in spec:
        if field == 'paid_date':
            return 'paid'
        if field == 'scheduled_date':
            return 'scheduled'
    return None


# ── Column visibility — Jobs List only (Sales Report deliberately kept
#    fixed, since as a financial report its column set shouldn't vary) ──────

# Single canonical catalog of every column Jobs List can show. Each
# entry: label (header text), align ('left'/'right').
COLUMN_CATALOG = {
    'scheduled':  {'label': 'Scheduled',     'align': 'left'},
    'paid':       {'label': 'Date Paid',     'align': 'left'},
    'ref':        {'label': 'Reference',     'align': 'left'},
    'invoice':    {'label': 'Invoice #',     'align': 'left'},
    'type':       {'label': 'Type',          'align': 'left'},
    'customer':   {'label': 'Customer',      'align': 'left'},
    'phone':      {'label': 'Phone',         'align': 'left'},
    'bike_desc':  {'label': 'Bike Description', 'align': 'left'},
    'gross':      {'label': 'Gross',         'align': 'right'},
    'payment':    {'label': 'Payment',       'align': 'left'},
    'amount':     {'label': 'Amount Paid',   'align': 'right'},
    'reconciled': {'label': 'Reconciled',    'align': 'center'},
    'status':     {'label': 'Status',        'align': 'left'},
}

# Defaults — exactly match what's hardcoded in the template, so a query
# with no Column Visibility selection renders identically to the
# pre-existing behaviour ("if none selected, the same fields as
# currently displayed will appear").
DEFAULT_COLUMN_VISIBILITY = {
    'desktop':   ['scheduled', 'paid', 'ref', 'invoice', 'type', 'customer',
                  'gross', 'payment', 'amount', 'status'],
    'landscape': ['scheduled', 'paid', 'ref', 'invoice', 'type', 'customer',
                  'gross', 'payment', 'amount', 'status'],
    'portrait':  ['scheduled', 'ref', 'type', 'customer', 'status'],
}


def get_column_catalog():
    """Return the column catalog as a list of (key, label, align)
    tuples for easy iteration in templates/JS."""
    return [(k, v['label'], v['align']) for k, v in COLUMN_CATALOG.items()]


def get_default_columns(layout='desktop'):
    """The hardcoded fallback column list for a layout, used when no
    Column Visibility set is selected at all."""
    return list(DEFAULT_COLUMN_VISIBILITY.get(layout, DEFAULT_COLUMN_VISIBILITY['desktop']))


def resolve_columns(visibility_set, layout='desktop'):
    """Resolve the column list to actually render, given a
    column_visibility_sets row (as a dict, see
    column_visibility_row_to_dict) or None.

    Falls back to the hardcoded default for `layout` if visibility_set
    is None, or if it has no value stored for the requested layout.

    Any column key in the resolved list that isn't in COLUMN_CATALOG is
    silently dropped, so a stale/edited catalog never breaks an
    existing saved set.
    """
    if visibility_set:
        cols = visibility_set.get(layout)
        if cols:
            return [c for c in cols if c in COLUMN_CATALOG]
    return [c for c in get_default_columns(layout) if c in COLUMN_CATALOG]


def column_visibility_row_to_dict(row):
    """Convert a column_visibility_sets DB row into a plain dict with
    the JSON arrays decoded. (The 'page' column is vestigial — Column
    Visibility only ever applies to Jobs List; kept in the schema only
    to avoid a destructive column drop on existing data.)"""
    if row is None:
        return None
    return {
        'id':        row['id'],
        'name':      row['name'],
        'desktop':   _loads_list(row['desktop']),
        'landscape': _loads_list(row['landscape']),
        'portrait':  _loads_list(row['portrait']),
    }




def get_query_visibility_set(conn, query):
    """Fetch the column_visibility_sets row linked to a query dict (as
    returned by query_row_to_dict), decoded to a dict via
    column_visibility_row_to_dict. Returns None if the query has no
    link, or the linked set no longer exists (e.g. deleted)."""
    if not query or not query.get('column_visibility_id'):
        return None
    row = conn.execute(
        "SELECT * FROM column_visibility_sets WHERE id=?",
        (query['column_visibility_id'],)).fetchone()
    return column_visibility_row_to_dict(row)




