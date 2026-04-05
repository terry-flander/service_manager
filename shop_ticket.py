"""
Shop ticket PDF generator — A5 portrait format.
A printed work order for the mechanic: customer details,
description, notes, parts used, plus blank lines for additions.
"""
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas as pdf_canvas
from datetime import date
import io, os

PAGE_W, PAGE_H = A5          # 148 x 210 mm  →  419.5 x 595.3 pts
M  = 12 * mm                 # margin
CW = PAGE_W - 2 * M          # content width

LOGO_PATH = os.path.join(os.path.dirname(__file__), 'static', 'img', 'flying_bike_logo.png')

# Extra blank parts rows to print at the bottom for handwritten additions
BLANK_PARTS_ROWS = 4


def _hline(c, y, x1=None, x2=None, width=0.5, color=colors.black):
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.line(x1 or M, y, x2 or (PAGE_W - M), y)
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)


def _label(c, x, y, text):
    """Small uppercase grey label."""
    c.setFont('Helvetica-Bold', 6.5)
    c.setFillColor(colors.HexColor('#888888'))
    c.drawString(x, y, text.upper())
    c.setFillColor(colors.black)


def _value(c, x, y, text, size=9, bold=False):
    c.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
    c.drawString(x, y, str(text) if text else '')


def _wrapped_value(c, x, y, text, max_width, size=9, line_height=4.5*mm):
    """
    Draw text wrapping within max_width.
    Returns the Y position after the last line drawn.
    """
    if not text:
        return y
    c.setFont('Helvetica', size)
    words = str(text).replace('\r\n', ' ').replace('\n', ' ').split()
    line  = ''
    lines = []
    for word in words:
        test = (line + ' ' + word).strip()
        if c.stringWidth(test, 'Helvetica', size) <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    for ln in lines:
        c.drawString(x, y, ln)
        y -= line_height
    return y


