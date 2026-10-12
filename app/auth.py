import hashlib
import hmac
from fastapi import Header, HTTPException
from config import ADMIN_API_KEY
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


def require_admin(x_admin_key: str | None = Header(default=None)):
    if not x_admin_key or not hmac.compare_digest(
        x_admin_key.encode(), ADMIN_API_KEY.encode()
    ):
        raise HTTPException(status_code=401, detail="Invalid admin key")