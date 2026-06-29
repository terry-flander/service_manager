from flask import Blueprint, render_template, request, make_response, send_file
from models import get_db
from routes.invoice import calc_totals
from datetime import date
import csv
import io

reports_bp = Blueprint('reports', __name__)

STATUS_OPTIONS = [
    ('pending',     'Pending'),
    ('scheduled',   'Scheduled'),
    ('in_progress', 'In Progress'),
    ('complete',    'Complete'),
    ('invoiced',    'Invoiced'),
    ('paid',        'Paid'),
]

JOB_TYPE_OPTIONS = [
    ('booking',  'Booking (FB-)'),
    ('workshop', 'Workshop + Sales (PB-/CS-)'),
    ('rental',   'Rental (RB-)'),
    ('sale',     None),  # included with workshop — not shown separately
]


def _get_report_data(date_from, date_to, job_types, show_cash=False, sort_by='paid',
                      saved_query=None):
    """
    Fetch all jobs in range, calculate financials per job.
    Returns list of row dicts ordered by paid_date.

    If saved_query is provided, its statuses/payment_types/search filters
    are applied via the shared resolve_query_filters() helper, and its
    Status selection REPLACES the old hardcoded invoiced/paid restriction
    (an explicit decision — a saved query's Status filter should mean
    something on this report, not be silently overridden). If the saved
    query has no statuses selected, the invoiced/paid default still applies
    so an empty Status filter doesn't accidentally show pending/lost jobs
    in a financial report.
    """
    # sale always travels with workshop
    if 'workshop' in job_types and 'sale' not in job_types:
        job_types = list(job_types) + ['sale']
    if 'sale' in job_types and 'workshop' not in job_types:
        job_types = [t for t in job_types if t != 'sale']
    # Filter out the display-only None entry
    job_types = [t for t in job_types if t]
    if not job_types:
        return []

    jt_ph = ','.join('?' * len(job_types))
    params = [date_from, date_to, date_from, date_to] + list(job_types)

    cash_clause  = '' if show_cash else "AND (j.payment_type IS NULL OR j.payment_type != 'Cash')"
    order_clause = 'j.scheduled_date ASC' if sort_by == 'scheduled' else \
                   'coalesce(j.paid_date, j.scheduled_date) ASC'

    # Status: saved query's own selection wins if it has one, otherwise
    # default to invoiced/paid (financial report default).
    extra_clause = ''
    if saved_query and saved_query.get('statuses'):
        statuses = saved_query['statuses']
        ph = ','.join('?' * len(statuses))
        extra_clause += f" AND j.status IN ({ph})"
        params.extend(statuses)
    else:
        extra_clause += " AND j.status IN ('invoiced', 'paid')"

    if saved_query and saved_query.get('payment_types'):
        pts = saved_query['payment_types']
        ph = ','.join('?' * len(pts))
        extra_clause += f" AND j.payment_type IN ({ph})"
        params.extend(pts)

    search = (saved_query or {}).get('search', '').strip() if saved_query else ''
    if search:
        extra_clause += " AND (j.customer_name LIKE ? OR j.customer_phone LIKE ?)"
        params.extend([f'%{search}%', f'%{search}%'])

    if saved_query and saved_query.get('gross_min') not in (None, ''):
        extra_clause += " AND j.total >= ?"
        params.append(float(saved_query['gross_min']))
    if saved_query and saved_query.get('gross_max') not in (None, ''):
        extra_clause += " AND j.total <= ?"
        params.append(float(saved_query['gross_max']))

    with get_db() as conn:
        jobs = conn.execute(f"""
            SELECT j.id, j.reference, j.invoice_number, j.job_type, j.customer_name,
                   j.scheduled_date, j.paid_date, j.status,
                   j.amount_paid, j.payment_type,
                   j.subtotal, j.gst, j.total,
                   coalesce(j.paid_date, j.scheduled_date) as report_date
            FROM jobs j
            WHERE (
                (j.scheduled_date IS NOT NULL AND j.scheduled_date BETWEEN ? AND ?)
                OR
                (j.scheduled_date IS NULL AND j.paid_date BETWEEN ? AND ?)
            )
              AND j.job_type IN ({jt_ph})
              {extra_clause}
              {cash_clause}
            ORDER BY {order_clause}, j.id ASC
        """.format(order_clause=order_clause, jt_ph=jt_ph, extra_clause=extra_clause,
                   cash_clause=cash_clause), params).fetchall()

        # subtotal/gst/total are now stored directly on the jobs row —
        # no more per-row parts fetch + calc_totals() call needed here.
        rows = []
        for job in jobs:
            rows.append({
                'id':             job['id'],
                'reference':      job['reference'],
                'invoice_number': job['invoice_number'] or '',
                'job_type':       job['job_type'],
                'customer_name':  job['customer_name'],
                'paid_date':      job['paid_date'] or '',
                'scheduled_date': job['scheduled_date'] or '',
                'status':         job['status'],
                'gross':          job['total'] or 0.0,
                'gst':            job['gst'] or 0.0,
                'net':            job['subtotal'] or 0.0,
                'amount_paid':    float(job['amount_paid'] or 0),
                'payment_type':   job['payment_type'] or '',
            })

    return rows


