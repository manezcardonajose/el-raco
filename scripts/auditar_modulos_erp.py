# -*- coding: utf-8 -*-
"""Auditoria estatica inicial de modulos El Raco.

No modifica la ERP. Genera un inventario de rutas, funciones, plantillas,
permisos aparentes, filtros de empresa/centro y puntos a revisar.
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "publicaciones" / "el_raco"
DOC_PATH = ROOT / "docs" / "auditoria_modulos_erp.md"


ROUTE_DECORATOR_RE = re.compile(r"\.route\(")
TEMPLATE_RE = re.compile(r"render_template\(\s*[\"']([^\"']+)[\"']")
URL_FOR_RE = re.compile(r"url_for\(\s*[\"']([^\"']+)[\"']")
SQL_DML_RE = re.compile(r"(?<!\.)\b(INSERT|UPDATE|DELETE|DROP)\b", flags=re.I)
SQL_DDL_RE = re.compile(r"(?<!\.)\b(CREATE|ALTER)\b", flags=re.I)
SCHEMA_ENSURE_RE = re.compile(r"\b(_?ensure|asegurar|schema|CREATE\s+TABLE|ALTER\s+TABLE)\b", flags=re.I)
PERMISSION_CHECK_RE = re.compile(
    r"\b(usuario_tiene_permiso|usuario_puede_[a-zA-Z0-9_]+|permiso_requerido|usuario_es_global_operacion|_puede_[a-zA-Z0-9_]+|_requiere_[a-zA-Z0-9_]+|_bloquear_[a-zA-Z0-9_]+)\b"
)

PUBLIC_ROUTE_POLICY = {
    "api_trabajos_impresion": ("controlada", "API de impresion protegida por token de dispositivo."),
    "api_ping_impresion": ("controlada", "API de impresion protegida por token de dispositivo."),
    "api_contenido_trabajo_impresion": ("controlada", "API de impresion protegida por token de dispositivo."),
    "api_raw_trabajo_impresion": ("controlada", "API de impresion protegida por token de dispositivo."),
    "api_estado_trabajo_impresion": ("controlada", "API de impresion protegida por token de dispositivo."),
    "habitacion_ical_public": ("controlada", "Calendario publico protegido por token largo por habitacion."),
    "publico_cliente": ("controlada", "Pantalla publica informativa con empresa explicita."),
    "publico_queja": ("controlada", "Formulario publico con honeypot, limite por huella y trazabilidad."),
    "checkout_simulado": ("controlada", "Checkout simulado protegido por referencia larga, caducidad y empresa resuelta desde la operacion."),
    "webhook": ("controlada", "Webhook publico resuelto por configuracion y rechazado si la firma/secreto no valida."),
}


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1", errors="replace")


def decorator_name(dec: ast.AST) -> str:
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Attribute):
        return dec.attr
    if isinstance(dec, ast.Call):
        return decorator_name(dec.func)
    if isinstance(dec, ast.Subscript):
        return decorator_name(dec.value)
    return ast.dump(dec)[:80]


def route_methods(dec: ast.AST) -> list[str]:
    if not isinstance(dec, ast.Call):
        return ["GET"]
    for kw in dec.keywords:
        if kw.arg == "methods":
            try:
                value = ast.literal_eval(kw.value)
                if isinstance(value, (list, tuple)):
                    return [str(v).upper() for v in value]
            except Exception:
                return ["GET", "POST?"]
    return ["GET"]


def route_rule(dec: ast.AST) -> str:
    if not isinstance(dec, ast.Call) or not dec.args:
        return ""
    try:
        return str(ast.literal_eval(dec.args[0]))
    except Exception:
        return ""


def analyze_python(path: Path) -> dict:
    text = read_text(path)
    item = {
        "path": rel(path),
        "module": path.parent.name if path.name in {"routes.py", "__init__.py"} else path.stem,
        "syntax_ok": True,
        "syntax_error": "",
        "functions": [],
        "routes": [],
        "templates": sorted(set(TEMPLATE_RE.findall(text))),
        "url_for": sorted(set(URL_FOR_RE.findall(text))),
        "public_routes": [],
        "metrics": {
            "lines": len(text.splitlines()),
            "except_exception": len(re.findall(r"except\s+Exception", text)),
            "bare_except": len(re.findall(r"except\s*:", text)),
            "todo_fixme": len(re.findall(r"\b(TODO|FIXME)\b", text, flags=re.I)),
            "pass_statements": len(re.findall(r"^\s*pass\s*$", text, flags=re.M)),
            "raw_sql": len(re.findall(r"\bSELECT\b|\bINSERT\b|\bUPDATE\b|\bDELETE\b", text, flags=re.I)),
            "commits": len(re.findall(r"\.commit\(", text)),
        },
        "flags": [],
    }
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        item["syntax_ok"] = False
        item["syntax_error"] = f"{exc.msg} line {exc.lineno}"
        item["flags"].append({"level": "bloqueante", "code": "syntax_error", "detail": item["syntax_error"]})
        return item

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        source_segment = ast.get_source_segment(text, node) or ""
        decorators = [decorator_name(d) for d in node.decorator_list]
        uses_permission = bool(PERMISSION_CHECK_RE.search(source_segment))
        function = {
            "name": node.name,
            "line": node.lineno,
            "decorators": decorators,
            "has_login_required": "login_required" in decorators,
            "uses_permission": uses_permission,
            "uses_empresa": "empresa_id" in source_segment or "_empresa_id" in source_segment or "user_empresa_id" in source_segment,
            "uses_centro": "centro_id" in source_segment or "centro_activo_id" in source_segment,
            "has_commit": ".commit(" in source_segment,
            "has_execute": ".execute(" in source_segment,
        }
        item["functions"].append(function)
        route_decorators = [d for d in node.decorator_list if ROUTE_DECORATOR_RE.search(ast.unparse(d) if hasattr(ast, "unparse") else ast.dump(d))]
        for dec in route_decorators:
            methods = route_methods(dec)
            route = {
                "function": node.name,
                "line": node.lineno,
                "rule": route_rule(dec),
                "methods": methods,
                "decorators": decorators,
                "has_login_required": function["has_login_required"],
                "uses_permission": function["uses_permission"],
                "uses_empresa": function["uses_empresa"],
                "uses_centro": function["uses_centro"],
            }
            item["routes"].append(route)
            if not route["has_login_required"] and not path.as_posix().endswith("auth/routes.py"):
                policy, note = PUBLIC_ROUTE_POLICY.get(node.name, ("sin_clasificar", "Ruta publica sin clasificar."))
                item["public_routes"].append({
                    "function": node.name,
                    "line": node.lineno,
                    "rule": route["rule"],
                    "methods": methods,
                    "policy": policy,
                    "note": note,
                })
                if policy == "revisar_alta":
                    item["flags"].append({
                        "level": "alta",
                        "code": "public_route_requires_security_review",
                        "detail": f"{node.name} {methods} {route['rule']} line {node.lineno}: {note}",
                    })
                elif policy == "revisar_media":
                    item["flags"].append({
                        "level": "media",
                        "code": "public_route_needs_operational_controls",
                        "detail": f"{node.name} {methods} {route['rule']} line {node.lineno}: {note}",
                    })
                elif policy != "controlada":
                    item["flags"].append({
                        "level": "alta",
                        "code": "route_without_login_required",
                        "detail": f"{node.name} {methods} {route['rule']} line {node.lineno}",
                    })
            public_policy = PUBLIC_ROUTE_POLICY.get(node.name, ("", ""))[0]
            if (
                any(m in {"POST", "PUT", "PATCH", "DELETE"} for m in methods)
                and not route["uses_permission"]
                and public_policy != "controlada"
            ):
                item["flags"].append({
                    "level": "media",
                    "code": "mutating_route_without_explicit_permission_check",
                    "detail": f"{node.name} {methods} {route['rule']} line {node.lineno}",
                })
            if methods == ["GET"] and (
                ".commit(" in source_segment
                or SQL_DML_RE.search(source_segment)
                or SQL_DDL_RE.search(source_segment)
            ):
                has_dml = bool(SQL_DML_RE.search(source_segment))
                has_schema_change = bool(SQL_DDL_RE.search(source_segment))
                has_commit = ".commit(" in source_segment
                looks_like_schema_init = has_schema_change and not has_dml and bool(SCHEMA_ENSURE_RE.search(source_segment))
                if looks_like_schema_init:
                    item["flags"].append({
                        "level": "media",
                        "code": "get_route_initializes_schema",
                        "detail": f"{node.name} {methods} {route['rule']} line {node.lineno}",
                    })
                    continue
                if has_commit and not has_dml and not has_schema_change:
                    item["flags"].append({
                        "level": "media",
                        "code": "get_route_commits_without_visible_dml",
                        "detail": f"{node.name} {methods} {route['rule']} line {node.lineno}",
                    })
                    continue
                item["flags"].append({
                    "level": "alta",
                    "code": "get_route_mutates_data",
                    "detail": f"{node.name} {methods} {route['rule']} line {node.lineno}",
                })
            if route["has_login_required"] and not route["uses_empresa"]:
                item["flags"].append({
                    "level": "media",
                    "code": "logged_route_without_empresa_scope_visible",
                    "detail": f"{node.name} {methods} {route['rule']} line {node.lineno}",
                })
    return item


def analyze_templates() -> dict:
    templates = {}
    for path in sorted((ROOT / "templates").rglob("*.html")):
        text = read_text(path)
        templates[rel(path)] = {
            "url_for": sorted(set(URL_FOR_RE.findall(text))),
            "forms_post": len(re.findall(r"<form[^>]+method=[\"']post[\"']", text, flags=re.I)),
            "has_csrf": "csrf" in text.lower(),
            "lines": len(text.splitlines()),
        }
    return templates


def main() -> None:
    py_files = [ROOT / "app.py"]
    py_files.extend(sorted((ROOT / "blueprints").glob("*/routes.py")))
    py_files.extend(sorted((ROOT / "blueprints").glob("*/__init__.py")))
    py_files.extend(sorted((ROOT / "helpers").glob("*.py")))
    modules = [analyze_python(path) for path in py_files if path.exists()]
    templates = analyze_templates()

    endpoints = {}
    for module in modules:
        for route in module["routes"]:
            endpoints[f"{module['module']}.{route['function']}"] = route

    referenced = Counter()
    for tpl in templates.values():
        referenced.update(tpl["url_for"])
    for module in modules:
        referenced.update(module["url_for"])

    missing_refs = sorted(ref for ref in referenced if ref not in endpoints and "." in ref)
    flags = []
    for module in modules:
        for flag in module["flags"]:
            flags.append({**flag, "path": module["path"], "module": module["module"]})

    summary = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "modules": len(modules),
        "routes": sum(len(m["routes"]) for m in modules),
        "functions": sum(len(m["functions"]) for m in modules),
        "templates": len(templates),
        "flags": len(flags),
        "missing_url_for_refs": len(missing_refs),
        "by_level": dict(Counter(f["level"] for f in flags)),
        "by_code": dict(Counter(f["code"] for f in flags)),
    }
    payload = {
        "summary": summary,
        "modules": modules,
        "templates": templates,
        "missing_url_for_refs": missing_refs[:300],
        "flags": flags,
        "public_routes": [
            {**public_route, "path": module["path"], "module": module["module"]}
            for module in modules
            for public_route in module.get("public_routes", [])
        ],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / f"auditoria_modulos_erp_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    top_modules = sorted(modules, key=lambda m: (len(m["flags"]), len(m["routes"])), reverse=True)
    lines = [
        "# Auditoria de modulos ERP/PMS",
        "",
        f"Fecha: {summary['fecha']}",
        "",
        "## Resumen",
        "",
        f"- Modulos Python revisados: {summary['modules']}",
        f"- Rutas detectadas: {summary['routes']}",
        f"- Funciones detectadas: {summary['functions']}",
        f"- Plantillas revisadas: {summary['templates']}",
        f"- Alertas estaticas: {summary['flags']}",
        f"- Referencias `url_for` no resueltas por inventario estatico: {summary['missing_url_for_refs']}",
        "",
        "## Criterios de revision",
        "",
        "- Seguridad: ruta autenticada, permiso, auditoria y no exposicion accidental.",
        "- Ambito: empresa y centro separados cuando corresponde.",
        "- Operativa: la pantalla debe hacer lo que promete y ser entendible.",
        "- Datos: no mezclar simulacion con real, no inventar costes como reales.",
        "- IA: toda salida generada debe poder reportarse y no ejecutar decisiones criticas sola.",
        "- Trazabilidad: cambios importantes con estado, usuario, fecha y origen.",
        "",
        "## Modulos con mas señales",
        "",
        "| Modulo | Rutas | Funciones | Alertas | Except Exception | SQL | Commits |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for module in top_modules[:25]:
        metrics = module["metrics"]
        lines.append(
            f"| {module['path']} | {len(module['routes'])} | {len(module['functions'])} | {len(module['flags'])} | "
            f"{metrics['except_exception']} | {metrics['raw_sql']} | {metrics['commits']} |"
        )
    lines.extend([
        "",
        "## Primeras alertas a revisar",
        "",
    ])
    for flag in flags[:120]:
        lines.append(f"- **{flag['level']}** `{flag['code']}` en `{flag['path']}`: {flag['detail']}")
    lines.extend([
        "",
        "## Rutas publicas detectadas",
        "",
    ])
    for route in payload["public_routes"]:
        lines.append(
            f"- `{route['path']}` `{route['function']}` {route['methods']} `{route['rule']}`: "
            f"{route['policy']} - {route['note']}"
        )
    lines.extend([
        "",
        "## Referencias url_for pendientes de revisar",
        "",
    ])
    for ref in missing_refs[:120]:
        lines.append(f"- `{ref}`")
    lines.extend([
        "",
        "## Siguiente forma de trabajo",
        "",
        "1. Revisar primero rutas bloqueantes o altas.",
        "2. Despues revisar modulos nucleares: auth, recepcion, compras, produccion, recetas, rrhh, contabilidad, tpv, asistente IA.",
        "3. En cada modulo comprobar abrir/listar/crear/editar/filtrar por empresa y centro.",
        "4. Simular Hotel 1 y Albarracin tras cada bloque de correcciones.",
        "5. Documentar que queda fuera de beta.",
    ])
    DOC_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "json": str(json_path), "doc": str(DOC_PATH), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
