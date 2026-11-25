from flask import Blueprint, request, jsonify, current_app
from controllers.shiprocket_controller import ShipRocketController
from auth.utils import super_admin_role_required , merchant_role_required
from common.response import success_response, error_response
from services.shiprocket_webhook import process_shiprocket_webhook, WebhookError
import os
import traceback
from flask_jwt_extended import jwt_required

shiprocket_bp = Blueprint('shiprocket', __name__, url_prefix='/api/shiprocket')

@shiprocket_bp.route('/serviceability', methods=['POST'])
@jwt_required()
def check_serviceability():
    """
    Check courier serviceability and get shipping charges
    ---
    tags:
      - ShipRocket
    security:
      - Bearer: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required:
              - pickup_pincode
              - delivery_pincode
              - weight
            properties:
              pickup_pincode:
                type: string
                description: Pickup location pincode
              delivery_pincode:
                type: string
                description: Delivery location pincode
              weight:
                type: number
                format: float
                description: Package weight in kg
              cod:
                type: boolean
                default: false
                description: Whether this is a COD order (true) or prepaid (false)
              order_id:
                type: string
                description: Order ID for the request (optional, not used for serviceability checks)
              cod_amount:
                type: number
                format: float
                description: COD amount (only if cod is true)
    responses:
      200:
        description: Serviceability check successful
        schema:
          type: object
          properties:
            success:
              type: boolean
            data:
              type: object
              properties:
                available_courier_companies:
                  type: array
                  items:
                    type: object
                    properties:
                      courier_company_id:
                        type: integer
                      courier_name:
                        type: string
                      rate:
                        type: number
                      estimated_delivery_days:
                        type: string
      400:
        description: Invalid request data
      500:
        description: Internal server error
    """
    try:
        data = request.get_json()
        
        if not data:
            return error_response("Request data is required", 400)
        
        required_fields = ['pickup_pincode', 'delivery_pincode', 'weight']
        for field in required_fields:
            if field not in data:
                return error_response(f"Missing required field: {field}", 400)
        
        pickup_pincode = data['pickup_pincode']
        delivery_pincode = data['delivery_pincode']
        weight = float(data['weight'])
        cod = data.get('cod', False)  # Boolean: true for COD, false for prepaid
        cod_amount = float(data.get('cod_amount', 0))  # COD amount if it's a COD order
        order_id = data.get('order_id')  # Optional order_id
        
        if weight <= 0:
            return error_response("Weight must be greater than 0", 400)
        
        # Convert boolean cod to the format expected by ShipRocket
        # If cod is True, use cod_amount, otherwise use 0
        shiprocket_cod = cod_amount if cod else 0
        
        shiprocket = ShipRocketController()
        response = shiprocket.check_serviceability(
            pickup_pincode=pickup_pincode,
            delivery_pincode=delivery_pincode,
            weight=weight,
            cod=shiprocket_cod  # Pass the calculated COD amount
        )
        
        # Just return the controller's response (may have empty list and/or message)
        return success_response("Serviceability check successful", response)
        
    except ValueError as e:
        return error_response(f"Invalid data format: {str(e)}", 400)
    except Exception as e:
        current_app.logger.error(f"Serviceability check failed: {str(e)}")
        return error_response(f"Serviceability check failed: {str(e)}", 500)

