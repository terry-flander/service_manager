"""
po_parser.py — Parse purchase order text or PDF in multiple formats into a
list of line dicts: {supplier_sku, supplier_desc, qty, unit_price_inc_gst}

Supported formats (auto-detected):
  BPW     : "{qty} x {desc} ({sku}) = ${extended}"
  PSI     : Email blocks with SKU: lines and tab-separated qty/price
  BikeBox : PDF purchase order (pdfplumber table extraction)
  CSV     : Comma or tab separated with headers
  Free    : Best-effort extraction of qty, description, price from any line
"""
import re
import csv
import io
import logging

log = logging.getLogger('app')


def parse(text):
    """Parse text into a list of line dicts. Returns (lines, format_detected)."""
    # Normalise line endings and strip BOM
    text = text.replace('\r\n', '\n').replace('\r', '\n').strip()
    if text.startswith('\ufeff'):
        text = text[1:]
    if not text:
        return [], 'empty'

    lines, fmt = _try_bpw(text)
    if lines:
        return lines, fmt

    lines, fmt = _try_psi(text)
    if lines:
        return lines, fmt

    lines, fmt = _try_csv(text)
    if lines:
        return lines, fmt

    lines, fmt = _try_freeform(text)
    return lines, fmt


def parse_pdf(file_bytes):
    """Parse a PDF file (bytes) into line dicts. Returns (lines, format_detected).
    Tries BikeBox then BikeCorp formats, falls back to text extraction → parse()."""
    try:
        import pdfplumber
        import io as _io
        log.info(f"parse_pdf: got {len(file_bytes)} bytes, trying pdfplumber")
        with pdfplumber.open(_io.BytesIO(file_bytes)) as pdf:
            log.info(f"parse_pdf: opened PDF, {len(pdf.pages)} page(s)")
            lines, fmt = _try_bikebox_pdf(pdf)
            if lines:
                log.info(f"parse_pdf: bikebox detected, {len(lines)} lines")
                return lines, fmt
            lines, fmt = _try_bikecorp_pdf(pdf)
            if lines:
                log.info(f"parse_pdf: bikecorp detected, {len(lines)} lines")
                return lines, fmt
            text = '\n'.join(
                page.extract_text() or '' for page in pdf.pages
            ).strip()
            log.info(f"parse_pdf: structured parse failed, extracted {len(text)} chars of text")
        if text:
            result = parse(text)
            log.info(f"parse_pdf: text parse returned {len(result[0])} lines, fmt={result[1]}")
            return result
        log.warning("parse_pdf: no text extracted from PDF")
        return [], 'pdf-empty'
    except ImportError:
        log.error("parse_pdf: pdfplumber not installed")
        return [], 'pdf-no-pdfplumber'
    except Exception as e:
        log.warning(f"parse_pdf error: {e}")
        return [], 'pdf-error'


# ── BikeBox PDF format ────────────────────────────────────────────────────────
# Prices in BikeBox PDFs are ex-GST — multiply by 1.1 for inc-GST

_BB_SKU_ONLY_RE = re.compile(
    r'^([A-Z][A-Z0-9\-]+)\s+'
    r'(?:(?:ea|each|Each|pc|Pc|EACH)\s+)?'
    r'(\d+(?:\.\d+)?)\s+'
    r'([\d,]+\.?\d*)\s+'
    r'([\d,]+\.?\d*)$'
)
_BB_INLINE_RE = re.compile(
    r'^([A-Z][A-Z0-9\-]+)\s+'
    r'(.+?)\s+'
    r'(?:(?:ea|each|Each|pc|Pc|EACH)\s+)?'
    r'(\d+(?:\.\d+)?)\s+'
    r'([\d,]+\.?\d*)\s+'
    r'([\d,]+\.?\d*)$'
)
_BB_INLINE_TRUNC_RE = re.compile(
    r'^([A-Z][A-Z0-9\-]+)\s+'
    r'(.+?)\s+'
    r'(?:(?:ea|each|Each|pc|Pc|EACH)\s+)?'
    r'(\d+(?:\.\d+)?)\s+'
    r'([\d,]+\.\d{2})$'
)
_BB_QTY_PRICE_RE = re.compile(
    r'^(?:(?:ea|each|Each|pc|Pc|EACH)\s+)?'
    r'(\d+(?:\.\d+)?)\s+'
    r'([\d,]+\.?\d*)\s+'
    r'([\d,]+\.?\d*)$'
)


def _bb_val(qty, up, ext):
    """Validate extended ≈ qty × unit_price within 5%."""
    try:
        q = float(str(qty).replace(',', ''))
        u = float(str(up).replace(',', ''))
        e = float(str(ext).replace(',', ''))
        return e > 0 and abs(q * u - e) / max(e, 0.01) < 0.05
    except Exception:
        return False


