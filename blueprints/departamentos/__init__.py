from flask import Blueprint

departamentos_bp = Blueprint(
    "departamentos",
    __name__,
    url_prefix="/departamentos"
)

from . import routes