"""
Invoice numbering — FY-aware, sequential, race-safe.

Format: `<prefix>/<FY>/<6-digit-serial>` — e.g. `INV/2026-27/000001`.

Mandated by Rule 46 of the CGST Rules: invoice numbers must be sequential,
unique, and ≤16 characters per FY. Our format is 15 chars for INV, 14 for CN —
both fit.

Race safety: we use `SELECT last_serial FROM invoice_sequence WHERE FY = ? FOR
UPDATE`, increment, UPDATE, then INSERT the Invoice row — all in one
transaction. Concurrent workers block briefly instead of handing out duplicates.
"""
from datetime import date
from typing import Tuple

from sqlalchemy.exc import IntegrityError

from common.database import db
from models.invoice import Invoice, InvoiceSequence


_SERIAL_PAD = 6     # 6 digits = 999,999 invoices per FY. Plenty for our scale.
_DEFAULT_PREFIX = "INV"


def current_financial_year(today=None) -> str:
    """
    India's financial year runs 1 Apr → 31 Mar. A date in Jan-Mar belongs to
    the FY that STARTED the previous year.

    Returns `YYYY-YY` (e.g. `2026-27` for any date between 1 Apr 2026 and
    31 Mar 2027 inclusive).
    """
    today = today or date.today()
    if today.month >= 4:
        start_year = today.year
    else:
        start_year = today.year - 1
    end_year = (start_year + 1) % 100
    return f"{start_year}-{end_year:02d}"


def format_invoice_number(prefix: str, fy: str, serial: int) -> str:
    return f"{prefix}/{fy}/{serial:0{_SERIAL_PAD}d}"


def next_number(prefix: str = _DEFAULT_PREFIX, fy: str = None) -> Tuple[str, str, int]:
    """
    Allocate the next invoice number atomically.

    Returns `(invoice_number, financial_year, serial)`.

    Race-safe: uses SELECT ... FOR UPDATE inside the transaction. Concurrent
    callers block on the row lock until the first commits.

    DOES NOT commit — caller must commit after also inserting the Invoice row
    so the pair is atomic. If the caller rolls back, the serial is NOT
    consumed (because the UPDATE rolls back too).
    """
    fy = fy or current_financial_year()

    # Lock the FY row. If it doesn't exist yet, create it under the lock.
    seq = (
        db.session.query(InvoiceSequence)
        .filter_by(financial_year=fy)
        .with_for_update()
        .first()
    )
    if seq is None:
        # Try to insert a seed row. On concurrent creates, catch IntegrityError
        # and reload under the lock.
        try:
            seq = InvoiceSequence(financial_year=fy, last_serial=0)
            db.session.add(seq)
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            seq = (
                db.session.query(InvoiceSequence)
                .filter_by(financial_year=fy)
                .with_for_update()
                .first()
            )

    seq.last_serial = (seq.last_serial or 0) + 1
    db.session.flush()

    serial = seq.last_serial
    return format_invoice_number(prefix, fy, serial), fy, serial


def issue_invoice(
    order_id,
    merchant_id,
    taxable_value,
    total_gst,
    total_amount,
    prefix: str = _DEFAULT_PREFIX,
) -> Invoice:
    """
    Allocate the next number AND persist the Invoice row in one transaction.

    Returns the committed Invoice. On DB error, rolls back AND leaves the
    sequence untouched (because the sequence UPDATE rolls back with it).
    """
    invoice_number, fy, serial = next_number(prefix=prefix)

    invoice = Invoice(
        invoice_number=invoice_number,
        financial_year=fy,
        serial=serial,
        order_id=order_id,
        merchant_id=merchant_id,
        taxable_value=taxable_value,
        total_gst=total_gst,
        total_amount=total_amount,
    )
    db.session.add(invoice)
    db.session.commit()
    return invoice
