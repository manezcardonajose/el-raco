# -*- coding: utf-8 -*-
"""Corrige estaciones de buffet mal clasificadas.

Aplica reglas conservadoras: bolleria a desayuno/panaderia, quesos y fiambres
a frio, tartas a postres y quesadillas/snack caliente a caliente.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Corregir estaciones de buffet.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=EMPRESA_ID)
    parser.add_argument("--centro-id", type=int, default=None, help="Si se omite, corrige todos los centros de la empresa.")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def norm(text: str) -> str:
    return (text or "").lower().replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ñ", "n")


def estacion_correcta(nombre: str, contexto: str = "") -> tuple[str | None, str]:
    n = norm(nombre)
    ctx = norm(contexto)
    if any(x in n for x in ("napolitana", "croissant", "ensaimada", "magdalena", "donut", "berlina", "palmera", "caracola", "hojaldre")):
        return "Desayuno - bolleria/panes", "Bolleria de desayuno: no va en linea caliente."
    if "quesadilla" in n:
        return "Calientes - snack", "Quesadilla es elaboracion caliente/snack."
    if any(x in n for x in ("tarta", "flan", "mousse", "natilla", "arroz con leche", "brownie", "bizcocho")):
        return "Postres", "Postre o pasteleria."
    quesos_fiambres = (
        "queso", "edam", "gouda", "havarti", "cheddar", "chedar", "emmental",
        "emental", "roquefort", "fresco", "fiambre", "embutido", "jamon york",
        "jamon serrano", "mortadela", "salami", "chorizo", "pavo lonchas",
    )
    if any(x in n for x in quesos_fiambres) and not any(x in n for x in ("pizza", "salsa", "gratin")):
        return "Frios - quesos y fiambres", "Quesos/fiambres siempre en frio."
    if any(x in n for x in ("ensalada", "gazpacho", "salpicon", "pepino", "lechuga", "tomate")) or "frio" in ctx:
        return "Frios", "Plato frio."
    return None, ""


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        cambios_lineas = 0
        params: list[object] = [args.empresa_id]
        centro_filter = ""
        if args.centro_id is not None:
            centro_filter = " AND (pp.centro_id=? OR pp.centro_id IS NULL)"
            params.append(args.centro_id)
        rows = conn.execute(
            """
            SELECT lp.id, lp.estacion_buffet, r.nombre, pp.turno, s.nombre AS subdepartamento
            FROM lineas_produccion lp
            JOIN recetas r ON r.id=lp.receta_id
            JOIN partes_produccion pp ON pp.id=lp.parte_id
            LEFT JOIN subdepartamentos s ON s.id=pp.subdepartamento_id
            WHERE lp.empresa_id=?
            """ + centro_filter,
            params,
        ).fetchall()
        for row in rows:
            estacion, motivo = estacion_correcta(row["nombre"], f"{row['turno']} {row['subdepartamento']}")
            if estacion and (row["estacion_buffet"] or "") != estacion:
                conn.execute(
                    """
                    UPDATE lineas_produccion
                    SET estacion_buffet=?,
                        regla_calculo=COALESCE(regla_calculo, '') || ?
                    WHERE id=?
                    """,
                    (estacion, f" | Correccion buffet: {motivo}", row["id"]),
                )
                cambios_lineas += 1

        cambios_etiquetas = 0
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='simulador_buffet_etiquetas'").fetchone():
            etiqueta_params: list[object] = [args.empresa_id]
            etiqueta_filter = ""
            if args.centro_id is not None:
                etiqueta_filter = " AND centro_id=?"
                etiqueta_params.append(args.centro_id)
            for row in conn.execute(
                "SELECT id, nombre_plato, estacion_buffet FROM simulador_buffet_etiquetas WHERE empresa_id=?" + etiqueta_filter,
                etiqueta_params,
            ).fetchall():
                estacion, _motivo = estacion_correcta(row["nombre_plato"], "")
                if estacion and (row["estacion_buffet"] or "") != estacion:
                    conn.execute("UPDATE simulador_buffet_etiquetas SET estacion_buffet=? WHERE id=?", (estacion, row["id"]))
                    cambios_etiquetas += 1

        cambios_montajes = 0
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='montaje_buffet_lineas'").fetchone():
            linea_params: list[object] = [args.empresa_id]
            linea_filter = ""
            if args.centro_id is not None:
                linea_filter = " AND centro_id=?"
                linea_params.append(args.centro_id)
            lineas = conn.execute(
                "SELECT id, centro_id, tipo, temperatura FROM buffet_lineas WHERE empresa_id=?" + linea_filter,
                linea_params,
            ).fetchall()
            by_centro_tipo = {}
            for linea in lineas:
                by_centro_tipo.setdefault((linea["centro_id"], (linea["tipo"] or "").lower()), linea)
            montaje_params: list[object] = [args.empresa_id]
            montaje_filter = ""
            if args.centro_id is not None:
                montaje_filter = " AND m.centro_id=?"
                montaje_params.append(args.centro_id)
            for row in conn.execute(
                """
                SELECT ml.id, ml.plato, ml.linea_id, ml.temperatura, ml.razon_ia, m.centro_id
                FROM montaje_buffet_lineas ml
                JOIN montajes_buffet m ON m.id=ml.montaje_id
                WHERE ml.empresa_id=?
                """ + montaje_filter,
                montaje_params,
            ).fetchall():
                estacion, motivo = estacion_correcta(row["plato"], "")
                if not estacion:
                    continue
                tipo = "frio" if estacion.startswith("Frios") else "postres" if estacion == "Postres" else "neutro" if "bolleria" in estacion else "caliente"
                centro_id = row["centro_id"]
                linea = by_centro_tipo.get((centro_id, tipo)) or by_centro_tipo.get((centro_id, "frio" if tipo == "postres" else tipo))
                if linea and row["linea_id"] != linea["id"]:
                    conn.execute(
                        """
                        UPDATE montaje_buffet_lineas
                        SET linea_id=?, temperatura=?, razon_ia=COALESCE(razon_ia,'') || ?
                        WHERE id=?
                        """,
                        (linea["id"], linea["temperatura"], f" | Correccion: {motivo}", row["id"]),
                    )
                    cambios_montajes += 1
        conn.commit()
        print(f"Correccion buffet: {cambios_lineas} lineas produccion, {cambios_etiquetas} etiquetas, {cambios_montajes} lineas montaje.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
