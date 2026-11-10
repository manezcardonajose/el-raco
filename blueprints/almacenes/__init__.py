from flask import Blueprint

almacenes_bp = Blueprint('almacenes', __name__, url_prefix='/almacenes')

from . import routes