def _group_by_month(rows, sort_by='paid'):
    """Group rows into months → days. sort_by: 'paid' or 'scheduled'."""
    from collections import OrderedDict
    from datetime import datetime
    months = OrderedDict()
    for row in rows:
        d = (row['scheduled_date'] if sort_by == 'scheduled' else
             (row['paid_date'] or row['scheduled_date'])) or ''
        month_key = d[:7] if d else 'Unknown'
        months.setdefault(month_key, OrderedDict())
        day_key = d[:10] if d else 'Unknown'
        months[month_key].setdefault(day_key, []).append(row)

    result = []
    for month_key, days in months.items():
        try:
            month_label = datetime.strptime(month_key, '%Y-%m').strftime('%B %Y')
        except Exception:
            month_label = month_key

        day_groups = []
        all_month_rows = []
        for day_key, day_rows in days.items():
            try:
                day_label = datetime.strptime(day_key, '%Y-%m-%d').strftime('%a %-d %b')
            except Exception:
                day_label = day_key
            day_sub = {
                'gross': sum(r['gross'] for r in day_rows),
                'gst':   sum(r['gst']   for r in day_rows),
                'net':   sum(r['net']   for r in day_rows),
                'count': len(day_rows),
            }
            day_groups.append((day_label, day_rows, day_sub))
            all_month_rows.extend(day_rows)

        month_sub = {
            'gross': sum(r['gross'] for r in all_month_rows),
            'gst':   sum(r['gst']   for r in all_month_rows),
            'net':   sum(r['net']   for r in all_month_rows),
        }
        result.append((month_label, day_groups, month_sub))
    return result


def _grand_totals(rows):
    return {
        'gross': sum(r['gross'] for r in rows),
        'gst':   sum(r['gst']   for r in rows),
        'net':   sum(r['net']   for r in rows),
        'count': len(rows),
    }


# ── HTML report ───────────────────────────────────────────────────────────────

