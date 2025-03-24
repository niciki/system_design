from fastapi import FastAPI, HTTPException, Depends, status, Request
from enum import Enum
from pydantic import BaseModel, Field, validator, model_validator
from datetime import datetime, date
import logging
import os
from typing import List, Optional
from pydantic.types import constr
import httpx
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth-service:8000")

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

fake_orders_db = {}
order_id_counter = 1

app = FastAPI()

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
    global order_id_counter

    if order.client_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Can only create orders for yourself"
        )

    order_id = order_id_counter
    order_id_counter += 1
    
    total_amount = sum(item.price * item.quantity for item in order.items)
    now = datetime.utcnow()
    
    new_order = Order(
        order_id=order_id,
        client_id=current_user.user_id,
        items=order.items,
        total_amount=total_amount,
        payment_method=order.payment_method,
        delivery_type=order.delivery_type,
        delivery_address=order.delivery_address,
        created_at=now,
        updated_at=now,
        estimated_delivery=order.estimated_delivery,
        notes=order.notes
    )

    fake_orders_db[order_id] = new_order
    logger.info(f"Order {order_id} created by {current_user.username}")
    return new_order

@app.get("/orders/", response_model=List[Order])
async def read_orders(current_user: UserPublic = Depends(require_client)):
    client_orders = [order for order in fake_orders_db.values() if order.client_id == current_user.user_id]
    return client_orders

@app.get("/orders/{order_id}", response_model=Order)
async def read_order(
    order_id: int,
    current_user: UserPublic = Depends(get_current_active_user)
):
    order = fake_orders_db.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    if order.client_id != current_user.user_id and current_user.role not in [Role.ADMIN, Role.COURIER]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this order"
        )
    
    return order

@app.put("/orders/{order_id}/status", response_model=Order)
async def update_order_status(
    order_id: int,
    order_update: OrderUpdate,
    current_user: UserPublic = Depends(require_courier)
):
    order = fake_orders_db.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    now = datetime.utcnow()
    
    if order_update.status:
        order.status = order_update.status
        if order_update.status == OrderStatus.DELIVERED:
            order.delivered_at = now
    
    if order_update.estimated_delivery:
        order.estimated_delivery = order_update.estimated_delivery
    
    if order_update.notes:
        order.notes = order_update.notes
    
    order.updated_at = now
    
    logger.info(f"Order {order_id} updated by {current_user.username}")
    return order

@app.delete("/orders/{order_id}")
async def delete_order(
    order_id: int,
    current_user: UserPublic = Depends(require_client)
):
    order = fake_orders_db.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    if order.client_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete another user's order"
        )
    
    if order.status not in [OrderStatus.CREATED, OrderStatus.PROCESSING]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete order that has already been shipped"
        )
    
    del fake_orders_db[order_id]
    logger.info(f"Order {order_id} deleted by {current_user.username}")
    return {"message": "Order deleted successfully"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")