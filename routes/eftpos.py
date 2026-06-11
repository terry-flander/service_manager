"""
EFTPOS transaction reconciliation.
Routes:
  GET/POST /eftpos/import          — upload CSV, preview, confirm import
  GET      /eftpos/reconcile       — interactive reconciliation grid
  POST     /eftpos/reconcile/<id>  — record a reconciliation match
  POST     /eftpos/unmatch/<id>    — unlink a reconciliation
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from models import get_db
from datetime import datetime
import csv, io

eftpos_bp = Blueprint('eftpos', __name__)


def _parse_txn_date(dt_str):
    """Parse '30 Apr 2026 16:49:40' → ('2026-04-30', '30 Apr 2026 16:49:40')"""
    if not dt_str:
        return None, None
    for fmt in ('%d %b %Y %H:%M:%S', '%d/%m/%Y %H:%M:%S', '%d/%m/%Y'):
        try:
            dt = datetime.strptime(dt_str.strip(), fmt)
            return dt.strftime('%Y-%m-%d'), dt_str.strip()
        except ValueError:
            continue
    return None, dt_str.strip()


def _parse_csv(file_stream):
    """Parse uploaded CSV, return (rows, skipped_count, errors)."""
    text = file_stream.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    rows, skipped = [], 0
    errors = []
    for i, row in enumerate(reader, 1):
        status = (row.get('Transaction Status') or '').strip()
        if status != 'Approved':
            skipped += 1
            continue
        ref = (row.get('Reference Number') or '').strip()
        if not ref:
            errors.append(f"Row {i}: missing Reference Number")
            continue
        date_str = (row.get('Transaction Date') or '').strip()
        txn_date, txn_datetime = _parse_txn_date(date_str)
        try:
            amount = float((row.get('Amount') or '0').replace(',', ''))
        except ValueError:
            errors.append(f"Row {i}: invalid Amount")
            continue
        try:
            total_amount = float((row.get('Total Amount') or '0').replace(',', ''))
        except ValueError:
            total_amount = amount
        try:
            surcharge = float((row.get('Surcharge') or '0').replace(',', ''))
        except ValueError:
            surcharge = 0.0
        try:
            settlement_amount = float((row.get('Settlement Amount') or '0').replace(',', ''))
        except ValueError:
            settlement_amount = None

        # Parse settlement date
        settle_date_str = (row.get('Settlement Date') or '').strip()
        settle_date, _ = _parse_txn_date(settle_date_str)

        rows.append({
            'reference_number':     ref,
            'rrn':                  (row.get('RRN') or '').strip(),
            'transaction_datetime': txn_datetime,
            'transaction_date':     txn_date,
            'method':               (row.get('Method') or '').strip(),
            'amount':               amount,
            'total_amount':         total_amount,
            'surcharge':            surcharge,
            'terminal_id':          (row.get('Terminal ID') or '').strip(),
            'card_number':          (row.get('Card Number') or '').strip(),
            'transaction_status':   status,
            'pay_status':           (row.get('Pay Status') or '').strip(),
            'settlement_date':      settle_date,
            'settlement_amount':    settlement_amount,
        })
    return rows, skipped, errors


@eftpos_bp.route('/eftpos/import', methods=['GET', 'POST'])
def import_csv():
    if request.method == 'POST':
        f = request.files.get('csv_file')
        if not f or not f.filename:
            flash('No file selected.', 'danger')
            return redirect(url_for('eftpos.import_csv'))

        rows, skipped, errors = _parse_csv(f)

        with get_db() as conn:
            imported, dupes = 0, 0
            for r in rows:
                existing = conn.execute(
                    "SELECT id FROM eftpos_transactions WHERE reference_number=?",
                    (r['reference_number'],)).fetchone()
                if existing:
                    dupes += 1
                    continue
                conn.execute("""
                    INSERT INTO eftpos_transactions
                        (reference_number, rrn, transaction_datetime, transaction_date,
                         method, amount, total_amount, surcharge, terminal_id, card_number,
                         transaction_status, pay_status, settlement_date, settlement_amount)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (r['reference_number'], r['rrn'], r['transaction_datetime'],
                      r['transaction_date'], r['method'], r['amount'], r['total_amount'],
                      r['surcharge'], r['terminal_id'], r['card_number'],
                      r['transaction_status'], r['pay_status'],
                      r['settlement_date'], r['settlement_amount']))
                imported += 1
            conn.commit()

        parts = [f"{imported} imported"]
        if dupes:    parts.append(f"{dupes} duplicate{'s' if dupes!=1 else ''} skipped")
        if skipped:  parts.append(f"{skipped} declined skipped")
        if errors:   parts.append(f"{len(errors)} error{'s' if len(errors)!=1 else ''}")
        flash(', '.join(parts) + '.', 'success' if not errors else 'danger')
        return redirect(url_for('eftpos.reconcile'))

    return render_template('eftpos/import.html')


