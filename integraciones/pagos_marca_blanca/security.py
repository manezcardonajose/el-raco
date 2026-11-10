"""Gestion segura de secretos para pasarelas.

Regla principal: la ERP no guarda datos de tarjeta. Aqui solo se protegen
secretos tecnicos del comercio: API keys, claves Redsys, HMAC de webhooks, etc.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any


class SecretError(RuntimeError):
    pass


def _fernet():
    try:
        from cryptography.fernet import Fernet  # type: ignore

        return Fernet
    except Exception:
        return None


def _master_key() -> str:
    return (os.environ.get("PAYMENT_MASTER_KEY") or os.environ.get("ERP_PAYMENT_MASTER_KEY") or "").strip()


def generate_master_key() -> str:
    fernet = _fernet()
    if fernet:
        return fernet.generate_key().decode("ascii")
    raw = os.urandom(32)
    return base64.urlsafe_b64encode(raw).decode("ascii")


def crypto_available() -> bool:
    return bool(_fernet() and _master_key())


def encrypt_text(value: str) -> str:
    """Cifra texto si hay cryptography+master key.

    Si no hay soporte criptografico, obliga a usar referencias de entorno. Esto
    evita caer en el error de guardar claves en claro en SQLite.
    """
    value = value or ""
    if value.startswith("env:"):
        return value
    key = _master_key()
    fernet = _fernet()
    if not key or not fernet:
        raise SecretError("Define PAYMENT_MASTER_KEY o usa secretos tipo env:NOMBRE_VARIABLE.")
    return "fernet:" + fernet(key.encode("ascii")).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_text(value: str | None) -> str:
    if not value:
        return ""
    if value.startswith("env:"):
        return os.environ.get(value[4:], "")
    if value.startswith("fernet:"):
        key = _master_key()
        fernet = _fernet()
        if not key or not fernet:
            raise SecretError("Falta PAYMENT_MASTER_KEY para descifrar secretos de pago.")
        return fernet(key.encode("ascii")).decrypt(value[7:].encode("ascii")).decode("utf-8")
    return ""


def pack_secrets(secrets: dict[str, str]) -> str:
    packed = {}
    for name, value in secrets.items():
        value = (value or "").strip()
        if value:
            packed[name] = encrypt_text(value)
    return json.dumps(packed, ensure_ascii=False)


def unpack_secrets(payload: str | None) -> dict[str, str]:
    try:
        raw = json.loads(payload or "{}")
    except Exception:
        raw = {}
    return {key: decrypt_text(value) for key, value in raw.items()}


def masked_secrets(payload: str | None) -> dict[str, str]:
    try:
        raw = json.loads(payload or "{}")
    except Exception:
        raw = {}
    result = {}
    for key, value in raw.items():
        if str(value).startswith("env:"):
            result[key] = f"env:{str(value)[4:]}"
        elif value:
            result[key] = "********"
        else:
            result[key] = ""
    return result


@dataclass
class WebhookVerification:
    ok: bool
    reason: str = ""


def verify_hmac_signature(body: bytes, signature: str, secret: str) -> WebhookVerification:
    if not secret:
        return WebhookVerification(False, "sin secreto")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected, signature or ""):
        return WebhookVerification(True)
    return WebhookVerification(False, "firma no valida")


def safe_json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)
