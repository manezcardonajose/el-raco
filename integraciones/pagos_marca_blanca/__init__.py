"""Modulo reusable de pagos marca blanca.

Este paquete no depende de Flask ni de El Raco. Se puede copiar/importar en
otras ERPs y exponerlo con la capa web que corresponda.
"""

from .service import PaymentService
from .schema import ensure_payment_schema

__all__ = ["PaymentService", "ensure_payment_schema"]
