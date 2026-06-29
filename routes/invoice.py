from flask import Blueprint, render_template, make_response, send_file, url_for
from models import get_db
from datetime import date, timedelta
import csv, io

invoice_bp = Blueprint('invoice', __name__)


def calc_totals(job_parts, tax_inclusive):
    """
    tax_inclusive=True  → prices already include GST. GST = total / 11
    tax_inclusive=False → prices are ex-GST. GST = subtotal * 0.10
    tax_inclusive=2     → GST Exempt: treat like inclusive for pricing but gst=0
    """
    line_total = sum(jp['quantity'] * jp['unit_cost'] for jp in job_parts)
    if tax_inclusive == 2:          # GST Exempt — raw total, no GST
        return round(line_total, 2), 0.0, round(line_total, 2)
    if tax_inclusive:
        total    = line_total
        gst      = round(total / 11, 2)
        subtotal = round(total - gst, 2)
    else:
        subtotal = line_total
        gst      = round(subtotal * 0.10, 2)
        total    = round(subtotal + gst, 2)
    return subtotal, gst, total


@invoice_bp.route('/jobs/<int:job_id>/invoice')
def view_invoice(job_id):
    with get_db() as conn:
        job = conn.execute("""
            SELECT j.*, r.name as region_name
            FROM jobs j JOIN regions r ON j.region_id=r.id
            WHERE j.id=?
        """, (job_id,)).fetchone()
        job_parts = conn.execute(
            "SELECT * FROM job_parts WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    if not job:
        return "Job not found", 404

    tax_raw = job['tax_inclusive'] or 0
    gst_exempt = (tax_raw == 2)
    subtotal, gst, total = job['subtotal'] or 0.0, job['gst'] or 0.0, job['total'] or 0.0
    tax_inclusive = bool(tax_raw) and not gst_exempt

    today    = date.today()
    due_date = today + timedelta(days=30)

    return render_template('invoice/view.html',
                           job=job, job_parts=job_parts,
                           tax_inclusive=tax_inclusive,
                           gst_exempt=gst_exempt,
                           subtotal=subtotal, gst=gst, total=total,
                           today=today, due_date=due_date)


@invoice_bp.route('/jobs/<int:job_id>/invoice/xero-csv')
def xero_csv(job_id):
    import re as _re
    with get_db() as conn:
        job = conn.execute("""
            SELECT j.*, r.name as region_name
            FROM jobs j JOIN regions r ON j.region_id=r.id
            WHERE j.id=?
        """, (job_id,)).fetchone()
        job_parts = conn.execute(
            "SELECT * FROM job_parts WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    if not job:
        return "Job not found", 404

    tax_raw   = job['tax_inclusive'] or 0
    gst_exempt = (tax_raw == 2)
    if (job['payment_type'] or '').lower() == 'cash' or gst_exempt:
        tax_type             = 'GST Free Income'
        amounts_are_inclusive = 'false'
    elif tax_raw:
        tax_type             = 'GST on Income'
        amounts_are_inclusive = 'true'
    else:
        tax_type             = 'GST on Income'
        amounts_are_inclusive = 'false'

    invoice_num = job['invoice_number'] or f"INV-{job['reference']}"
    due_date    = (date.today() + timedelta(days=7)).strftime('%d/%m/%Y')

    # Address fields
    address   = job['address'] or ''
    suburb    = job['suburb']  or ''
    # Extract 4-digit postcode from address if present
    pc_match  = _re.search(r'\b(\d{4})\b', address)
    postcode  = pc_match.group(1) if pc_match else ''

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'ContactName', 'EmailAddress', 'POAddressLine1',
        'POCity', 'PORegion', 'POPostalCode',
        'DueDate', 'InventoryItemCode', 'Description',
        'Quantity', 'UnitAmount', 'Discount',
        'AccountCode', 'TaxType', 'TaxAmount',
        'TrackingName1', 'TrackingOption1', 'Currency',
        'InvoiceNumber', 'Reference', 'AmountsAreInclusive',
    ])

    # Fields repeated on every row per Xero multi-line invoice spec
    contact_name  = job['customer_name']    or ''
    email_addr    = job['customer_email']   or ''
    po_addr       = address
    po_city       = suburb
    po_region     = 'Victoria'
    po_postcode   = postcode

    lines = job_parts if job_parts else [None]
    for i, jp in enumerate(lines):
        first = (i == 0)
        if jp:
            writer.writerow([
                contact_name, email_addr, po_addr,
                po_city, po_region, po_postcode,
                due_date if first else '',
                jp['part_number'] or '',
                jp['description'],
                jp['quantity'],
                f"{jp['unit_cost']:.2f}",
                '', '240', tax_type, '',
                '', '', 'AUD',
                invoice_num,
                job['reference'] if first else '',
                amounts_are_inclusive if first else '',
            ])
        else:
            writer.writerow([
                contact_name, email_addr, po_addr,
                po_city, po_region, po_postcode,
                due_date, '',
                job['description'] or 'Service call',
                1, '0.00', '',
                '240', tax_type, '',
                '', '', 'AUD',
                invoice_num, job['reference'], amounts_are_inclusive,
            ])

    with get_db() as conn:
        conn.execute("UPDATE jobs SET status='invoiced' WHERE id=?", (job_id,))
        conn.commit()

    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = f'attachment; filename={invoice_num}.csv'
    response.headers['Content-Type'] = 'text/csv'
    return response

    with get_db() as conn:
        conn.execute("UPDATE jobs SET status='invoiced' WHERE id=?", (job_id,))
        conn.commit()

    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = f'attachment; filename={invoice_num}.csv'
    response.headers['Content-Type'] = 'text/csv'
    return response


