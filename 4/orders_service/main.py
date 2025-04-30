from fastapi import FastAPI, HTTPException, Depends, status, Request
from enum import Enum
from pydantic import BaseModel, Field, validator, model_validator
from datetime import datetime, date
import logging
import os
from typing import List, Optional
import httpx
import re
import asyncpg
from asyncpg.pool import Pool
from asyncpg.exceptions import UniqueViolationError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth-service:8000")

# Database connection pool
database_pool: Optional[Pool] = None

async def create_db_pool():
    global database_pool
    database_pool = await asyncpg.create_pool(
        host=os.getenv("DB_HOST", "postgres"),
        port=os.getenv("DB_PORT", "5432"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "postgres123"),
        database=os.getenv("DB_NAME", "order_service"),
        min_size=1,
        max_size=10
    )

async def close_db_pool():
    global database_pool
    if database_pool:
        await database_pool.close()

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

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    await create_db_pool()
    logger.info("Database connection pool created")

@app.on_event("shutdown")
async def shutdown_event():
    await close_db_pool()
    logger.info("Database connection pool closed")


async def get_current_user(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing"
        )
    
    token = auth_header.split(" ")[1] if " " in auth_header else auth_header
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
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

async def get_current_active_user(current_user: UserPublic = Depends(get_current_user)):
    return current_user

async def require_client(current_user: UserPublic = Depends(get_current_active_user)):
    if current_user.role != Role.CLIENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only clients can perform this action"
        )
    return current_user

async def require_courier(current_user: UserPublic = Depends(get_current_active_user)):
    if current_user.role != Role.COURIER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only couriers can perform this action"
        )
    return current_user


@app.post("/orders/", status_code=status.HTTP_201_CREATED, response_model=Order)
async def create_order(
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

    async with database_pool.acquire() as conn:
        try:
            # Start transaction
            async with conn.transaction():
                # Insert order
                order_record = await conn.fetchrow(
                    """INSERT INTO orders (
                        client_id, total_amount, status, payment_method, 
                        delivery_type, created_at, updated_at, 
                        estimated_delivery, notes
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    RETURNING order_id, client_id, total_amount, status, 
                    payment_method, delivery_type, created_at, updated_at, 
                    estimated_delivery, notes""",
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

                # Insert order items
                for item in order.items:
                    await conn.execute(
                        """INSERT INTO order_items (
                            order_id, product_id, name, quantity, price
                        ) VALUES ($1, $2, $3, $4, $5)""",
                        order_record["order_id"],
                        item.product_id,
                        item.name,
                        item.quantity,
                        item.price
                    )

                # If delivery address is provided, insert it
                delivery_address_id = None
                if order.delivery_address and order.delivery_type != DeliveryType.PICKUP:
                    address = order.delivery_address
                    addr_record = await conn.fetchrow(
                        """INSERT INTO addresses (
                            user_id, street, city, postal_code, country, is_default
                        ) VALUES ($1, $2, $3, $4, $5, $6)
                        RETURNING address_id""",
                        current_user.user_id,
                        address.street,
                        address.city,
                        address.postal_code,
                        address.country,
                        False
                    )
                    delivery_address_id = addr_record["address_id"]

                    # Update order with delivery address
                    await conn.execute(
                        "UPDATE orders SET delivery_address_id = $1 WHERE order_id = $2",
                        delivery_address_id,
                        order_record["order_id"]
                    )

                # Get all items for the response
                items = await conn.fetch(
                    "SELECT * FROM order_items WHERE order_id = $1",
                    order_record["order_id"]
                )

                # Get delivery address if exists
                delivery_address = None
                if delivery_address_id:
                    addr = await conn.fetchrow(
                        "SELECT * FROM addresses WHERE address_id = $1",
                        delivery_address_id
                    )
                    delivery_address = Address(
                        street=addr["street"],
                        city=addr["city"],
                        postal_code=addr["postal_code"],
                        country=addr["country"]
                    )

                new_order = Order(
                    order_id=order_record["order_id"],
                    client_id=order_record["client_id"],
                    items=[OrderItem(**item) for item in items],
                    total_amount=float(order_record["total_amount"]),
                    status=OrderStatus(order_record["status"]),
                    payment_method=PaymentMethod(order_record["payment_method"]),
                    delivery_type=DeliveryType(order_record["delivery_type"]),
                    delivery_address=delivery_address,
                    created_at=order_record["created_at"],
                    updated_at=order_record["updated_at"],
                    estimated_delivery=order_record["estimated_delivery"],
                    notes=order_record["notes"]
                )

                logger.info(f"Order {new_order.order_id} created by {current_user.username}")
                return new_order

        except UniqueViolationError as e:
            logger.error(f"Database error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order creation failed"
            )

@app.get("/orders/", response_model=List[Order])
async def read_orders(current_user: UserPublic = Depends(require_client)):
    async with database_pool.acquire() as conn:
        orders = await conn.fetch(
            "SELECT * FROM orders WHERE client_id = $1",
            current_user.user_id
        )

        result = []
        for order in orders:
            # Get order items
            items = await conn.fetch(
                "SELECT * FROM order_items WHERE order_id = $1",
                order["order_id"]
            )

            # Get delivery address if exists
            delivery_address = None
            if order["delivery_address_id"]:
                addr = await conn.fetchrow(
                    "SELECT * FROM addresses WHERE address_id = $1",
                    order["delivery_address_id"]
                )
                delivery_address = Address(
                    street=addr["street"],
                    city=addr["city"],
                    postal_code=addr["postal_code"],
                    country=addr["country"]
                )

            result.append(Order(
                order_id=order["order_id"],
                client_id=order["client_id"],
                items=[OrderItem(**item) for item in items],
                total_amount=float(order["total_amount"]),
                status=OrderStatus(order["status"]),
                payment_method=PaymentMethod(order["payment_method"]),
                delivery_type=DeliveryType(order["delivery_type"]),
                delivery_address=delivery_address,
                created_at=order["created_at"],
                updated_at=order["updated_at"],
                delivered_at=order["delivered_at"],
                estimated_delivery=order["estimated_delivery"],
                notes=order["notes"]
            ))

        return result

@app.get("/orders/{order_id}", response_model=Order)
async def read_order(
    order_id: int,
    current_user: UserPublic = Depends(get_current_active_user)
):
    async with database_pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT * FROM orders WHERE order_id = $1",
            order_id
        )

        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        if order["client_id"] != current_user.user_id and current_user.role not in [Role.ADMIN, Role.COURIER]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this order"
            )

        # Get order items
        items = await conn.fetch(
            "SELECT * FROM order_items WHERE order_id = $1",
            order_id
        )

        # Get delivery address if exists
        delivery_address = None
        if order["delivery_address_id"]:
            addr = await conn.fetchrow(
                "SELECT * FROM addresses WHERE address_id = $1",
                order["delivery_address_id"]
            )
            delivery_address = Address(
                street=addr["street"],
                city=addr["city"],
                postal_code=addr["postal_code"],
                country=addr["country"]
            )

        return Order(
            order_id=order["order_id"],
            client_id=order["client_id"],
            items=[OrderItem(**item) for item in items],
            total_amount=float(order["total_amount"]),
            status=OrderStatus(order["status"]),
            payment_method=PaymentMethod(order["payment_method"]),
            delivery_type=DeliveryType(order["delivery_type"]),
            delivery_address=delivery_address,
            created_at=order["created_at"],
            updated_at=order["updated_at"],
            delivered_at=order["delivered_at"],
            estimated_delivery=order["estimated_delivery"],
            notes=order["notes"]
        )

