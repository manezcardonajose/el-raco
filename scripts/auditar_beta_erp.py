"""Auditoria de cierre beta de El Raco ERP/PMS.

Objetivo:
- inventariar blueprints y rutas vivas registradas en Flask,
- detectar enlaces de menu que apuntan a endpoints inexistentes,
- contar bases de datos y scripts de simulacion/prueba,
- generar un informe Markdown para trabajar modulo por modulo.

No modifica datos.
"""

from __future__ import annotations

import argparse
import ast
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "publicaciones" / "el_raco"


def _load_app():
    import sys

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app import app  # noqa: WPS433

    return app


def _blueprints_registrados(app):
    items = []
    for name, bp in sorted(app.blueprints.items()):
        items.append(
            {
                "name": name,
                "url_prefix": getattr(bp, "url_prefix", "") or "",
                "import_name": getattr(bp, "import_name", "") or "",
            }
        )
    return items


def _rutas_registradas(app):
    rutas = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: (r.endpoint, r.rule)):
        methods = sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"})
        rutas.append(
            {
                "endpoint": rule.endpoint,
                "rule": rule.rule,
                "methods": ",".join(methods),
                "blueprint": rule.endpoint.split(".", 1)[0] if "." in rule.endpoint else "app",
            }
        )
    return rutas


def _extraer_url_for_de_templates():
    refs = []
    pattern = re.compile(r"url_for\(\s*(['\"])(?P<endpoint>[^'\"]+)\1")
    for path in sorted((ROOT / "templates").rglob("*.html")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for match in pattern.finditer(text):
            refs.append(
                {
                    "template": str(path.relative_to(ROOT)),
                    "endpoint": match.group("endpoint"),
                    "line": text[: match.start()].count("\n") + 1,
                }
            )
    return refs


def _extraer_hrefs_duros_base():
    base = ROOT / "templates" / "base.html"
    if not base.exists():
        return []
    text = base.read_text(encoding="utf-8", errors="ignore")
    refs = []
    pattern = re.compile(r'href="(?P<href>/[^"{#][^"]*)"')
    for match in pattern.finditer(text):
        href = match.group("href")
        if href.startswith(("/static", "/auth/logout")):
            continue
        refs.append({"href": href, "line": text[: match.start()].count("\n") + 1})
    return refs


def _probar_hrefs(app, hrefs):
    resultados = []
    with app.test_client() as client:
        for ref in hrefs:
            href = ref["href"]
            try:
                response = client.get(href, follow_redirects=False)
                status = int(response.status_code)
                ok = status in {200, 301, 302, 303, 307, 308, 401, 403}
                destino = response.headers.get("Location", "")
            except Exception as exc:
                status = "error"
                ok = False
                destino = str(exc)
            resultados.append(
                {
                    "line": ref["line"],
                    "href": href,
                    "status": status,
                    "ok": ok,
                    "destino": destino,
                }
            )
    return resultados


def _scripts_prueba():
    scripts_dir = ROOT / "scripts"
    if not scripts_dir.exists():
        return []
    claves = (
        "simular",
        "simulador",
        "auditar",
        "prueba",
        "generar",
        "apretar",
        "diagnosticar",
        "seed",
        "reparar",
        "normalizar",
    )
    return [
        p.name
        for p in sorted(scripts_dir.glob("*.py"))
        if any(token in p.name.lower() for token in claves)
    ]


def _dbs():
    items = []
    for path in sorted(ROOT.glob("*.db")):
        try:
            stat = path.stat()
            tablas = 0
            with sqlite3.connect(path) as conn:
                tablas = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                ).fetchone()[0]
        except Exception:
            stat = path.stat()
            tablas = None
        items.append(
            {
                "name": path.name,
                "size_mb": round(stat.st_size / 1024 / 1024, 2),
                "tables": tablas,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
        )
    return items


def _modulos_desde_carpetas():
    bp_dir = ROOT / "blueprints"
    if not bp_dir.exists():
        return []
    return sorted(
        p.name for p in bp_dir.iterdir() if p.is_dir() and not p.name.startswith("__")
    )


def _estado_modulos(blueprints, rutas, carpetas):
    registered = {b["name"] for b in blueprints}
    rutas_por_bp = defaultdict(int)
    for ruta in rutas:
        rutas_por_bp[ruta["blueprint"]] += 1
    rows = []
    for modulo in carpetas:
        rows.append(
            {
                "modulo": modulo,
                "registrado": "si" if modulo in registered else "no",
                "rutas": rutas_por_bp.get(modulo, 0),
                "estado_beta": "vivo" if modulo in registered and rutas_por_bp.get(modulo, 0) else "revisar",
            }
        )
    return rows


def _markdown(data):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Auditoria beta El Raco ERP/PMS",
        "",
        f"Generado: {now}",
        "",
        "## Resumen",
        "",
        f"- Blueprints registrados: {len(data['blueprints'])}",
        f"- Rutas Flask vivas: {len(data['rutas'])}",
        f"- Referencias `url_for` en templates: {len(data['url_for_refs'])}",
        f"- Endpoints referenciados no encontrados: {len(data['missing_endpoints'])}",
        f"- Enlaces duros en menu/base: {len(data['hrefs_duros'])}",
        f"- Enlaces duros con error real: {sum(1 for h in data['hrefs_test'] if not h['ok'])}",
        f"- Scripts de prueba/simulacion detectados: {len(data['scripts_prueba'])}",
        f"- Bases `.db` detectadas: {len(data['dbs'])}",
        "",
        "## Modulos beta",
        "",
        "| Modulo | Registrado | Rutas | Estado |",
        "|---|---:|---:|---|",
    ]
    for row in data["modulos"]:
        lines.append(
            f"| {row['modulo']} | {row['registrado']} | {row['rutas']} | {row['estado_beta']} |"
        )

    lines.extend(["", "## Endpoints referenciados no encontrados", ""])
    if data["missing_endpoints"]:
        lines.extend(["| Template | Linea | Endpoint |", "|---|---:|---|"])
        for ref in data["missing_endpoints"][:200]:
            lines.append(f"| {ref['template']} | {ref['line']} | `{ref['endpoint']}` |")
    else:
        lines.append("No se detectan endpoints rotos en llamadas `url_for`.")

    lines.extend(["", "## Enlaces duros en `base.html`", ""])
    if data["hrefs_duros"]:
        lines.extend(["| Linea | Href | Estado | Resultado |", "|---:|---|---:|---|"])
        for ref in data["hrefs_test"][:200]:
            resultado = "ok" if ref["ok"] else "revisar"
            lines.append(f"| {ref['line']} | `{ref['href']}` | {ref['status']} | {resultado} |")
    else:
        lines.append("No se detectan enlaces duros relevantes.")

    lines.extend(["", "## Bases de datos locales", ""])
    lines.extend(["| DB | MB | Tablas | Modificada |", "|---|---:|---:|---|"])
    for db in data["dbs"]:
        lines.append(f"| {db['name']} | {db['size_mb']} | {db['tables']} | {db['modified']} |")

    lines.extend(["", "## Scripts de prueba/simulacion", ""])
    for script in data["scripts_prueba"]:
        lines.append(f"- `{script}`")

    lines.extend(
        [
            "",
            "## Siguiente cierre beta",
            "",
            "1. Probar primero los menus de `base.html` que usan enlaces duros.",
            "2. Ejecutar prueba de rutas GET autenticadas con usuario beta.",
            "3. Ejecutar simulador en control sano para Hotel 1 y Albarracin.",
            "4. Ejecutar simulador bruto para Hotel 1 y clasificar roturas.",
            "5. Reparar por orden: login/centro activo, IA, PMS, compras, produccion, RRHH, contabilidad, APPCC.",
        ]
    )
    return "\n".join(lines) + "\n"


