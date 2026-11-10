from flask import Blueprint

inventario_bp = Blueprint("inventario", __name__, url_prefix="/inventario")

from . import routes
