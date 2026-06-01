"""
Shop ticket PDF — A4 landscape, two A5 tickets side by side.

Left:  Shop Ticket  — full detail including internal notes + blank rows
Right: Customer Copy — same layout, no internal notes, parts section
       titled 'Estimated Parts and Labour', GST-inclusive total shown
       when parts exist.

A light dashed vertical centre line separates the two halves.
"""
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units    import mm
from reportlab.lib          import colors
from reportlab.pdfgen       import canvas as pdf_canvas
from datetime               import date
import io, os

# ── Page geometry ──────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = landscape(A4)   # 297 × 210 mm → 841.9 × 595.3 pts
HALF_W = PAGE_W / 2              # each A5 area

M_TOP = 16 * mm                  # top margin — more breathing room
M_BOT = 6  * mm                  # bottom margin — less, shifts content up visually
M   = 11 * mm                    # side margin inside each half
PAD = 4  * mm                    # extra gutter from centre line

LOGO_PATH = os.path.join(os.path.dirname(__file__),
                          'static', 'img', 'flying_bike_logo.png')

BLANK_PARTS_ROWS = 4   # blank handwritten rows on shop ticket only


# ── Drawing primitives ─────────────────────────────────────────────────────────

def _hline(c, x1, x2, y, width=0.5, color=colors.black):
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.line(x1, y, x2, y)
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)


def _label(c, x, y, text):
    c.setFont('Helvetica-Bold', 6.5)
    c.setFillColor(colors.HexColor('#888888'))
    c.drawString(x, y, text.upper())
    c.setFillColor(colors.black)


def _value(c, x, y, text, size=9, bold=False, right=False, x_right=None):
    c.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
    if right and x_right is not None:
        c.drawRightString(x_right, y, str(text) if text else '')
    else:
        c.drawString(x, y, str(text) if text else '')


def _wrapped(c, x, y, text, max_width, size=9, lh=4.5*mm):
    if not text:
        return y
    c.setFont('Helvetica', size)
    words = str(text).replace('\r\n', ' ').replace('\n', ' ').split()
    line, lines = '', []
    for w in words:
        test = (line + ' ' + w).strip()
        if c.stringWidth(test, 'Helvetica', size) <= max_width:
            line = test
        else:
            if line: lines.append(line)
            line = w
    if line: lines.append(line)
    for ln in lines:
        c.drawString(x, y, ln)
        y -= lh
    return y


# ── Single ticket renderer ─────────────────────────────────────────────────────

