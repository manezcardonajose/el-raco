from flask import Blueprint

recepcion_bp = Blueprint("recepcion", __name__, url_prefix="/recepcion")

from . import routes