@reports_bp.route('/reports/sales', methods=['GET', 'POST'])
def sales():
    import json as _json
    from flask import session as _sess
    from job_queries import query_row_to_dict, get_resolved_date_range
    user_id = _sess.get('user_id')
    today          = date.today()
    first_of_month = today.replace(day=1).isoformat()
    default_to     = today.isoformat()

    DISPLAY_PREFS_KEY = f'report_display_prefs_{user_id}'

    query_id = request.args.get('query_id', '').strip() or request.form.get('query_id', '').strip()
    saved_query = None

    with get_db() as conn:
        # ── Display preferences (Sort By / Daily subtotals) — independent
        #    of which (if any) saved query is active. A dedicated small
        #    form on the report posts just these two fields.
        if 'display_prefs' in request.form:
            show_daily = 'show_daily' in request.form
            sort_by    = request.form.get('sort_by', 'paid')
            conn.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (DISPLAY_PREFS_KEY, _json.dumps({'show_daily': show_daily, 'sort_by': sort_by})))
            conn.commit()
        else:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (DISPLAY_PREFS_KEY,)).fetchone()
            try:
                display_prefs = _json.loads(row['value']) if row else {}
            except Exception:
                display_prefs = {}
            show_daily = display_prefs.get('show_daily', False)
            sort_by    = display_prefs.get('sort_by', 'paid')

        if query_id:
            row = conn.execute(
                "SELECT * FROM job_queries WHERE id=?", (query_id,)).fetchone()
            saved_query = query_row_to_dict(row)

        if saved_query:
            date_from, date_to = get_resolved_date_range(saved_query)
            # "All Time" (or any preset with no bound) -> wide-open range,
            # not a silent fallback to "this month" which would be wrong.
            date_from = date_from or '1900-01-01'
            date_to   = date_to   or today.isoformat()
            job_types  = saved_query.get('job_types') or ['booking', 'workshop', 'sale']
            from job_queries import get_report_sort_by
            query_sort_by = get_report_sort_by(saved_query)
            if query_sort_by:
                sort_by = query_sort_by
            conn.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f'report_prefs_{user_id}', _json.dumps({'query_id': query_id})))
            conn.commit()
        elif request.method == 'POST' and 'display_prefs' not in request.form:
            date_from = request.form.get('date_from', first_of_month)
            date_to   = request.form.get('date_to',   default_to)
            job_types  = request.form.getlist('job_types') or ['booking', 'workshop', 'sale']
            # Save to settings
            prefs = {'date_from': date_from, 'date_to': date_to,
                     'job_types': job_types}
            conn.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f'report_prefs_{user_id}', _json.dumps(prefs)))
            conn.commit()
        else:
            is_clear = request.args.get('clear') == '1'
            if is_clear:
                conn.execute(
                    "DELETE FROM settings WHERE key=?", (f'report_prefs_{user_id}',))
                conn.commit()
                date_from  = first_of_month
                date_to    = default_to
                job_types  = ['booking', 'workshop', 'sale']
            else:
                # Restore saved prefs if available
                row = conn.execute(
                    "SELECT value FROM settings WHERE key=?",
                    (f'report_prefs_{user_id}',)).fetchone()
                if row:
                    try:
                        prefs = _json.loads(row['value'])
                        if prefs.get('query_id'):
                            return redirect(url_for('reports.sales', query_id=prefs['query_id'], run='1'))
                        date_from = prefs.get('date_from', first_of_month)
                        date_to   = prefs.get('date_to',   default_to)
                        job_types  = prefs.get('job_types',  ['booking', 'workshop', 'sale'])
                    except Exception:
                        date_from  = first_of_month
                        date_to    = default_to
                        job_types  = ['booking', 'workshop', 'sale']
                else:
                    date_from  = first_of_month
                    date_to    = default_to
                    job_types  = ['booking', 'workshop', 'sale']
                    sort_by    = 'paid'

    ran = bool(request.method == 'POST' or request.args.get('run'))

    # Get show_cash_payments from current user
    try:
        with get_db() as _uc:
            _u = _uc.execute('SELECT show_cash_payments FROM users WHERE id=?',
                             (user_id,)).fetchone()
            show_cash = bool(_u and _u['show_cash_payments'])
    except Exception:
        show_cash = False

    rows    = _get_report_data(date_from, date_to, job_types, show_cash, sort_by,
                               saved_query=saved_query) if ran else []
    months  = _group_by_month(rows, sort_by) if ran else {}
    totals  = _grand_totals(rows) if ran else {}

    return render_template('reports/sales.html',
                           date_from=date_from, date_to=date_to,
                           job_types=job_types,
                           show_daily=show_daily, sort_by=sort_by,
                           show_cash=show_cash,
                           JOB_TYPE_OPTIONS=JOB_TYPE_OPTIONS,
                           months=months, rows=rows, totals=totals,
                           ran=ran, query_id=query_id, saved_query=saved_query)


# ── CSV export ────────────────────────────────────────────────────────────────

