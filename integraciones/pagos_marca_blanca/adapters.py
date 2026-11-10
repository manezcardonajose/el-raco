"""Adaptadores de pasarela.

Los adaptadores devuelven una respuesta normalizada. Si falta SDK/clave real,
la operacion queda preparada o falla de forma controlada sin tocar datos de
tarjeta.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class GatewayResult:
    ok: bool
    estado: str
    proveedor_payment_id: str = ""
    proveedor_session_id: str = ""
    checkout_url: str = ""
    raw_response: dict[str, Any] | None = None
    error: str = ""


class BaseGateway:
    code = "base"

    def __init__(self, public_config: dict[str, Any], secrets: dict[str, str], mode: str = "test"):
        self.public_config = public_config or {}
        self.secrets = secrets or {}
        self.mode = mode or "test"

    def create_payment(self, operation: dict[str, Any]) -> GatewayResult:
        raise NotImplementedError

    def refund(self, operation: dict[str, Any], amount: float | None = None) -> GatewayResult:
        return GatewayResult(False, "error", error="Devolucion no implementada para esta pasarela.")

    def verify_webhook(self, body: bytes, headers: dict[str, str]) -> bool:
        return False


class SimulatedGateway(BaseGateway):
    code = "simulado"

    def create_payment(self, operation: dict[str, Any]) -> GatewayResult:
        ref = operation["referencia_interna"]
        token = hashlib.sha256(f"{ref}:{time.time()}".encode("utf-8")).hexdigest()[:24]
        return GatewayResult(
            True,
            "pendiente",
            proveedor_payment_id=f"SIM-PAY-{token}",
            proveedor_session_id=f"SIM-SES-{token}",
            checkout_url=f"/pagos/checkout-simulado/{ref}",
            raw_response={"simulado": True, "token": token},
        )

    def refund(self, operation: dict[str, Any], amount: float | None = None) -> GatewayResult:
        ref = operation.get("referencia_interna", "")
        token = hashlib.sha256(f"refund:{ref}:{amount}:{time.time()}".encode("utf-8")).hexdigest()[:20]
        return GatewayResult(True, "devuelto", proveedor_payment_id=f"SIM-REF-{token}", raw_response={"simulado": True, "amount": amount})

    def verify_webhook(self, body: bytes, headers: dict[str, str]) -> bool:
        secret = self.secrets.get("webhook_secret", "")
        if not secret:
            return True
        signature = headers.get("X-Pagos-Signature") or headers.get("x-pagos-signature") or ""
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


class StripeGateway(BaseGateway):
    code = "stripe"

    def create_payment(self, operation: dict[str, Any]) -> GatewayResult:
        api_key = self.secrets.get("api_key") or self.secrets.get("secret_key")
        if not api_key:
            return GatewayResult(False, "error", error="Falta api_key/secret_key de Stripe.")
        try:
            import stripe  # type: ignore
        except Exception:
            return GatewayResult(
                False,
                "pendiente_sdk",
                error="Instala el SDK stripe para activar cobros reales: pip install stripe.",
                raw_response={"provider": "stripe", "requires_sdk": True},
            )
        stripe.api_key = api_key
        amount = int(round(float(operation["importe"]) * 100))
        success_url = self.public_config.get("success_url") or operation.get("success_url") or ""
        cancel_url = self.public_config.get("cancel_url") or operation.get("cancel_url") or ""
        try:
            session = stripe.checkout.Session.create(
                mode="payment",
                payment_method_types=self.public_config.get("payment_method_types") or ["card"],
                line_items=[
                    {
                        "price_data": {
                            "currency": (operation.get("moneda") or "EUR").lower(),
                            "product_data": {"name": operation.get("descripcion") or operation["referencia_interna"]},
                            "unit_amount": amount,
                        },
                        "quantity": 1,
                    }
                ],
                success_url=success_url,
                cancel_url=cancel_url,
                client_reference_id=operation["referencia_interna"],
                customer_email=operation.get("cliente_email") or None,
            )
            return GatewayResult(True, "pendiente", proveedor_session_id=session.id, checkout_url=session.url or "", raw_response=dict(session))
        except Exception as exc:
            return GatewayResult(False, "error", error=str(exc), raw_response={"provider": "stripe"})


class RedsysGateway(BaseGateway):
    code = "redsys"

    def create_payment(self, operation: dict[str, Any]) -> GatewayResult:
        required = ["merchant_code", "terminal", "secret_key"]
        missing = [key for key in required if not (self.secrets.get(key) or self.public_config.get(key))]
        if missing:
            return GatewayResult(False, "error", error=f"Faltan campos Redsys: {', '.join(missing)}")
        amount = int(round(float(operation["importe"]) * 100))
        order = operation["referencia_interna"][-12:].replace("-", "")[:12]
        paymethods = self.public_config.get("paymethods") or "T"
        params = {
            "DS_MERCHANT_AMOUNT": str(amount),
            "DS_MERCHANT_ORDER": order,
            "DS_MERCHANT_MERCHANTCODE": self.secrets.get("merchant_code") or self.public_config.get("merchant_code"),
            "DS_MERCHANT_CURRENCY": "978",
            "DS_MERCHANT_TRANSACTIONTYPE": "0",
            "DS_MERCHANT_TERMINAL": self.secrets.get("terminal") or self.public_config.get("terminal"),
            "DS_MERCHANT_MERCHANTURL": self.public_config.get("merchant_url", ""),
            "DS_MERCHANT_URLOK": self.public_config.get("url_ok", ""),
            "DS_MERCHANT_URLKO": self.public_config.get("url_ko", ""),
            "DS_MERCHANT_PAYMETHODS": paymethods,
        }
        encoded = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
        local_signature = hmac.new(self.secrets["secret_key"].encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
        url = self.public_config.get("endpoint") or ("https://sis-t.redsys.es:25443/sis/realizarPago" if self.mode == "test" else "https://sis.redsys.es/sis/realizarPago")
        return GatewayResult(
            True,
            "pendiente_redireccion",
            proveedor_session_id=order,
            checkout_url=url,
            raw_response={"provider": "redsys", "form_params": params, "local_signature": local_signature, "requires_redsys_signature_adapter": True},
        )


class AdyenGateway(BaseGateway):
    code = "adyen"

    def create_payment(self, operation: dict[str, Any]) -> GatewayResult:
        api_key = self.secrets.get("api_key")
        merchant_account = self.secrets.get("merchant_account") or self.public_config.get("merchant_account")
        if not api_key or not merchant_account:
            return GatewayResult(False, "error", error="Falta api_key o merchant_account de Adyen.")
        return GatewayResult(
            True,
            "pendiente_integracion",
            raw_response={
                "provider": "adyen",
                "merchant_account": merchant_account,
                "reference": operation["referencia_interna"],
                "amount": {"currency": operation.get("moneda") or "EUR", "value": int(round(float(operation["importe"]) * 100))},
                "message": "Payload preparado. Conectar Checkout/API/Terminal de Adyen en despliegue real.",
            },
        )


GATEWAYS = {
    "simulado": SimulatedGateway,
    "stripe": StripeGateway,
    "redsys": RedsysGateway,
    "adyen": AdyenGateway,
}


def build_gateway(provider: str, public_config: dict[str, Any], secrets: dict[str, str], mode: str) -> BaseGateway:
    cls = GATEWAYS.get((provider or "").lower())
    if not cls:
        raise ValueError(f"Pasarela no soportada: {provider}")
    return cls(public_config, secrets, mode)
