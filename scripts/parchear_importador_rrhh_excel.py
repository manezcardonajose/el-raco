# -*- coding: utf-8 -*-
"""Añade al routes.py una ruta web para importar RRHH desde Excel.
No sobrescribe el archivo completo: inserta un bloque al final si no existe.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / "blueprints" / "rrhh" / "routes.py"
BASE = ROOT / "templates" / "base.html"

BLOQUE = r'''

# ============================================================
# IMPORTADOR RRHH DESDE EXCEL
# ============================================================
@rrhh_bp.route("/importar-excel", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def importar_excel_rrhh():
    from pathlib import Path
    from werkzeug.utils import secure_filename
    from blueprints.rrhh.importador_excel import importar_rrhh_desde_excel

    resultado = None
    if request.method == "POST":
        archivo = request.files.get("archivo")
        if not archivo or not archivo.filename.lower().endswith(".xlsx"):
            flash("Sube un archivo Excel .xlsx válido.", "warning")
            return redirect(url_for("rrhh.importar_excel_rrhh"))

        upload_dir = Path("uploads") / "rrhh_imports"
        upload_dir.mkdir(parents=True, exist_ok=True)
        ruta = upload_dir / secure_filename(archivo.filename)
        archivo.save(str(ruta))

        limite_raw = request.form.get("limite")
        limite = int(limite_raw) if limite_raw and limite_raw.isdigit() else None
        conn = get_db()
        resultado = importar_rrhh_desde_excel(
            conn,
            str(ruta),
            empresa_id=_empresa_id(),
            modo=request.form.get("modo") or "socios",
            hoja_generica=request.form.get("hoja") or "EMPLEADOS",
            limite=limite,
            solo_activos=request.form.get("solo_activos") == "1",
            estrategia_operativa=request.form.get("estrategia_operativa") or "sumatorio",
            tipo_empleado=request.form.get("tipo_empleado") or "plantilla",
        )
        conn.commit()
        conn.close()
        if resultado.get("errores"):
            flash("Importación finalizada con errores.", "danger")
        else:
            flash("Importación RRHH completada.", "success")

    return render_template("rrhh/importar_excel.html", resultado=resultado)
'''

if not ROUTES.exists():
    raise SystemExit(f"No existe {ROUTES}")
text = ROUTES.read_text(encoding="utf-8")
if "def importar_excel_rrhh" not in text:
    ROUTES.write_text(text.rstrip() + BLOQUE + "\n", encoding="utf-8")
    print("OK: ruta /rrhh/importar-excel añadida a routes.py")
else:
    print("OK: la ruta /rrhh/importar-excel ya existía")

# Añadir enlace al menú RRHH de base.html si se encuentra el bloque.
if BASE.exists():
    base = BASE.read_text(encoding="utf-8")
    enlace = "{{ url_for('rrhh.importar_excel_rrhh') }}"
    if enlace not in base and "Roles y permisos" in base:
        marker = "<i class=\"fas fa-user-shield\"></i> Roles y permisos"
        pos = base.find(marker)
        if pos != -1:
            end_a = base.find("</a>", pos)
            if end_a != -1:
                insert = '''\n                    <a href="{{ url_for('rrhh.importar_excel_rrhh') }}" class="menu-item sub-item">\n                        <i class="fas fa-file-excel"></i> Importar Excel RRHH\n                    </a>'''
                base = base[:end_a+4] + insert + base[end_a+4:]
                BASE.write_text(base, encoding="utf-8")
                print("OK: enlace Importar Excel RRHH añadido a base.html")
            else:
                print("AVISO: no se pudo localizar cierre del enlace Roles y permisos.")
        else:
            print("AVISO: no se pudo localizar el menú RRHH en base.html.")
    else:
        print("OK: enlace de menú ya existía o base.html no necesita cambios")
