from flask import Blueprint

appcc_bp = Blueprint('appcc', __name__, url_prefix='/appcc')

from . import routes