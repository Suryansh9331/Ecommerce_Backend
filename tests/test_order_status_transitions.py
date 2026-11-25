"""
Unit tests for order status transition rules.

Core regression: the pre-Phase-2 code allowed any status change via the PUT
endpoint — including DELIVERED → PENDING_PAYMENT. These tests pin down the
graph so future edits can't quietly reintroduce the bug.
"""
import pytest

from models.enums import OrderStatusEnum
from services.order_status_transitions import (
    IllegalStatusTransition,
    is_allowed,
    validate_transition,
    TERMINAL_STATUSES,
)


# --- Happy paths ---

@pytest.mark.unit
@pytest.mark.parametrize("src,dst", [
    (OrderStatusEnum.PENDING_PAYMENT, OrderStatusEnum.PROCESSING),
    (OrderStatusEnum.PROCESSING, OrderStatusEnum.SHIPPED),
    (OrderStatusEnum.SHIPPED, OrderStatusEnum.IN_TRANSIT),
    (OrderStatusEnum.SHIPPED, OrderStatusEnum.DELIVERED),  # courier skipped IN_TRANSIT update
    (OrderStatusEnum.IN_TRANSIT, OrderStatusEnum.OUT_FOR_DELIVERY),
    (OrderStatusEnum.OUT_FOR_DELIVERY, OrderStatusEnum.DELIVERED),
    (OrderStatusEnum.DELIVERED, OrderStatusEnum.RETURN_REQUESTED),
    (OrderStatusEnum.RETURN_REQUESTED, OrderStatusEnum.RETURN_APPROVED),
    (OrderStatusEnum.RETURN_APPROVED, OrderStatusEnum.RETURN_RECEIVED),
    (OrderStatusEnum.REFUND_PROCESSING, OrderStatusEnum.REFUNDED),
])
def test_allowed_transitions(src, dst):
    assert is_allowed(src, dst) is True
    validate_transition(src, dst)  # should not raise


@pytest.mark.unit
def test_same_state_is_a_no_op_allowed():
    assert is_allowed(OrderStatusEnum.DELIVERED, OrderStatusEnum.DELIVERED)
    validate_transition(OrderStatusEnum.DELIVERED, OrderStatusEnum.DELIVERED)


# --- The bug this guards against ---

@pytest.mark.unit
def test_delivered_cannot_move_backwards_to_pending_payment():
    """The exact bug Phase 2 is fixing."""
    assert not is_allowed(OrderStatusEnum.DELIVERED, OrderStatusEnum.PENDING_PAYMENT)
    with pytest.raises(IllegalStatusTransition):
        validate_transition(OrderStatusEnum.DELIVERED, OrderStatusEnum.PENDING_PAYMENT)


@pytest.mark.unit
def test_shipped_cannot_go_back_to_processing():
    with pytest.raises(IllegalStatusTransition):
        validate_transition(OrderStatusEnum.SHIPPED, OrderStatusEnum.PROCESSING)


@pytest.mark.unit
def test_cancelled_orders_cannot_be_reactivated():
    with pytest.raises(IllegalStatusTransition):
        validate_transition(
            OrderStatusEnum.CANCELLED_BY_CUSTOMER,
            OrderStatusEnum.PROCESSING,
        )


@pytest.mark.unit
def test_refunded_cannot_transition_to_anything():
    assert OrderStatusEnum.REFUNDED in TERMINAL_STATUSES
    with pytest.raises(IllegalStatusTransition):
        validate_transition(OrderStatusEnum.REFUNDED, OrderStatusEnum.DELIVERED)


# --- Admin override ---

@pytest.mark.unit
def test_admin_override_bypasses_the_graph():
    """Emergency path for genuine data-repair cases."""
    # Would normally raise:
    with pytest.raises(IllegalStatusTransition):
        validate_transition(OrderStatusEnum.DELIVERED, OrderStatusEnum.PENDING_PAYMENT)
    # ...but override is allowed:
    validate_transition(
        OrderStatusEnum.DELIVERED,
        OrderStatusEnum.PENDING_PAYMENT,
        allow_admin_override=True,
    )


# --- Graph sanity ---

@pytest.mark.unit
def test_terminal_states_are_exactly_what_we_expect():
    expected_terminal = {
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.RETURN_COMPLETED,
        OrderStatusEnum.RETURN_REJECTED,
        OrderStatusEnum.CANCELLED_BY_CUSTOMER,
        OrderStatusEnum.CANCELLED_BY_MERCHANT,
        OrderStatusEnum.CANCELLED_BY_ADMIN,
    }
    assert TERMINAL_STATUSES == expected_terminal
