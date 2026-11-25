"""Controller-level tests for Phase 2 Sprint 2 fulfillment endpoints.

Covers accept, mark-RTS, bulk-RTS, and the e-way bill setter. The Shiprocket
call inside mark_rts is monkey-patched so we don't hit the real API — we're
testing our orchestration (state transitions, invoice allocation, fail-soft
behaviour), not Shiprocket's responses.
"""
from decimal import Decimal
from unittest.mock import patch

import pytest

from app import create_app
from common.database import db


@pytest.fixture
def app():
    application = create_app("testing")
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


# -------------- fixtures --------------

def _mk_user(email):
    from auth.models.models import User, UserRole
    u = User(email=email, first_name="X", last_name="Y",
             role=UserRole.USER, is_email_verified=True)
    u.set_password("StrongPass123")
    db.session.add(u)
    db.session.flush()
    return u


def _mk_merchant_owner(email="mo@ex.com"):
    from auth.models.models import User, UserRole, MerchantProfile
    u = User(email=email, first_name="Mer", last_name="Chant",
             role=UserRole.MERCHANT, is_email_verified=True)
    u.set_password("StrongPass123")
    db.session.add(u)
    db.session.flush()
    m = MerchantProfile(
        user_id=u.id, business_name="Test Store", business_email=f"biz{u.id}@ex.com",
        business_phone="+919876543210", business_address="1 Market Rd",
        country_code="IN", state_province="Maharashtra", city="Pune",
        postal_code="411001", gstin="27ABCDE1234F1Z5",
    )
    db.session.add(m)
    db.session.flush()
    return u, m


def _mk_address(user, state="Maharashtra"):
    from models.user_address import UserAddress
    from models.enums import AddressTypeEnum
    a = UserAddress(
        user_id=user.id, contact_name="Buyer", contact_phone="+919811111111",
        address_line1="42 Residency Rd", city="Pune", state_province=state,
        postal_code="411001", country_code="IN",
        address_type=AddressTypeEnum.SHIPPING,
    )
    db.session.add(a)
    db.session.flush()
    return a


def _mk_order(buyer, merchant, addr, status=None):
    from models.order import Order, OrderItem
    from models.enums import OrderStatusEnum, PaymentStatusEnum, PaymentMethodEnum
    order = Order(
        user_id=buyer.id,
        order_status=status or OrderStatusEnum.PENDING_PAYMENT,
        subtotal_amount=Decimal("100.00"),
        discount_amount=Decimal("0.00"),
        tax_amount=Decimal("18.00"),
        shipping_amount=Decimal("0.00"),
        total_amount=Decimal("118.00"),
        currency="INR",
        payment_method=PaymentMethodEnum.CREDIT_CARD,
        payment_status=PaymentStatusEnum.SUCCESSFUL,
        shipping_address_id=addr.address_id,
        billing_address_id=addr.address_id,
    )
    db.session.add(order)
    db.session.flush()
    item = OrderItem(
        order_id=order.order_id, product_id=None, merchant_id=merchant.id,
        product_name_at_purchase="Widget", sku_at_purchase="W-1",
        quantity=1,
        final_base_price_for_gst_calc=Decimal("100.00"),
        gst_rate_applied_at_purchase=Decimal("18.00"),
        gst_amount_per_unit=Decimal("18.00"),
        unit_price_inclusive_gst=Decimal("118.00"),
        line_item_total_inclusive_gst=Decimal("118.00"),
    )
    db.session.add(item)
    db.session.commit()
    return order


# -------------- accept --------------

def test_accept_stamps_accepted_at_and_sets_processing(app):
    from controllers.merchant.order_controller import MerchantOrderController
    from models.enums import OrderStatusEnum
    with app.app_context():
        owner, merchant = _mk_merchant_owner()
        buyer = _mk_user("b1@ex.com")
        addr = _mk_address(buyer)
        order = _mk_order(buyer, merchant, addr)
        oid, uid = order.order_id, owner.id

        result = MerchantOrderController.accept_order(user_id=uid, order_id=oid)

        assert result["already_accepted"] is False
        assert result["accepted_at"] is not None
        assert result["order_status"] == OrderStatusEnum.PROCESSING.value


