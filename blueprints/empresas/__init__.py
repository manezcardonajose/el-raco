from flask import Blueprint

empresas_bp = Blueprint('empresas', __name__, url_prefix='/empresas')

from . import routes