@app.put("/orders/{order_id}/status", response_model=Order)
async def update_order_status(
    order_id: int,
    order_update: OrderUpdate,
    current_user: UserPublic = Depends(require_courier)
):
    now = datetime.utcnow()
    updates = []
    params = [now, order_id]
    
    if order_update.status:
        updates.append("status = $3")
        params.append(order_update.status.value)
        if order_update.status == OrderStatus.DELIVERED:
            updates.append("delivered_at = $4")
            params.append(now)
    
    if order_update.estimated_delivery:
        pos = len(params) + 1
        updates.append(f"estimated_delivery = ${pos}")
        params.append(order_update.estimated_delivery)
    
    if order_update.notes:
        pos = len(params) + 1
        updates.append(f"notes = ${pos}")
        params.append(order_update.notes)
    
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update"
        )

    async with database_pool.acquire() as conn:
        query = f"""
            UPDATE orders 
            SET updated_at = $1, {', '.join(updates)}
            WHERE order_id = $2
            RETURNING *
        """
        order = await conn.fetchrow(query, *params)

        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        # Get order items
        items = await conn.fetch(
            "SELECT * FROM order_items WHERE order_id = $1",
            order_id
        )

        # Get delivery address if exists
        delivery_address = None
        if order["delivery_address_id"]:
            addr = await conn.fetchrow(
                "SELECT * FROM addresses WHERE address_id = $1",
                order["delivery_address_id"]
            )
            delivery_address = Address(
                street=addr["street"],
                city=addr["city"],
                postal_code=addr["postal_code"],
                country=addr["country"]
            )

        logger.info(f"Order {order_id} updated by {current_user.username}")
        return Order(
            order_id=order["order_id"],
            client_id=order["client_id"],
            items=[OrderItem(**item) for item in items],
            total_amount=float(order["total_amount"]),
            status=OrderStatus(order["status"]),
            payment_method=PaymentMethod(order["payment_method"]),
            delivery_type=DeliveryType(order["delivery_type"]),
            delivery_address=delivery_address,
            created_at=order["created_at"],
            updated_at=order["updated_at"],
            delivered_at=order["delivered_at"],
            estimated_delivery=order["estimated_delivery"],
            notes=order["notes"]
        )

@app.delete("/orders/{order_id}")
async def delete_order(
    order_id: int,
    current_user: UserPublic = Depends(require_client)
):
    async with database_pool.acquire() as conn:
        async with conn.transaction():
            # First check if order exists and belongs to user
            order = await conn.fetchrow(
                "SELECT * FROM orders WHERE order_id = $1",
                order_id
            )

            if not order:
                raise HTTPException(status_code=404, detail="Order not found")
            
            if order["client_id"] != current_user.user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cannot delete another user's order"
                )
            
            if order["status"] not in [OrderStatus.CREATED.value, OrderStatus.PROCESSING.value]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot delete order that has already been shipped"
                )
            
            # Delete order (cascade will delete order_items)
            await conn.execute(
                "DELETE FROM orders WHERE order_id = $1",
                order_id
            )
            
            logger.info(f"Order {order_id} deleted by {current_user.username}")
            return {"message": "Order deleted successfully"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")