@invoice_bp.route('/jobs/<int:job_id>/invoice/pdf')
def pdf_invoice(job_id):
    """HTML wrapper with close-tab button — embeds the actual PDF via iframe."""
    with get_db() as conn:
        job = conn.execute("SELECT reference FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        return "Job not found", 404
    return render_template('invoice/pdf_view.html',
                           page_title=f"Invoice — INV-{job['reference']}",
                           pdf_url=url_for('invoice.pdf_invoice_file', job_id=job_id))


@invoice_bp.route('/jobs/<int:job_id>/invoice/pdf/file')
def pdf_invoice_file(job_id):
    """Serves the raw PDF bytes for the invoice."""
    with get_db() as conn:
        job = conn.execute("""
            SELECT j.*, r.name as region_name
            FROM jobs j JOIN regions r ON j.region_id=r.id
            WHERE j.id=?
        """, (job_id,)).fetchone()
        job_parts = conn.execute(
            "SELECT * FROM job_parts WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    if not job:
        return "Job not found", 404

    tax_raw = job['tax_inclusive'] or 0
    gst_exempt = (tax_raw == 2)
    subtotal, gst, total = job['subtotal'] or 0.0, job['gst'] or 0.0, job['total'] or 0.0
    tax_inclusive = bool(tax_raw) and not gst_exempt

    from invoice_pdf import generate_invoice_pdf
    buf = generate_invoice_pdf(job, job_parts, tax_inclusive, subtotal, gst, total)

    inv_num = f"INV-{job['reference'].lower()}"
    return send_file(buf, mimetype='application/pdf',
                     as_attachment=False, download_name=f"{inv_num}.pdf")


@invoice_bp.route('/jobs/<int:job_id>/ticket')
def shop_ticket(job_id):
    """HTML wrapper with close-tab button — embeds the shop ticket PDF via iframe."""
    with get_db() as conn:
        job = conn.execute("SELECT reference FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        return "Job not found", 404
    return render_template('invoice/pdf_view.html',
                           page_title=f"Shop Ticket — {job['reference']}",
                           pdf_url=url_for('invoice.shop_ticket_file', job_id=job_id))


@invoice_bp.route('/jobs/<int:job_id>/ticket/file')
def shop_ticket_file(job_id):
    """Serves the raw PDF bytes for the shop ticket."""
    with get_db() as conn:
        job = conn.execute("""
            SELECT j.*, r.name as region_name
            FROM jobs j JOIN regions r ON j.region_id=r.id
            WHERE j.id=?
        """, (job_id,)).fetchone()
        job_parts = conn.execute(
            "SELECT * FROM job_parts WHERE job_id=? ORDER BY id",
            (job_id,)).fetchall()
    if not job:
        return "Job not found", 404

    from shop_ticket import generate_shop_ticket
    buf   = generate_shop_ticket(job, job_parts)
    fname = f"ticket-{job['reference'].lower()}.pdf"
    return send_file(buf, mimetype='application/pdf',
                     as_attachment=False, download_name=fname)


@invoice_bp.route('/jobs/<int:job_id>/ticket/print')
def shop_ticket_print(job_id):
    """HTML shop ticket — A5 portrait, opens browser print dialog on button click."""
    with get_db() as conn:
        job = conn.execute("""
            SELECT j.*, r.name as region_name
            FROM jobs j
            JOIN regions r ON j.region_id = r.id
            WHERE j.id=?
        """, (job_id,)).fetchone()
        job_parts = conn.execute(
            "SELECT * FROM job_parts WHERE job_id=? ORDER BY id",
            (job_id,)).fetchall()
    if not job:
        return "Job not found", 404

    return render_template('invoice/shop_ticket_print.html',
                           job=dict(job),
                           job_parts=[dict(p) for p in job_parts],
                           today=date.today())