def test_accept_is_idempotent(app):
    from controllers.merchant.order_controller import MerchantOrderController
    with app.app_context():
        owner, merchant = _mk_merchant_owner()
        buyer = _mk_user("b2@ex.com")
        addr = _mk_address(buyer)
        order = _mk_order(buyer, merchant, addr)
        oid, uid = order.order_id, owner.id

        first = MerchantOrderController.accept_order(user_id=uid, order_id=oid)
        second = MerchantOrderController.accept_order(user_id=uid, order_id=oid)

        assert first["already_accepted"] is False
        assert second["already_accepted"] is True
        # accepted_at should NOT have been overwritten
        assert first["accepted_at"] == second["accepted_at"]


def test_accept_rejects_terminal_status(app):
    from controllers.merchant.order_controller import MerchantOrderController
    from models.enums import OrderStatusEnum
    with app.app_context():
        owner, merchant = _mk_merchant_owner()
        buyer = _mk_user("b3@ex.com")
        addr = _mk_address(buyer)
        order = _mk_order(buyer, merchant, addr, status=OrderStatusEnum.DELIVERED)
        oid, uid = order.order_id, owner.id

        with pytest.raises(ValueError):
            MerchantOrderController.accept_order(user_id=uid, order_id=oid)


# -------------- mark-RTS --------------

def _patch_shiprocket(success=True, shipment_id=1234, label_url="https://sr.example/label.pdf"):
    """Patch ShipRocketController methods used by mark_rts."""
    stub_create = {
        "success": success,
        "shipment_id": shipment_id if success else None,
        "awb_code": "AWB123" if success else None,
    }
    stub_label = {"label_url": label_url} if success else {}
    return patch.multiple(
        "controllers.shiprocket_controller.ShipRocketController",
        create_shiprocket_order_from_db_order=lambda self, **kw: stub_create,
        generate_label=lambda self, shipment_id: stub_label,
    )


def test_mark_rts_happy_path_allocates_invoice_and_calls_shiprocket(app):
    from controllers.merchant.order_controller import MerchantOrderController
    from models.enums import OrderStatusEnum
    with app.app_context():
        owner, merchant = _mk_merchant_owner()
        buyer = _mk_user("b4@ex.com")
        addr = _mk_address(buyer)
        order = _mk_order(buyer, merchant, addr, status=OrderStatusEnum.PROCESSING)
        oid, uid = order.order_id, owner.id

        with _patch_shiprocket():
            result = MerchantOrderController.mark_rts(user_id=uid, order_id=oid)

        assert result["order_status"] == OrderStatusEnum.PENDING_SHIPMENT.value
        assert result["rts_marked_at"] is not None
        assert result["invoice_number"] is not None
        assert result["invoice_number"].startswith("INV/")
        assert result["label_url"] == "https://sr.example/label.pdf"
        assert result["already_rts"] is False


def test_mark_rts_fails_soft_when_shiprocket_errors(app):
    from controllers.merchant.order_controller import MerchantOrderController
    from models.enums import OrderStatusEnum
    with app.app_context():
        owner, merchant = _mk_merchant_owner()
        buyer = _mk_user("b5@ex.com")
        addr = _mk_address(buyer)
        order = _mk_order(buyer, merchant, addr, status=OrderStatusEnum.PROCESSING)
        oid, uid = order.order_id, owner.id

        def _raise(self, **kw):
            raise Exception("SR auth down")

        with patch.object(
            __import__("controllers.shiprocket_controller", fromlist=["ShipRocketController"]).ShipRocketController,
            "create_shiprocket_order_from_db_order",
            _raise,
        ):
            result = MerchantOrderController.mark_rts(user_id=uid, order_id=oid)

        # Order still flipped, invoice still allocated.
        assert result["order_status"] == OrderStatusEnum.PENDING_SHIPMENT.value
        assert result["invoice_number"] is not None
        assert result["shiprocket"]["success"] is False


