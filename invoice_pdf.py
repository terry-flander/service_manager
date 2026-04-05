"""
PDF Invoice generator matching The Flying Bike invoice template.
Layout: TAX INVOICE section + PAYMENT ADVICE tearoff at bottom.
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from datetime import date, timedelta
import io, os

# ── Business constants ────────────────────────────────────────────────────────
BUSINESS_NAME  = "Elad Shelzer T/As The Flying Bike"
BUSINESS_ADDR  = ["255 Hawthorn Rd", "CAULFIELD NORTH VIC 3161", "AUSTRALIA"]
BUSINESS_ABN   = "56 361 357 249"
BUSINESS_BSB   = "013 304"
BUSINESS_ACCT  = "401523996"
BUSINESS_BANK  = "The Flying Bike Australia"
PAYMENT_DAYS   = 14

PAGE_W, PAGE_H = A4          # 595.27 x 841.89 pts
M              = 20 * mm     # left/right margin


def _fmt(val):
    """Format a float as $0.00."""
    return f"${val:,.2f}"


def _draw_hline(c, x1, x2, y, width=0.5, color=colors.black):
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.line(x1, y, x2, y)
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)


def _bold(c, size=9):
    c.setFont("Helvetica-Bold", size)

def _reg(c, size=9):
    c.setFont("Helvetica", size)


def generate_invoice_pdf(job, job_parts, tax_inclusive, subtotal, gst, total):
    """
    Returns a BytesIO containing the PDF.
    job         — sqlite3.Row with all job fields + customer_name etc.
    job_parts   — list of sqlite3.Row
    tax_inclusive — bool
    subtotal, gst, total — floats (pre-calculated)
    """
    buf = io.BytesIO()
    c   = canvas.Canvas(buf, pagesize=A4)

    amount_paid = float(job['amount_paid'] or 0)
    amount_due  = max(total - amount_paid, 0)
    paid_date   = job['paid_date'] or ''
    inv_date    = date.today()
    due_date    = inv_date + timedelta(days=PAYMENT_DAYS)
    inv_num     = f"INV-{job['reference'].lower()}"

    # Customer address lines — split address at each comma for formatting
    cust_lines = [job['customer_name']]
    if job['address']:
        for part in job['address'].split(','):
            part = part.strip()
            if part:
                cust_lines.append(part.upper() if part == part.upper() else part)

    # ── TAX INVOICE header — three columns aligned at same top Y ────────────
    #
    #  LEFT (M..0.46W)   CENTRE (0.48W..0.67W)   RIGHT (0.68W..PAGE_W-M)
    #  TAX INVOICE        Invoice Date             [logo]
    #  customer address   Invoice Number           Elad Shelzer T/As ...
    #                     ABN                      255 Hawthorn Rd ...

    # Column boundaries
    meta_x  = PAGE_W * 0.48   # centre col start
    biz_x   = PAGE_W * 0.68   # right col start  — wide enough for full business name
    right_w = PAGE_W - M - biz_x   # ~47mm

    # ── Logo — top of page, within right column ───────────────────────────────
    logo_path = os.path.join(os.path.dirname(__file__), 'static', 'img', 'flying_bike_logo.png')
    logo_top  = PAGE_H - M          # flush with top margin
    logo_h    = right_w * 0.65      # height proportional to width
    logo_bot  = logo_top - logo_h   # bottom edge of logo

    if os.path.exists(logo_path):
        c.drawImage(logo_path,
                    biz_x, logo_bot,
                    width=right_w, height=logo_h,
                    preserveAspectRatio=True, mask='auto')

    # ── Row top — all three columns share same TOP edge ──────────────────────
    # ReportLab draws text with baseline at Y. To top-align, subtract cap-height.
    # Cap-height ≈ 0.718 × font_size for Helvetica.
    CAP = 0.718
    header_top = logo_bot - 4*mm   # shared top edge for all three columns

    # LEFT: "TAX INVOICE" — baseline adjusted so cap-top = header_top
    tax_size = 28
    c.setFont("Helvetica-Bold", tax_size)
    c.drawString(M, header_top - tax_size * CAP, "TAX INVOICE")

    # CENTRE: Invoice Date / Number / ABN — 8pt label, top = header_top
    lbl_size = 8
    meta_y = header_top - lbl_size * CAP
    fields = [
        ("Invoice Date",   inv_date.strftime("%-d %b %Y")),
        ("Invoice Number", inv_num),
        ("ABN",            BUSINESS_ABN),
    ]
    for label, value in fields:
        _bold(c, lbl_size); c.drawString(meta_x, meta_y, label)
        meta_y -= 4.5*mm
        _reg(c, lbl_size);  c.drawString(meta_x, meta_y, value)
        meta_y -= 7*mm

    # RIGHT: Business name + address — 8pt, top = header_top
    biz_y = header_top - lbl_size * CAP
    _reg(c, lbl_size)
    for line in [BUSINESS_NAME] + BUSINESS_ADDR:
        c.drawString(biz_x, biz_y, line)
        biz_y -= 4.5*mm

    # LEFT continued: customer address below "TAX INVOICE" cap height
    cust_y = header_top - tax_size * CAP - 8*mm
    _reg(c, 9)
    for line in cust_lines:
        c.drawString(M + 5*mm, cust_y, line)
        cust_y -= 4.5*mm

    # ── Line items table ──────────────────────────────────────────────────────
    # Table starts below the lowest of: customer address, meta fields, biz address
    # cust_y and meta_y and biz_y are left pointing at their last-drawn positions
    table_top = min(cust_y, meta_y, biz_y) - 8*mm
    col = {
        'desc':  M,
        'qty':   PAGE_W - M - 95*mm,
        'price': PAGE_W - M - 65*mm,
        'gst':   PAGE_W - M - 35*mm,
        'amt':   PAGE_W - M,
    }

    # Table header
    _draw_hline(c, M, PAGE_W - M, table_top + 5*mm, 1.0)
    _bold(c, 9)
    c.drawString(col['desc'],  table_top, "Description")
    c.drawRightString(col['qty'],   table_top, "Quantity")
    c.drawRightString(col['price'], table_top, "Unit Price")
    c.drawRightString(col['gst'],   table_top, "GST")
    c.drawRightString(col['amt'],   table_top, "Amount AUD")
    _draw_hline(c, M, PAGE_W - M, table_top - 2*mm, 0.5)

    row_y = table_top - 7*mm
    _reg(c, 9)
    for jp in job_parts:
        qty  = jp['quantity']
        uc   = jp['unit_cost']
        line = qty * uc
        if tax_inclusive:
            unit_ex = uc / 1.1
            line_ex = qty * unit_ex
        else:
            unit_ex = uc
            line_ex = line

        desc = jp['description']
        # Truncate long descriptions
        if len(desc) > 55:
            desc = desc[:52] + "…"

        c.drawString(col['desc'],  row_y, desc)
        c.drawRightString(col['qty'],   row_y, f"{qty:.2f}")
        c.drawRightString(col['price'], row_y, f"{unit_ex:.4f}")
        c.drawRightString(col['gst'],   row_y, "10%")
        c.drawRightString(col['amt'],   row_y, f"{line_ex:.2f}")
        row_y -= 6*mm

    # ── Totals block ──────────────────────────────────────────────────────────
    totals_y = row_y - 4*mm
    _draw_hline(c, col['price'] - 5*mm, PAGE_W - M, totals_y + 3*mm, 0.4,
                colors.HexColor('#cccccc'))

    def _total_row(label, value, bold=False, y=None):
        nonlocal totals_y
        ty = y if y is not None else totals_y
        if bold:
            _bold(c, 9)
        else:
            _reg(c, 9)
        c.drawRightString(col['gst'],  ty, label)
        c.drawRightString(col['amt'],  ty, value)
        if y is None:
            totals_y -= 5.5*mm

    _total_row("Subtotal",           _fmt(subtotal)[1:])
    _total_row(f"TOTAL  GST  10%",   _fmt(gst)[1:])
    _draw_hline(c, col['price'] - 5*mm, PAGE_W - M, totals_y + 3*mm, 0.5)
    _total_row("TOTAL AUD",          _fmt(total)[1:],  bold=True)
    if amount_paid > 0:
        _total_row("Less Amount Paid",   _fmt(amount_paid)[1:])
    _draw_hline(c, col['price'] - 5*mm, PAGE_W - M, totals_y + 3*mm, 1.0)
    _total_row("AMOUNT DUE AUD",     _fmt(amount_due)[1:], bold=True)
    _draw_hline(c, col['price'] - 5*mm, PAGE_W - M, totals_y + 3*mm, 0.5)

    # ── Payment terms ─────────────────────────────────────────────────────────
    terms_y = totals_y - 12*mm
    _bold(c, 9)
    c.drawString(M, terms_y, f"Due Date: {due_date.strftime('%-d %b %Y')}")
    terms_y -= 5*mm
    _reg(c, 8.5)
    for line in [
        f"Payment must be made within {PAYMENT_DAYS} days of issue",
        "We accept payment via direct deposit into the following account:",
        BUSINESS_BANK,
        f"BSB: {BUSINESS_BSB}",
        f"Account: {BUSINESS_ACCT}",
        "*please state this invoice number when making a payment",
    ]:
        c.drawString(M, terms_y, line)
        terms_y -= 4.5*mm

    # ── Tearoff scissors line ─────────────────────────────────────────────────
    tear_y = 105*mm
    c.setDash([3, 3])
    _draw_hline(c, M, PAGE_W - M, tear_y, 0.5, colors.HexColor('#999999'))
    c.setDash([])
    # Scissors icon approximation
    _reg(c, 10)
    c.drawString(M - 4*mm, tear_y - 1.5*mm, "✂")

    # ── PAYMENT ADVICE section ────────────────────────────────────────────────
    pa_y = tear_y - 8*mm

    c.setFont("Helvetica-Bold", 22)
    c.drawString(M, pa_y, "PAYMENT ADVICE")
    pa_y -= 8*mm

    # Left: "To" block
    _reg(c, 9)
    c.drawString(M, pa_y, "To:")
    addr_x = M + 12*mm
    for line in [BUSINESS_NAME] + BUSINESS_ADDR:
        if len(line) > 30:
            words = line.split(); cur = ""
            for w in words:
                if len(cur)+len(w)+1 > 30:
                    c.drawString(addr_x, pa_y, cur.strip()); pa_y -= 4.5*mm; cur = w+" "
                else:
                    cur += w+" "
            if cur.strip():
                c.drawString(addr_x, pa_y, cur.strip()); pa_y -= 4.5*mm
        else:
            c.drawString(addr_x, pa_y, line); pa_y -= 4.5*mm

    # Right: payment details grid
    grid_x  = PAGE_W * 0.52
    grid_x2 = PAGE_W - M
    grid_y  = tear_y - 10*mm

    pa_rows = [
        ("Customer",       job['customer_name'], False),
        ("Invoice Number", inv_num,              False),
        ("Amount Due",     _fmt(amount_due),     True),
        ("Due Date",       due_date.strftime("%-d %b %Y"), False),
    ]
    for label, value, bold in pa_rows:
        _draw_hline(c, grid_x, grid_x2, grid_y + 4.5*mm, 0.3,
                    colors.HexColor('#cccccc'))
        _bold(c, 8);  c.drawString(grid_x, grid_y, label)
        if bold:
            _bold(c, 9)
        else:
            _reg(c, 9)
        c.drawString(grid_x + 35*mm, grid_y, value)
        grid_y -= 9*mm

    # Amount Enclosed line
    _draw_hline(c, grid_x, grid_x2, grid_y + 4.5*mm, 0.3,
                colors.HexColor('#cccccc'))
    _bold(c, 8)
    c.drawString(grid_x, grid_y, "Amount Enclosed")
    _draw_hline(c, grid_x, grid_x2, grid_y - 2*mm, 0.5)
    grid_y -= 8*mm
    _reg(c, 7.5)
    c.setFillColor(colors.HexColor('#666666'))
    c.drawString(grid_x, grid_y, "Enter the amount you are paying above")
    c.setFillColor(colors.black)

    c.save()
    buf.seek(0)
    return buf
