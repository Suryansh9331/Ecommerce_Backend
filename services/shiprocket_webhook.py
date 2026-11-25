"""
Shiprocket webhook handler.

Shiprocket POSTs status updates to this endpoint whenever a shipment progresses
through their system. The handler:

1. Verifies the shared-secret token (configured as SHIPROCKET_WEBHOOK_TOKEN).
2. Looks up the local Shipment by AWB (forward) or reverse AWB (return trip).
3. Maps Shiprocket's status string to our ShipmentStatusEnum.
4. Updates Shipment, propagates milestone statuses onto the Order,
   appends an audit row to OrderStatusHistory.

Separated from the HTTP route so it's trivially unit-testable with no Flask
request plumbing.
"""
from datetime import datetime, timezone

from common.database import db
from models.enums import ShipmentStatusEnum, OrderStatusEnum
from models.order import Order, OrderStatusHistory
from models.shipment import Shipment


# Shiprocket's `current_status` string -> our ShipmentStatusEnum
_SHIPMENT_STATUS_MAP = {
    "NEW": ShipmentStatusEnum.PENDING_PICKUP,
    "LABEL_GENERATED": ShipmentStatusEnum.LABEL_CREATED,
    "LABEL CREATED": ShipmentStatusEnum.LABEL_CREATED,
    "PICKUP SCHEDULED": ShipmentStatusEnum.LABEL_CREATED,
    "PICKUP GENERATED": ShipmentStatusEnum.LABEL_CREATED,
    "PICKUP QUEUED": ShipmentStatusEnum.LABEL_CREATED,
    "SHIPPED": ShipmentStatusEnum.IN_TRANSIT,
    "IN TRANSIT": ShipmentStatusEnum.IN_TRANSIT,
    "OUT FOR DELIVERY": ShipmentStatusEnum.OUT_FOR_DELIVERY,
    "DELIVERED": ShipmentStatusEnum.DELIVERED,
    "DELIVERY ATTEMPTED": ShipmentStatusEnum.DELIVERY_ATTEMPTED,
    "UNDELIVERED": ShipmentStatusEnum.DELIVERY_ATTEMPTED,
    "RTO INITIATED": ShipmentStatusEnum.RETURN_TO_SENDER,
    "RTO IN TRANSIT": ShipmentStatusEnum.RETURN_TO_SENDER,
    "RTO DELIVERED": ShipmentStatusEnum.RETURN_TO_SENDER,
    "CANCELLED": ShipmentStatusEnum.CANCELLED,
    "CANCELED": ShipmentStatusEnum.CANCELLED,
    "LOST": ShipmentStatusEnum.EXCEPTION,
    "DAMAGED": ShipmentStatusEnum.EXCEPTION,
    "EXCEPTION": ShipmentStatusEnum.EXCEPTION,
}


# Shipment-status milestones that should also move the Order forward.
# Anything not listed leaves the order untouched (e.g., EXCEPTION on a shipment
# shouldn't flip the order to a failure state without human review).
_ORDER_STATUS_PROPAGATION = {
    ShipmentStatusEnum.IN_TRANSIT: OrderStatusEnum.IN_TRANSIT,
    ShipmentStatusEnum.OUT_FOR_DELIVERY: OrderStatusEnum.OUT_FOR_DELIVERY,
    ShipmentStatusEnum.DELIVERED: OrderStatusEnum.DELIVERED,
}


class WebhookError(Exception):
    """Raised for malformed or unauthenticated webhook payloads."""

    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.http_status = http_status


def verify_token(received_token, expected_token):
    """Constant-time comparison to avoid timing attacks when checking secrets."""
    if not expected_token:
        # If no token is configured, we refuse ALL webhooks rather than
        # silently accepting them. Operator must set SHIPROCKET_WEBHOOK_TOKEN.
        raise WebhookError("Webhook not configured on server", http_status=503)
    if not received_token:
        raise WebhookError("Missing webhook token header", http_status=401)
    # Simple constant-time compare
    if len(received_token) != len(expected_token):
        raise WebhookError("Invalid webhook token", http_status=401)
    mismatch = 0
    for a, b in zip(received_token, expected_token):
        mismatch |= ord(a) ^ ord(b)
    if mismatch != 0:
        raise WebhookError("Invalid webhook token", http_status=401)
    return True


def _find_shipment(payload):
    """
    Locate the Shipment this webhook is about. Shiprocket sends `awb`
    (forward trip) for most updates; on returns it sends the reverse AWB. We
    look up by either.
    """
    awb = (payload.get("awb") or "").strip()
    if not awb:
        raise WebhookError("Missing 'awb' in webhook payload")

    shipment = Shipment.query.filter_by(awb_code=awb).first()
    if shipment is None:
        shipment = Shipment.query.filter_by(reverse_awb_code=awb).first()
    if shipment is None:
        raise WebhookError(
            f"No shipment found for AWB {awb}", http_status=404
        )
    return shipment


def _map_status(sr_status):
    """Shiprocket status string -> our ShipmentStatusEnum. Returns None if unknown."""
    if not sr_status:
        return None
    return _SHIPMENT_STATUS_MAP.get(sr_status.strip().upper())


def process_shiprocket_webhook(payload, received_token, expected_token,
                               change_logger_user_id=None):
    """
    Pure-function webhook processor. Returns a dict describing what changed.
    Raises WebhookError for auth / lookup problems.

    `change_logger_user_id`: optional; stamped into OrderStatusHistory as the
    actor. None means "system-triggered by webhook".
    """
    verify_token(received_token, expected_token)

    if not isinstance(payload, dict):
        raise WebhookError("Webhook payload must be a JSON object")

    shipment = _find_shipment(payload)
    old_shipment_status = shipment.shipment_status

    sr_status_raw = payload.get("current_status") or payload.get("shipment_status")
    new_shipment_status = _map_status(sr_status_raw)

    now = datetime.now(timezone.utc)
    shipment.sr_status_last_synced_at = now
    shipment.sr_last_synced_status = sr_status_raw

    changes = {
        "awb": payload.get("awb"),
        "sr_status": sr_status_raw,
        "shipment_status_changed": False,
        "order_status_changed": False,
    }

    if new_shipment_status and new_shipment_status != old_shipment_status:
        shipment.shipment_status = new_shipment_status
        changes["shipment_status_changed"] = True
        changes["old_shipment_status"] = old_shipment_status.value
        changes["new_shipment_status"] = new_shipment_status.value

        # Fill delivery timestamp on first DELIVERED transition
        if new_shipment_status == ShipmentStatusEnum.DELIVERED and not shipment.actual_delivery_date:
            shipment.actual_delivery_date = now

        # Propagate to Order if this is a milestone
        order_target = _ORDER_STATUS_PROPAGATION.get(new_shipment_status)
        if order_target and shipment.order_id:
            order = Order.query.filter_by(order_id=shipment.order_id).first()
            if order and order.order_status != order_target:
                prev_order_status = order.order_status
                order.order_status = order_target
                db.session.add(OrderStatusHistory(
                    order_id=order.order_id,
                    status=order_target,
                    changed_by_user_id=change_logger_user_id,
                    changed_at=now,
                    notes=(
                        f"Shiprocket webhook: {sr_status_raw} "
                        f"(from {prev_order_status.value if prev_order_status else '-'})"
                    ),
                ))
                changes["order_status_changed"] = True
                changes["old_order_status"] = prev_order_status.value if prev_order_status else None
                changes["new_order_status"] = order_target.value

    db.session.commit()
    return changes
