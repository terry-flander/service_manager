from flask import Blueprint, render_template, make_response, send_file, url_for
from models import get_db
from datetime import date, timedelta
import csv, io

invoice_bp = Blueprint('invoice', __name__)


def calc_totals(job_parts, tax_inclusive):
    """
    tax_inclusive=True  → prices already include GST.
                          GST = total / 11  (back-calculated)
                          subtotal (ex GST) = total - GST
    tax_inclusive=False → prices are ex-GST.
                          GST = subtotal * 0.10
                          total = subtotal + GST
    """
    line_total = sum(jp['quantity'] * jp['unit_cost'] for jp in job_parts)
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

    tax_inclusive = bool(job['tax_inclusive'])
    subtotal, gst, total = calc_totals(job_parts, tax_inclusive)
    today    = date.today()
    due_date = today + timedelta(days=30)

    return render_template('invoice/view.html',
                           job=job, job_parts=job_parts,
                           tax_inclusive=tax_inclusive,
                           subtotal=subtotal, gst=gst, total=total,
                           today=today, due_date=due_date)


@invoice_bp.route('/jobs/<int:job_id>/invoice/xero-csv')
def xero_csv(job_id):
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

    tax_inclusive = bool(job['tax_inclusive'])
    invoice_num   = f"INV-{job['reference']}"
    due_date      = (date.today() + timedelta(days=30)).strftime('%d/%m/%Y')

    # Xero tax type strings
    # Tax Inclusive → "GST on Income" with amounts as-is (Xero handles inclusive tax)
    # Tax Exclusive → "GST on Income" with Xero set to exclusive
    tax_type   = 'GST on Income'
    amounts_are_inclusive = 'true' if tax_inclusive else 'false'

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'ContactName', 'EmailAddress', 'POAddressLine1', 'DueDate',
        'InventoryItemCode', 'Description', 'Quantity', 'UnitAmount',
        'Discount', 'AccountCode', 'TaxType', 'TaxAmount',
        'TrackingName1', 'TrackingOption1', 'Currency',
        'InvoiceNumber', 'Reference', 'AmountsAreInclusive'
    ])

    lines = job_parts if job_parts else [None]
    for i, jp in enumerate(lines):
        first = (i == 0)
        if jp:
            writer.writerow([
                job['customer_name']    if first else '',
                (job['customer_email'] or '') if first else '',
                job['address']          if first else '',
                due_date                if first else '',
                jp['part_number'] or '',
                jp['description'],
                jp['quantity'],
                f"{jp['unit_cost']:.2f}",
                '', '200', tax_type, '',
                '', '', 'AUD',
                invoice_num if first else '',
                job['reference'] if first else '',
                amounts_are_inclusive if first else '',
            ])
        else:
            writer.writerow([
                job['customer_name'], job['customer_email'] or '',
                job['address'], due_date, '',
                job['description'] or 'Service call',
                1, '0.00', '', '200', tax_type, '',
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

    tax_inclusive = bool(job['tax_inclusive'])
    subtotal, gst, total = calc_totals(job_parts, tax_inclusive)

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