def generate_shop_ticket(job, job_parts):
    """
    Returns a BytesIO containing an A5 shop ticket PDF.
    job       — sqlite3.Row
    job_parts — list of sqlite3.Row from job_parts table
    """
    buf = io.BytesIO()
    c   = pdf_canvas.Canvas(buf, pagesize=A5)

    y = PAGE_H - M

    # ── Header: logo left, reference + date right ─────────────────────────────
    logo_h = 14 * mm
    logo_w = 28 * mm
    if os.path.exists(LOGO_PATH):
        c.drawImage(LOGO_PATH, M, y - logo_h,
                    width=logo_w, height=logo_h,
                    preserveAspectRatio=True, mask='auto')

    # Reference (large) top right
    c.setFont('Helvetica-Bold', 14)
    c.drawRightString(PAGE_W - M, y - 5*mm, job['reference'])

    # Date created top right below reference
    created = (job['created_at'] or '')[:10] or date.today().isoformat()
    try:
        from datetime import datetime
        created_fmt = datetime.strptime(created, '%Y-%m-%d').strftime('%-d %b %Y')
    except Exception:
        created_fmt = created
    c.setFont('Helvetica', 8)
    c.setFillColor(colors.HexColor('#666666'))
    c.drawRightString(PAGE_W - M, y - 10*mm, created_fmt)
    c.setFillColor(colors.black)

    y -= logo_h + 3*mm
    _hline(c, y, width=1.2)
    y -= 5*mm

    # ── Customer fields ───────────────────────────────────────────────────────
    def field_row(label, value, y, indent=0):
        """Draw label + value, underline, return next y."""
        _label(c, M + indent, y + 1*mm, label)
        y -= 4*mm
        _value(c, M + indent, y, value, size=10)
        y -= 2.5*mm
        _hline(c, y, x1=M + indent, width=0.3,
               color=colors.HexColor('#cccccc'))
        y -= 4*mm
        return y

    y = field_row('Name',  job['customer_name'] or '', y)
    y = field_row('Phone', job['customer_phone'] or '', y)
    y = field_row('Email', job['customer_email'] or '', y)

    # ── Description (multi-line) ──────────────────────────────────────────────
    _label(c, M, y + 1*mm, 'Message / Description')
    y -= 4*mm

    desc = job['description'] or ''
    if desc:
        y = _wrapped_value(c, M, y, desc, CW, size=9.5, line_height=5*mm)
    else:
        y -= 5*mm

    # Always draw 3 underlines for description (filled or blank)
    desc_line_count = max(3, len((desc or '').split('\n')) + 1)
    for _ in range(desc_line_count):
        y -= 1*mm
        _hline(c, y, width=0.3, color=colors.HexColor('#cccccc'))
        y -= 5*mm
    y -= 2*mm

    # ── Internal notes ────────────────────────────────────────────────────────
    _label(c, M, y + 1*mm, 'Internal Notes')
    y -= 4*mm

    notes = job['notes'] or ''
    if notes:
        y = _wrapped_value(c, M, y, notes, CW, size=9.5, line_height=5*mm)
    else:
        y -= 5*mm

    for _ in range(max(2, len((notes or '').split('\n')) + 1)):
        y -= 1*mm
        _hline(c, y, width=0.3, color=colors.HexColor('#cccccc'))
        y -= 5*mm
    y -= 3*mm

    # ── Parts used ────────────────────────────────────────────────────────────
    _hline(c, y, width=0.8)
    y -= 5*mm

    _label(c, M, y + 1*mm, 'Parts Used')
    y -= 4.5*mm

    # Column headers  (desc | qty | unit price | ext price)
    col_desc  = M
    col_qty   = PAGE_W - M - 50*mm
    col_price = PAGE_W - M - 24*mm
    col_ext   = PAGE_W - M

    c.setFont('Helvetica-Bold', 7)
    c.setFillColor(colors.HexColor('#888888'))
    c.drawString(col_desc, y, 'DESCRIPTION')
    c.drawRightString(col_qty,   y, 'QTY')
    c.drawRightString(col_price, y, 'UNIT PRICE')
    c.drawRightString(col_ext,   y, 'AMOUNT')
    c.setFillColor(colors.black)
    y -= 3*mm
    _hline(c, y, width=0.5)
    y -= 4.5*mm

    ROW_H = 7*mm

    # Existing parts
    for jp in job_parts:
        desc_text = jp['description'] or ''
        if len(desc_text) > 34:
            desc_text = desc_text[:33] + '…'
        ext = jp['quantity'] * jp['unit_cost']
        c.setFont('Helvetica', 9)
        c.drawString(col_desc, y, desc_text)
        c.drawRightString(col_qty,   y, f"{jp['quantity']:.2f}")
        c.drawRightString(col_price, y, f"${jp['unit_cost']:.2f}")
        c.drawRightString(col_ext,   y, f"${ext:.2f}")
        y -= 3*mm
        _hline(c, y, width=0.3, color=colors.HexColor('#cccccc'))
        y -= ROW_H - 3*mm

    # Blank rows for handwritten additions
    for _ in range(BLANK_PARTS_ROWS):
        y -= ROW_H - 4.5*mm
        _hline(c, y, width=0.3, color=colors.HexColor('#aaaaaa'))
        _hline(c, y, x1=col_qty   - 14*mm, x2=col_qty,
               width=0.3, color=colors.HexColor('#aaaaaa'))
        _hline(c, y, x1=col_price - 18*mm, x2=col_price,
               width=0.3, color=colors.HexColor('#aaaaaa'))
        _hline(c, y, x1=col_ext   - 18*mm, x2=col_ext,
               width=0.3, color=colors.HexColor('#aaaaaa'))
        y -= 2.5*mm

    y -= 3*mm
    _hline(c, y, width=0.8)

    # ── Footer ────────────────────────────────────────────────────────────────
    footer_y = M + 4*mm
    c.setFont('Helvetica', 7)
    c.setFillColor(colors.HexColor('#aaaaaa'))
    c.drawString(M, footer_y, 'the flying bike — mobile bicycle workshop')
    c.drawRightString(PAGE_W - M, footer_y,
                      f"Printed {date.today().strftime('%-d %b %Y')}")
    c.setFillColor(colors.black)

    c.save()
    buf.seek(0)
    return buf