def _bb_tag(line):
    """Return (tag, match, kind) for one line."""
    ms = _BB_SKU_ONLY_RE.match(line)
    if ms and _bb_val(ms.group(2), ms.group(3), ms.group(4)):
        return 'SKU', ms, 'sku_only'
    mi = _BB_INLINE_RE.match(line)
    if mi and _bb_val(mi.group(3), mi.group(4), mi.group(5)):
        return 'SKU', mi, 'inline'
    mt = _BB_INLINE_TRUNC_RE.match(line)
    if mt:
        return 'SKU', mt, 'inline_trunc'
    return 'DESC', None, None


def _try_bikebox_pdf(pdf):
    """Detect and parse BikeBox-style PDF purchase orders."""
    try:
        tables = pdf.pages[0].extract_tables()
        if not tables or len(tables[0]) < 3:
            return [], 'bikebox'
        cell = tables[0][2][0]
        if not cell:
            return [], 'bikebox'

        raw = [l.strip() for l in cell.split('\n') if l.strip()]

        # Pre-process: merge split SKU lines and floating qty/price rows
        merged = []
        i = 0
        while i < len(raw):
            line = raw[i]
            # Floating qty/price row (e.g. "Each 2 37.95 75.90") following a DESC
            qp = _BB_QTY_PRICE_RE.match(line)
            if qp and merged:
                prev = merged[-1]
                synthetic = prev + ' ' + line
                mi = _BB_INLINE_RE.match(synthetic)
                if mi and _bb_val(mi.group(3), mi.group(4), mi.group(5)):
                    merged[-1] = synthetic
                    i += 1
                    continue
            # Split SKU: fragment ending in hyphen + next line has SKU suffix
            split_m = re.match(r'^([A-Z][A-Z0-9\-]+-)\s+(.+)$', line)
            if split_m and i + 2 < len(raw):
                n1 = raw[i + 1]
                n1_m = re.match(r'^([A-Z]+)\s+(.*)$', n1)
                if n1_m:
                    full_sku  = split_m.group(1) + n1_m.group(1)
                    full_desc = split_m.group(2) + ' ' + n1_m.group(2)
                    qp2 = _BB_QTY_PRICE_RE.match(raw[i + 2])
                    if qp2:
                        synthetic = f"{full_sku} {full_desc} {raw[i+2]}"
                        mi = _BB_INLINE_RE.match(synthetic)
                        if mi and _bb_val(mi.group(3), mi.group(4), mi.group(5)):
                            merged.append(synthetic)
                            i += 3
                            continue
            merged.append(line)
            i += 1

        # Tag merged lines
        tagged = [(_bb_tag(l) + (l,)) for l in merged]
        # tagged[i] = (tag, match, kind, raw_line)

        sku_indices = [i for i, t in enumerate(tagged) if t[0] == 'SKU']
        if not sku_indices:
            return [], 'bikebox'

        results = []
        for n, si in enumerate(sku_indices):
            tag, m, kind, raw_line = tagged[si]
            next_si = sku_indices[n + 1] if n + 1 < len(sku_indices) else len(tagged)
            prev_si = sku_indices[n - 1] if n > 0 else -1

            between = [tagged[j][3] for j in range(prev_si + 1, si) if tagged[j][0] == 'DESC']
            after   = [tagged[j][3] for j in range(si + 1, next_si) if tagged[j][0] == 'DESC']

            if kind == 'sku_only':
                sku  = m.group(1)
                qty  = float(m.group(2))
                up   = float(m.group(3).replace(',', ''))
                ext  = float(m.group(4).replace(',', ''))
                pre  = between if n == 0 else between[1 if sku_indices[n-1]+1 < si else 0:]
                post = after[:1]
                desc = ' '.join(pre + post)
            else:
                sku          = m.group(1)
                inline_desc  = m.group(2).strip()
                qty          = float(m.group(3))
                up           = float(m.group(4).replace(',', ''))
                if kind == 'inline_trunc':
                    ext = round(qty * up, 2)
                else:
                    ext = float(m.group(5).replace(',', ''))
                post = after[:1]
                desc = inline_desc + (' ' + ' '.join(post) if post else '')

            # BikeBox prices are ex-GST → multiply by 1.1
            up_gst  = round(up * 1.1, 2)
            ext_gst = round(ext * 1.1, 2)

            results.append({
                'supplier_sku':        sku,
                'supplier_desc':       desc.strip(),
                'qty':                 qty,
                'unit_price_inc_gst':  up_gst,
                'extended_inc_gst':    ext_gst,
            })

        return (results, 'bikebox') if results else ([], 'bikebox')

    except Exception as e:
        log.debug(f"BikeBox PDF parse attempt failed: {e}")
        return [], 'bikebox'


