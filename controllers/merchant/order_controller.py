from common.database import db
from models.order import Order, OrderItem, OrderStatusHistory
from models.enums import OrderStatusEnum, PaymentStatusEnum
from sqlalchemy import and_, or_, distinct
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from auth.models.models import MerchantProfile, User
from services.order_status_transitions import (
    validate_transition,
    IllegalStatusTransition,
)
import logging

logger = logging.getLogger(__name__)


# States a merchant is allowed to accept FROM. After accept, we land on PROCESSING.
_ACCEPTABLE_FROM = {
    OrderStatusEnum.PENDING_PAYMENT,
    OrderStatusEnum.AWAITING_FULFILLMENT,
    OrderStatusEnum.PROCESSING,
}

# States from which merchant may mark Ready-To-Ship. Lands on PENDING_SHIPMENT.
_RTS_FROM = {
    OrderStatusEnum.AWAITING_FULFILLMENT,
    OrderStatusEnum.PROCESSING,
}

class MerchantOrderController:
    @staticmethod
    def get_merchant_orders(user_id: int, page: int = 1, per_page: int = 50, status: str = None, 
                          payment_status: str = None, start_date: str = None, end_date: str = None):
        """
        Get all orders for a merchant's products with pagination and filtering
        """
        try:
            # First get the merchant profile from user_id
            merchant = MerchantProfile.query.filter_by(user_id=user_id).first()
            if not merchant:
                raise ValueError(f"No merchant profile found for user ID {user_id}")

            merchant_id = merchant.id
            logger.info(f"Fetching orders for merchant: {merchant.business_name} (ID: {merchant_id})")
            
            # Base query to get orders with merchant's products
            query = Order.query.join(OrderItem).filter(OrderItem.merchant_id == merchant_id).distinct()
            
            
            # Apply order status filter if provided
            if status:
                try:
                    order_status = OrderStatusEnum(status.lower())
                    query = query.filter(Order.order_status == order_status)
                    logger.info(f"Filtering by order status: {status}")
                except ValueError:
                    logger.error(f"Invalid order status: {status}")
                    raise ValueError(f"Invalid order status: {status}")

            # Apply payment status filter if provided
            if payment_status:
                try:
                    payment_status_enum = PaymentStatusEnum(payment_status.lower())
                    query = query.filter(Order.payment_status == payment_status_enum)
                    logger.info(f"Filtering by payment status: {payment_status}")
                except ValueError:
                    logger.error(f"Invalid payment status: {payment_status}")
                    raise ValueError(f"Invalid payment status: {payment_status}")

            # Apply date range filter if provided
            if start_date:
                try:
                    start = datetime.fromisoformat(start_date)
                    query = query.filter(Order.order_date >= start)
                    logger.info(f"Filtering by start date: {start_date}")
                except ValueError:
                    logger.error(f"Invalid start date format: {start_date}")
                    raise ValueError(f"Invalid start date format: {start_date}")

            if end_date:
                try:
                    end = datetime.fromisoformat(end_date)
                    query = query.filter(Order.order_date <= end)
                    logger.info(f"Filtering by end date: {end_date}")
                except ValueError:
                    logger.error(f"Invalid end date format: {end_date}")
                    raise ValueError(f"Invalid end date format: {end_date}")

            # Get total count for pagination
            total = query.count()
            logger.info(f"Total orders found: {total}")

            # Apply pagination
            orders = query.order_by(Order.order_date.desc())\
                         .offset((page - 1) * per_page)\
                         .limit(per_page)\
                         .all()

            logger.info(f"Retrieved {len(orders)} orders for page {page}")

            # Calculate pagination info
            total_pages = (total + per_page - 1) // per_page
            has_next = page < total_pages
            has_prev = page > 1

            # Serialize orders with only the items belonging to this merchant
            serialized_orders = []
            for order in orders:
                order_data = order.serialize(include_items=False, include_history=True)
                # Filter items to only include those from this merchant
                merchant_items = [item.serialize() for item in order.items if item.merchant_id == merchant_id]
                order_data['items'] = merchant_items
                serialized_orders.append(order_data)

            return {
                'orders': serialized_orders,
                'pagination': {
                    'total': total,
                    'page': page,
                    'per_page': per_page,
                    'total_pages': total_pages,
                    'has_next': has_next,
                    'has_prev': has_prev
                }
            }

        except Exception as e:
            logger.error(f"Error fetching merchant orders: {str(e)}", exc_info=True)
            db.session.rollback()
            raise e

    @staticmethod
    def get_merchant_order_details(user_id: int, order_id: str):
        """
        Get detailed information about a specific order for a merchant
        """
        try:
            # First get the merchant profile from user_id
            merchant = MerchantProfile.query.filter_by(user_id=user_id).first()
            if not merchant:
                raise ValueError(f"No merchant profile found for user ID {user_id}")

            merchant_id = merchant.id
            logger.info(f"Fetching order details for order_id: {order_id}, merchant: {merchant.business_name}")
            
            # Get order with merchant's items
            order = Order.query.join(OrderItem)\
                             .filter(and_(
                                 Order.order_id == order_id,
                                 OrderItem.merchant_id == merchant_id
                             )).first()

            if not order:
                logger.warning(f"Order {order_id} not found for merchant {merchant.business_name}")
                raise ValueError(f"Order {order_id} not found or not associated with merchant {merchant.business_name}")

            # Get the full order data but filter items to only include merchant's items
            order_data = order.serialize(include_items=False, include_history=True, include_shipments=True)
            merchant_items = [item.serialize() for item in order.items if item.merchant_id == merchant_id]
            logger.info(f"Found {len(merchant_items)} items for merchant {merchant.business_name} in order {order_id}")
            order_data['items'] = merchant_items

            return order_data

        except Exception as e:
            logger.error(f"Error fetching merchant order details: {str(e)}", exc_info=True)
            db.session.rollback()
            raise e

    @staticmethod
    def get_merchant_order_stats(user_id: int, days: int = 30):
        """
        Get order statistics for a merchant
        """
        try:
            # First get the merchant profile from user_id
            merchant = MerchantProfile.query.filter_by(user_id=user_id).first()
            if not merchant:
                raise ValueError(f"No merchant profile found for user ID {user_id}")

            merchant_id = merchant.id
            logger.info(f"Fetching order stats for merchant: {merchant.business_name}, days: {days}")
            
            # Calculate date range
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=days)

            # Get total orders and revenue
            total_orders = Order.query.join(OrderItem)\
                                    .filter(and_(
                                        OrderItem.merchant_id == merchant_id,
                                        Order.order_date >= start_date
                                    )).distinct().count()

            logger.info(f"Total orders in last {days} days: {total_orders}")

            # Get orders by status
            status_counts = db.session.query(
                Order.order_status,
                db.func.count(distinct(Order.order_id))
            ).join(OrderItem)\
             .filter(and_(
                 OrderItem.merchant_id == merchant_id,
                 Order.order_date >= start_date
             ))\
             .group_by(Order.order_status)\
             .all()

            # Get daily order counts
            daily_orders = db.session.query(
                db.func.date(Order.order_date),
                db.func.count(distinct(Order.order_id))
            ).join(OrderItem)\
             .filter(and_(
                 OrderItem.merchant_id == merchant_id,
                 Order.order_date >= start_date
             ))\
             .group_by(db.func.date(Order.order_date))\
             .all()

            stats = {
                'total_orders': total_orders,
                'status_breakdown': {status.value: count for status, count in status_counts},
                'daily_orders': {str(date): count for date, count in daily_orders}
            }
            
            logger.info(f"Order stats for {merchant.business_name}: {stats}")
            return stats

        except Exception as e:
            logger.error(f"Error fetching merchant order stats: {str(e)}", exc_info=True)
            db.session.rollback()
            raise e

    # ---------- Phase 2 Sprint 2: fulfillment lifecycle ----------

    @staticmethod
    def _resolve_merchant(user_id: int) -> MerchantProfile:
        merchant = MerchantProfile.query.filter_by(user_id=user_id).first()
        if not merchant:
            raise ValueError(f"No merchant profile found for user ID {user_id}")
        return merchant

    @staticmethod
    def _load_order_for_merchant(order_id: str, merchant_id: int) -> Order:
        """Fetch an order that contains at least one line item owned by this merchant."""
        order = (
            Order.query.join(OrderItem)
            .filter(and_(Order.order_id == order_id, OrderItem.merchant_id == merchant_id))
            .first()
        )
        if not order:
            raise ValueError(f"Order {order_id} not found for this merchant")
        return order

    @staticmethod
    def _append_history(order: Order, new_status: OrderStatusEnum, user_id: int, note: str):
        hist = OrderStatusHistory(
            order_id=order.order_id,
            status=new_status,
            changed_by_user_id=user_id,
            notes=note,
        )
        db.session.add(hist)

    @staticmethod
    def accept_order(user_id: int, order_id: str):
        """Merchant clicks "Accept Order" — stamps accepted_at and moves to PROCESSING.

        Idempotent: if already accepted we return the current state without re-stamping.
        """
        merchant = MerchantOrderController._resolve_merchant(user_id)
        order = MerchantOrderController._load_order_for_merchant(order_id, merchant.id)

        if order.accepted_at is not None:
            # Already accepted. Idempotent success so the UI doesn't need to disambiguate.
            return {
                "order_id": order.order_id,
                "accepted_at": order.accepted_at.isoformat(),
                "order_status": order.order_status.value,
                "already_accepted": True,
            }

        if order.order_status not in _ACCEPTABLE_FROM:
            raise ValueError(
                f"Order cannot be accepted from status '{order.order_status.value}'"
            )

        try:
            previous = order.order_status
            if previous != OrderStatusEnum.PROCESSING:
                validate_transition(previous, OrderStatusEnum.PROCESSING)
                order.order_status = OrderStatusEnum.PROCESSING
                MerchantOrderController._append_history(
                    order,
                    OrderStatusEnum.PROCESSING,
                    user_id,
                    f"Merchant accepted order (was {previous.value})",
                )
            order.accepted_at = datetime.now(timezone.utc)
            db.session.commit()
            logger.info(
                f"Order {order_id} accepted by merchant {merchant.business_name} "
                f"(user {user_id})"
            )
            return {
                "order_id": order.order_id,
                "accepted_at": order.accepted_at.isoformat(),
                "order_status": order.order_status.value,
                "already_accepted": False,
            }
        except IllegalStatusTransition as e:
            db.session.rollback()
            raise ValueError(str(e))
        except Exception as e:
            db.session.rollback()
            logger.error(f"accept_order failed for {order_id}: {e}", exc_info=True)
            raise

    @staticmethod
    def _merchant_totals_for_invoice(order: Order, merchant_id: int):
        """Compute (taxable_value, total_gst, total_amount) for this merchant's slice
        of the order, used by invoice allocation in Sprint 1 numbering service."""
        taxable = Decimal("0.00")
        gst = Decimal("0.00")
        total = Decimal("0.00")
        for item in order.items:
            if item.merchant_id != merchant_id:
                continue
            qty = Decimal(item.quantity or 0)
            line_total = Decimal(item.line_item_total_inclusive_gst or 0)
            gst_per_unit = Decimal(item.gst_amount_per_unit or 0)
            line_gst = (gst_per_unit * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            line_taxable = (line_total - line_gst).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if line_taxable < 0:
                line_taxable = Decimal("0.00")
            taxable += line_taxable
            gst += line_gst
            total += line_total
        return taxable, gst, total

    @staticmethod
    def mark_rts(user_id: int, order_id: str, courier_id: int = None):
        """Merchant marks Ready-To-Ship.

        Side effects:
          * stamps rts_marked_at
          * transitions order to PENDING_SHIPMENT
          * allocates an FY-aware invoice number for this merchant's slice
          * calls Shiprocket to create the shipment (fail-soft: shipment row
            goes EXCEPTION and the status-sync scheduler retries)

        Idempotent on rts_marked_at: if already RTS'd, we re-attempt the
        Shiprocket call only if there is no live shipment yet.
        """
        from services.invoice_numbering_service import issue_invoice
        from models.invoice import Invoice
        from flask import current_app

        merchant = MerchantOrderController._resolve_merchant(user_id)
        order = MerchantOrderController._load_order_for_merchant(order_id, merchant.id)

        already_rts = order.rts_marked_at is not None

        if not already_rts:
            if order.order_status not in _RTS_FROM:
                raise ValueError(
                    f"Order cannot be marked RTS from status '{order.order_status.value}'"
                )
            try:
                previous = order.order_status
                validate_transition(previous, OrderStatusEnum.PENDING_SHIPMENT)
                order.order_status = OrderStatusEnum.PENDING_SHIPMENT
                order.rts_marked_at = datetime.now(timezone.utc)
                if order.accepted_at is None:
                    order.accepted_at = order.rts_marked_at  # accept-on-RTS fallback
                MerchantOrderController._append_history(
                    order,
                    OrderStatusEnum.PENDING_SHIPMENT,
                    user_id,
                    f"Merchant marked Ready-To-Ship (was {previous.value})",
                )
                db.session.flush()
            except IllegalStatusTransition as e:
                db.session.rollback()
                raise ValueError(str(e))

        # Allocate invoice number (Sprint 1) — only if we haven't already for
        # this (order, merchant) pair.
        invoice_number = None
        existing_inv = (
            db.session.query(Invoice)
            .filter_by(order_id=order.order_id, merchant_id=merchant.id)
            .first()
        )
        if existing_inv:
            invoice_number = existing_inv.invoice_number
        else:
            taxable, total_gst, total_amount = MerchantOrderController._merchant_totals_for_invoice(
                order, merchant.id
            )
            if total_amount > 0:
                try:
                    inv = issue_invoice(
                        order_id=order.order_id,
                        merchant_id=merchant.id,
                        taxable_value=taxable,
                        total_gst=total_gst,
                        total_amount=total_amount,
                    )
                    invoice_number = inv.invoice_number
                except Exception as e:
                    logger.error(
                        f"Invoice allocation failed for order {order_id} / merchant "
                        f"{merchant.id}: {e}",
                        exc_info=True,
                    )
                    # Don't abort RTS — we can retry invoice allocation later.

        # Commit the lifecycle + invoice row BEFORE calling Shiprocket. If SR
        # errors, we still want the order in PENDING_SHIPMENT so the merchant
        # can retry label generation from the UI.
        db.session.commit()

        # Trigger Shiprocket (Sprint 0). Fail-soft: controller already flips
        # the Shipment row to EXCEPTION on error.
        sr_result = None
        label_url = None
        try:
            from controllers.shiprocket_controller import ShipRocketController
            sr = ShipRocketController()
            sr_result = sr.create_shiprocket_order_from_db_order(
                order_id=order.order_id,
                merchant_id=merchant.id,
                pickup_address_id=None,  # fall back to merchant business address
                delivery_address_id=order.shipping_address_id,
                courier_id=courier_id,
            )
            # Request the label if we got a valid SR shipment_id back.
            sr_shipment_id = sr_result.get("shipment_id") if isinstance(sr_result, dict) else None
            if sr_result and sr_result.get("success") and sr_shipment_id:
                try:
                    label_resp = sr.generate_label(sr_shipment_id)
                    label_url = (label_resp or {}).get("label_url")
                except Exception as e:
                    logger.warning(
                        f"Label generation failed for SR shipment {sr_shipment_id}: {e}"
                    )
        except Exception as e:
            logger.error(
                f"Shiprocket create_order failed for {order_id}: {e}", exc_info=True
            )
            sr_result = {"success": False, "error": str(e)}

        return {
            "order_id": order.order_id,
            "rts_marked_at": order.rts_marked_at.isoformat() if order.rts_marked_at else None,
            "order_status": order.order_status.value,
            "invoice_number": invoice_number,
            "shiprocket": sr_result,
            "label_url": label_url,
            "already_rts": already_rts,
        }

    @staticmethod
    def bulk_rts(user_id: int, order_ids: list, courier_id: int = None):
        """Mark a batch of orders RTS. Each order is attempted independently —
        a failure on one does NOT abort the rest. Returns a per-order result."""
        if not isinstance(order_ids, list) or not order_ids:
            raise ValueError("order_ids must be a non-empty list")
        if len(order_ids) > 100:
            # Guard against runaway UI payloads — pick a sensible cap.
            raise ValueError("bulk RTS limited to 100 orders per call")

        results = []
        successes = 0
        for oid in order_ids:
            try:
                r = MerchantOrderController.mark_rts(user_id, oid, courier_id=courier_id)
                results.append({"order_id": oid, "success": True, **r})
                successes += 1
            except ValueError as e:
                results.append({"order_id": oid, "success": False, "error": str(e)})
            except Exception as e:
                logger.error(f"bulk_rts error on {oid}: {e}", exc_info=True)
                results.append({"order_id": oid, "success": False, "error": "internal_error"})
        return {
            "requested": len(order_ids),
            "succeeded": successes,
            "failed": len(order_ids) - successes,
            "results": results,
        }

    @staticmethod
    def set_eway_bill(user_id: int, order_id: str, eway_bill_number: str):
        """Record the manually-generated e-way bill number on the order.

        Phase 2 defers the paid GSP API — merchants generate the EWB on
        ewaybillgst.gov.in and paste the 12-digit number back here. Required
        for inter-state shipments > Rs 50,000.
        """
        if not eway_bill_number or not isinstance(eway_bill_number, str):
            raise ValueError("eway_bill_number is required")
        cleaned = eway_bill_number.strip()
        if not cleaned.isdigit() or len(cleaned) != 12:
            raise ValueError("eway_bill_number must be a 12-digit number")

        merchant = MerchantOrderController._resolve_merchant(user_id)
        order = MerchantOrderController._load_order_for_merchant(order_id, merchant.id)
        order.eway_bill_number = cleaned
        db.session.commit()
        return {"order_id": order.order_id, "eway_bill_number": cleaned} 