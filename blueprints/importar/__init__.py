from flask import Blueprint

importar_bp = Blueprint(
    "importar",
    __name__,
    url_prefix="/importar"
)

from . import routes