TERMINAL_MAP = {
    '47006970': {'label': 'Booking',  'job_types': ('booking',)},
    '47014210': {'label': 'Workshop', 'job_types': ('workshop', 'sale')},
}

def _terminal_label(terminal_id):
    return TERMINAL_MAP.get(terminal_id, {}).get('label', terminal_id or '—')


def _candidate_query(conn, txn_date, amount, limit=10):
    """
    Return candidate jobs for a transaction.
    All job types included — a booking may be paid at the workshop terminal.
    Date window: paid_date within txn_date -1 week to +4 weeks.
    Exact match requires same date AND same amount.
    Already-reconciled paid jobs are included (for split payments) with is_reconciled=1.
    """
    rows = conn.execute("""
        SELECT j.id, j.reference, j.customer_name, j.paid_date,
               j.amount_paid, j.payment_type, j.status, j.job_type,
               CASE WHEN j.reconciled_eftpos IS NOT NULL THEN 1 ELSE 0 END as is_reconciled,
               j.reconciled_eftpos,
               CASE
                 WHEN j.paid_date = ? AND ABS(j.amount_paid - ?) < 0.01 THEN 'exact'
                 WHEN j.paid_date = ?                                    THEN 'date_match'
                 WHEN ABS(j.amount_paid - ?) < 0.01                     THEN 'amount_match'
                 ELSE 'near'
               END as match_type
        FROM jobs j
        WHERE j.status = 'paid'
          AND j.payment_type IN ('EFTPOS','VISA','MASTERCARD','AMEX')
          AND j.paid_date BETWEEN date(?, '-7 days') AND date(?, '+28 days')
        ORDER BY
          j.reconciled_eftpos IS NOT NULL ASC,   -- unreconciled first
          CASE WHEN j.paid_date = ? AND ABS(j.amount_paid - ?) < 0.01 THEN 0
               WHEN j.paid_date = ?                                    THEN 1
               WHEN ABS(j.amount_paid - ?) < 0.01                     THEN 2
               ELSE 3 END,
          ABS(julianday(j.paid_date) - julianday(?)) ASC,
          j.paid_date ASC
        LIMIT ?
    """, (txn_date, amount,
          txn_date,
          amount,
          txn_date, txn_date,
          txn_date, amount,
          txn_date,
          amount,
          txn_date,
          limit)).fetchall()
    return [dict(r) for r in rows]


@eftpos_bp.route('/eftpos/reconcile')
def reconcile():
    import json as _json
    user_id   = session.get('user_id')
    PREFS_KEY = f'eftpos_recon_{user_id}'

    # Detect whether the filter form was submitted
    # Use a sentinel param so unchecked checkbox is distinguishable from first load
    form_submitted = 'submitted' in request.args

    if form_submitted:
        date_from         = request.args.get('date_from', '')
        date_to           = request.args.get('date_to',   '')
        # Checkbox: present = '1', absent = unchecked = False
        unreconciled_only = request.args.get('unreconciled_only', '') == '1'
        # Save prefs
        with get_db() as conn:
            conn.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (PREFS_KEY, _json.dumps({
                    'date_from': date_from, 'date_to': date_to,
                    'unreconciled_only': unreconciled_only})))
            conn.commit()
    else:
        # Restore saved prefs, or sensible defaults
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (PREFS_KEY,)).fetchone()
        if row:
            try:
                prefs = _json.loads(row['value'])
                date_from         = prefs.get('date_from', '')
                date_to           = prefs.get('date_to',   '')
                unreconciled_only = prefs.get('unreconciled_only', True)
            except Exception:
                date_from, date_to, unreconciled_only = '', '', True
        else:
            date_from, date_to, unreconciled_only = '', '', True

    with get_db() as conn:
        # Combined query: all transactions in range, filtered by unreconciled_only
        where, params = ["et.transaction_date IS NOT NULL"], []
        if date_from:
            where.append("et.transaction_date >= ?"); params.append(date_from)
        if date_to:
            where.append("et.transaction_date <= ?"); params.append(date_to)
        if unreconciled_only:
            where.append("et.reconciled_at IS NULL")

        txns = [dict(r) for r in conn.execute(f"""
            SELECT et.*
            FROM eftpos_transactions et
            WHERE {' AND '.join(where)}
            ORDER BY et.transaction_date ASC, et.transaction_datetime ASC
        """, params).fetchall()]

        # Candidate jobs for each unreconciled transaction
        candidates = {}
        for txn in txns:
            if txn['reconciled_at']:
                continue  # reconciled — no candidates needed
            candidates[txn['reference_number']] = _candidate_query(
                conn, txn['transaction_date'], txn['amount'], limit=10)

        # Build reconciled_jobs lookup: ref_number → list of matched jobs
        reconciled_jobs = {}
        for txn in txns:
            if txn['reconciled_at']:
                jobs = conn.execute("""
                    SELECT id, reference, customer_name FROM jobs
                    WHERE reconciled_eftpos = ?
                """, (txn['reference_number'],)).fetchall()
                reconciled_jobs[txn['reference_number']] = [dict(j) for j in jobs]

    return render_template('eftpos/reconcile.html',
                           txns=txns, candidates=candidates,
                           reconciled_jobs=reconciled_jobs,
                           date_from=date_from, date_to=date_to,
                           unreconciled_only=unreconciled_only,
                           terminal_label=_terminal_label)


