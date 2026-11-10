from flask import Blueprint

productos_bp = Blueprint('productos', __name__, url_prefix='/productos')

from . import routes