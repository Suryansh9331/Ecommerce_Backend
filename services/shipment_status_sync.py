"""
Shipment status polling — safety net for missed Shiprocket webhooks.

Runs every 15 minutes (via APScheduler in app.py). Finds non-terminal shipments
that either (a) have no recent sync timestamp or (b) haven't moved in a while,
and polls Shiprocket's tracking endpoint to refresh their status.

Idempotent — skips terminal shipments (DELIVERED / CANCELLED) so we don't
keep hitting SR for closed ones.
"""
from datetime import datetime, timedelta, timezone

from common.database import db
from models.enums import ShipmentStatusEnum, OrderStatusEnum
from models.order import Order, OrderStatusHistory
from models.shipment import Shipment


# Statuses we stop polling once reached.
_TERMINAL_STATUSES = {
    ShipmentStatusEnum.DELIVERED,
    ShipmentStatusEnum.CANCELLED,
    ShipmentStatusEnum.RETURN_TO_SENDER,
}

# How stale a sync has to be before we poll again. 30 min is a reasonable
# safety net — Shiprocket webhooks usually arrive within seconds; if nothing
# has come for half an hour, something's off.
_STALE_AFTER_MINUTES = 30

# Hard cap on how many shipments we poll per run — protects SR from a flood if
# the system has thousands of pending shipments after a long outage.
_MAX_PER_RUN = 50


def _find_stale_shipments(now):
    """Return shipments eligible for polling, ordered by oldest sync first."""
    stale_cutoff = now - timedelta(minutes=_STALE_AFTER_MINUTES)
    return (
        Shipment.query
        .filter(Shipment.shiprocket_shipment_id.isnot(None))
        .filter(~Shipment.shipment_status.in_(_TERMINAL_STATUSES))
        .filter(
            db.or_(
                Shipment.sr_status_last_synced_at.is_(None),
                Shipment.sr_status_last_synced_at < stale_cutoff,
            )
        )
        .order_by(Shipment.sr_status_last_synced_at.asc().nullsfirst())
        .limit(_MAX_PER_RUN)
        .all()
    )


def _apply_sr_status(shipment, sr_response, now, logger=None):
    """
    Apply a Shiprocket tracking response to a Shipment row.

    Returns a short string describing what happened (for logging).
    """
    # Reuse the webhook's status map so both paths behave identically.
    from services.shiprocket_webhook import (
        _SHIPMENT_STATUS_MAP,
        _ORDER_STATUS_PROPAGATION,
    )

    # SR tracking responses vary — try the common shapes.
    raw_status = None
    track = sr_response.get("tracking_data") or {}
    if isinstance(track, dict):
        raw_status = (
            (track.get("shipment_track", [{}])[0] or {}).get("current_status")
            or track.get("track_status")
        )
    if not raw_status:
        raw_status = sr_response.get("current_status") or sr_response.get("status")

    shipment.sr_status_last_synced_at = now
    shipment.sr_last_synced_status = raw_status

    if not raw_status:
        return "no_status_in_response"

    mapped = _SHIPMENT_STATUS_MAP.get(raw_status.strip().upper())
    if not mapped:
        return f"unknown_sr_status:{raw_status}"

    if mapped == shipment.shipment_status:
        return "no_change"

    prev = shipment.shipment_status
    shipment.shipment_status = mapped
    if mapped == ShipmentStatusEnum.DELIVERED and not shipment.actual_delivery_date:
        shipment.actual_delivery_date = now

    # Propagate to order status if this is a milestone
    order_target = _ORDER_STATUS_PROPAGATION.get(mapped)
    if order_target and shipment.order_id:
        order = Order.query.filter_by(order_id=shipment.order_id).first()
        if order and order.order_status != order_target:
            prev_order = order.order_status
            order.order_status = order_target
            db.session.add(OrderStatusHistory(
                order_id=order.order_id,
                status=order_target,
                changed_at=now,
                notes=(
                    f"Shiprocket status poll: {raw_status} "
                    f"(from {prev_order.value if prev_order else '-'})"
                ),
            ))

    return f"changed:{prev.value}->{mapped.value}"


def sync_pending_shipment_statuses(logger=None):
    """
    Main entry point called by the scheduler. Polls SR for stale shipments.

    Returns a dict summary: {total, updated, unchanged, errors, details}.
    """
    # Import inside the function so the scheduler can import this module at
    # startup without yet having Flask / SR config loaded.
    from controllers.shiprocket_controller import ShipRocketController

    now = datetime.now(timezone.utc)
    shiprocket = ShipRocketController()

    stale = _find_stale_shipments(now)
    summary = {"total": len(stale), "updated": 0, "unchanged": 0, "errors": 0, "details": []}

    for shipment in stale:
        try:
            response = shiprocket._make_request(
                "GET",
                f"courier/track/shipment/{shipment.shiprocket_shipment_id}",
            )
            outcome = _apply_sr_status(shipment, response, now, logger=logger)
            db.session.commit()
            if outcome.startswith("changed:"):
                summary["updated"] += 1
            else:
                summary["unchanged"] += 1
            summary["details"].append({
                "shipment_id": shipment.shipment_id,
                "awb": shipment.awb_code,
                "outcome": outcome,
            })
        except Exception as e:
            db.session.rollback()
            summary["errors"] += 1
            # Store the error on the row so operators can see persistent failures.
            try:
                shipment.sr_error_message = (
                    f"status_sync: {str(e)}"[:65535]
                )
                shipment.sr_status_last_synced_at = now
                db.session.commit()
            except Exception:
                db.session.rollback()
            if logger:
                logger.warning(
                    f"Status sync failed for shipment {shipment.shipment_id} "
                    f"(AWB {shipment.awb_code}): {e}"
                )

    if logger and summary["total"] > 0:
        logger.info(
            "Shipment status sync: "
            f"total={summary['total']} updated={summary['updated']} "
            f"unchanged={summary['unchanged']} errors={summary['errors']}"
        )
    return summary
