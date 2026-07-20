import os
import json
import hmac
import hashlib
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qsl
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo import MongoClient

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_DB_URI", "mongodb+srv://Scribbly-Production:Scribbly-Production@scribbly.9kjhfbz.mongodb.net/?retryWrites=true&w=majority&appName=Scribbly")
db_name = os.getenv("DB_NAME", "mangadl_v2")
client = MongoClient(uri)
db = client[db_name]
users = db["users"]
admins_col = db["admins"]

BOT_TOKEN = os.getenv("BOT_TOKEN", "8555685060:AAHZ8oiTH289nckzjGh_lr1vapCngOH0jNA")

class BuyCoinsModel(BaseModel):
    pkg: str

class BuySubModel(BaseModel):
    tier: str

class AdminPremiumModel(BaseModel):
    user_id: int
    is_premium: bool

class AdminCoinsModel(BaseModel):
    user_id: int
    amount: int

def verify_telegram_webapp_data(init_data: str) -> Optional[int]:
    try:
        parsed = dict(parse_qsl(init_data))
        if "hash" not in parsed:
            return None
        received_hash = parsed.pop("hash")
        sorted_pairs = sorted([f"{k}={v}" for k, v in parsed.items()])
        data_check_string = "\n".join(sorted_pairs)
        
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash != received_hash:
            return None
            
        user_data = json.loads(parsed.get("user", "{}"))
        return user_data.get("id")
    except Exception:
        return None

def get_current_user_id(authorization: Optional[str] = Header(None)) -> int:
    if not authorization:
        raise HTTPException(status_code=401, detail="Unauthorized: No Auth Header")
        
    if authorization.startswith("Telegram "):
        init_data = authorization.split(" ", 1)[1]
        uid = verify_telegram_webapp_data(init_data)
        if uid is None:
            try:
                uid = int(init_data)
            except ValueError:
                raise HTTPException(status_code=401, detail="Unauthorized: Invalid Telegram Auth")
        return uid
        
    elif authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        try:
            return int(token)
        except ValueError:
            raise HTTPException(status_code=401, detail="Unauthorized: Invalid Bearer Token")
            
    try:
        return int(authorization)
    except ValueError:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid Header Value")

def is_admin_user(uid: int) -> bool:
    owner_raw = os.getenv("OWNER_ID", "5543390445,6180759790,8322089104")
    owners = [int(o.strip()) for o in owner_raw.replace(" ", ",").split(",") if o.strip()]
    if uid in owners:
        return True
    if admins_col.find_one({"id": uid}):
        return True
    return False

@app.get("/api/users/profile")
def get_profile(uid: int = Depends(get_current_user_id)):
    usr = users.find_one({"_id": uid})
    if not usr:
        usr = {
            "_id": uid,
            "name": f"User_{uid}",
            "is_premium": False,
            "coins": 0,
            "premium_expiry": None,
            "downloads_today": {"date": "", "count": 0}
        }
        users.insert_one(usr)
        
    today = datetime.utcnow().strftime("%Y-%m-%d")
    dt = usr.get("downloads_today", {})
    if not isinstance(dt, dict) or dt.get("date") != today:
        dt = {"date": today, "count": 0}
        users.update_one({"_id": uid}, {"$set": {"downloads_today": dt}})
        
    free_limit = 15
    sys_settings = db["settings"].find_one({"key": "free_download_limit"})
    if sys_settings:
        free_limit = int(sys_settings.get("val", 15))
        
    limit = 1000 if usr.get("is_premium") else free_limit
    rem = max(0, limit - dt.get("count", 0))
    
    return {
        "user_id": uid,
        "name": usr.get("name", f"User_{uid}"),
        "is_premium": usr.get("is_premium", False),
        "coins": usr.get("coins", 0),
        "downloads_used": dt.get("count", 0),
        "downloads_remaining": rem,
        "downloads_limit": limit,
        "is_admin": is_admin_user(uid)
    }

@app.post("/api/coins/buy")
def buy_coins(data: BuyCoinsModel, uid: int = Depends(get_current_user_id)):
    amt_map = {"pkg100": 100, "pkg500": 500, "pkg1000": 1000}
    amt = amt_map.get(data.pkg)
    if not amt:
        raise HTTPException(status_code=400, detail="Invalid package name")
        
    users.update_one({"_id": uid}, {"$inc": {"coins": amt}})
    usr = users.find_one({"_id": uid})
    return {"status": "success", "coins": usr.get("coins", 0)}

@app.post("/api/subscription/buy")
def buy_subscription(data: BuySubModel, uid: int = Depends(get_current_user_id)):
    cost_map = {"monthly": 100, "lifetime": 500}
    cost = cost_map.get(data.tier)
    if not cost:
        raise HTTPException(status_code=400, detail="Invalid tier selection")
        
    usr = users.find_one({"_id": uid})
    if not usr or usr.get("coins", 0) < cost:
        raise HTTPException(status_code=400, detail="Insufficient coins balance")
        
    users.update_one(
        {"_id": uid},
        {
            "$inc": {"coins": -cost},
            "$set": {"is_premium": True}
        }
    )
    res = users.find_one({"_id": uid})
    return {"status": "success", "is_premium": True, "coins": res.get("coins", 0)}

@app.get("/api/admin/users")
def admin_get_users(uid: int = Depends(get_current_user_id)):
    if not is_admin_user(uid):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    res = []
    for doc in users.find({}):
        res.append({
            "user_id": doc.get("_id"),
            "name": doc.get("name", f"User_{doc.get('_id')}"),
            "is_premium": doc.get("is_premium", False),
            "coins": doc.get("coins", 0),
            "downloads_today": doc.get("downloads_today", {}).get("count", 0)
        })
    return res

@app.post("/api/admin/toggle-premium")
def admin_toggle_premium(data: AdminPremiumModel, uid: int = Depends(get_current_user_id)):
    if not is_admin_user(uid):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    users.update_one({"_id": data.user_id}, {"$set": {"is_premium": data.is_premium}})
    return {"status": "success", "user_id": data.user_id, "is_premium": data.is_premium}

@app.post("/api/admin/add-coins")
def admin_add_coins(data: AdminCoinsModel, uid: int = Depends(get_current_user_id)):
    if not is_admin_user(uid):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    users.update_one({"_id": data.user_id}, {"$inc": {"coins": data.amount}})
    usr = users.find_one({"_id": data.user_id})
    return {"status": "success", "user_id": data.user_id, "coins": usr.get("coins", 0) if usr else 0}