# ── BPW format ────────────────────────────────────────────────────────────────
# Pattern: "{qty} x {description} ({sku}) = ${extended_price}"
_BPW_RE = re.compile(
    r'^(\d+(?:\.\d+)?)\s+x\s+'       # qty x
    r'(.+?)\s+'                        # description
    r'\(([^)]+)\)\s*'                  # (sku)
    r'=\s*\$?([\d,]+\.?\d*)\s*$',     # = $price
    re.IGNORECASE)


def _try_bpw(text):
    lines = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        m = _BPW_RE.match(raw)
        if not m:
            return [], 'bpw'  # one non-matching line invalidates BPW
        qty      = float(m.group(1))
        desc     = m.group(2).strip().rstrip(',').strip()
        sku      = m.group(3).strip()
        extended = float(m.group(4).replace(',', ''))
        unit_price = round(extended / qty, 4) if qty else extended
        lines.append({
            'supplier_sku':         sku,
            'supplier_desc':        desc,
            'qty':                  qty,
            'unit_price_inc_gst':   unit_price,
            'extended_inc_gst':     extended,
        })
    return (lines, 'bpw') if lines else ([], 'bpw')


# ── PSI format ────────────────────────────────────────────────────────────────
# Pattern: desc \n\n SKU: xxx \n\n qty \t $extended \n [next desc or total]

_PSI_ITEM_RE = re.compile(
    r'([^\n]+?)\n\n'               # description line + blank line
    r'SKU:\s*(\S+)\s*\n\n'        # SKU: xxx + blank line
    r'(\d+(?:\.\d+)?)\s*\t\s*'    # qty + tab
    r'\$?([\d,]+\.?\d*)',          # $price
    re.IGNORECASE)


def _try_psi(text):
    """Detect PSI format by presence of SKU: pattern."""
    if 'SKU:' not in text:
        return [], 'psi'

    lines = []
    for m in _PSI_ITEM_RE.finditer(text):
        desc     = m.group(1).strip()
        sku      = m.group(2).strip()
        qty      = float(m.group(3))
        extended = float(m.group(4).replace(',', ''))
        # Skip header/summary rows
        if desc.lower() in ('items', 'qty', 'price (inc gst)', 'subtotal',
                             'grand total', 'total'):
            continue
        unit_price = round(extended / qty, 4) if qty else extended
        lines.append({
            'supplier_sku':        sku,
            'supplier_desc':       desc,
            'qty':                 qty,
            'unit_price_inc_gst':  unit_price,
            'extended_inc_gst':    extended,
        })

    return (lines, 'psi') if lines else ([], 'psi')


# ── CSV format ────────────────────────────────────────────────────────────────
# Comma or tab separated; tries to find qty, description, price columns by header

_CSV_DESC_HEADERS  = {'description', 'desc', 'item', 'name', 'product',
                       'part', 'part name', 'part description'}
_CSV_QTY_HEADERS   = {'qty', 'quantity', 'q', 'units', 'ordered'}
_CSV_PRICE_HEADERS = {'price', 'unit price', 'unit cost', 'cost',
                       'price (inc gst)', 'price inc gst', 'unit_cost',
                       'unit_price', 'amount'}
_CSV_SKU_HEADERS   = {'sku', 'part number', 'part_number', 'part#',
                       'code', 'item code', 'product code'}


def _try_csv(text):
    """Try to parse as CSV/TSV with headers."""
    # Must have at least one comma or tab
    if ',' not in text and '\t' not in text:
        return [], 'csv'

    dialect = 'excel-tab' if text.count('\t') > text.count(',') else 'excel'
    try:
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        rows = list(reader)
        if not rows:
            return [], 'csv'
    except Exception:
        return [], 'csv'

    # Map headers
    headers = {k.strip().lower(): k for k in rows[0].keys()}

    desc_key  = next((headers[h] for h in headers if h in _CSV_DESC_HEADERS), None)
    qty_key   = next((headers[h] for h in headers if h in _CSV_QTY_HEADERS), None)
    price_key = next((headers[h] for h in headers if h in _CSV_PRICE_HEADERS), None)
    sku_key   = next((headers[h] for h in headers if h in _CSV_SKU_HEADERS), None)

    if not (desc_key or qty_key or price_key):
        return [], 'csv'

    lines = []
    for row in rows:
        desc  = row.get(desc_key, '').strip() if desc_key else ''
        sku   = row.get(sku_key, '').strip()  if sku_key  else ''
        try:
            qty = float(re.sub(r'[^\d.]', '', row.get(qty_key, '1') or '1') or 1)
        except Exception:
            qty = 1
        try:
            raw_price = row.get(price_key, '0') or '0'
            price = float(re.sub(r'[^\d.]', '', raw_price) or 0)
        except Exception:
            price = 0
        if not desc and not sku:
            continue
        lines.append({
            'supplier_sku':        sku,
            'supplier_desc':       desc or sku,
            'qty':                 qty,
            'unit_price_inc_gst':  price,
            'extended_inc_gst':    round(price * qty, 2),
        })

    return (lines, 'csv') if lines else ([], 'csv')


