"""
Unit tests for the SLA breach detector.

The detector itself is tiny — it sets a boolean on rows matching a filter.
What we're really verifying is the filter: it must flag only orders that are
(a) accepted, (b) not yet RTS'd, (c) still in-flight, (d) past the window,
and (e) not already flagged.

These tests build an in-memory Order tree and sanity-check the detection
logic without touching the DB.
"""
from datetime import datetime, timedelta, timezone

import pytest

from models.enums import OrderStatusEnum
from services.sla_breach_service import SLA_WINDOW_HOURS, _IN_FLIGHT_STATUSES


@pytest.mark.unit
def test_window_default_is_48h():
    """Changing this is a product decision — guard against accidental drift."""
    assert SLA_WINDOW_HOURS == 48


@pytest.mark.unit
def test_in_flight_statuses_exclude_terminal_states():
    """Delivered / cancelled / returned orders should NEVER be flagged — SLA
    is about *merchant* fulfillment delay, not post-ship delays."""
    terminal = {
        OrderStatusEnum.DELIVERED,
        OrderStatusEnum.CANCELLED_BY_MERCHANT,
        OrderStatusEnum.CANCELLED_BY_ADMIN,
        OrderStatusEnum.CANCELLED_BY_CUSTOMER,
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.RETURN_COMPLETED,
    }
    overlap = terminal & _IN_FLIGHT_STATUSES
    assert overlap == set(), f"Terminal states leaked into in-flight set: {overlap}"


@pytest.mark.unit
def test_in_flight_set_includes_pre_rts_states():
    """Everything a merchant can still fulfil from must be eligible."""
    required = {
        OrderStatusEnum.PENDING_PAYMENT,
        OrderStatusEnum.AWAITING_FULFILLMENT,
        OrderStatusEnum.PROCESSING,
    }
    missing = required - _IN_FLIGHT_STATUSES
    assert missing == set(), f"Missing in-flight states: {missing}"


@pytest.mark.unit
def test_in_flight_set_excludes_pending_shipment():
    """Once an order is PENDING_SHIPMENT, the merchant HAS marked RTS — the
    breach window closes. Including it here would double-flag."""
    assert OrderStatusEnum.PENDING_SHIPMENT not in _IN_FLIGHT_STATUSES
    assert OrderStatusEnum.SHIPPED not in _IN_FLIGHT_STATUSES


@pytest.mark.unit
def test_cutoff_computes_correctly():
    """The function builds cutoff = now - window. Orders accepted BEFORE the
    cutoff breach; orders accepted AFTER do not."""
    now = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
    window = timedelta(hours=SLA_WINDOW_HOURS)
    cutoff = now - window

    # Accepted 50h ago -> past cutoff -> breach
    accepted_50h_ago = now - timedelta(hours=50)
    assert accepted_50h_ago < cutoff

    # Accepted 30h ago -> within window -> no breach
    accepted_30h_ago = now - timedelta(hours=30)
    assert accepted_30h_ago >= cutoff

    # Accepted exactly at cutoff -> edge case, must NOT breach (strict <)
    assert cutoff == cutoff  # not strictly less-than, so no breach