def auditar():
    app = _load_app()
    blueprints = _blueprints_registrados(app)
    rutas = _rutas_registradas(app)
    endpoints = {r["endpoint"] for r in rutas}
    url_for_refs = _extraer_url_for_de_templates()
    missing = [ref for ref in url_for_refs if ref["endpoint"] not in endpoints]
    hrefs_duros = _extraer_hrefs_duros_base()
    hrefs_test = _probar_hrefs(app, hrefs_duros)
    carpetas = _modulos_desde_carpetas()
    data = {
        "blueprints": blueprints,
        "rutas": rutas,
        "url_for_refs": url_for_refs,
        "missing_endpoints": missing,
        "hrefs_duros": hrefs_duros,
        "hrefs_test": hrefs_test,
        "scripts_prueba": _scripts_prueba(),
        "dbs": _dbs(),
        "modulos": _estado_modulos(blueprints, rutas, carpetas),
    }
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Carpeta de salida")
    args = parser.parse_args()
    data = auditar()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"auditoria_beta_erp_{stamp}.md"
    out_path.write_text(_markdown(data), encoding="utf-8")
    print(out_path)
    print(
        f"blueprints={len(data['blueprints'])} rutas={len(data['rutas'])} "
        f"missing_endpoints={len(data['missing_endpoints'])} hrefs_duros={len(data['hrefs_duros'])}"
    )


if __name__ == "__main__":
    main()
