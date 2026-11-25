"""
Order fulfillment SLA breach detector.

Flips `orders.sla_breached` to True when a merchant has accepted an order
(accepted_at stamped) but hasn't marked RTS within the SLA window. The default
window is 48 hours — Meesho uses the same for standard merchants, and our
fulfillment process doesn't need more time at the 1k-user scale.

Runs daily via APScheduler (see app.py). Idempotent — orders already flagged
are left alone; flipped orders never un-flip (they're reported in the daily
admin digest and the merchant dashboard badge).
"""
from datetime import datetime, timedelta, timezone

from common.database import db
from models.enums import OrderStatusEnum
from models.order import Order


# SLA window from accepted_at to rts_marked_at. 48 hours is the Meesho norm.
SLA_WINDOW_HOURS = 48

# Statuses we still consider "in-flight" — DELIVERED / cancelled orders are
# past the point where a breach matters.
_IN_FLIGHT_STATUSES = {
    OrderStatusEnum.PENDING_PAYMENT,
    OrderStatusEnum.AWAITING_FULFILLMENT,
    OrderStatusEnum.PROCESSING,
}


def detect_sla_breaches(logger=None, window_hours: int = SLA_WINDOW_HOURS):
    """
    Scan accepted-but-not-RTS'd orders and flip sla_breached where applicable.

    Returns a dict summary: {total_flagged, scanned, cutoff_iso}.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)

    candidates = (
        Order.query
        .filter(Order.accepted_at.isnot(None))
        .filter(Order.rts_marked_at.is_(None))
        .filter(Order.sla_breached.is_(False))
        .filter(Order.accepted_at < cutoff)
        .filter(Order.order_status.in_(_IN_FLIGHT_STATUSES))
        .all()
    )

    flagged = 0
    for order in candidates:
        order.sla_breached = True
        flagged += 1

    if flagged:
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            if logger:
                logger.error(f"SLA breach commit failed: {e}", exc_info=True)
            raise

    summary = {
        "scanned": len(candidates),
        "total_flagged": flagged,
        "cutoff_iso": cutoff.isoformat(),
        "window_hours": window_hours,
    }
    if logger and flagged:
        logger.info(
            f"SLA breach scan: flagged {flagged} orders accepted before "
            f"{cutoff.isoformat()} and not RTS'd"
        )
    return summary
