from flask import Blueprint

compras_bp = Blueprint('compras', __name__, url_prefix='/compras')

from . import routes