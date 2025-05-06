from fastapi import FastAPI, HTTPException, Depends, status, Request
from enum import Enum
from pydantic import BaseModel, Field, validator, model_validator
from datetime import datetime, date
import logging
import os
from typing import List, Optional
import httpx
import re
import psycopg2
from psycopg2 import pool
import redis
import json
from contextlib import contextmanager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth-service:8000")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# Database connection pool and Redis client
database_pool = None
redis_client = None

app = FastAPI()

@app.on_event("startup")
def startup_event():
    global database_pool, redis_client
    create_db_pool()
    create_redis_client()
    logger.info("Database connection pool and Redis client created")

@app.on_event("shutdown")
def shutdown_event():
    close_db_pool()
    close_redis_client()
    logger.info("Database connection pool and Redis client closed")

def create_db_pool():
    global database_pool
    database_pool = psycopg2.pool.SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        host=os.getenv("DB_HOST", "postgres"),
        port=os.getenv("DB_PORT", "5432"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "postgres123"),
        database=os.getenv("DB_NAME", "order_service")
    )

def close_db_pool():
    global database_pool
    if database_pool:
        database_pool.closeall()

def create_redis_client():
    global redis_client
    try:
        redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            decode_responses=False  
        )
        if not redis_client.ping():
            logger.error("Redis ping failed")
            redis_client = None
        else:
            logger.info("Successfully connected to Redis")
    except redis.ConnectionError as e:
        logger.error(f"Cannot connect to Redis: {str(e)}")
        redis_client = None

def close_redis_client():
    global redis_client
    if redis_client:
        redis_client.close()

@contextmanager
def get_db_connection():
    conn = None
    try:
        conn = database_pool.getconn()
        yield conn
    finally:
        if conn:
            database_pool.putconn(conn)

@contextmanager
def get_db_cursor():
    with get_db_connection() as conn:
        cursor = None
        try:
            cursor = conn.cursor()
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            if cursor:
                cursor.close()

class Role(str, Enum):
    CLIENT = "client"
    ADMIN = "admin"
    COURIER = "courier"
    WAREHOUSE_MANAGER = "warehouse_manager"

class OrderStatus(str, Enum):
    CREATED = "created"
    PROCESSING = "processing"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

class PaymentMethod(str, Enum):
    CASH = "cash"
    CARD = "card"
    ONLINE = "online"

class DeliveryType(str, Enum):
    STANDARD = "standard"
    EXPRESS = "express"
    PICKUP = "pickup"

class UserPublic(BaseModel):
    user_id: int
    username: str = Field(..., min_length=3, max_length=50)
    full_name: Optional[str] = Field(None, max_length=100)
    role: Role

class Address(BaseModel):
    street: str = Field(..., max_length=100)
    city: str = Field(..., max_length=50)
    postal_code: str = Field(..., max_length=20)
    country: str = Field(..., max_length=50)
    
    @validator('postal_code')
    def validate_postal_code(cls, v):
        if not re.match(r'^[a-zA-Z0-9\- ]+$', v):
            raise ValueError('Invalid postal code format')
        return v

class OrderItem(BaseModel):
    product_id: int
    name: str = Field(..., max_length=100)
    quantity: int = Field(..., gt=0)
    price: float = Field(..., gt=0)
    
    @validator('price')
    def round_price(cls, v):
        return round(v, 2)

class Order(BaseModel):
    order_id: int
    client_id: int
    items: List[OrderItem]
    total_amount: float = Field(..., gt=0)
    status: OrderStatus = OrderStatus.CREATED
    payment_method: PaymentMethod
    delivery_type: DeliveryType
    delivery_address: Optional[Address] = None
    created_at: datetime
    updated_at: datetime
    delivered_at: Optional[datetime] = None
    estimated_delivery: Optional[date] = None
    notes: Optional[str] = Field(None, max_length=500)
    
    @validator('total_amount')
    def validate_total_amount(cls, v, values):
        if 'items' in values:
            calculated_total = sum(item.price * item.quantity for item in values['items'])
            if abs(v - calculated_total) > 0.01:  
                raise ValueError('Total amount does not match sum of items')
        return round(v, 2)
    
    @model_validator(mode="after")
    def validate_delivery_fields(cls, values):
        delivery_type = values.delivery_type
        delivery_address = values.delivery_address
        
        if delivery_type != DeliveryType.PICKUP and not delivery_address:
            raise ValueError('Delivery address is required for non-pickup orders')
        
        if delivery_type == DeliveryType.PICKUP and delivery_address:
            raise ValueError('Delivery address should not be provided for pickup orders')
            
        return values

