from fastapi import FastAPI, HTTPException, Depends, status, Request
from enum import Enum
from pydantic import BaseModel, Field, validator
from datetime import datetime
import logging
import os
from typing import List, Optional
import httpx
import motor.motor_asyncio
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth-service:8000")

# MongoDB connection
client = motor.motor_asyncio.AsyncIOMotorClient(
    host=os.getenv("MONGO_HOST", "mongodb"),
    port=int(os.getenv("MONGO_PORT", "27017")),
    username=os.getenv("MONGO_USER", "mongo"),
    password=os.getenv("MONGO_PASSWORD", "mongo123")
)
db = client[os.getenv("MONGO_DB", "analytics_service")]
events_collection = db["events"]

class EventType(str, Enum):
    PAGE_VIEW = "page_view"
    BUTTON_CLICK = "button_click"
    FORM_SUBMIT = "form_submit"
    PRODUCT_VIEW = "product_view"
    CART_ADD = "cart_add"
    CHECKOUT_START = "checkout_start"
    PURCHASE = "purchase"

class UserPublic(BaseModel):
    user_id: int
    username: str = Field(..., min_length=3, max_length=50)
    full_name: Optional[str] = Field(None, max_length=100)
    role: str

class EventBase(BaseModel):
    event_type: EventType
    user_id: Optional[int] = None
    session_id: str = Field(..., min_length=8, max_length=64)
    page_url: str = Field(..., max_length=500)
    referrer_url: Optional[str] = Field(None, max_length=500)
    user_agent: Optional[str] = Field(None, max_length=500)
    ip_address: Optional[str] = Field(None, max_length=45)
    metadata: Optional[dict] = None

    @validator('ip_address')
    def validate_ip_address(cls, v):
        if v and not (v.count('.') == 3 or ':' in v):  # Basic IPv4/IPv6 check
            raise ValueError('Invalid IP address format')
        return v

class EventCreate(EventBase):
    pass

class Event(EventBase):
    id: str
    timestamp: datetime

    class Config:
        json_encoders = {ObjectId: str}

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

@app.post("/events/", status_code=status.HTTP_201_CREATED, response_model=Event)
async def create_event(
    event: EventCreate,
    current_user: Optional[UserPublic] = Depends(get_current_active_user)
):
    # If user is authenticated, override the user_id from the event
    if current_user:
        event_dict = event.dict(exclude={"user_id"})
        event_dict["user_id"] = current_user.user_id
    else:
        event_dict = event.dict()
    
    event_dict["timestamp"] = datetime.utcnow()
    
    try:
        result = await events_collection.insert_one(event_dict)
        new_event = await events_collection.find_one({"_id": result.inserted_id})
        new_event["id"] = str(new_event["_id"])
        return Event(**new_event)
    except DuplicateKeyError as e:
        logger.error(f"Duplicate event error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Event creation failed"
        )

@app.get("/events/", response_model=List[Event])
async def read_events(
    event_type: Optional[EventType] = None,
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    page_url: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 100,
    current_user: UserPublic = Depends(get_current_active_user)
):
    if current_user.role != "admin" and user_id and user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view other users' events"
        )
    
    query = {}
    
    if event_type:
        query["event_type"] = event_type.value
    if user_id:
        query["user_id"] = user_id
    if session_id:
        query["session_id"] = session_id
    if page_url:
        query["page_url"] = {"$regex": page_url, "$options": "i"}
    if start_date and end_date:
        query["timestamp"] = {"$gte": start_date, "$lte": end_date}
    elif start_date:
        query["timestamp"] = {"$gte": start_date}
    elif end_date:
        query["timestamp"] = {"$lte": end_date}
    
    events = []
    async for event in events_collection.find(query).sort("timestamp", -1).limit(limit):
        event["id"] = str(event["_id"])
        events.append(Event(**event))
    
    return events

@app.get("/events/{event_id}", response_model=Event)
async def read_event(
    event_id: str,
    current_user: UserPublic = Depends(get_current_active_user)
):
    try:
        event = await events_collection.find_one({"_id": ObjectId(event_id)})
    except:
        raise HTTPException(status_code=400, detail="Invalid event ID format")
    
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    if current_user.role != "admin" and event.get("user_id") != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this event"
        )
    
    event["id"] = str(event["_id"])
    return Event(**event)

@app.get("/stats/summary")
async def get_summary_stats(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    current_user: UserPublic = Depends(get_current_active_user)
):
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can view summary stats"
        )
    
    match_stage = {}
    if start_date and end_date:
        match_stage["timestamp"] = {"$gte": start_date, "$lte": end_date}
    elif start_date:
        match_stage["timestamp"] = {"$gte": start_date}
    elif end_date:
        match_stage["timestamp"] = {"$lte": end_date}
    
    pipeline = []
    if match_stage:
        pipeline.append({"$match": match_stage})
    
    pipeline.extend([
        {
            "$group": {
                "_id": "$event_type",
                "count": {"$sum": 1},
                "unique_users": {"$addToSet": "$user_id"}
            }
        },
        {
            "$project": {
                "event_type": "$_id",
                "count": 1,
                "unique_users_count": {"$size": "$unique_users"},
                "_id": 0
            }
        }
    ])
    
    results = []
    async for result in events_collection.aggregate(pipeline):
        results.append(result)
    
    return {"stats": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002, log_level="info")