@shiprocket_bp.route('/create-order', methods=['POST'])
@jwt_required()
def create_shiprocket_order():
    """
    Create ShipRocket order from database order
    ---
    tags:
      - ShipRocket
    security:
      - Bearer: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required:
              - order_id
              - merchant_id
              - delivery_address_id
            properties:
              order_id:
                type: string
                description: Internal order ID
              merchant_id:
                type: integer
                description: Merchant ID
              pickup_address_id:
                type: integer
                description: Pickup address ID (optional, will use merchant address if not provided)
              delivery_address_id:
                type: integer
                description: Delivery address ID
              courier_id:
                type: integer
                description: Preferred courier ID (optional)
    responses:
      200:
        description: ShipRocket order created successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            data:
              type: object
              properties:
                shiprocket_order_id:
                  type: integer
                shipment_id:
                  type: integer
                awb_code:
                  type: string
                courier_name:
                  type: string
                tracking_number:
                  type: string
                serviceability:
                  type: object
                order_response:
                  type: object
                awb_response:
                  type: object
                pickup_response:
                  type: object
                db_shipment:
                  type: object
      400:
        description: Invalid request data
      404:
        description: Order, merchant, or address not found
      500:
        description: Internal server error
    """
    try:
        data = request.get_json()
        
        if not data:
            return error_response("Request data is required", 400)
        
        required_fields = ['order_id', 'merchant_id', 'delivery_address_id']
        for field in required_fields:
            if field not in data:
                return error_response(f"Missing required field: {field}", 400)
        
        order_id = data['order_id']
        merchant_id = int(data['merchant_id'])
        delivery_address_id = int(data['delivery_address_id'])
        pickup_address_id = int(data['pickup_address_id']) if data.get('pickup_address_id') else None
        courier_id = int(data.get('courier_id')) if data.get('courier_id') else None
        
        # Validate order_id format
        if not order_id or not isinstance(order_id, str):
            return error_response("Invalid order_id format", 400)
        
        # Validate merchant_id
        if merchant_id <= 0:
            return error_response("Invalid merchant_id", 400)
        
        # Validate delivery_address_id
        if delivery_address_id <= 0:
            return error_response("Invalid delivery_address_id", 400)
        
        # Validate pickup_address_id if provided
        if pickup_address_id is not None and pickup_address_id <= 0:
            return error_response("Invalid pickup_address_id", 400)
        
        # Validate courier_id if provided
        if courier_id is not None and courier_id <= 0:
            return error_response("Invalid courier_id", 400)
        
        shiprocket = ShipRocketController()
        response = shiprocket.create_shiprocket_order_from_db_order(
            order_id=order_id,
            merchant_id=merchant_id,
            pickup_address_id=pickup_address_id,
            delivery_address_id=delivery_address_id,
            courier_id=courier_id
        )
        
        return success_response("ShipRocket order created successfully", response)
        
    except ValueError as e:
        return error_response(f"Invalid data format: {str(e)}", 400)
    except Exception as e:
        return error_response(f"ShipRocket order creation failed: {str(e)}", 500)

@shiprocket_bp.route('/assign-awb', methods=['POST'])
@merchant_role_required
def assign_awb():
    """
    Assign AWB (Airway Bill) to shipment
    ---
    tags:
      - ShipRocket
    security:
      - Bearer: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required:
              - shipment_id
              - courier_id
            properties:
              shipment_id:
                type: integer
                description: ShipRocket shipment ID
              courier_id:
                type: integer
                description: Courier ID from serviceability response
    responses:
      200:
        description: AWB assigned successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            data:
              type: object
              properties:
                awb_code:
                  type: string
                courier_name:
                  type: string
      400:
        description: Invalid request data
      500:
        description: Internal server error
    """
    try:
        data = request.get_json()
        
        if not data:
            return error_response("Request data is required", 400)
        
        required_fields = ['shipment_id', 'courier_id']
        for field in required_fields:
            if field not in data:
                return error_response(f"Missing required field: {field}", 400)
        
        shipment_id = int(data['shipment_id'])
        courier_id = int(data['courier_id'])
        
        shiprocket = ShipRocketController()
        response = shiprocket.assign_awb(shipment_id, courier_id)
        
        return success_response("AWB assigned successfully", response)
        
    except ValueError as e:
        return error_response(f"Invalid data format: {str(e)}", 400)
    except Exception as e:
        return error_response(f"AWB assignment failed: {str(e)}", 500)

