"""
Order status transition rules.

Pre-Phase-2 bug: the PUT /orders/{id}/status endpoint accepted any destination
status, which meant a DELIVERED order could be flipped back to PENDING_PAYMENT
via a stray admin click. This module encodes the valid transitions as a
single-source-of-truth graph so the API, the Shiprocket webhook, and future
automation all share the same rules.

Terminal states (REFUNDED, RETURN_COMPLETED) have no outgoing edges —
nothing moves them forward. Admin overrides go through the explicit
`allow_admin_override` flag, which bypasses the graph.
"""
from models.enums import OrderStatusEnum


# Directed graph of allowed transitions. Keys are the CURRENT state; values
# are the set of states you may transition TO.
_ALLOWED: dict[OrderStatusEnum, set[OrderStatusEnum]] = {
    OrderStatusEnum.PENDING_PAYMENT: {
        OrderStatusEnum.AWAITING_FULFILLMENT,
        OrderStatusEnum.PROCESSING,
        OrderStatusEnum.CANCELLED_BY_CUSTOMER,
        OrderStatusEnum.CANCELLED_BY_MERCHANT,
        OrderStatusEnum.CANCELLED_BY_ADMIN,
    },
    OrderStatusEnum.AWAITING_FULFILLMENT: {
        OrderStatusEnum.PROCESSING,
        OrderStatusEnum.PENDING_SHIPMENT,
        OrderStatusEnum.CANCELLED_BY_CUSTOMER,
        OrderStatusEnum.CANCELLED_BY_MERCHANT,
        OrderStatusEnum.CANCELLED_BY_ADMIN,
    },
    OrderStatusEnum.PROCESSING: {
        OrderStatusEnum.PENDING_SHIPMENT,
        OrderStatusEnum.SHIPPED,
        OrderStatusEnum.CANCELLED_BY_CUSTOMER,
        OrderStatusEnum.CANCELLED_BY_MERCHANT,
        OrderStatusEnum.CANCELLED_BY_ADMIN,
    },
    OrderStatusEnum.PENDING_SHIPMENT: {
        OrderStatusEnum.SHIPPED,
        OrderStatusEnum.CANCELLED_BY_MERCHANT,
        OrderStatusEnum.CANCELLED_BY_ADMIN,
    },
    OrderStatusEnum.SHIPPED: {
        OrderStatusEnum.IN_TRANSIT,
        OrderStatusEnum.OUT_FOR_DELIVERY,
        OrderStatusEnum.DELIVERED,  # Courier may skip IN_TRANSIT update
    },
    OrderStatusEnum.IN_TRANSIT: {
        OrderStatusEnum.OUT_FOR_DELIVERY,
        OrderStatusEnum.DELIVERED,
    },
    OrderStatusEnum.OUT_FOR_DELIVERY: {
        OrderStatusEnum.DELIVERED,
        OrderStatusEnum.IN_TRANSIT,  # Delivery attempt failed, back in transit
    },
    OrderStatusEnum.DELIVERED: {
        OrderStatusEnum.RETURN_REQUESTED,
        OrderStatusEnum.REFUND_REQUESTED,
    },
    OrderStatusEnum.RETURN_REQUESTED: {
        OrderStatusEnum.RETURN_APPROVED,
        OrderStatusEnum.RETURN_REJECTED,
    },
    OrderStatusEnum.RETURN_APPROVED: {
        OrderStatusEnum.RETURN_RECEIVED,
        OrderStatusEnum.RETURN_REJECTED,  # QC fail post-pickup
    },
    OrderStatusEnum.RETURN_RECEIVED: {
        OrderStatusEnum.RETURN_COMPLETED,
        OrderStatusEnum.REFUND_PROCESSING,
    },
    OrderStatusEnum.REFUND_REQUESTED: {
        OrderStatusEnum.REFUND_PROCESSING,
        OrderStatusEnum.RETURN_REJECTED,
    },
    OrderStatusEnum.REFUND_PROCESSING: {
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.PARTIALLY_REFUNDED,
    },
    # Terminal / near-terminal states below — no outgoing edges unless shown.
    OrderStatusEnum.REFUNDED: set(),
    OrderStatusEnum.PARTIALLY_REFUNDED: {
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.RETURN_COMPLETED,
    },
    OrderStatusEnum.RETURN_COMPLETED: set(),
    OrderStatusEnum.RETURN_REJECTED: set(),
    OrderStatusEnum.CANCELLED_BY_CUSTOMER: set(),
    OrderStatusEnum.CANCELLED_BY_MERCHANT: set(),
    OrderStatusEnum.CANCELLED_BY_ADMIN: set(),
}


TERMINAL_STATUSES = {
    s for s, next_set in _ALLOWED.items() if not next_set
}


class IllegalStatusTransition(ValueError):
    """Raised by `validate_transition` when the API tries to make an illegal jump."""


def is_allowed(from_status: OrderStatusEnum, to_status: OrderStatusEnum) -> bool:
    """Does the graph permit from → to? Same-state is always allowed (no-op)."""
    if from_status == to_status:
        return True
    return to_status in _ALLOWED.get(from_status, set())


def validate_transition(from_status, to_status, *, allow_admin_override=False):
    """
    Raise IllegalStatusTransition unless the transition is in the graph.

    `allow_admin_override=True` bypasses the check — used by the superadmin
    status-fix endpoint when an order genuinely needs manual repair (rare).
    The caller is responsible for logging the override in OrderStatusHistory.
    """
    if allow_admin_override:
        return
    if not is_allowed(from_status, to_status):
        raise IllegalStatusTransition(
            f"Cannot move order from {from_status.value} to {to_status.value}. "
            f"Allowed next states: "
            f"{sorted(s.value for s in _ALLOWED.get(from_status, set())) or 'none (terminal)'}"
        )
