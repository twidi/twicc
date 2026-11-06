"""Private per-device registrations and one persistent VAPID key per instance."""

import base64
import hashlib
import os
import secrets
import tempfile
from pathlib import Path
from threading import RLock
from uuid import UUID

import orjson
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings

from twicc.auth.session_auth import compute_fingerprint
from twicc.paths import get_data_dir

KINDS = ("user_turn", "pending_request", "extra_usage", "peer")
lock = RLock()


def directory() -> Path:
    return get_data_dir() / "android-push"


def atomic_write(path: Path, content: bytes):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = stream.name
        try:
            stream.write(content)
            stream.flush()
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def configuration() -> dict:
    with lock:
        path = directory() / "vapid-private.pem"
        if not path.exists():
            # Preserve the key accepted by an existing prototype registration.
            legacy = get_data_dir() / "android-push-probe/vapid-private.pem"
            if legacy.exists():
                content = legacy.read_bytes()
            else:
                content = ec.generate_private_key(ec.SECP256R1()).private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
                )
            atomic_write(path, content)
        private = serialization.load_pem_private_key(path.read_bytes(), password=None)
        public = private.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return {"protocol": 1, "public_key": base64.urlsafe_b64encode(public).decode().rstrip("=")}


def device_path(device_id: str) -> Path:
    return directory() / "devices" / f"{UUID(device_id)}.json"


def read_device(device_id: str) -> dict | None:
    try:
        return orjson.loads(device_path(device_id).read_bytes())
    except (FileNotFoundError, ValueError):
        return None


def save_device(device: dict):
    atomic_write(device_path(device["device_id"]), orjson.dumps(device))


def valid_binding(device: dict) -> bool:
    return device.get("fingerprint") == compute_fingerprint(settings.TWICC_PASSWORD_HASH)


def enroll(device_id: str, name: str, preferences: dict, enabled: bool) -> str:
    configuration()
    token = secrets.token_urlsafe(32)
    with lock:
        save_device(
            {
                "device_id": str(UUID(device_id)),
                "name": name,
                "preferences": preferences,
                "enabled": enabled,
                "fingerprint": compute_fingerprint(settings.TWICC_PASSWORD_HASH),
                "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                "subscription": None,
            }
        )
    return token


def grant_device(device_id: str, token: str) -> dict | None:
    device = read_device(device_id)
    if not device or not valid_binding(device) or not token:
        return None
    return (
        device
        if secrets.compare_digest(device.get("token_hash", ""), hashlib.sha256(token.encode()).hexdigest())
        else None
    )


def devices() -> list[dict]:
    with lock:
        result = []
        for path in (directory() / "devices").glob("*.json"):
            device = read_device(path.stem)
            if device and valid_binding(device) and device.get("enabled") and device.get("subscription"):
                result.append(device)
        return result


def retire_subscription(snapshot: dict):
    with lock:
        current = read_device(snapshot["device_id"])
        if current and current.get("subscription") == snapshot.get("subscription"):
            current["subscription"] = None
            save_device(current)
