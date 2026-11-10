from flask import Blueprint

pisos_bp = Blueprint("pisos", __name__, url_prefix="/pisos")

from . import routes