def _draw_ticket(c, job, job_parts, ox, title_tag, show_notes,
                 parts_heading, show_total):
    """
    Draw one ticket inside its A5 area.
    ox          — x origin (left edge of this half)
    title_tag   — 'SHOP TICKET' or 'CUSTOMER COPY'
    show_notes  — whether to render internal notes section
    parts_heading — label above parts table
    show_total  — whether to show GST-inclusive total row
    """
    # Normalise to plain dict so .get() works regardless of sqlite3.Row vs dict
    if not isinstance(job, dict):
        job = dict(job)
    if job_parts and not isinstance(job_parts[0], dict):
        job_parts = [dict(jp) for jp in job_parts]
    # Content bounds within this half
    x0    = ox + M + PAD        # left content edge (a bit in from centre)
    x1    = ox + HALF_W - M     # right content edge
    cw    = x1 - x0             # usable content width
    top_y = PAGE_H - M_TOP

    # ── Header ─────────────────────────────────────────────────────────────────
    logo_h = 13 * mm
    logo_w = 26 * mm
    y = top_y

    if os.path.exists(LOGO_PATH):
        c.drawImage(LOGO_PATH, x0, y - logo_h,
                    width=logo_w, height=logo_h,
                    preserveAspectRatio=True, mask='auto')

    # Title tag (SHOP TICKET / CUSTOMER COPY) — same size as reference
    c.setFont('Helvetica-Bold', 13)
    c.setFillColor(colors.HexColor('#444444'))
    c.drawCentredString(x0 + cw * 0.5, y - 5*mm, title_tag)
    c.setFillColor(colors.black)

    # Reference — bold but same size as dates below
    c.setFont('Helvetica-Bold', 8.5)
    c.drawRightString(x1, y - 5*mm, job['reference'])

    # Date below reference
    created = (job.get('created_at') or '')[:10] or date.today().isoformat()
    try:
        from datetime import datetime as _dt
        created_fmt = _dt.strptime(created, '%Y-%m-%d').strftime('%-d %b %Y')
    except Exception:
        created_fmt = created

    # Scheduled date if set
    sched = job.get('scheduled_date') or ''
    try:
        from datetime import datetime as _dt
        sched_fmt = _dt.strptime(sched, '%Y-%m-%d').strftime('%-d %b %Y') if sched else ''
    except Exception:
        sched_fmt = ''

    c.setFont('Helvetica', 7.5)
    c.setFillColor(colors.HexColor('#666666'))
    c.drawRightString(x1, y - 10*mm, created_fmt)
    if sched_fmt:
        c.drawRightString(x1, y - 14.5*mm, f'Due: {sched_fmt}')
    c.setFillColor(colors.black)

    y -= logo_h + 3*mm
    _hline(c, x0, x1, y, width=1.2)
    y -= 5*mm

    # ── Customer fields ─────────────────────────────────────────────────────────
    grey_line  = colors.HexColor('#cccccc')
    # Shop address stacked on right side of Name/Phone rows (customer copy only)
    SHOP_LINES = [
        'Pista Bikes',
        '255 Hawthorn Road',
        'Caulfield 3162',
        'Phone: 0403 225 135',
    ]

    def field_row(label, value, y_, right_lines=None):
        """Draw a labelled field row.
        right_lines: list of strings to stack right-justified on the right half.
                     No underline is drawn under the right-side text.
        """
        _label(c, x0, y_ + 1*mm, label)
        y_ -= 4*mm
        _value(c, x0, y_, value or '', size=9.5)
        # Draw right-aligned lines if provided (no underline for those)
        if right_lines:
            # Right lines occupy the right half; draw stacked above baseline
            rl_y = y_ + (len(right_lines) - 1) * 4*mm
            for rl in right_lines:
                c.setFont('Helvetica', 8)
                c.drawRightString(x1, rl_y, rl)
                rl_y -= 4*mm
            c.setFont('Helvetica', 9.5)  # reset
        y_ -= 2.5*mm
        _hline(c, x0, x1, y_, width=0.3, color=grey_line)
        y_ -= 4*mm
        return y_

    # Name row — shop name + first address line on right (customer copy only)
    name_right  = SHOP_LINES[:2] if not show_notes else None
    phone_right = SHOP_LINES[2:] if not show_notes else None
    y = field_row('Name',  job.get('customer_name')  or '', y, right_lines=name_right)
    y = field_row('Phone', job.get('customer_phone') or '', y, right_lines=phone_right)
    # Bike — full width, no right-side content
    y = field_row('Bike',  job.get('bike_description') or '', y)

    # ── Description ────────────────────────────────────────────────────────────
    _label(c, x0, y + 1*mm, 'Message / Description')
    y -= 4*mm
    desc = job.get('description') or ''
    if desc:
        y = _wrapped(c, x0, y, desc, cw, size=9, lh=5*mm)
    else:
        y -= 5*mm

    n_lines = max(3, len(desc.split('\n')) + 1) if desc else 3
    for _ in range(n_lines):
        y -= 1*mm
        _hline(c, x0, x1, y, width=0.3, color=grey_line)
        y -= 5*mm
    y -= 2*mm

    # ── Internal notes (shop ticket only) ──────────────────────────────────────
    if show_notes:
        _label(c, x0, y + 1*mm, 'Internal Notes')
        y -= 4*mm
        notes = job.get('notes') or ''
        if notes:
            y = _wrapped(c, x0, y, notes, cw, size=9, lh=5*mm)
        else:
            y -= 5*mm
        for _ in range(max(2, len(notes.split('\n')) + 1) if notes else 2):
            y -= 1*mm
            _hline(c, x0, x1, y, width=0.3, color=grey_line)
            y -= 5*mm
        y -= 3*mm

    # ── Parts section ───────────────────────────────────────────────────────────
    _hline(c, x0, x1, y, width=0.8)
    y -= 5*mm

    _label(c, x0, y + 1*mm, parts_heading)
    y -= 4.5*mm

    # Column positions
    col_desc  = x0
    col_qty   = x1 - 46*mm
    col_price = x1 - 22*mm
    col_ext   = x1

    c.setFont('Helvetica-Bold', 7)
    c.setFillColor(colors.HexColor('#888888'))
    c.drawString     (col_desc,  y, 'DESCRIPTION')
    c.drawRightString(col_qty,   y, 'QTY')
    c.drawRightString(col_price, y, 'UNIT PRICE')
    c.drawRightString(col_ext,   y, 'AMOUNT')
    c.setFillColor(colors.black)
    y -= 3*mm
    _hline(c, x0, x1, y, width=0.5)
    y -= 4.5*mm

    ROW_H = 7*mm
    running_total = 0.0

    for jp in job_parts:
        desc_t = jp['description'] or ''
        max_chars = int(cw / 2.2)  # approx chars that fit
        if len(desc_t) > max_chars:
            desc_t = desc_t[:max_chars - 1] + '…'
        ext = jp['quantity'] * jp['unit_cost']
        running_total += ext
        c.setFont('Helvetica', 9)
        c.drawString     (col_desc,  y, desc_t)
        c.drawRightString(col_qty,   y, f"{jp['quantity']:.2f}")
        c.drawRightString(col_price, y, f"${jp['unit_cost']:.2f}")
        c.drawRightString(col_ext,   y, f"${ext:.2f}")
        y -= 3*mm
        _hline(c, x0, x1, y, width=0.3, color=grey_line)
        y -= ROW_H - 3*mm

    # Blank rows (shop ticket only)
    if not show_total:
        for _ in range(BLANK_PARTS_ROWS):
            y -= ROW_H - 4.5*mm
            _hline(c, x0,            x1,         y, width=0.3,
                   color=colors.HexColor('#aaaaaa'))
            _hline(c, col_qty  - 14*mm, col_qty,  y, width=0.3,
                   color=colors.HexColor('#aaaaaa'))
            _hline(c, col_price - 18*mm, col_price, y, width=0.3,
                   color=colors.HexColor('#aaaaaa'))
            _hline(c, col_ext  - 18*mm, col_ext,  y, width=0.3,
                   color=colors.HexColor('#aaaaaa'))
            y -= 2.5*mm

    y -= 3*mm
    # (no closing line below parts list)

    # ── Estimated total (customer copy, only when parts exist) ──────────────────
    if show_total and running_total > 0:
        y -= 6*mm          # clear gap below the thick closing line
        band_h = 10*mm
        band_x = x0
        band_w = x1 - x0
        c.setFillColor(colors.HexColor('#eeeeee'))
        c.rect(band_x, y - 2*mm, band_w, band_h, fill=1, stroke=0)
        c.setFillColor(colors.black)
        text_y = y + band_h * 0.28        # vertically centred in band
        # Label — left side of band
        c.setFont('Helvetica-Bold', 8.5)
        c.drawString(band_x + 3*mm, text_y, 'Estimated Total (GST incl.)')
        # Amount — right side of band
        c.setFont('Helvetica-Bold', 11)
        c.drawRightString(x1 - 1*mm, text_y - 0.5*mm, f'${running_total:.2f}')
        y -= band_h + 2*mm


