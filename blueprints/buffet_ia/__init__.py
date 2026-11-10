from flask import Blueprint

buffet_ia_bp = Blueprint("buffet_ia", __name__, url_prefix="/buffet-ia")

from . import routes