class OrderCreate(BaseModel):
    client_id: int
    items: List[OrderItem]
    payment_method: PaymentMethod
    delivery_type: DeliveryType
    delivery_address: Optional[Address] = None
    estimated_delivery: Optional[date] = None
    notes: Optional[str] = Field(None, max_length=500)
    
    @validator('estimated_delivery')
    def validate_estimated_delivery(cls, v):
        if v and v < date.today():
            raise ValueError('Estimated delivery date cannot be in the past')
        return v

class OrderUpdate(BaseModel):
    status: Optional[OrderStatus] = None
    estimated_delivery: Optional[date] = None
    notes: Optional[str] = Field(None, max_length=500)
    
    @validator('estimated_delivery')
    def validate_estimated_delivery(cls, v):
        if v and v < date.today():
            raise ValueError('Estimated delivery date cannot be in the past')
        return v

def get_current_user(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing"
        )
    
    token = auth_header.split(" ")[1] if " " in auth_header else auth_header
    
    try:
        response = httpx.get(
            f"{AUTH_SERVICE_URL}/users/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail="Could not validate credentials"
            )
            
        return UserPublic(**response.json())
        
    except httpx.RequestError as e:
        logger.error(f"Auth service connection error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable"
        )

def get_current_active_user(current_user: UserPublic = Depends(get_current_user)):
    return current_user

def require_client(current_user: UserPublic = Depends(get_current_active_user)):
    if current_user.role != Role.CLIENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only clients can perform this action"
        )
    return current_user

def require_courier(current_user: UserPublic = Depends(get_current_active_user)):
    if current_user.role != Role.COURIER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only couriers can perform this action"
        )
    return current_user

def get_order_cache_key(order_id: int) -> str:
    return f"order:{order_id}"

def get_order_from_cache(order_id: int) -> Optional[Order]:
    if not redis_client:
        logger.warning("Redis client not available")
        return None
    
    try:
        cached_data = redis_client.get(get_order_cache_key(order_id))
        if cached_data:
            order_data = json.loads(cached_data.decode('utf-8'))
            logger.info(f"Retrieved order {order_id} from cache")
            return Order(**order_data)
    except json.JSONDecodeError as e:
        logger.error(f"Cache decode error: {str(e)}")
    except redis.RedisError as e:
        logger.error(f"Redis error: {str(e)}")
    return None

def set_order_to_cache(order: Order):
    if not redis_client:
        logger.warning("Redis client not available")
        return
    
    try:
        order_data = json.dumps(order.dict(), default=str)
        redis_client.setex(
            get_order_cache_key(order.order_id),
            3600,  # 1 hour TTL
            order_data
        )
        logger.info(f"Order {order.order_id} cached successfully")
    except (TypeError, ValueError) as e:
        logger.error(f"Serialization error: {str(e)}")
    except redis.RedisError as e:
        logger.error(f"Redis error: {str(e)}")

def invalidate_order_cache(order_id: int):
    if not redis_client:
        return
    
    try:
        redis_client.delete(get_order_cache_key(order_id))
        logger.info(f"Invalidated cache for order {order_id}")
    except redis.RedisError as e:
        logger.error(f"Redis error: {str(e)}")

def get_user_orders_cache_key(user_id: int) -> str:
    return f"user_orders:{user_id}"

def invalidate_user_orders_cache(user_id: int):
    if not redis_client:
        return
    
    try:
        redis_client.delete(get_user_orders_cache_key(user_id))
        logger.info(f"Invalidated orders cache for user {user_id}")
    except redis.RedisError as e:
        logger.error(f"Redis error: {str(e)}")

def get_user_orders_from_cache(user_id: int) -> Optional[List[Order]]:
    if not redis_client:
        return None
    
    try:
        cached_data = redis_client.get(get_user_orders_cache_key(user_id))
        if cached_data:
            orders_data = json.loads(cached_data.decode('utf-8'))
            logger.info(f"Retrieved orders for user {user_id} from cache")
            return [Order(**order_data) for order_data in orders_data]
    except json.JSONDecodeError as e:
        logger.error(f"Cache decode error: {str(e)}")
    except redis.RedisError as e:
        logger.error(f"Redis error: {str(e)}")
    return None