@shiprocket_bp.route('/generate-pickup', methods=['POST'])
@merchant_role_required
def generate_pickup():
    """
    Generate pickup request for shipment
    ---
    tags:
      - ShipRocket
    security:
      - Bearer: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required:
              - shipment_id
            properties:
              shipment_id:
                type: integer
                description: ShipRocket shipment ID
    responses:
      200:
        description: Pickup generated successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            data:
              type: object
              properties:
                message:
                  type: string
      400:
        description: Invalid request data
      500:
        description: Internal server error
    """
    try:
        data = request.get_json()
        
        if not data:
            return error_response("Request data is required", 400)
        
        if 'shipment_id' not in data:
            return error_response("Missing required field: shipment_id", 400)
        
        shipment_id = int(data['shipment_id'])
        
        shiprocket = ShipRocketController()
        response = shiprocket.generate_pickup(shipment_id)
        
        return success_response("Pickup generated successfully", response)
        
    except ValueError as e:
        return error_response(f"Invalid data format: {str(e)}", 400)
    except Exception as e:
        return error_response(f"Pickup generation failed: {str(e)}", 500)

@shiprocket_bp.route('/tracking/<awb_code>', methods=['GET'])
def get_tracking_details(awb_code):
    """
    Get tracking details for a shipment
    ---
    tags:
      - ShipRocket
    parameters:
      - in: path
        name: awb_code
        type: string
        required: true
        description: AWB code
    responses:
      200:
        description: Tracking details retrieved successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            data:
              type: object
              properties:
                tracking_data:
                  type: object
      400:
        description: Invalid AWB code
      500:
        description: Internal server error
    """
    try:
        if not awb_code:
            return error_response("AWB code is required", 400)
        
        shiprocket = ShipRocketController()
        response = shiprocket.get_tracking_details(awb_code)
        
        return success_response("Tracking details retrieved successfully", response)
        
    except Exception as e:
        return error_response(f"Tracking details fetch failed: {str(e)}", 500)

@shiprocket_bp.route('/tracking/order/<order_id>', methods=['GET'])
@jwt_required()
def get_tracking_by_order_id(order_id):
    """
    Get tracking details for a shipment using order ID
    ---
    tags:
      - ShipRocket
    security:
      - Bearer: []
    parameters:
      - in: path
        name: order_id
        type: string
        required: true
        description: Order ID from your store
      - name: channel_id
        in: query
        type: integer
        required: false
        description: Channel ID corresponding to the store
    responses:
      200:
        description: Tracking details retrieved successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            data:
              type: object
              properties:
                tracking_data:
                  type: object
      400:
        description: Invalid order ID
      500:
        description: Internal server error
    """
    try:
        if not order_id:
            return error_response("Order ID is required", 400)
        
        # Get channel_id from query parameters
        channel_id = request.args.get('channel_id', type=int)
        
        shiprocket = ShipRocketController()
        response = shiprocket.get_tracking_by_order_id(order_id, channel_id)
        
        return success_response("Tracking details retrieved successfully", response)
        
    except Exception as e:
        return error_response(f"Tracking details fetch failed: {str(e)}", 500)

@shiprocket_bp.route('/tracking/shipment/<int:shipment_id>', methods=['GET'])
@jwt_required()
def get_shipment_tracking(shipment_id):
    """
    Get tracking details for a shipment using shipment ID from database
    ---
    tags:
      - ShipRocket
    security:
      - Bearer: []
    parameters:
      - in: path
        name: shipment_id
        type: integer
        required: true
        description: Shipment ID from database
    responses:
      200:
        description: Tracking details retrieved successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            data:
              type: object
              properties:
                tracking_data:
                  type: object
      400:
        description: Invalid shipment ID
      404:
        description: Shipment not found
      500:
        description: Internal server error
    """
    try:
        if not shipment_id:
            return error_response("Shipment ID is required", 400)
        
        shiprocket = ShipRocketController()
        response = shiprocket.get_shipment_tracking(shipment_id)
        
        return success_response("Tracking details retrieved successfully", response)
        
    except Exception as e:
        return error_response(f"Tracking details fetch failed: {str(e)}", 500)

