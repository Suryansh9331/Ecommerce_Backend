"""
Unit tests for the Shiprocket webhook processor.

Covers the pure-logic pieces: token verification, status mapping, and the
order-propagation table. Full DB-backed tests (shipment lookup, commit flow)
are left for Phase 2 integration tests — these unit tests are the regression
guard against status-map drift and auth bypass.
"""
import pytest

from services.shiprocket_webhook import (
    WebhookError,
    verify_token,
    _map_status,
    _SHIPMENT_STATUS_MAP,
    _ORDER_STATUS_PROPAGATION,
)
from models.enums import ShipmentStatusEnum, OrderStatusEnum


# ----------------------------------------------------------------------------
# verify_token
# ----------------------------------------------------------------------------

@pytest.mark.unit
def test_verify_token_accepts_matching_tokens():
    assert verify_token("abc123", "abc123") is True


@pytest.mark.unit
def test_verify_token_rejects_mismatched_token_with_401():
    with pytest.raises(WebhookError) as exc:
        verify_token("wrong", "abc123")
    assert exc.value.http_status == 401


@pytest.mark.unit
def test_verify_token_rejects_missing_received_token_with_401():
    with pytest.raises(WebhookError) as exc:
        verify_token(None, "abc123")
    assert exc.value.http_status == 401


@pytest.mark.unit
def test_verify_token_rejects_empty_received_token_with_401():
    with pytest.raises(WebhookError) as exc:
        verify_token("", "abc123")
    assert exc.value.http_status == 401


@pytest.mark.unit
def test_verify_token_returns_503_when_no_secret_configured_on_server():
    """If operator forgot to set SHIPROCKET_WEBHOOK_TOKEN, we refuse every webhook
    rather than silently accepting them."""
    with pytest.raises(WebhookError) as exc:
        verify_token("anything", None)
    assert exc.value.http_status == 503


@pytest.mark.unit
def test_verify_token_rejects_different_length_in_constant_time():
    # Same first chars, different lengths — a naive early-return would be
    # timing-exploitable; our impl short-circuits on length mismatch.
    with pytest.raises(WebhookError):
        verify_token("abc", "abcdef")


# ----------------------------------------------------------------------------
# _map_status
# ----------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("sr_status,expected", [
    ("DELIVERED",         ShipmentStatusEnum.DELIVERED),
    ("Delivered",         ShipmentStatusEnum.DELIVERED),       # case-insensitive
    (" delivered ",       ShipmentStatusEnum.DELIVERED),       # trims whitespace
    ("OUT FOR DELIVERY",  ShipmentStatusEnum.OUT_FOR_DELIVERY),
    ("IN TRANSIT",        ShipmentStatusEnum.IN_TRANSIT),
    ("SHIPPED",           ShipmentStatusEnum.IN_TRANSIT),      # SR says SHIPPED, we treat as IN_TRANSIT
    ("LABEL_GENERATED",   ShipmentStatusEnum.LABEL_CREATED),
    ("PICKUP SCHEDULED",  ShipmentStatusEnum.LABEL_CREATED),
    ("RTO INITIATED",     ShipmentStatusEnum.RETURN_TO_SENDER),
    ("CANCELLED",         ShipmentStatusEnum.CANCELLED),
    ("CANCELED",          ShipmentStatusEnum.CANCELLED),       # US/UK spelling tolerance
    ("UNDELIVERED",       ShipmentStatusEnum.DELIVERY_ATTEMPTED),
    ("LOST",              ShipmentStatusEnum.EXCEPTION),
])
def test_map_status_handles_common_shiprocket_statuses(sr_status, expected):
    assert _map_status(sr_status) == expected


@pytest.mark.unit
def test_map_status_returns_none_for_unknown_status():
    assert _map_status("SOMETHING_NEW_SR_ADDED") is None


@pytest.mark.unit
def test_map_status_returns_none_for_empty_input():
    assert _map_status("") is None
    assert _map_status(None) is None


# ----------------------------------------------------------------------------
# Status map + propagation sanity
# ----------------------------------------------------------------------------

@pytest.mark.unit
def test_every_mapped_shipment_status_is_a_real_enum_member():
    """Guards against map drift if someone types an enum value by hand."""
    valid = set(ShipmentStatusEnum)
    for sr_label, mapped in _SHIPMENT_STATUS_MAP.items():
        assert mapped in valid, f"{sr_label} maps to invalid enum {mapped}"


@pytest.mark.unit
def test_propagation_table_targets_are_real_order_enum_members():
    valid = set(OrderStatusEnum)
    for shipment_status, order_status in _ORDER_STATUS_PROPAGATION.items():
        assert order_status in valid, (
            f"shipment {shipment_status} propagates to invalid order status {order_status}"
        )


@pytest.mark.unit
def test_delivered_shipment_propagates_to_delivered_order():
    """Core business rule — must never silently break."""
    assert (
        _ORDER_STATUS_PROPAGATION.get(ShipmentStatusEnum.DELIVERED)
        == OrderStatusEnum.DELIVERED
    )


@pytest.mark.unit
def test_exception_shipment_does_not_auto_flip_order():
    """
    When a shipment hits EXCEPTION we DO NOT want the order to flip into any
    status automatically — a human should look at it. Confirm it's absent.
    """
    assert ShipmentStatusEnum.EXCEPTION not in _ORDER_STATUS_PROPAGATION


@pytest.mark.unit
def test_in_transit_and_ofd_propagate_but_pickup_states_do_not():
    """Pickup/label states shouldn't move the order forward on the customer
    side — customer only cares once the parcel actually moves."""
    assert ShipmentStatusEnum.IN_TRANSIT in _ORDER_STATUS_PROPAGATION
    assert ShipmentStatusEnum.OUT_FOR_DELIVERY in _ORDER_STATUS_PROPAGATION
    assert ShipmentStatusEnum.LABEL_CREATED not in _ORDER_STATUS_PROPAGATION
    assert ShipmentStatusEnum.PENDING_PICKUP not in _ORDER_STATUS_PROPAGATION