def set_user_orders_to_cache(user_id: int, orders: List[Order]):
    if not redis_client:
        return
    
    try:
        orders_data = json.dumps([order.dict() for order in orders], default=str)
        redis_client.setex(
            get_user_orders_cache_key(user_id),
            300,  # 5 minutes TTL
            orders_data
        )
        logger.info(f"Cached orders for user {user_id}")
    except (TypeError, ValueError) as e:
        logger.error(f"Serialization error: {str(e)}")
    except redis.RedisError as e:
        logger.error(f"Redis error: {str(e)}")

def fetch_full_order_from_db(cursor, order_id: int) -> Optional[Order]:
    cursor.execute(
        """
        SELECT order_id, client_id, total_amount, status, payment_method,
               delivery_type, delivery_address_id, created_at, updated_at,
               delivered_at, estimated_delivery, notes
        FROM orders
        WHERE order_id = %s
        """,
        (order_id,)
    )
    order = cursor.fetchone()
    
    if not order:
        return None
    
    cursor.execute("SELECT * FROM order_items WHERE order_id = %s", (order_id,))
    items = cursor.fetchall()

    delivery_address = None
    if order[6]:  # delivery_address_id
        cursor.execute("SELECT * FROM addresses WHERE address_id = %s", (order[6],))
        addr = cursor.fetchone()
        if addr:
            delivery_address = Address(
                street=addr[2],
                city=addr[3],
                postal_code=addr[4],
                country=addr[5]
            )

    total_amount = order[2]
    if total_amount is None:
        total_amount = sum(item[4] * item[5] for item in items)  # quantity * price
        logger.warning(f"Order {order_id} had NULL total_amount, calculated as {total_amount}")

    return Order(
        order_id=order[0],
        client_id=order[1],
        items=[OrderItem(
            product_id=item[2],
            name=item[3],
            quantity=item[4],
            price=item[5]
        ) for item in items],
        total_amount=float(total_amount),
        status=OrderStatus(order[3]),
        payment_method=PaymentMethod(order[4]),
        delivery_type=DeliveryType(order[5]),
        delivery_address=delivery_address,
        created_at=order[7],
        updated_at=order[8],
        delivered_at=order[9],
        estimated_delivery=order[10],
        notes=order[11]
    )

def fetch_user_orders_from_db(cursor, user_id: int) -> List[Order]:
    cursor.execute("SELECT order_id FROM orders WHERE client_id = %s", (user_id,))
    orders = cursor.fetchall()
    
    result = []
    for order in orders:
        full_order = fetch_full_order_from_db(cursor, order[0])
        if full_order:
            result.append(full_order)
    
    return result

@app.post("/orders/", status_code=status.HTTP_201_CREATED, response_model=Order)
def create_order(
    order: OrderCreate,
    current_user: UserPublic = Depends(require_client)
):
    if order.client_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Can only create orders for yourself"
        )

    total_amount = sum(item.price * item.quantity for item in order.items)
    now = datetime.utcnow()

    with get_db_cursor() as cursor:
        try:
            # Insert order
            cursor.execute(
                """INSERT INTO orders (
                    client_id, total_amount, status, payment_method, 
                    delivery_type, created_at, updated_at, 
                    estimated_delivery, notes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING order_id""",
                (
                    current_user.user_id,
                    total_amount,
                    OrderStatus.CREATED.value,
                    order.payment_method.value,
                    order.delivery_type.value,
                    now,
                    now,
                    order.estimated_delivery,
                    order.notes
                )
            )
            order_id = cursor.fetchone()[0]

            # Insert order items
            for item in order.items:
                cursor.execute(
                    """INSERT INTO order_items (
                        order_id, product_id, name, quantity, price
                    ) VALUES (%s, %s, %s, %s, %s)""",
                    (order_id, item.product_id, item.name, item.quantity, item.price)
                )

            # If delivery address is provided
            delivery_address_id = None
            if order.delivery_address and order.delivery_type != DeliveryType.PICKUP:
                address = order.delivery_address
                cursor.execute(
                    """INSERT INTO addresses (
                        user_id, street, city, postal_code, country, is_default
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING address_id""",
                    (
                        current_user.user_id,
                        address.street,
                        address.city,
                        address.postal_code,
                        address.country,
                        False
                    )
                )
                delivery_address_id = cursor.fetchone()[0]

                cursor.execute(
                    "UPDATE orders SET delivery_address_id = %s WHERE order_id = %s",
                    (delivery_address_id, order_id)
                )

            # Get full order
            new_order = fetch_full_order_from_db(cursor, order_id)
            
            # Invalidate cache
            invalidate_user_orders_cache(current_user.user_id)
            
            logger.info(f"Order {order_id} created by {current_user.username}")
            return new_order

        except Exception as e:
            logger.error(f"Database error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order creation failed"
            )