# ── Public entry point ─────────────────────────────────────────────────────────

def generate_shop_ticket(job, job_parts):
    """
    Returns a BytesIO containing an A4 landscape PDF with:
      Left  — Shop Ticket (full detail)
      Right — Customer Copy (no notes, estimated total)
    """
    buf = io.BytesIO()
    c   = pdf_canvas.Canvas(buf, pagesize=landscape(A4))

    # Left half: shop ticket
    _draw_ticket(c, job, job_parts,
                 ox=0,
                 title_tag='SHOP TICKET',
                 show_notes=True,
                 parts_heading='Parts Used',
                 show_total=False)

    # Right half: customer copy
    _draw_ticket(c, job, job_parts,
                 ox=HALF_W,
                 title_tag='CUSTOMER COPY',
                 show_notes=False,
                 parts_heading='Estimated Parts and Labour',
                 show_total=True)

    # ── Vertical dashed centre divider ─────────────────────────────────────────
    cx = HALF_W
    c.setStrokeColor(colors.HexColor('#888888'))
    c.setLineWidth(1.5)
    c.setDash(4, 4)              # 4pt dash, 4pt gap — more visible
    c.line(cx, M_BOT, cx, PAGE_H - M_TOP)
    c.setDash()
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)
    c.setLineWidth(0.5)

    c.save()
    buf.seek(0)
    return buf