@eftpos_bp.route('/eftpos/candidates')
def candidates():
    """AJAX: job candidates for a transaction."""
    ref = request.args.get('ref', '')
    with get_db() as conn:
        txn = conn.execute(
            "SELECT * FROM eftpos_transactions WHERE reference_number=?", (ref,)
        ).fetchone()
        if not txn:
            return jsonify([])
        rows = _candidate_query(conn, txn['transaction_date'], txn['amount'], limit=20)
    return jsonify(rows)


@eftpos_bp.route('/eftpos/match', methods=['POST'])
def match():
    """Link a transaction to one or more jobs and mark all as reconciled."""
    txn_id  = request.form.get('txn_id', type=int)
    job_ids = request.form.getlist('job_id[]', type=int) or \
              ([request.form.get('job_id', type=int)] if request.form.get('job_id') else [])
    user_id = session.get('user_id')

    if not txn_id or not job_ids:
        return jsonify({'ok': False, 'error': 'Missing txn_id or job_id'}), 400

    new_paid_date   = request.form.get('paid_date',   '').strip() or None
    new_amount_paid = request.form.get('amount_paid', '').strip()
    new_amount_paid = float(new_amount_paid) if new_amount_paid else None

    with get_db() as conn:
        txn = conn.execute(
            "SELECT * FROM eftpos_transactions WHERE id=?", (txn_id,)).fetchone()
        if not txn:
            return jsonify({'ok': False, 'error': 'Transaction not found'}), 404

        now = datetime.utcnow().isoformat()
        conn.execute("""
            UPDATE eftpos_transactions
            SET job_id=?, reconciled_at=?, reconciled_by=?
            WHERE id=?
        """, (job_ids[0], now, user_id, txn_id))

        for job_id in job_ids:
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                continue
            updates, up_params = [], []
            if new_paid_date:
                updates.append("paid_date=?"); up_params.append(new_paid_date)
            if new_amount_paid is not None:
                updates.append("amount_paid=?"); up_params.append(new_amount_paid)
            # Only set reconciled_eftpos if not already set (split payment — job may
            # already be reconciled against a different transaction)
            if not job['reconciled_eftpos']:
                updates.append("reconciled_eftpos=?")
                up_params.append(txn['reference_number'])
            if updates:
                up_params.append(job_id)
                conn.execute(f"UPDATE jobs SET {', '.join(updates)} WHERE id=?", up_params)

        conn.commit()

    return jsonify({'ok': True, 'job_count': len(job_ids)})


@eftpos_bp.route('/eftpos/unmatch', methods=['POST'])
def unmatch():
    """Unlink a transaction from all its matched jobs."""
    txn_id  = request.form.get('txn_id', type=int)
    with get_db() as conn:
        txn = conn.execute(
            "SELECT * FROM eftpos_transactions WHERE id=?", (txn_id,)).fetchone()
        if not txn:
            return jsonify({'ok': False, 'error': 'Not found'}), 404
        # Clear reconciliation on ALL jobs sharing this reference number
        conn.execute(
            "UPDATE jobs SET reconciled_eftpos=NULL WHERE reconciled_eftpos=?",
            (txn['reference_number'],))
        conn.execute("""
            UPDATE eftpos_transactions
            SET job_id=NULL, reconciled_at=NULL, reconciled_by=NULL
            WHERE id=?
        """, (txn_id,))
        conn.commit()
    return jsonify({'ok': True})

