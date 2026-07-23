from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import hash_password, verify_password, create_token, get_current_user
from app.database import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register")
async def register(req: dict):
    username = (req.get("username") or "").strip()
    password = req.get("password") or ""
    if len(username) < 3 or len(password) < 6:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Username >= 3 chars, password >= 6 chars")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            if cur.fetchone():
                raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
            cur.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (username, hash_password(password)),
            )
            user_id = cur.lastrowid
            role = "user"
    token = create_token(user_id, username, role)
    return {"token": token, "user": {"id": user_id, "username": username, "role": role}}


@router.post("/login")
async def login(req: dict):
    username = (req.get("username") or "").strip()
    password = req.get("password") or ""

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, password_hash, role FROM users WHERE username = %s",
                (username,),
            )
            user = cur.fetchone()

    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    token = create_token(user["id"], user["username"], user["role"])
    return {"token": token, "user": {"id": user["id"], "username": user["username"], "role": user["role"]}}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return user
