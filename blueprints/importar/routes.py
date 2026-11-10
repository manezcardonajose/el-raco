from flask import render_template, request, flash
from blueprints.auth.routes import login_required
from . import importar_bp
from .service_importador import importar_desde_carpeta


@importar_bp.route("/", methods=["GET"])
@login_required
def index():
    return render_template("importar/importar_recetas.html")


@importar_bp.route("/masivo", methods=["GET", "POST"])
@login_required
def masivo():
    if request.method == "POST":
        ruta_carpeta = request.form.get("ruta_carpeta", "").strip()

        if not ruta_carpeta:
            flash("Debes indicar una carpeta.", "danger")
            return render_template("importar/masivo.html")

        resultados = importar_desde_carpeta(ruta_carpeta)
        return render_template("importar/resultados.html", resultados=resultados)

    return render_template("importar/masivo.html")