@shiprocket_bp.route('/tracking/db-order/<order_id>', methods=['GET'])
@jwt_required()
def get_tracking_by_db_order_id(order_id):
    """
    Get tracking details using order_id from your database Order model
    ---
    tags:
      - ShipRocket
    security:
      - Bearer: []
    parameters:
      - in: path
        name: order_id
        type: string
        required: true
        description: Order ID from your database (e.g., "ORD-20250728114812-8D8759")
    responses:
      200:
        description: Tracking details retrieved successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            data:
              type: object
              properties:
                order_id:
                  type: string
                shipments:
                  type: object
                  additionalProperties:
                    type: object
                    properties:
                      shipment_id:
                        type: integer
                      merchant_id:
                        type: integer
                      carrier_name:
                        type: string
                      tracking_number:
                        type: string
                      shiprocket_order_id:
                        type: integer
                      tracking_data:
                        type: object
                      error:
                        type: string
      400:
        description: Invalid order ID
      404:
        description: Order not found
      500:
        description: Internal server error
    """
    try:
        if not order_id:
            return error_response("Order ID is required", 400)
        
        shiprocket = ShipRocketController()
        response = shiprocket.get_tracking_by_db_order_id(order_id)
        
        return success_response("Tracking details retrieved successfully", response)
        
    except Exception as e:
        return error_response(f"Tracking details fetch failed: {str(e)}", 500)

# Previously: /bulk-create-orders — removed because delivery_address_id was
# hardcoded to None, making every call fail. The real bulk-ship path is
# /create-orders-for-all-merchants (below), which looks up the addresses from
# the order. Phase 2 Sprint 2 will add a proper merchant-side bulk-RTS endpoint
# that iterates over accepted orders and calls the single-order flow per order.


@shiprocket_bp.route('/create-orders-for-all-merchants', methods=['POST'])
@jwt_required()
def create_shiprocket_orders_for_all_merchants():
    """
    Create ShipRocket orders for all merchants involved in a single order
    ---
    tags:
      - ShipRocket
    security:
      - Bearer: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required:
              - order_id
              - delivery_address_id
            properties:
              order_id:
                type: string
                description: Internal order ID
              delivery_address_id:
                type: integer
                description: Delivery address ID
              courier_id:
                type: integer
                description: Preferred courier ID for all shipments (optional)
    responses:
      200:
        description: ShipRocket orders created successfully for all merchants
        schema:
          type: object
          properties:
            success:
              type: boolean
            data:
              type: object
              properties:
                total_merchants:
                  type: integer
                successful_merchants:
                  type: array
                  items:
                    type: integer
                failed_merchants:
                  type: array
                  items:
                    type: integer
                merchant_responses:
                  type: object
      400:
        description: Invalid request data
      404:
        description: Order or delivery address not found
      500:
        description: Internal server error
    """
    try:
        data = request.get_json()
        
        if not data:
            return error_response("Request data is required", 400)
        
        required_fields = ['order_id', 'delivery_address_id']
        for field in required_fields:
            if field not in data:
                return error_response(f"Missing required field: {field}", 400)
        
        order_id = data['order_id']
        delivery_address_id = int(data['delivery_address_id'])
        courier_id = int(data.get('courier_id')) if data.get('courier_id') else None
        
        # Validate order_id format
        if not order_id or not isinstance(order_id, str):
            return error_response("Invalid order_id format", 400)
        
        # Validate delivery_address_id
        if delivery_address_id <= 0:
            return error_response("Invalid delivery_address_id", 400)
        
        # Validate courier_id if provided
        if courier_id is not None and courier_id <= 0:
            return error_response("Invalid courier_id", 400)
        
        shiprocket = ShipRocketController()
        response = shiprocket.create_shiprocket_orders_for_all_merchants(
            order_id=order_id,
            delivery_address_id=delivery_address_id,
            courier_id=courier_id
        )
        
        return success_response("ShipRocket orders created successfully for all merchants", response)
        
    except ValueError as e:
        return error_response(f"Invalid data format: {str(e)}", 400)
    except Exception as e:
        return error_response(f"ShipRocket order creation failed: {str(e)}", 500)

@shiprocket_bp.route('/pickup-locations', methods=['GET'])
@jwt_required()
def get_pickup_locations():
    """
    Get all pickup locations from ShipRocket
    ---
    tags:
      - ShipRocket
    security:
      - Bearer: []
    responses:
      200:
        description: Pickup locations retrieved successfully
      500:
        description: Internal server error
    """
    try:
        shiprocket = ShipRocketController()
        response = shiprocket.get_pickup_locations()
        return success_response("Pickup locations retrieved successfully", response)
        
    except Exception as e:
        return error_response(f"Failed to get pickup locations: {str(e)}", 500)

