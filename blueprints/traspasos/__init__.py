from flask import Blueprint

traspasos_bp = Blueprint('traspasos', __name__, url_prefix='/traspasos')

from . import routes