@app.get("/orders/", response_model=List[Order])
def read_orders(current_user: UserPublic = Depends(require_client)):
    # Try cache first
    cached_orders = get_user_orders_from_cache(current_user.user_id)
    if cached_orders is not None:
        logger.info(f"Returning cached orders for user {current_user.user_id}")
        return cached_orders

    with get_db_cursor() as cursor:
        orders = fetch_user_orders_from_db(cursor, current_user.user_id)
        
        # Cache the result
        set_user_orders_to_cache(current_user.user_id, orders)
        
        return orders

@app.get("/orders/{order_id}", response_model=Order)
def read_order(
    order_id: int,
    current_user: UserPublic = Depends(get_current_active_user)
):
    # Try cache first
    cached_order = get_order_from_cache(order_id)
    if cached_order is not None:
        if cached_order.client_id != current_user.user_id and current_user.role not in [Role.ADMIN, Role.COURIER]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this order"
            )
        logger.info(f"Returning cached order {order_id}")
        return cached_order

    with get_db_cursor() as cursor:
        order = fetch_full_order_from_db(cursor, order_id)

        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        if order.client_id != current_user.user_id and current_user.role not in [Role.ADMIN, Role.COURIER]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this order"
            )

        # Cache the order
        set_order_to_cache(order)
        
        return order

@app.put("/orders/{order_id}/status", response_model=Order)
def update_order_status(
    order_id: int,
    order_update: OrderUpdate,
    current_user: UserPublic = Depends(require_courier)
):
    now = datetime.utcnow()
    updates = []
    params = [now, order_id]
    
    if order_update.status:
        updates.append("status = %s")
        params.append(order_update.status.value)
        if order_update.status == OrderStatus.DELIVERED:
            updates.append("delivered_at = %s")
            params.append(now)
    
    if order_update.estimated_delivery:
        updates.append("estimated_delivery = %s")
        params.append(order_update.estimated_delivery)
    
    if order_update.notes:
        updates.append("notes = %s")
        params.append(order_update.notes)
    
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update"
        )

    with get_db_cursor() as cursor:
        set_clause = ", ".join(updates)
        query = f"""
            UPDATE orders 
            SET updated_at = %s, {set_clause}
            WHERE order_id = %s
            RETURNING *
        """
        cursor.execute(query, params)
        order_record = cursor.fetchone()

        if not order_record:
            raise HTTPException(status_code=404, detail="Order not found")

        # Get full updated order
        updated_order = fetch_full_order_from_db(cursor, order_id)
        
        # Update cache
        set_order_to_cache(updated_order)
        invalidate_user_orders_cache(updated_order.client_id)

        logger.info(f"Order {order_id} updated by {current_user.username}")
        return updated_order

@app.delete("/orders/{order_id}")
def delete_order(
    order_id: int,
    current_user: UserPublic = Depends(require_client)
):
    with get_db_cursor() as cursor:
        try:
            # Check if order exists and belongs to user
            cursor.execute(
                "SELECT client_id, status FROM orders WHERE order_id = %s",
                (order_id,)
            )
            order = cursor.fetchone()

            if not order:
                raise HTTPException(status_code=404, detail="Order not found")
            
            if order[0] != current_user.user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cannot delete another user's order"
                )
            
            if order[1] not in [OrderStatus.CREATED.value, OrderStatus.PROCESSING.value]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot delete order that has already been shipped"
                )
            
            # Delete order
            cursor.execute("DELETE FROM orders WHERE order_id = %s", (order_id,))
            
            # Invalidate caches
            invalidate_order_cache(order_id)
            invalidate_user_orders_cache(current_user.user_id)
            
            logger.info(f"Order {order_id} deleted by {current_user.username}")
            return {"message": "Order deleted successfully"}
        except Exception as e:
            logger.error(f"Error deleting order: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error deleting order"
            )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")