@shiprocket_bp.route('/merchant/<int:merchant_id>/pickup-location', methods=['POST'])
@jwt_required()
def create_merchant_pickup_location(merchant_id):
    """
    Create pickup location for a merchant in ShipRocket
    ---
    tags:
      - ShipRocket
    security:
      - Bearer: []
    parameters:
      - in: path
        name: merchant_id
        required: true
        type: integer
        description: Merchant ID
    responses:
      200:
        description: Pickup location created successfully
      404:
        description: Merchant not found
      500:
        description: Internal server error
    """
    try:
        shiprocket = ShipRocketController()
        pickup_location_name = shiprocket.create_merchant_pickup_location(merchant_id)
        return success_response("Pickup location created successfully", {
            "pickup_location_name": pickup_location_name,
            "merchant_id": merchant_id
        })
        
    except Exception as e:
        return error_response(f"Failed to create pickup location: {str(e)}", 500)

@shiprocket_bp.route('/merchant/<int:merchant_id>/pickup-location', methods=['GET'])
@jwt_required()
def get_merchant_pickup_location(merchant_id):
    """
    Get or create pickup location for a merchant
    ---
    tags:
      - ShipRocket
    security:
      - Bearer: []
    parameters:
      - in: path
        name: merchant_id
        required: true
        type: integer
        description: Merchant ID
    responses:
      200:
        description: Pickup location retrieved/created successfully
      404:
        description: Merchant not found
      500:
        description: Internal server error
    """
    try:
        shiprocket = ShipRocketController()
        pickup_location_name = shiprocket.get_or_create_merchant_pickup_location(merchant_id)
        return success_response("Pickup location retrieved/created successfully", {
            "pickup_location_name": pickup_location_name,
            "merchant_id": merchant_id
        })
        
    except Exception as e:
        return error_response(f"Failed to get pickup location: {str(e)}", 500) 

@shiprocket_bp.route('/create-shop-order', methods=['POST'])
@jwt_required()
def create_shiprocket_shop_order():
    """
    Create ShipRocket order for a shop order where the shop is the primary pickup location
    ---
    tags:
      - ShipRocket
    security:
      - Bearer: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required:
              - shop_order_id
              - shop_id
              - delivery_address_id
            properties:
              shop_order_id:
                type: string
                description: Shop order ID from database
              shop_id:
                type: integer
                description: Shop ID
              delivery_address_id:
                type: integer
                description: Delivery address ID
              courier_id:
                type: integer
                description: Preferred courier ID (optional)
    responses:
      200:
        description: ShipRocket shop order created successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            data:
              type: object
              properties:
                shiprocket_order_id:
                  type: integer
                shipment_id:
                  type: integer
                awb_code:
                  type: string
                courier_name:
                  type: string
                tracking_number:
                  type: string
                serviceability:
                  type: object
                db_shipment:
                  type: object
                courier_data:
                  type: object
      400:
        description: Invalid request data
      404:
        description: Shop order, shop, or address not found
      500:
        description: Internal server error
    """
    try:
        data = request.get_json()
        
        if not data:
            return error_response("Request data is required", 400)
        
        required_fields = ['shop_order_id', 'shop_id', 'delivery_address_id']
        for field in required_fields:
            if field not in data:
                return error_response(f"Missing required field: {field}", 400)
        
        shop_order_id = data['shop_order_id']
        shop_id = int(data['shop_id'])
        delivery_address_id = int(data['delivery_address_id'])
        courier_id = int(data.get('courier_id')) if data.get('courier_id') else None
        
        # Validate shop_order_id format
        if not shop_order_id or not isinstance(shop_order_id, str):
            return error_response("Invalid shop_order_id format", 400)
        
        # Validate shop_id
        if shop_id <= 0:
            return error_response("Invalid shop_id", 400)
        
        # Validate delivery_address_id
        if delivery_address_id <= 0:
            return error_response("Invalid delivery_address_id", 400)
        
        # Validate courier_id if provided
        if courier_id is not None and courier_id <= 0:
            return error_response("Invalid courier_id", 400)
        
        shiprocket = ShipRocketController()
        response = shiprocket.create_shiprocket_order_for_shop(
            shop_order_id=shop_order_id,
            shop_id=shop_id,
            delivery_address_id=delivery_address_id,
            courier_id=courier_id
        )
        
        return success_response("ShipRocket shop order created successfully", response)
        
    except ValueError as e:
        return error_response(f"Invalid data format: {str(e)}", 400)
    except Exception as e:
        return error_response(f"ShipRocket shop order creation failed: {str(e)}", 500)

