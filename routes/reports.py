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
    ('workshop', 'Workshop (PB-)'),
]


def _get_report_data(date_from, date_to, job_types, statuses):
    """
    Fetch all jobs in range, calculate financials per job.
    Returns list of row dicts sorted by scheduled_date.
    """
    if not job_types or not statuses:
        return []

    jt_ph = ','.join('?' * len(job_types))
    st_ph = ','.join('?' * len(statuses))
    params = [date_from, date_to] + list(job_types) + list(statuses)

    with get_db() as conn:
        jobs = conn.execute(f"""
            SELECT j.id, j.reference, j.job_type, j.customer_name,
                   j.scheduled_date, j.status, j.tax_inclusive,
                   j.amount_paid
            FROM jobs j
            WHERE j.scheduled_date BETWEEN ? AND ?
              AND j.job_type IN ({jt_ph})
              AND j.status IN ({st_ph})
            ORDER BY j.scheduled_date ASC, j.id ASC
        """, params).fetchall()

        rows = []
        for job in jobs:
            parts = conn.execute(
                "SELECT * FROM job_parts WHERE job_id=?",
                (job['id'],)).fetchall()
            subtotal, gst, total = calc_totals(parts, bool(job['tax_inclusive']))
            rows.append({
                'id':           job['id'],
                'reference':    job['reference'],
                'job_type':     job['job_type'],
                'customer_name': job['customer_name'],
                'scheduled_date': job['scheduled_date'],
                'status':        job['status'],
                'gross':         total,
                'gst':           gst,
                'net':           subtotal,
                'amount_paid':   float(job['amount_paid'] or 0),
            })
    return rows


def _group_by_month(rows):
    """Group rows into months, return list of (month_label, rows, subtotals)."""
    from collections import OrderedDict
    months = OrderedDict()
    for row in rows:
        d = row['scheduled_date'] or ''
        key = d[:7] if d else 'Unknown'  # YYYY-MM
        months.setdefault(key, []).append(row)

    result = []
    for key, month_rows in months.items():
        try:
            from datetime import datetime
            label = datetime.strptime(key, '%Y-%m').strftime('%B %Y')
        except Exception:
            label = key
        subtotals = {
            'gross': sum(r['gross'] for r in month_rows),
            'gst':   sum(r['gst']   for r in month_rows),
            'net':   sum(r['net']   for r in month_rows),
        }
        result.append((label, month_rows, subtotals))
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
    today      = date.today()
    first_of_month = today.replace(day=1).isoformat()
    default_to = today.isoformat()

    # Default: current month, all job types
    date_from  = request.form.get('date_from', first_of_month)
    date_to    = request.form.get('date_to',   default_to)
    job_types  = request.form.getlist('job_types') or ['booking', 'workshop']
    statuses   = request.form.getlist('statuses') or ['invoiced']

    rows    = _get_report_data(date_from, date_to, job_types, statuses)
    months  = _group_by_month(rows)
    totals  = _grand_totals(rows)

    return render_template('reports/sales.html',
                           date_from=date_from, date_to=date_to,
                           job_types=job_types, statuses=statuses,
                           STATUS_OPTIONS=STATUS_OPTIONS,
                           JOB_TYPE_OPTIONS=JOB_TYPE_OPTIONS,
                           months=months, rows=rows, totals=totals,
                           ran=bool(request.method == 'POST' or request.args.get('run')))


# ── CSV export ────────────────────────────────────────────────────────────────

@reports_bp.route('/reports/sales/csv')
def sales_csv():
    date_from = request.args.get('date_from', date.today().replace(day=1).isoformat())
    date_to   = request.args.get('date_to',   date.today().isoformat())
    job_types = request.args.getlist('job_types') or ['booking', 'workshop']
    statuses  = request.args.getlist('statuses') or ['invoiced']

    rows = _get_report_data(date_from, date_to, job_types, statuses)

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
    date_from = request.args.get('date_from', date.today().replace(day=1).isoformat())
    date_to   = request.args.get('date_to',   date.today().isoformat())
    job_types = request.args.getlist('job_types') or ['booking', 'workshop']
    statuses  = request.args.getlist('statuses') or ['invoiced']

    rows   = _get_report_data(date_from, date_to, job_types, statuses)
    months = _group_by_month(rows)
    totals = _grand_totals(rows)

    buf = _build_pdf(date_from, date_to, job_types, statuses, months, totals)
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
        st_label = ', '.join(s.replace('_',' ').title() for s in statuses)
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

    for month_label, month_rows, sub in months:
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
