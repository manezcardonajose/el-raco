from flask import Blueprint

tpv_bp = Blueprint(
    "tpv",
    __name__,
    url_prefix="/tpv",
)

from . import routes
