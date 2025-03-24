from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, validator, Field
from typing import List, Optional
from datetime import datetime, timedelta
import jwt
import os
from jwt import PyJWTError
import bcrypt
from enum import Enum
import logging
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("SECRET_KEY", "your-secure-secret-key-with-at-least-32-chars")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
MASTER_USERNAME = os.getenv("MASTER_USERNAME", "admin")
MASTER_PASSWORD = os.getenv("MASTER_PASSWORD", "secret")

class Role(str, Enum):
    CLIENT = "client"
    ADMIN = "admin"
    COURIER = "courier"

class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, pattern="^[a-zA-Z0-9_]+$")
    full_name: Optional[str] = Field(None, max_length=100)
    role: Role = Role.CLIENT

    @validator('username')
    def validate_username(cls, v):
        if not re.match("^[a-zA-Z0-9_]+$", v):
            raise ValueError("Username can only contain letters, numbers and underscores")
        return v

    @validator('full_name')
    def validate_full_name(cls, v):
        if v is not None and not re.match("^[a-zA-Zа-яА-ЯёЁ ]+$", v):
            raise ValueError("Full name can only contain letters and spaces")
        return v

class UserCreate(UserBase):
    password: str = Field(..., min_length=8)
    roles: List[Role] = Field(..., min_items=1, max_items=1)

    @validator("password")
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v

    @validator("roles")
    def validate_roles(cls, v):
        if len(v) != 1 or not all(role in [Role.CLIENT, Role.COURIER] for role in v):
            raise ValueError("User can only have one role: either 'client' or 'courier'")
        return v

class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(None, max_length=100)
    password: Optional[str] = Field(None, min_length=8)
    disabled: Optional[bool] = None

    @validator('full_name')
    def validate_full_name(cls, v):
        if v is not None and not re.match("^[a-zA-Zа-яА-ЯёЁ ]+$", v):
            raise ValueError("Full name can only contain letters and spaces")
        return v

    @validator("password")
    def validate_password(cls, v):
        if v is not None:
            if len(v) < 8:
                raise ValueError("Password must be at least 8 characters long")
            if not any(c.isupper() for c in v):
                raise ValueError("Password must contain at least one uppercase letter")
            if not any(c.isdigit() for c in v):
                raise ValueError("Password must contain at least one digit")
        return v

class UserInDB(UserBase):
    user_id: int
    hashed_password: str
    disabled: bool = False

class UserPublic(UserBase):
    user_id: int

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    user_id: Optional[str] = None

fake_users_db = {}
user_id_counter = 1

def create_master_user():
    global user_id_counter
    master_username = "admin"
    master_password = "secret"
    
    if not get_user_by_username(master_username):
        hashed_password = hash_password(master_password)
        master_user = UserInDB(
            user_id=user_id_counter,
            username=master_username,
            full_name="Master Administrator",
            role=Role.ADMIN,
            hashed_password=hashed_password
        )
        fake_users_db[user_id_counter] = master_user
        user_id_counter += 1
        logger.info("Master user 'admin' created successfully")

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    create_master_user()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_user_by_username(username: str) -> Optional[UserInDB]:
    return next((user for user in fake_users_db.values() if user.username == username), None)

def get_user_by_id(user_id: int) -> Optional[UserInDB]:
    return fake_users_db.get(user_id)