@shiprocket_bp.route('/shop/<int:shop_id>/pickup-location', methods=['POST'])
@jwt_required()
def create_shop_pickup_location(shop_id):
    """
    Create pickup location for a shop in ShipRocket
    ---
    tags:
      - ShipRocket
    security:
      - Bearer: []
    parameters:
      - in: path
        name: shop_id
        required: true
        type: integer
        description: Shop ID
    responses:
      200:
        description: Shop pickup location created successfully
      404:
        description: Shop not found
      500:
        description: Internal server error
    """
    try:
        shiprocket = ShipRocketController()
        pickup_location_name = shiprocket.create_shop_pickup_location(shop_id)
        return success_response("Shop pickup location created successfully", {
            "pickup_location_name": pickup_location_name,
            "shop_id": shop_id
        })
        
    except Exception as e:
        return error_response(f"Failed to create shop pickup location: {str(e)}", 500)

@shiprocket_bp.route('/shop/<int:shop_id>/pickup-location', methods=['GET'])
@jwt_required()
def get_shop_pickup_location(shop_id):
    """
    Get or create pickup location for a shop
    ---
    tags:
      - ShipRocket
    security:
      - Bearer: []
    parameters:
      - in: path
        name: shop_id
        required: true
        type: integer
        description: Shop ID
    responses:
      200:
        description: Shop pickup location retrieved/created successfully
      404:
        description: Shop not found
      500:
        description: Internal server error
    """
    try:
        shiprocket = ShipRocketController()
        pickup_location_name = shiprocket.get_or_create_shop_pickup_location(shop_id)
        return success_response("Shop pickup location retrieved/created successfully", {
            "pickup_location_name": pickup_location_name,
            "shop_id": shop_id
        })

    except Exception as e:
        return error_response(f"Failed to get shop pickup location: {str(e)}", 500)


