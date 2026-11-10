from flask import Blueprint

informes_bp = Blueprint('informes', __name__, url_prefix='/informes')

from . import routes