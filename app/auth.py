import base64
import hashlib
import hmac
import json
import time
from fastapi import Header, HTTPException
from config import ADMIN_API_KEY, DEVICE_TOKEN_SECRET
from database import supabase


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def require_device(
    x_device_id: str | None = Header(default=None),
    x_device_key: str | None = Header(default=None),
):
    """Every device request must send X-Device-ID and X-Device-Key.
    Unknown device and wrong key return the same error on purpose, so
    nobody can probe which device IDs exist."""
    if not x_device_id or not x_device_key:
        raise HTTPException(status_code=401, detail="Invalid device credentials")

    result = supabase.table("devices") \
        .select("id, org_id, device_id, device_key_hash, is_active") \
        .eq("device_id", x_device_id) \
        .execute()

    row = result.data[0] if result.data else None
    stored_hash = row.get("device_key_hash") if row else None

    if not row or not stored_hash or not hmac.compare_digest(
        stored_hash.encode(), _hash_key(x_device_key).encode()
    ):
        raise HTTPException(status_code=401, detail="Invalid device credentials")

    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="Device disabled")

    return row


# --- Buddy firmware session tokens -----------------------------------------
# Buddy firmware has no per-device secret: on boot it identifies itself by
# MAC address at the OTA endpoint, which returns a short-lived signed token
# that the device then presents on the WebSocket.

DEVICE_TOKEN_TTL_SECONDS = 7 * 24 * 3600


def normalize_mac(mac: str) -> str:
    return mac.strip().lower().replace("-", ":")


def get_device_by_mac(mac: str) -> dict | None:
    result = supabase.table("devices") \
        .select("id, org_id, device_id, is_active") \
        .eq("device_id", normalize_mac(mac)) \
        .execute()
    return result.data[0] if result.data else None


def get_device_by_id(device_row_id: str) -> dict | None:
    result = supabase.table("devices") \
        .select("id, org_id, device_id, is_active") \
        .eq("id", device_row_id) \
        .execute()
    return result.data[0] if result.data else None


def _sign(payload: str) -> str:
    return hmac.new(DEVICE_TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_device_token(device: dict) -> str:
    """Token format: <payload>.<hmac>, payload = b64url(json). No spaces, so
    the firmware adds the "Bearer " prefix itself."""
    payload = base64.urlsafe_b64encode(json.dumps({
        "d": device["id"],
        "m": device["device_id"],
        "e": int(time.time()) + DEVICE_TOKEN_TTL_SECONDS,
    }, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{payload}.{_sign(payload)}"


def verify_device_token(token: str, mac: str | None) -> dict | None:
    """Returns {"d": device row id, "m": mac} if the token is valid, was
    issued to this MAC, and has not expired; otherwise None."""
    if not DEVICE_TOKEN_SECRET or not token:
        return None
    if token.lower().startswith("bearer "):
        token = token[7:]
    payload, _, sig = token.strip().partition(".")
    if not payload or not hmac.compare_digest(sig.encode(), _sign(payload).encode()):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return None
    if data.get("e", 0) < time.time():
        return None
    if mac is not None and normalize_mac(mac) != data.get("m"):
        return None
    return data


def require_admin(x_admin_key: str | None = Header(default=None)):
    if not x_admin_key or not hmac.compare_digest(
        x_admin_key.encode(), ADMIN_API_KEY.encode()
    ):
        raise HTTPException(status_code=401, detail="Invalid admin key")