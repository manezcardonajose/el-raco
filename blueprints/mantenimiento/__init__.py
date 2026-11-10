from flask import Blueprint

mantenimiento_bp = Blueprint('mantenimiento', __name__, url_prefix='/mantenimiento')

from . import routes