@shiprocket_bp.route('/return-pickup', methods=['POST'])
@merchant_role_required
def create_return_pickup():
    """
    Schedule a reverse (return) pickup for an existing shipment.

    Body: {"shipment_id": <int>}

    Looks up the original forward shipment, builds the SR return payload using
    the stored customer delivery address (now source) and merchant pickup
    address (now destination), calls SR's reverse pickup API, then stores the
    reverse AWB + return_shipment_id back on the Shipment row.

    Phase 3 wiring (not here): the return approval flow will call this
    automatically when a merchant approves a return request.
    """
    from models.shipment import Shipment
    from common.database import db

    data = request.get_json(silent=True) or {}
    shipment_id = data.get("shipment_id")
    if not shipment_id:
        return error_response("shipment_id is required", 400)

    shipment = Shipment.query.filter_by(shipment_id=shipment_id).first()
    if not shipment:
        return error_response(f"Shipment {shipment_id} not found", 404)
    if not shipment.shiprocket_order_id:
        return error_response(
            "Shipment has no Shiprocket order ID — cannot reverse-pickup", 400
        )
    if shipment.reverse_awb_code:
        return success_response(
            "Return pickup already exists for this shipment",
            {
                "shipment_id": shipment.shipment_id,
                "reverse_awb_code": shipment.reverse_awb_code,
                "return_shipment_id": shipment.return_shipment_id,
            },
        )

    pickup = shipment.delivery_address   # customer address — picked up FROM
    drop = shipment.pickup_address       # merchant address — delivered TO
    if not pickup or not drop:
        return error_response(
            "Both pickup and delivery addresses must exist on the original shipment",
            400,
        )

    return_payload = {
        "order_id": f"RTN-{shipment.order_id}-{shipment.shipment_id}",
        "order_date": shipment.shipped_date.strftime("%Y-%m-%d %H:%M") if shipment.shipped_date else "",
        "channel_id": "",
        "pickup_customer_name": pickup.contact_name or "",
        "pickup_last_name": "",
        "pickup_address": pickup.address_line1 or "",
        "pickup_address_2": getattr(pickup, "address_line2", "") or "",
        "pickup_city": pickup.city,
        "pickup_state": pickup.state_province,
        "pickup_country": pickup.country_code or "India",
        "pickup_pincode": pickup.postal_code,
        "pickup_email": "",
        "pickup_phone": pickup.contact_phone or "",
        "shipping_customer_name": drop.contact_name or "",
        "shipping_last_name": "",
        "shipping_address": drop.address_line1 or "",
        "shipping_address_2": getattr(drop, "address_line2", "") or "",
        "shipping_city": drop.city,
        "shipping_state": drop.state_province,
        "shipping_country": drop.country_code or "India",
        "shipping_pincode": drop.postal_code,
        "shipping_email": "",
        "shipping_phone": drop.contact_phone or "",
        "order_items": [{
            "name": f"Return for order {shipment.order_id}",
            "sku": f"RTN-{shipment.order_id}",
            "units": 1,
            "selling_price": "1",
        }],
        "payment_method": "Prepaid",
        "sub_total": 1,
        "length": 10, "breadth": 10, "height": 10, "weight": 0.5,
    }

    try:
        shiprocket = ShipRocketController()
        response = shiprocket.create_return_order(return_payload)
    except Exception as e:
        current_app.logger.exception("Return pickup SR call failed")
        return error_response(f"Reverse pickup creation failed: {str(e)}", 502)

    if response.get("status") != 200:
        return error_response(
            f"Shiprocket rejected reverse pickup: {response.get('message', 'Unknown error')}",
            502,
        )

    sr_data = response.get("data", {}) or {}
    shipment.return_shipment_id = sr_data.get("shipment_id")
    shipment.reverse_awb_code = (
        sr_data.get("awb_code")
        or (sr_data.get("response", {}) or {}).get("awb_code")
    )
    db.session.commit()

    return success_response("Return pickup scheduled successfully", {
        "shipment_id": shipment.shipment_id,
        "return_shipment_id": shipment.return_shipment_id,
        "reverse_awb_code": shipment.reverse_awb_code,
        "shiprocket_response": response,
    })


@shiprocket_bp.route('/webhook', methods=['POST'])
def shiprocket_webhook():
    """
    Receive shipment status updates pushed by Shiprocket.

    Shiprocket calls this endpoint whenever a shipment progresses (shipped →
    in-transit → out-for-delivery → delivered, or exception/RTO). We verify
    the shared-secret token (header `X-Api-Key`) matches
    `SHIPROCKET_WEBHOOK_TOKEN` configured on the server, then update the
    local Shipment + Order rows.

    Public endpoint — not JWT-protected — since Shiprocket cannot send our
    user tokens. Security = token in header.
    """
    expected_token = os.getenv("SHIPROCKET_WEBHOOK_TOKEN")
    received_token = request.headers.get("X-Api-Key") or request.headers.get("x-api-key")

    payload = request.get_json(silent=True)

    try:
        result = process_shiprocket_webhook(
            payload=payload or {},
            received_token=received_token,
            expected_token=expected_token,
        )
    except WebhookError as e:
        current_app.logger.warning(f"Shiprocket webhook rejected: {e}")
        return jsonify({"status": "error", "message": str(e)}), e.http_status
    except Exception as e:
        current_app.logger.exception("Shiprocket webhook handler crashed")
        return jsonify({"status": "error", "message": "Internal error"}), 500

    # Return 200 quickly so Shiprocket doesn't retry
    return jsonify({"status": "ok", "changes": result}), 200 