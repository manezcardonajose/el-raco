#!/usr/bin/env python3
"""Arranque estable de El Raco ERP para Windows y entorno local."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = Path(os.environ.get("ERP_ENV_FILE") or (ROOT / ".env")).resolve()


def main() -> int:
    if not ENV_FILE.is_file():
        print("Falta .env. Ejecuta primero INSTALAR_ERP.bat.")
        return 2
    load_dotenv(ENV_FILE, override=True)
    secret = os.environ.get("SECRET_KEY") or ""
    if len(secret) < 32 or secret == "dev-insecure-key-cambiar-en-produccion":
        print("La configuracion no tiene una SECRET_KEY segura. Repite la instalacion.")
        return 2
    db_value = os.environ.get("ERP_DATABASE") or ""
    db_path = Path(db_value)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
        os.environ["ERP_DATABASE"] = str(db_path.resolve())
    if not db_path.is_file():
        print(f"No se encuentra la base configurada: {db_path}")
        return 2
    try:
        from waitress import serve
    except ImportError:
        print("Falta el servidor Waitress. Ejecuta de nuevo INSTALAR_ERP.bat.")
        return 2
    sys.path.insert(0, str(ROOT))
    from app import app

    host = os.environ.get("ERP_HOST", "127.0.0.1")
    port = int(os.environ.get("ERP_PORT", "5001"))
    print(f"El Raco ERP esta disponible en http://{host}:{port}")
    print("Manten esta ventana abierta. Pulsa Ctrl+C para detenerla.")
    serve(app, host=host, port=port, threads=8)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
