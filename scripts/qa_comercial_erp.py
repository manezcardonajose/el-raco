#!/usr/bin/env python3
"""Prueba comercial repetible sobre una copia desechable de la ERP."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sqlite3
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]


class Enlaces(HTMLParser):
    def __init__(self):
        super().__init__()
        self.valores: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.valores.append(href)


def args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="QA no destructiva de El Raco ERP")
    p.add_argument("--source", default=str(ROOT / "simulacion_el_raco.db"))
    p.add_argument("--report", default="")
    p.add_argument("--keep-copy", action="store_true")
    return p.parse_args()


def sesion(client, user_id: int, role: str) -> None:
    with client.session_transaction() as s:
        s.clear()
        s.update(
            user_id=user_id,
            user_nombre=f"QA {role}",
            user_email=f"qa.{role}@local.invalid",
            user_rol=role,
            user_empresa_id=1,
            user_es_global=1 if role == "contable" else 0,
            debe_cambiar_password=0,
        )


def main() -> int:
    cfg = args()
    source = Path(cfg.source).resolve()
    if not source.is_file():
        print(f"ERROR: no existe la base origen: {source}", file=sys.stderr)
        return 2
    temp_dir = ROOT / "temp"
    temp_dir.mkdir(exist_ok=True)
    copy = temp_dir / f"qa_comercial_{secrets.token_hex(6)}.db"
    shutil.copy2(source, copy)

    os.environ["ERP_DATABASE"] = str(copy)
    os.environ["SECRET_KEY"] = secrets.token_urlsafe(48)
    os.environ["ERP_COOKIE_SECURE"] = "false"
    sys.path.insert(0, str(ROOT))

    from app import app
    from database import get_db, init_db
    from blueprints.auth.routes import _ensure_auth_schema
    from helpers.schema_bootstrap import inicializar_esquemas_modulos

    init_db()
    with app.test_request_context("/"):
        from flask import session
        session.update(user_id=0, user_rol="admin", user_empresa_id=1, user_es_global=1)
        schema_conn = get_db()
        try:
            inicializar_esquemas_modulos(schema_conn)
        finally:
            schema_conn.close()
    ids = {}
    isolation = {}
    password = secrets.token_urlsafe(18)
    with app.app_context():
        conn = get_db()
        _ensure_auth_schema(conn)
        for role in ("admin", "contable", "camarero", "recepcion", "empleado"):
            row = conn.execute("SELECT id FROM roles WHERE nombre=?", (role,)).fetchone()
            email = f"qa.{role}@local.invalid"
            conn.execute("DELETE FROM usuarios WHERE email=?", (email,))
            cur = conn.execute(
                """INSERT INTO usuarios
                (nombre,email,password_hash,rol_id,empresa_id,activo,debe_cambiar_password)
                VALUES (?,?,?,?,1,1,0)""",
                (f"QA {role}", email, generate_password_hash(password), row["id"]),
            )
            ids[role] = cur.lastrowid
        empresa_2 = conn.execute(
            "INSERT INTO empresas(nombre,nif,activo) VALUES('QA Empresa Dos','QA-2',1)"
        ).lastrowid
        centro_a = conn.execute(
            "INSERT INTO centros_trabajo(nombre,codigo,empresa_id,activo) VALUES('QA Centro A','QA-A',1,1)"
        ).lastrowid
        centro_b = conn.execute(
            "INSERT INTO centros_trabajo(nombre,codigo,empresa_id,activo) VALUES('QA Centro B','QA-B',1,1)"
        ).lastrowid
        dep_a = conn.execute(
            "INSERT INTO departamentos(nombre,empresa_id,centro_id,activo) VALUES('QA Departamento A',1,?,1)",
            (centro_a,),
        ).lastrowid
        dep_b = conn.execute(
            "INSERT INTO departamentos(nombre,empresa_id,centro_id,activo) VALUES('QA Departamento B',1,?,1)",
            (centro_b,),
        ).lastrowid
        conn.execute(
            "INSERT INTO productos(nombre,codigo,unidad,activo,empresa_id) VALUES('QA Producto Empresa Uno','QA-ISO-1','ud',1,1)"
        )
        conn.execute(
            "INSERT INTO productos(nombre,codigo,unidad,activo,empresa_id) VALUES('QA Producto Empresa Dos','QA-ISO-2','ud',1,?)",
            (empresa_2,),
        )
        isolation.update(empresa_2=empresa_2, centro_a=centro_a, centro_b=centro_b, dep_a=dep_a, dep_b=dep_b)
        conn.commit()
        conn.close()

    app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
    csrf_probe = app.test_client().post(
        "/auth/login",
        data={"email": "qa.admin@local.invalid", "password": password},
        follow_redirects=False,
    )
    csrf_ok = csrf_probe.status_code == 400
    admin = app.test_client()
    admin.get("/auth/login")
    with admin.session_transaction() as s:
        login_csrf = s.get("_csrf_token")
    login = admin.post(
        "/auth/login",
        data={"email": "qa.admin@local.invalid", "password": password, "csrf_token": login_csrf},
        follow_redirects=False,
    )
    login_ok = login.status_code in (302, 303) and "/auth/login" not in (login.headers.get("Location") or "")
    home = admin.get("/", follow_redirects=True)
    parser = Enlaces()
    parser.feed(home.get_data(as_text=True))
    paths = {"/"}
    for href in parser.valores:
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
            continue
        if parsed.path.startswith("/static/") or parsed.path == "/auth/logout":
            continue
        paths.add(parsed.path + (("?" + parsed.query) if parsed.query else ""))

    route_failures = []
    for path in sorted(paths):
        response = admin.get(path, follow_redirects=True)
        if response.status_code >= 400:
            route_failures.append({"path": path, "status": response.status_code})

    public_paths = {"/experiencia/publico"}
    auth_failures = []
    anon = app.test_client()
    for path in sorted(paths):
        response = anon.get(path, follow_redirects=False)
        if path in public_paths:
            continue
        if response.status_code not in (302, 303, 307, 308):
            auth_failures.append({"path": path, "status": response.status_code})

    matrix = {
        "contable": [("/contabilidad/", True), ("/rrhh/seleccion", False), ("/auth/usuarios", False), ("/tpv/", False)],
        "camarero": [("/tpv/", True), ("/contabilidad/", False), ("/auth/usuarios", False)],
        "recepcion": [("/recepcion/", True), ("/contabilidad/", False), ("/auth/usuarios", False)],
        "empleado": [("/rrhh/empleado", True), ("/contabilidad/", False), ("/auth/usuarios", False)],
    }
    role_failures = []
    for role, checks in matrix.items():
        client = app.test_client()
        sesion(client, ids[role], role)
        for path, allowed in checks:
            response = client.get(path, follow_redirects=False)
            location = response.headers.get("Location") or ""
            passed = response.status_code == 200 if allowed else response.status_code in (302, 303) and location == "/"
            if not passed:
                role_failures.append({"role": role, "path": path, "allowed": allowed, "status": response.status_code, "location": location})

    isolation_failures = []
    empresa_uno = app.test_client()
    sesion(empresa_uno, ids["admin"], "admin")
    html_uno = empresa_uno.get("/productos/?buscar=QA-ISO", follow_redirects=True).get_data(as_text=True)
    if "QA Producto Empresa Uno" not in html_uno or "QA Producto Empresa Dos" in html_uno:
        isolation_failures.append("productos_empresa_1")

    empresa_dos = app.test_client()
    sesion(empresa_dos, ids["admin"], "admin")
    with empresa_dos.session_transaction() as s:
        s["user_empresa_id"] = isolation["empresa_2"]
    html_dos = empresa_dos.get("/productos/?buscar=QA-ISO", follow_redirects=True).get_data(as_text=True)
    if "QA Producto Empresa Dos" not in html_dos or "QA Producto Empresa Uno" in html_dos:
        isolation_failures.append("productos_empresa_2")

    centro = app.test_client()
    sesion(centro, ids["admin"], "admin")
    with centro.session_transaction() as s:
        s.update(
            centro_activo_id=isolation["centro_a"],
            user_centro_id=isolation["centro_a"],
            user_centro_bloqueado=1,
        )
    html_centro = centro.get("/departamentos/", follow_redirects=True).get_data(as_text=True)
    if "QA Departamento A" not in html_centro or "QA Departamento B" in html_centro:
        isolation_failures.append("listado_centro")
    detalle_otro = centro.get(f"/departamentos/{isolation['dep_b']}", follow_redirects=False)
    if detalle_otro.status_code not in (302, 303):
        isolation_failures.append("detalle_otro_centro")

    with sqlite3.connect(copy) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]

    result = {
        "ok": bool(login_ok and csrf_ok and home.status_code == 200 and integrity == "ok" and not route_failures and not auth_failures and not role_failures and not isolation_failures),
        "source": str(source),
        "copy": str(copy),
        "login_ok": login_ok,
        "csrf_ok": csrf_ok,
        "visible_routes": len(paths),
        "route_failures": route_failures,
        "auth_failures": auth_failures,
        "role_failures": role_failures,
        "isolation_failures": isolation_failures,
        "integrity": integrity,
    }
    output = json.dumps(result, ensure_ascii=False, indent=2)
    print(output)
    if cfg.report:
        report = Path(cfg.report).resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(output + "\n", encoding="utf-8")
    if not cfg.keep_copy:
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(copy) + suffix).unlink(missing_ok=True)
            except PermissionError:
                pass
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
