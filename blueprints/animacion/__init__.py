from flask import Blueprint

animacion_bp = Blueprint("animacion", __name__, url_prefix="/animacion")

from . import routes
