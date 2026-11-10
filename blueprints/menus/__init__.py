from flask import Blueprint

menus_bp = Blueprint('menus', __name__, url_prefix='/menus')

from . import routes