@reports_bp.route('/reports/sales/csv')
def sales_csv():
    from job_queries import query_row_to_dict, get_resolved_date_range
    query_id = request.args.get('query_id', '').strip()
    saved_query = None
    if query_id:
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM job_queries WHERE id=?", (query_id,)).fetchone()
            saved_query = query_row_to_dict(row)

    if saved_query:
        date_from, date_to = get_resolved_date_range(saved_query)
        date_from = date_from or '1900-01-01'
        date_to   = date_to   or date.today().isoformat()
        job_types = saved_query.get('job_types') or ['booking', 'workshop', 'sale']
    else:
        date_from = request.args.get('date_from', date.today().replace(day=1).isoformat())
        date_to   = request.args.get('date_to',   date.today().isoformat())
        job_types = request.args.getlist('job_types') or ['booking', 'workshop']

    show_cash = False
    rows = _get_report_data(date_from, date_to, job_types, show_cash,
                            saved_query=saved_query)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['Reference', 'Type', 'Date', 'Customer',
                     'Gross (inc GST)', 'GST', 'Net (ex GST)', 'Status'])

    current_month = None
    month_gross = month_gst = month_net = 0.0

    for row in rows:
        month = (row['scheduled_date'] or '')[:7]
        if current_month is not None and month != current_month:
            # Write month subtotal
            try:
                from datetime import datetime
                ml = datetime.strptime(current_month, '%Y-%m').strftime('%B %Y')
            except Exception:
                ml = current_month
            writer.writerow(['', '', f'Subtotal {ml}', '',
                             f'{month_gross:.2f}', f'{month_gst:.2f}',
                             f'{month_net:.2f}', ''])
            writer.writerow([])
            month_gross = month_gst = month_net = 0.0

        current_month = month
        month_gross += row['gross']
        month_gst   += row['gst']
        month_net   += row['net']

        writer.writerow([
            row['reference'],
            row['job_type'].title(),
            row['scheduled_date'] or '',
            row['customer_name'],
            f"{row['gross']:.2f}",
            f"{row['gst']:.2f}",
            f"{row['net']:.2f}",
            row['status'].replace('_', ' ').title(),
        ])

    # Final month subtotal
    if current_month:
        try:
            ml = datetime.strptime(current_month, '%Y-%m').strftime('%B %Y')
        except Exception:
            ml = current_month
        writer.writerow(['', '', f'Subtotal {ml}', '',
                         f'{month_gross:.2f}', f'{month_gst:.2f}',
                         f'{month_net:.2f}', ''])
        writer.writerow([])

    # Grand total
    totals = _grand_totals(rows)
    writer.writerow(['', '', 'GRAND TOTAL', f"{totals['count']} jobs",
                     f"{totals['gross']:.2f}",
                     f"{totals['gst']:.2f}",
                     f"{totals['net']:.2f}", ''])

    fname = f"sales_report_{date_from}_to_{date_to}.csv"
    response = make_response(buf.getvalue())
    response.headers['Content-Disposition'] = f'attachment; filename={fname}'
    response.headers['Content-Type'] = 'text/csv'
    return response


# ── PDF export ────────────────────────────────────────────────────────────────

@reports_bp.route('/reports/sales/pdf')
def sales_pdf():
    from job_queries import query_row_to_dict, get_resolved_date_range
    query_id = request.args.get('query_id', '').strip()
    saved_query = None
    if query_id:
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM job_queries WHERE id=?", (query_id,)).fetchone()
            saved_query = query_row_to_dict(row)

    if saved_query:
        date_from, date_to = get_resolved_date_range(saved_query)
        date_from = date_from or '1900-01-01'
        date_to   = date_to   or date.today().isoformat()
        job_types = saved_query.get('job_types') or ['booking', 'workshop', 'sale']
    else:
        date_from = request.args.get('date_from', date.today().replace(day=1).isoformat())
        date_to   = request.args.get('date_to',   date.today().isoformat())
        job_types = request.args.getlist('job_types') or ['booking', 'workshop']

    statuses  = ['invoiced', 'paid']

    rows   = _get_report_data(date_from, date_to, job_types, show_cash=False,
                              saved_query=saved_query)
    months = _group_by_month(rows)
    totals = _grand_totals(rows)

    buf = _build_pdf(date_from, date_to, job_types, None, months, totals)
    fname = f"sales_report_{date_from}_to_{date_to}.pdf"
    return send_file(buf, mimetype='application/pdf',
                     as_attachment=True, download_name=fname)