def test_mark_rts_rejects_non_processing_state(app):
    from controllers.merchant.order_controller import MerchantOrderController
    from models.enums import OrderStatusEnum
    with app.app_context():
        owner, merchant = _mk_merchant_owner()
        buyer = _mk_user("b6@ex.com")
        addr = _mk_address(buyer)
        order = _mk_order(buyer, merchant, addr, status=OrderStatusEnum.DELIVERED)
        oid, uid = order.order_id, owner.id

        with pytest.raises(ValueError):
            MerchantOrderController.mark_rts(user_id=uid, order_id=oid)


def test_mark_rts_is_idempotent_on_invoice_allocation(app):
    """Running mark_rts twice must not allocate two invoice numbers for the
    same (order, merchant) pair."""
    from controllers.merchant.order_controller import MerchantOrderController
    from models.enums import OrderStatusEnum
    from models.invoice import Invoice
    with app.app_context():
        owner, merchant = _mk_merchant_owner()
        buyer = _mk_user("b7@ex.com")
        addr = _mk_address(buyer)
        order = _mk_order(buyer, merchant, addr, status=OrderStatusEnum.PROCESSING)
        oid, uid, mid = order.order_id, owner.id, merchant.id

        with _patch_shiprocket():
            first = MerchantOrderController.mark_rts(user_id=uid, order_id=oid)
            second = MerchantOrderController.mark_rts(user_id=uid, order_id=oid)

        assert second["already_rts"] is True
        assert first["invoice_number"] == second["invoice_number"]
        count = Invoice.query.filter_by(order_id=oid, merchant_id=mid).count()
        assert count == 1


# -------------- bulk-RTS --------------

def test_bulk_rts_returns_per_order_results(app):
    from controllers.merchant.order_controller import MerchantOrderController
    from models.enums import OrderStatusEnum
    with app.app_context():
        owner, merchant = _mk_merchant_owner()
        buyer = _mk_user("b8@ex.com")
        addr = _mk_address(buyer)
        o1 = _mk_order(buyer, merchant, addr, status=OrderStatusEnum.PROCESSING)
        o2 = _mk_order(buyer, merchant, addr, status=OrderStatusEnum.PROCESSING)
        o3 = _mk_order(buyer, merchant, addr, status=OrderStatusEnum.DELIVERED)  # will fail
        ids = [o1.order_id, o2.order_id, o3.order_id]
        uid = owner.id

        with _patch_shiprocket():
            result = MerchantOrderController.bulk_rts(user_id=uid, order_ids=ids)

        assert result["requested"] == 3
        assert result["succeeded"] == 2
        assert result["failed"] == 1
        failed = [r for r in result["results"] if not r["success"]]
        assert len(failed) == 1
        assert failed[0]["order_id"] == o3.order_id


def test_bulk_rts_rejects_empty_list(app):
    from controllers.merchant.order_controller import MerchantOrderController
    with app.app_context():
        owner, _ = _mk_merchant_owner()
        with pytest.raises(ValueError):
            MerchantOrderController.bulk_rts(user_id=owner.id, order_ids=[])


def test_bulk_rts_rejects_huge_batch(app):
    from controllers.merchant.order_controller import MerchantOrderController
    with app.app_context():
        owner, _ = _mk_merchant_owner()
        oversized = [f"ORD-{i}" for i in range(101)]
        with pytest.raises(ValueError):
            MerchantOrderController.bulk_rts(user_id=owner.id, order_ids=oversized)


# -------------- e-way bill --------------

