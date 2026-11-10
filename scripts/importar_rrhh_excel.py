# -*- coding: utf-8 -*-
"""CLI para importar RRHH desde Excel a pepe4.db.

Ejemplos:
  python scripts/importar_rrhh_excel.py --excel Nexora_Rentabilidad_Hoteles_Referencias.xlsx --modo socios --limite 44
  python scripts/importar_rrhh_excel.py --excel empleados_cliente.xlsx --modo generico --hoja EMPLEADOS --tipo real
  python scripts/importar_rrhh_excel.py --excel Nexora_Rentabilidad_Hoteles_Referencias.xlsx --modo operativa --estrategia sumatorio --limite 44
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from blueprints.rrhh.importador_excel import importar_rrhh_desde_excel  # noqa: E402


def localizar_db(ruta: str | None) -> Path:
    if ruta:
        return Path(ruta)
    candidatos = [
        ROOT / "pepe4.db",
        ROOT / "instance" / "pepe4.db",
        ROOT / "instance" / "app.db",
        ROOT / "app.db",
    ]
    for c in candidatos:
        if c.exists():
            return c
    return ROOT / "pepe4.db"


def main() -> int:
    parser = argparse.ArgumentParser(description="Importar empleados/categorías RRHH desde Excel")
    parser.add_argument("--excel", required=True, help="Ruta del archivo Excel .xlsx")
    parser.add_argument("--db", default=None, help="Ruta de la base SQLite. Por defecto intenta pepe4.db")
    parser.add_argument("--empresa-id", type=int, default=1)
    parser.add_argument("--modo", choices=["socios", "operativa", "generico", "todo_ops"], default="socios")
    parser.add_argument("--hoja", default="EMPLEADOS", help="Hoja para modo generico")
    parser.add_argument("--limite", type=int, default=None, help="Límite de empleados a crear. Ej: 44")
    parser.add_argument("--solo-activos", action="store_true", help="En SOCIOS importa solo Activo=Sí")
    parser.add_argument("--estrategia", choices=["sumatorio", "max_por_rol"], default="sumatorio", help="Para modo operativa")
    parser.add_argument("--tipo", choices=["plantilla", "real"], default="plantilla")
    args = parser.parse_args()

    db_path = localizar_db(args.db)
    excel_path = Path(args.excel)
    if not excel_path.exists():
        print(f"ERROR: no existe el Excel: {excel_path}")
        return 2

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        resultado = importar_rrhh_desde_excel(
            conn,
            str(excel_path),
            empresa_id=args.empresa_id,
            modo=args.modo,
            hoja_generica=args.hoja,
            limite=args.limite,
            solo_activos=args.solo_activos,
            estrategia_operativa=args.estrategia,
            tipo_empleado=args.tipo,
        )
        conn.commit()
    finally:
        conn.close()
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    return 1 if resultado.get("errores") else 0


if __name__ == "__main__":
    raise SystemExit(main())