def _build_pdf(date_from, date_to, job_types, statuses, months, totals):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas as pdfcanvas
    import os

    PAGE_W, PAGE_H = landscape(A4)
    M  = 15 * mm
    CW = PAGE_W - 2 * M

    # Column widths and X positions
    cols = {
        'date':   (M,              28*mm),
        'ref':    (M + 28*mm,      22*mm),
        'type':   (M + 50*mm,      18*mm),
        'cust':   (M + 68*mm,      65*mm),
        'gross':  (M + 133*mm,     30*mm),
        'gst':    (M + 163*mm,     25*mm),
        'net':    (M + 188*mm,     30*mm),
        'status': (M + 218*mm,     CW - 218*mm),
    }

    def right(key, y, text, bold=False, size=8):
        x, w = cols[key]
        c.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
        c.drawRightString(x + w, y, str(text))

    def left(key, y, text, bold=False, size=8):
        x, _ = cols[key]
        c.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
        c.drawString(x, y, str(text))

    def hline(y, x1=M, x2=PAGE_W-M, width=0.4, col=colors.black):
        c.setStrokeColor(col)
        c.setLineWidth(width)
        c.line(x1, y, x2, y)
        c.setStrokeColor(colors.black)

    def fmt(v): return f"${v:,.2f}"

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=landscape(A4))

    def draw_header(y):
        # Logo
        logo = os.path.join(os.path.dirname(__file__), '..', 'static', 'img', 'branding.png')
        if os.path.exists(logo):
            c.drawImage(logo, M, y - 10*mm, width=35*mm, height=8*mm,
                        preserveAspectRatio=True, mask='auto')

        c.setFont('Helvetica-Bold', 14)
        c.drawString(M + 38*mm, y - 4*mm, 'Sales Report')
        c.setFont('Helvetica', 8)
        jt_label = ' & '.join(j.title() for j in job_types)
        st_label = 'Invoiced/Paid'
        c.drawString(M + 38*mm, y - 9*mm,
                     f"{date_from} to {date_to}   |   {jt_label}   |   {st_label}   |   {totals['count']} jobs")
        y -= 14*mm

        # Column headers
        hline(y + 4*mm, width=0.8)
        c.setFont('Helvetica-Bold', 7.5)
        c.setFillColor(colors.HexColor('#666666'))
        for label, key in [('Date','date'),('Reference','ref'),('Type','type'),
                            ('Customer','cust')]:
            left(key, y, label, bold=True, size=7.5)
        for label, key in [('Gross (inc GST)','gross'),('GST','gst'),
                            ('Net (ex GST)','net'),('Status','status')]:
            right(key, y, label, bold=True, size=7.5)
        c.setFillColor(colors.black)
        hline(y - 2*mm, width=0.4)
        return y - 6*mm

    ROW_H   = 5.5*mm
    MIN_Y   = M + 12*mm

    y = PAGE_H - M
    y = draw_header(y)

    page_num = 1

    for month_label, day_groups, sub in months:
        # Flatten the day-groups structure into a plain list of row dicts,
        # in date order — day_groups is [(day_label, day_rows, day_sub), ...]
        month_rows = []
        for _day_label, day_rows, _day_sub in day_groups:
            month_rows.extend(day_rows)

        # Month header band
        if y < MIN_Y + ROW_H * 3:
            c.showPage(); page_num += 1
            y = PAGE_H - M; y = draw_header(y)

        c.setFillColor(colors.HexColor('#1e293b'))
        c.rect(M, y - ROW_H * 0.7, CW, ROW_H, fill=1, stroke=0)
        c.setFillColor(colors.HexColor('#e2e8f0'))
        c.setFont('Helvetica-Bold', 8)
        c.drawString(M + 2*mm, y - ROW_H * 0.5 + 1, month_label)
        c.setFillColor(colors.black)
        y -= ROW_H

        for row in month_rows:
            if y < MIN_Y:
                c.showPage(); page_num += 1
                y = PAGE_H - M; y = draw_header(y)

            # Alternate row shading
            if month_rows.index(row) % 2 == 1:
                c.setFillColor(colors.HexColor('#f8fafc'))
                c.rect(M, y - ROW_H * 0.7, CW, ROW_H, fill=1, stroke=0)
                c.setFillColor(colors.black)

            # Truncate customer name
            cname = row['customer_name']
            max_chars = 38
            if len(cname) > max_chars:
                cname = cname[:max_chars-1] + '…'

            left('date',   y, row['scheduled_date'] or '—')
            left('ref',    y, row['reference'])
            left('type',   y, row['job_type'].title())
            left('cust',   y, cname)
            right('gross', y, fmt(row['gross']))
            right('gst',   y, fmt(row['gst']))
            right('net',   y, fmt(row['net']))
            right('status',y, row['status'].replace('_',' ').title())
            y -= ROW_H

        # Month subtotal
        hline(y + ROW_H * 0.8, width=0.3,
              col=colors.HexColor('#94a3b8'))
        c.setFont('Helvetica-Bold', 8)
        c.drawRightString(cols['date'][0] + cols['date'][1] + cols['ref'][1] +
                          cols['type'][1] + cols['cust'][1], y,
                          f'Subtotal {month_label}')
        right('gross', y, fmt(sub['gross']), bold=True)
        right('gst',   y, fmt(sub['gst']),   bold=True)
        right('net',   y, fmt(sub['net']),   bold=True)
        y -= ROW_H * 1.5

    # Grand total
    if y < MIN_Y + ROW_H * 2:
        c.showPage(); page_num += 1
        y = PAGE_H - M; y = draw_header(y)

    hline(y + ROW_H * 0.8, width=1.0)
    c.setFont('Helvetica-Bold', 9)
    c.drawString(M, y, f"GRAND TOTAL  ({totals['count']} jobs)")
    right('gross', y, fmt(totals['gross']), bold=True, size=9)
    right('gst',   y, fmt(totals['gst']),   bold=True, size=9)
    right('net',   y, fmt(totals['net']),   bold=True, size=9)
    hline(y - ROW_H * 0.6, width=0.5)

    # Page numbers
    c.save()
    buf.seek(0)
    return buf


