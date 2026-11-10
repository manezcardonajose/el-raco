from flask import Blueprint

contabilidad_bp = Blueprint('contabilidad', __name__, url_prefix='/contabilidad')

from . import routes
