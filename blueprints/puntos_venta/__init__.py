from flask import Blueprint

puntos_venta_bp = Blueprint(
    "puntos_venta",
    __name__,
    url_prefix="/puntos_venta"
)

from . import routes