def test_set_eway_bill_accepts_12_digits(app):
    from controllers.merchant.order_controller import MerchantOrderController
    with app.app_context():
        owner, merchant = _mk_merchant_owner()
        buyer = _mk_user("b9@ex.com")
        addr = _mk_address(buyer)
        order = _mk_order(buyer, merchant, addr)
        oid, uid = order.order_id, owner.id

        result = MerchantOrderController.set_eway_bill(
            user_id=uid, order_id=oid, eway_bill_number="123456789012"
        )
        assert result["eway_bill_number"] == "123456789012"


@pytest.mark.parametrize("bad_value", [
    "abc",            # non-numeric
    "123",            # too short
    "1234567890123",  # too long
    "",               # empty
    "12345678901a",   # letter inside digits
])
def test_set_eway_bill_rejects_invalid_values(app, bad_value):
    from controllers.merchant.order_controller import MerchantOrderController
    with app.app_context():
        owner, merchant = _mk_merchant_owner()
        buyer = _mk_user("b10@ex.com")
        addr = _mk_address(buyer)
        order = _mk_order(buyer, merchant, addr)
        oid, uid = order.order_id, owner.id

        with pytest.raises(ValueError):
            MerchantOrderController.set_eway_bill(
                user_id=uid, order_id=oid, eway_bill_number=bad_value
            )


# -------------- SLA breach detector --------------

def test_sla_breach_flags_only_overdue_accepted_orders(app):
    from datetime import datetime, timedelta, timezone
    from models.enums import OrderStatusEnum
    from services.sla_breach_service import detect_sla_breaches

    with app.app_context():
        owner, merchant = _mk_merchant_owner()
        buyer = _mk_user("b11@ex.com")
        addr = _mk_address(buyer)

        # 1. Accepted 50h ago, not RTS'd, PROCESSING -> should flag
        overdue = _mk_order(buyer, merchant, addr, status=OrderStatusEnum.PROCESSING)
        overdue.accepted_at = datetime.now(timezone.utc) - timedelta(hours=50)

        # 2. Accepted 10h ago -> within window, no flag
        fresh = _mk_order(buyer, merchant, addr, status=OrderStatusEnum.PROCESSING)
        fresh.accepted_at = datetime.now(timezone.utc) - timedelta(hours=10)

        # 3. Overdue but already RTS'd -> no flag
        shipped = _mk_order(buyer, merchant, addr, status=OrderStatusEnum.PENDING_SHIPMENT)
        shipped.accepted_at = datetime.now(timezone.utc) - timedelta(hours=100)
        shipped.rts_marked_at = datetime.now(timezone.utc) - timedelta(hours=1)

        # 4. Overdue but DELIVERED (terminal) -> no flag
        delivered = _mk_order(buyer, merchant, addr, status=OrderStatusEnum.DELIVERED)
        delivered.accepted_at = datetime.now(timezone.utc) - timedelta(hours=100)

        db.session.commit()

        summary = detect_sla_breaches()

        assert summary["total_flagged"] == 1

        db.session.refresh(overdue)
        db.session.refresh(fresh)
        db.session.refresh(shipped)
        db.session.refresh(delivered)
        assert overdue.sla_breached is True
        assert fresh.sla_breached is False
        assert shipped.sla_breached is False
        assert delivered.sla_breached is False


def test_sla_breach_second_run_is_noop(app):
    """Running the detector twice must not double-count already-flagged orders."""
    from datetime import datetime, timedelta, timezone
    from models.enums import OrderStatusEnum
    from services.sla_breach_service import detect_sla_breaches

    with app.app_context():
        owner, merchant = _mk_merchant_owner()
        buyer = _mk_user("b12@ex.com")
        addr = _mk_address(buyer)

        overdue = _mk_order(buyer, merchant, addr, status=OrderStatusEnum.PROCESSING)
        overdue.accepted_at = datetime.now(timezone.utc) - timedelta(hours=50)
        db.session.commit()

        first = detect_sla_breaches()
        second = detect_sla_breaches()

        assert first["total_flagged"] == 1
        assert second["total_flagged"] == 0
