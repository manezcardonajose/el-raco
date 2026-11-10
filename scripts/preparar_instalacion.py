#!/usr/bin/env python3
"""Prepara una instalacion nueva y verificable de El Raco ERP.

No copia datos de demostracion y nunca crea credenciales conocidas.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sqlite3
import string
import sys
import getpass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crear una instalacion limpia de El Raco ERP")
    parser.add_argument("--db", default=str(ROOT / "instance" / "erp.db"))
    parser.add_argument("--empresa", default="", help="Nombre comercial o razon social")
    parser.add_argument("--nif", default="")
    parser.add_argument("--email-empresa", default="")
    parser.add_argument("--admin-email", default="")
    parser.add_argument("--admin-nombre", default="Administrador")
    parser.add_argument("--admin-password", default="", help="Si se omite, se genera una clave aleatoria")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--replace-env", action="store_true")
    parser.add_argument("--no-env", action="store_true", help="No crear archivo de configuracion")
    return parser.parse_args()


def clave_aleatoria(longitud: int = 20) -> str:
    alfabeto = string.ascii_letters + string.digits + "-_!"
    while True:
        valor = "".join(secrets.choice(alfabeto) for _ in range(longitud))
        if any(c.islower() for c in valor) and any(c.isupper() for c in valor) and any(c.isdigit() for c in valor):
            return valor


def escribir_env(path: Path, db_path: Path, secret_key: str, reemplazar: bool) -> None:
    if path.exists() and not reemplazar:
        raise FileExistsError(
            f"Ya existe {path}. Usa --replace-env solo si quieres sustituir su configuracion."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    contenido = (
        "# Generado por preparar_instalacion.py\n"
        "ENV=local\n"
        f"SECRET_KEY={secret_key}\n"
        f"ERP_DATABASE={db_path.as_posix()}\n"
        "ERP_HOST=127.0.0.1\n"
        "ERP_PORT=5001\n"
        "ERP_COOKIE_SECURE=false\n"
        "ERP_PUBLIC_URL=http://127.0.0.1:5001\n"
        "DB_TIMEOUT=20\n"
        "LOG_LEVEL=INFO\n"
        "LOG_FILE=logs/pepe4.log\n"
    )
    path.write_text(contenido, encoding="utf-8")


def main() -> int:
    args = argumentos()
    if not args.empresa and sys.stdin.isatty():
        args.empresa = input("Nombre del establecimiento o empresa: ").strip()
    if not args.admin_email and sys.stdin.isatty():
        args.admin_email = input("Email del administrador: ").strip()
    db_path = Path(args.db).expanduser().resolve()
    env_path = Path(args.env_file).expanduser().resolve()
    password_generada = False
    if args.admin_password:
        password = args.admin_password
    elif sys.stdin.isatty():
        password = getpass.getpass("Clave inicial (minimo 12 caracteres): ")
        confirmar = getpass.getpass("Repite la clave: ")
        if password != confirmar:
            print("ERROR: las claves no coinciden.", file=sys.stderr)
            return 2
    else:
        password = clave_aleatoria()
        password_generada = True
    if not args.empresa.strip():
        print("ERROR: indica el nombre de la empresa o establecimiento.", file=sys.stderr)
        return 2
    if len(password) < 12:
        print("ERROR: la clave del administrador debe tener al menos 12 caracteres.", file=sys.stderr)
        return 2
    if not args.admin_email.strip() or "@" not in args.admin_email:
        print("ERROR: indica un email valido para el administrador.", file=sys.stderr)
        return 2

    db_path.parent.mkdir(parents=True, exist_ok=True)
    secret_key = secrets.token_urlsafe(48)
    os.environ["ERP_DATABASE"] = str(db_path)
    os.environ["ERP_ADMIN_EMAIL"] = args.admin_email.strip().lower()
    os.environ["ERP_ADMIN_PASSWORD"] = password
    os.environ["ERP_ADMIN_NAME"] = args.admin_nombre.strip() or "Administrador"
    os.environ["SECRET_KEY"] = secret_key

    sys.path.insert(0, str(ROOT))
    from database import init_db
    from blueprints.auth.routes import init_auth_db
    from database import get_db
    from helpers.schema_bootstrap import inicializar_esquemas_modulos
    from app import app
    from flask import session

    init_db()
    init_auth_db()
    conn_modulos = get_db()
    try:
        with app.test_request_context("/"):
            session.update(
                user_id=0,
                user_rol="admin",
                user_empresa_id=1,
                user_es_global=1,
                debe_cambiar_password=0,
            )
            modulos = inicializar_esquemas_modulos(conn_modulos)
    except Exception:
        conn_modulos.rollback()
        raise
    finally:
        conn_modulos.close()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE empresas SET nombre=?, nif=?, email=? WHERE id=1",
            (args.empresa.strip(), args.nif.strip() or None, args.email_empresa.strip() or None),
        )
        conn.commit()
        integridad = conn.execute("PRAGMA integrity_check").fetchone()[0]
        admin = conn.execute(
            "SELECT id, debe_cambiar_password FROM usuarios WHERE LOWER(email)=LOWER(?) AND activo=1",
            (args.admin_email.strip(),),
        ).fetchone()
    finally:
        conn.close()

    if integridad != "ok" or not admin:
        print("ERROR: la instalacion no supero la comprobacion final.", file=sys.stderr)
        return 1

    if not args.no_env:
        escribir_env(env_path, db_path, secret_key, args.replace_env)

    print("INSTALACION CORRECTA")
    print(f"Base de datos: {db_path}")
    if not args.no_env:
        print(f"Configuracion: {env_path}")
    print(f"Administrador: {args.admin_email.strip().lower()}")
    print(f"Esquemas de modulos preparados: {len(modulos)}")
    if password_generada:
        print(f"Clave temporal (se muestra una sola vez): {password}")
    print("El administrador debera cambiar la clave en el primer acceso.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