# ── Freeform ──────────────────────────────────────────────────────────────────
# Best-effort: find lines with a quantity, description, and price

_FREE_LINE_RE = re.compile(
    r'^'
    r'(?:(\d+(?:\.\d+)?)\s*[xX@]\s*)?'   # optional: qty x or qty @
    r'(.+?)\s*'                             # description
    r'(?:\$|AUD)?\s*([\d,]+\.\d{2})'      # price with decimal
    r'\s*$'
)


def _try_freeform(text):
    lines = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw or raw.startswith('#'):
            continue
        # Skip lines that look like totals/headers
        lower = raw.lower()
        if any(kw in lower for kw in ('total', 'subtotal', 'shipping', 'freight',
                                       'gst', 'tax', 'surcharge', 'grand', 'credit')):
            continue
        m = _FREE_LINE_RE.match(raw)
        if m:
            qty_s, desc, price_s = m.group(1), m.group(2), m.group(3)
            qty   = float(qty_s) if qty_s else 1.0
            price = float(price_s.replace(',', ''))
            desc  = desc.strip().rstrip(',').strip()
            # Assume price is extended if qty > 1 and = sign present
            unit_price = round(price / qty, 4) if qty > 1 else price
            lines.append({
                'supplier_sku':        '',
                'supplier_desc':       desc,
                'qty':                 qty,
                'unit_price_inc_gst':  unit_price,
                'extended_inc_gst':    price,
            })
    return (lines, 'freeform') if lines else ([], 'freeform')


# ── BikeCorp (Bicycle Corporation) PDF format ─────────────────────────────────
# Tax invoice with structured table. Columns detected by header name.
# Net Price Excl. GST × 1.1 = price_inc_gst.
# Handles both 13-col and 11-col layouts, multi-page invoices.

_BC_SKIP_PREFIXES = ('***', 'delivery', 'for pick', 'direct deposit',
                     'bsb', 'account no', 'payment ref', 'invoices dated',
                     'please send', 'please ensure', 'any disputes')


def _try_bikecorp_pdf(pdf):
    """Detect and parse BikeCorp (Bicycle Corporation) tax invoice PDFs."""
    results = []
    found_header = False

    try:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue
                # Find the item header row
                hdr_idx = None
                for ri, row in enumerate(table):
                    cells = [str(c or '').strip().lower() for c in row]
                    flat = ' '.join(cells)
                    if 'item code' in flat and 'item description' in flat:
                        hdr_idx = ri
                        break
                if hdr_idx is None:
                    continue

                found_header = True
                hdr = [str(c or '').strip() for c in table[hdr_idx]]

                # Map columns by header content
                def find_col(keywords):
                    for i, h in enumerate(hdr):
                        hl = h.lower().replace('\n', ' ')
                        if all(k in hl for k in keywords):
                            return i
                    return None

                sku_col  = find_col(['item code']) or 0
                desc_col = find_col(['item description']) or 1
                qty_col  = find_col(['order', 'qty'])
                net_col  = find_col(['net price']) or find_col(['excl'])

                if qty_col is None or net_col is None:
                    continue

                for row in table[hdr_idx + 1:]:
                    cells = [str(c or '').strip() for c in row]
                    if len(cells) <= max(sku_col, desc_col, qty_col, net_col):
                        continue

                    sku  = cells[sku_col]
                    desc = cells[desc_col]
                    qty_s = cells[qty_col]
                    net_s = cells[net_col]

                    # Skip blank, footer, and annotation rows
                    if not sku and not desc:
                        continue
                    low = (sku + ' ' + desc).lower().strip()
                    if not low or any(low.startswith(p) for p in _BC_SKIP_PREFIXES):
                        continue
                    if not sku or sku.startswith('*'):
                        continue

                    try:
                        qty = float(qty_s.replace(',', '')) if qty_s else 0
                        net = float(net_s.replace(',', '')) if net_s else 0
                    except ValueError:
                        continue

                    if qty <= 0 or net <= 0:
                        continue

                    price_inc = round(net * 1.1, 2)
                    ext_inc   = round(price_inc * qty, 2)

                    results.append({
                        'supplier_sku':        sku,
                        'supplier_desc':       desc,
                        'qty':                 qty,
                        'unit_price_inc_gst':  price_inc,
                        'extended_inc_gst':    ext_inc,
                    })

        if found_header and results:
            return results, 'bikecorp'
        return [], 'bikecorp'

    except Exception as e:
        log.debug(f"BikeCorp PDF parse attempt failed: {e}")
        return [], 'bikecorp'
