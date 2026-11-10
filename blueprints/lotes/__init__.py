from flask import Blueprint

lotes_bp = Blueprint('lotes', __name__, url_prefix='/lotes')

from . import routes