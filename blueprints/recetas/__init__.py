
from flask import Blueprint

recetas_bp = Blueprint('recetas', __name__, url_prefix='/recetas')

from . import routes