@reports_bp.route('/reports/parts')
def parts_usage():
    from models import get_db
    import csv as _csv, io as _io
    from flask import request as _req, make_response as _mr

    include_adhoc = _req.args.get('include_adhoc', '0') == '1'
    export_csv    = _req.args.get('export', '') == 'csv'

    with get_db() as conn:
        catalogue_rows = conn.execute("""
            SELECT p.id, p.name, p.part_number, p.unit_cost, p.active,
                   COUNT(jp.id)       AS usage_count,
                   SUM(jp.quantity)   AS total_qty,
                   MIN(jp.unit_cost)  AS min_cost,
                   MAX(jp.unit_cost)  AS max_cost,
                   0                  AS is_adhoc
            FROM parts p
            LEFT JOIN job_parts jp ON jp.part_id = p.id
            GROUP BY p.id
            ORDER BY p.name COLLATE NOCASE
        """).fetchall()

        adhoc_rows = []
        if include_adhoc:
            adhoc_rows = conn.execute("""
                SELECT NULL              AS id,
                       jp.description   AS name,
                       jp.part_number   AS part_number,
                       NULL             AS unit_cost,
                       1                AS active,
                       COUNT(jp.id)     AS usage_count,
                       SUM(jp.quantity) AS total_qty,
                       MIN(jp.unit_cost) AS min_cost,
                       MAX(jp.unit_cost) AS max_cost,
                       1                AS is_adhoc
                FROM job_parts jp
                WHERE jp.part_id IS NULL
                GROUP BY LOWER(jp.description)
                ORDER BY jp.description COLLATE NOCASE
            """).fetchall()

    rows = [dict(r) for r in catalogue_rows] + [dict(r) for r in adhoc_rows]
    if include_adhoc:
        rows.sort(key=lambda r: (r['name'] or '').lower())

    if export_csv:
        out = _io.StringIO()
        w   = _csv.writer(out)
        w.writerow(['Name','Part Number','List Price','Active',
                    'Jobs Used','Total Qty','Min Charged','Max Charged','Ad-hoc'])
        for r in rows:
            w.writerow([
                r['name'], r['part_number'] or '',
                f"{r['unit_cost']:.2f}" if r['unit_cost'] is not None else '',
                'Yes' if r['active'] else 'No',
                r['usage_count'] or 0,
                f"{r['total_qty']:.2f}" if r['total_qty'] else '',
                f"{r['min_cost']:.2f}" if r['min_cost'] is not None else '',
                f"{r['max_cost']:.2f}" if r['max_cost'] is not None else '',
                'Yes' if r['is_adhoc'] else 'No',
            ])
        resp = _mr(out.getvalue())
        resp.headers['Content-Disposition'] = 'attachment; filename=parts_usage.csv'
        resp.headers['Content-Type'] = 'text/csv'
        return resp

    return render_template('reports/parts_usage.html',
                           rows=rows, include_adhoc=include_adhoc)


