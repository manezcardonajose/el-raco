from flask import Blueprint

rrhh_bp = Blueprint('rrhh', __name__, url_prefix='/rrhh')

from . import routes
from . import cuadrante_extras