def authenticate_user(username: str, password: str) -> Optional[UserInDB]:
    user = get_user_by_username(username)
    if not user:
        logger.warning(f"User {username} not found")
        return None
    if not verify_password(password, user.hashed_password):
        logger.warning(f"Invalid password for user {username}")
        return None
    return user

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    
    to_encode.update({"exp": expire})
    
    if 'sub' in to_encode:
        to_encode['sub'] = str(to_encode['sub'])
    
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    logger.info(f"Generated token for user_id: {to_encode.get('sub')}")
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme)) -> UserInDB:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")
        
        if not user_id_str:
            logger.error("No 'sub' field in token")
            raise credentials_exception
            
        try:
            user_id = int(user_id_str)
        except ValueError:
            logger.error(f"Invalid user_id format: {user_id_str}")
            raise credentials_exception
            
        user = get_user_by_id(user_id)
        if not user:
            logger.error(f"User with id {user_id} not found")
            raise credentials_exception
            
        logger.info(f"Authenticated user: {user.username}")
        return user
        
    except jwt.ExpiredSignatureError:
        logger.warning("Token has expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except PyJWTError as e:
        logger.error(f"JWT error: {str(e)}")
        raise credentials_exception
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise credentials_exception

async def get_current_active_user(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.disabled:
        logger.warning(f"User {current_user.username} is disabled")
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

async def require_admin(current_user: UserInDB = Depends(get_current_active_user)) -> UserInDB:
    if current_user.role != Role.ADMIN:
        logger.warning(f"User {current_user.username} attempted admin action without privileges")
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return current_user

@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        logger.warning(f"Failed login attempt for username: {form_data.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.user_id},
        expires_delta=access_token_expires
    )
    
    logger.info(f"Successful login for user: {user.username}")
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/users/me", response_model=UserPublic)
async def read_users_me(current_user: UserInDB = Depends(get_current_active_user)):
    return current_user

@app.post("/users/", response_model=UserPublic)
async def create_user(user: UserCreate):
    global user_id_counter
    
    if get_user_by_username(user.username):
        logger.warning(f"Attempt to create existing username: {user.username}")
        raise HTTPException(status_code=400, detail="Username already registered")
    
    hashed_password = hash_password(user.password)
    user_id = user_id_counter
    user_id_counter += 1
    
    user_dict = UserInDB(
        user_id=user_id,
        username=user.username,
        full_name=user.full_name,
        role=user.roles[0], 
        hashed_password=hashed_password
    )
    
    fake_users_db[user_id] = user_dict
    logger.info(f"Created new user: {user.username} with ID: {user_id}")
    return user_dict

@app.get("/users/", response_model=List[UserPublic])
async def read_users(current_user: UserInDB = Depends(require_admin)):
    return list(fake_users_db.values())

@app.get("/users/{user_id}", response_model=UserPublic)
async def read_user(user_id: int, current_user: UserInDB = Depends(get_current_active_user)):
    if user_id != current_user.user_id and current_user.role != Role.ADMIN:
        logger.warning(f"User {current_user.username} attempted unauthorized access to user {user_id}")
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    user = get_user_by_id(user_id)
    if user is None:
        logger.error(f"User with id {user_id} not found")
        raise HTTPException(status_code=404, detail="User not found")
    return user

@app.put("/users/{user_id}", response_model=UserPublic)
async def update_user(
    user_id: int,
    user_update: UserUpdate,
    current_user: UserInDB = Depends(get_current_active_user)
):
    if user_id != current_user.user_id and current_user.role != Role.ADMIN:
        logger.warning(f"User {current_user.username} attempted unauthorized update of user {user_id}")
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    user = get_user_by_id(user_id)
    if user is None:
        logger.error(f"User with id {user_id} not found")
        raise HTTPException(status_code=404, detail="User not found")
    
    if user_update.full_name is not None:
        user.full_name = user_update.full_name
    
    if user_update.password is not None:
        user.hashed_password = hash_password(user_update.password)
    
    if user_update.disabled is not None and current_user.role == Role.ADMIN:
        user.disabled = user_update.disabled
    
    logger.info(f"User {user_id} updated by {current_user.username}")
    return user

@app.delete("/users/{user_id}")
async def delete_user(user_id: int, current_user: UserInDB = Depends(require_admin)):
    if user_id not in fake_users_db:
        logger.error(f"Attempt to delete non-existent user: {user_id}")
        raise HTTPException(status_code=404, detail="User not found")
    
    user_to_delete = fake_users_db[user_id]
    if user_to_delete.username == "admin":
        logger.warning(f"Attempt to delete master admin user")
        raise HTTPException(status_code=403, detail="Cannot delete master admin user")
    
    del fake_users_db[user_id]
    logger.info(f"User {user_id} deleted by {current_user.username}")
    return {"message": "User deleted successfully"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

# source venv/bin/activate
# uvicorn main:app --reload