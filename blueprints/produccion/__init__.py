from flask import Blueprint

produccion_bp = Blueprint('produccion', __name__, url_prefix='/produccion')

from . import routes