from flask import Blueprint

movimientos_bp = Blueprint('movimientos', __name__, url_prefix='/movimientos')

from . import routes