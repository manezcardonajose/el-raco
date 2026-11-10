from flask import Blueprint

experiencia_bp = Blueprint("experiencia", __name__, url_prefix="/experiencia")

from . import routes