@reports_bp.route('/reports/unreconciled-eftpos')
def unreconciled_eftpos():
    from models import get_db
    import json as _json
    from flask import session as _sess
    user_id   = _sess.get('user_id')
    PREFS_KEY = f'unrecon_eftpos_{user_id}'

    if 'submitted' in request.args:
        date_from = request.args.get('date_from', '')
        date_to   = request.args.get('date_to',   '')
        with get_db() as conn:
            conn.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (PREFS_KEY, _json.dumps({'date_from': date_from, 'date_to': date_to})))
            conn.commit()
    else:
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (PREFS_KEY,)).fetchone()
        if row:
            try:
                prefs = _json.loads(row['value'])
                date_from = prefs.get('date_from', '')
                date_to   = prefs.get('date_to',   '')
            except Exception:
                date_from, date_to = '', ''
        else:
            date_from, date_to = '', ''

    with get_db() as conn:
        where  = ["j.status = 'paid'",
                  "j.payment_type IN ('EFTPOS','VISA','MASTERCARD','AMEX')",
                  "j.reconciled_eftpos IS NULL"]
        params = []
        if date_from:
            where.append("coalesce(j.paid_date, j.scheduled_date) >= ?")
            params.append(date_from)
        if date_to:
            where.append("coalesce(j.paid_date, j.scheduled_date) <= ?")
            params.append(date_to)

        jobs = conn.execute(f"""
            SELECT j.id, j.reference, j.job_type, j.customer_name,
                   j.scheduled_date, j.paid_date, j.amount_paid,
                   j.payment_type, j.total
            FROM jobs j
            WHERE {' AND '.join(where)}
            ORDER BY coalesce(j.paid_date, j.scheduled_date) ASC, j.id ASC
        """, params).fetchall()

        # Gross total per job (for display) — read directly, no
        # per-row parts fetch + calc_totals() needed any more.
        rows = []
        for job in jobs:
            rows.append({'job': dict(job), 'total': job['total'] or 0.0})

    return render_template('reports/unreconciled_eftpos.html',
                           rows=rows, date_from=date_from, date_to=date_to)


@reports_bp.route('/reports/unreconciled-eftpos/transactions')
def unreconciled_eftpos_transactions():
    """AJAX: return unreconciled EFTPOS transactions near a job's paid date."""
    from models import get_db
    from flask import jsonify
    job_id = request.args.get('job_id', type=int)
    if not job_id:
        return jsonify([])
    with get_db() as conn:
        job = conn.execute(
            "SELECT paid_date, amount_paid FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not job:
            return jsonify([])
        paid_date  = job['paid_date'] or ''
        amount     = float(job['amount_paid'] or 0)
        # All unmatched transactions, sorted by match quality then date proximity
        rows = conn.execute("""
            SELECT id, reference_number, method, transaction_date,
                   amount, total_amount, surcharge, terminal_id
            FROM eftpos_transactions
            WHERE job_id IS NULL
              AND reconciled_at IS NULL
            ORDER BY
              CASE
                WHEN transaction_date = ? AND ABS(amount - ?) < 0.01 THEN 0
                WHEN transaction_date = ?                             THEN 1
                WHEN ABS(amount - ?) < 0.01                          THEN 2
                ELSE 3
              END,
              ABS(julianday(transaction_date) - julianday(?)) ASC,
              transaction_date ASC
            LIMIT 30
        """, (paid_date, amount, paid_date, amount, paid_date or 'now')
        ).fetchall()
    return jsonify([dict(r) for r in rows])
