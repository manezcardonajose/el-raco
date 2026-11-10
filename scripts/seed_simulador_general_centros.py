# -*- coding: utf-8 -*-
"""Crea la capa general del simulador por empresa/centro.

El simulador no debe ser "un hotel suelto". Debe ser un motor comun donde
cada centro tenga su configuracion, escenarios, corridas y resultados.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "pepe4.db"
BACKUP_DIR = ROOT / "backups"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sembrar configuracion general del simulador.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--sin-backup", action="store_true")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = (), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row and row[0] is not None else default
    except sqlite3.Error:
        return default


def backup_db(path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = BACKUP_DIR / f"{path.stem}_pre_simulador_general_{stamp}{path.suffix}"
    shutil.copy2(path, out)
    return out


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulador_config_centros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            centro_id INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            tipo_negocio TEXT NOT NULL DEFAULT 'hotel',
            modo_aislamiento TEXT NOT NULL DEFAULT 'mixto',
            db_laboratorio TEXT,
            horizonte_defecto TEXT NOT NULL DEFAULT '12m',
            fecha_inicio_defecto TEXT,
            estado TEXT NOT NULL DEFAULT 'activo',
            nivel_riesgo TEXT NOT NULL DEFAULT 'medio',
            permite_escritura_erp INTEGER NOT NULL DEFAULT 0,
            descripcion TEXT,
            parametros_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(empresa_id, centro_id)
        );

        CREATE TABLE IF NOT EXISTS simulador_escenarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_id INTEGER NOT NULL,
            codigo TEXT NOT NULL,
            nombre TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'operativo',
            estado TEXT NOT NULL DEFAULT 'activo',
            prioridad INTEGER NOT NULL DEFAULT 50,
            descripcion TEXT,
            parametros_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(config_id, codigo)
        );

        CREATE TABLE IF NOT EXISTS simulador_corridas_generales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_id INTEGER NOT NULL,
            escenario_id INTEGER,
            codigo TEXT NOT NULL,
            nombre TEXT NOT NULL,
            fecha_inicio TEXT,
            fecha_fin TEXT,
            dias INTEGER,
            estado TEXT NOT NULL DEFAULT 'preparada',
            origen TEXT NOT NULL DEFAULT 'manual',
            script_principal TEXT,
            db_destino TEXT,
            resumen_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT,
            finished_at TEXT,
            UNIQUE(config_id, codigo)
        );

        CREATE TABLE IF NOT EXISTS simulador_resultados_generales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_general_id INTEGER NOT NULL,
            empresa_id INTEGER NOT NULL,
            centro_id INTEGER NOT NULL,
            fecha TEXT,
            modulo TEXT NOT NULL,
            indicador TEXT NOT NULL,
            valor_num REAL,
            valor_texto TEXT,
            severidad TEXT NOT NULL DEFAULT 'info',
            recomendacion TEXT,
            fuente_tabla TEXT,
            fuente_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_sim_cfg_centro
            ON simulador_config_centros(empresa_id, centro_id);
        CREATE INDEX IF NOT EXISTS idx_sim_esc_config
            ON simulador_escenarios(config_id, estado, tipo);
        CREATE INDEX IF NOT EXISTS idx_sim_corr_config
            ON simulador_corridas_generales(config_id, estado);
        CREATE INDEX IF NOT EXISTS idx_sim_res_centro
            ON simulador_resultados_generales(empresa_id, centro_id, fecha, modulo);
        """
    )


def centro_nombre(conn: sqlite3.Connection, empresa_id: int, centro_id: int, fallback: str) -> str:
    if not table_exists(conn, "centros_trabajo"):
        return fallback
    row = conn.execute(
        "SELECT nombre FROM centros_trabajo WHERE id=? AND empresa_id=?",
        (centro_id, empresa_id),
    ).fetchone()
    return str(row["nombre"]) if row and row["nombre"] else fallback


def upsert_config(conn: sqlite3.Connection, data: dict) -> int:
    conn.execute(
        """
        INSERT INTO simulador_config_centros
        (empresa_id, centro_id, nombre, tipo_negocio, modo_aislamiento, db_laboratorio,
         horizonte_defecto, fecha_inicio_defecto, estado, nivel_riesgo,
         permite_escritura_erp, descripcion, parametros_json, updated_at)
        VALUES (:empresa_id, :centro_id, :nombre, :tipo_negocio, :modo_aislamiento, :db_laboratorio,
                :horizonte_defecto, :fecha_inicio_defecto, :estado, :nivel_riesgo,
                :permite_escritura_erp, :descripcion, :parametros_json, CURRENT_TIMESTAMP)
        ON CONFLICT(empresa_id, centro_id) DO UPDATE SET
            nombre=excluded.nombre,
            tipo_negocio=excluded.tipo_negocio,
            modo_aislamiento=excluded.modo_aislamiento,
            db_laboratorio=excluded.db_laboratorio,
            horizonte_defecto=excluded.horizonte_defecto,
            fecha_inicio_defecto=excluded.fecha_inicio_defecto,
            estado=excluded.estado,
            nivel_riesgo=excluded.nivel_riesgo,
            permite_escritura_erp=excluded.permite_escritura_erp,
            descripcion=excluded.descripcion,
            parametros_json=excluded.parametros_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        data,
    )
    row = conn.execute(
        "SELECT id FROM simulador_config_centros WHERE empresa_id=? AND centro_id=?",
        (data["empresa_id"], data["centro_id"]),
    ).fetchone()
    return int(row["id"])


def upsert_scenario(conn: sqlite3.Connection, config_id: int, data: dict) -> int:
    payload = dict(data, config_id=config_id)
    conn.execute(
        """
        INSERT INTO simulador_escenarios
        (config_id, codigo, nombre, tipo, estado, prioridad, descripcion, parametros_json, updated_at)
        VALUES (:config_id, :codigo, :nombre, :tipo, :estado, :prioridad, :descripcion,
                :parametros_json, CURRENT_TIMESTAMP)
        ON CONFLICT(config_id, codigo) DO UPDATE SET
            nombre=excluded.nombre,
            tipo=excluded.tipo,
            estado=excluded.estado,
            prioridad=excluded.prioridad,
            descripcion=excluded.descripcion,
            parametros_json=excluded.parametros_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        payload,
    )
    row = conn.execute(
        "SELECT id FROM simulador_escenarios WHERE config_id=? AND codigo=?",
        (config_id, data["codigo"]),
    ).fetchone()
    return int(row["id"])


def upsert_run(conn: sqlite3.Connection, config_id: int, scenario_id: int, data: dict) -> int:
    payload = dict(data, config_id=config_id, escenario_id=scenario_id)
    conn.execute(
        """
        INSERT INTO simulador_corridas_generales
        (config_id, escenario_id, codigo, nombre, fecha_inicio, fecha_fin, dias, estado,
         origen, script_principal, db_destino, resumen_json)
        VALUES (:config_id, :escenario_id, :codigo, :nombre, :fecha_inicio, :fecha_fin, :dias,
                :estado, :origen, :script_principal, :db_destino, :resumen_json)
        ON CONFLICT(config_id, codigo) DO UPDATE SET
            escenario_id=excluded.escenario_id,
            nombre=excluded.nombre,
            fecha_inicio=excluded.fecha_inicio,
            fecha_fin=excluded.fecha_fin,
            dias=excluded.dias,
            estado=excluded.estado,
            origen=excluded.origen,
            script_principal=excluded.script_principal,
            db_destino=excluded.db_destino,
            resumen_json=excluded.resumen_json
        """,
        payload,
    )
    row = conn.execute(
        "SELECT id FROM simulador_corridas_generales WHERE config_id=? AND codigo=?",
        (config_id, data["codigo"]),
    ).fetchone()
    return int(row["id"])


def refresh_albarracin_results(conn: sqlite3.Connection, corrida_id: int) -> int:
    conn.execute(
        "DELETE FROM simulador_resultados_generales WHERE corrida_general_id=?",
        (corrida_id,),
    )
    if not table_exists(conn, "simulador_albarracin_ocupacion_resumen"):
        return 0
    rows = conn.execute(
        """
        SELECT substr(fecha, 1, 7) mes,
               ROUND(AVG(ocupacion_pct), 2) ocupacion,
               SUM(pax) pax,
               SUM(habitaciones_ocupadas) habitaciones_ocupadas
        FROM simulador_albarracin_ocupacion_resumen
        WHERE empresa_id=2 AND centro_id=8 AND year=2027
        GROUP BY substr(fecha, 1, 7)
        ORDER BY mes
        """
    ).fetchall()
    total = 0
    for row in rows:
        conn.execute(
            """
            INSERT INTO simulador_resultados_generales
            (corrida_general_id, empresa_id, centro_id, fecha, modulo, indicador,
             valor_num, valor_texto, severidad, recomendacion, fuente_tabla)
            VALUES (?, 2, 8, ?, 'recepcion', 'ocupacion_media_mensual',
                    ?, ?, ?, ?, 'simulador_albarracin_ocupacion_resumen')
            """,
            (
                corrida_id,
                f"{row['mes']}-01",
                row["ocupacion"],
                f"{row['ocupacion']}% ocupacion media; {row['pax']} pax simulados",
                "riesgo" if float(row["ocupacion"] or 0) < 35 else "info",
                "Revisar accion comercial y costes fijos." if float(row["ocupacion"] or 0) < 35 else "Cruzar con costes, comidas y plantilla.",
            ),
        )
        total += 1
    return total


def main() -> None:
    args = parse_args()
    db = Path(args.db).resolve()
    if not db.exists():
        raise SystemExit(f"No existe la base: {db}")
    backup = None if args.sin_backup else backup_db(db)

    conn = connect(db)
    try:
        ensure_schema(conn)

        hotel1_name = centro_nombre(conn, 1, 3, "Hotel 1")
        alb_name = centro_nombre(conn, 2, 8, "Hotel Albarracin")

        hotel1_config = upsert_config(
            conn,
            {
                "empresa_id": 1,
                "centro_id": 3,
                "nombre": hotel1_name,
                "tipo_negocio": "hotel_laboratorio",
                "modo_aislamiento": "db_laboratorio",
                "db_laboratorio": "simulacion_hotel1_vida.db",
                "horizonte_defecto": "9m",
                "fecha_inicio_defecto": "2026-06-01",
                "estado": "activo",
                "nivel_riesgo": "alto",
                "permite_escritura_erp": 0,
                "descripcion": "Laboratorio de prueba de fuego: romper, medir y aprender sin tocar la base real.",
                "parametros_json": json.dumps(
                    {
                        "habitaciones_aprox": scalar(conn, "SELECT COUNT(*) FROM habitaciones_recepcion WHERE empresa_id=1 AND centro_id=3 AND COALESCE(activo,1)=1"),
                        "uso": "prueba_fuego",
                        "regla": "todo dato peligroso vive en base laboratorio",
                    },
                    ensure_ascii=False,
                ),
            },
        )
        hotel1_scenario = upsert_scenario(
            conn,
            hotel1_config,
            {
                "codigo": "prueba_fuego_vida_operativa",
                "nombre": "Vida operativa y prueba de fuego",
                "tipo": "operativo_integral",
                "estado": "activo",
                "prioridad": 10,
                "descripcion": "Reservas, compras, cocina, RRHH, mantenimiento, pagos, cierres, overbooking y aprendizaje IA.",
                "parametros_json": json.dumps(
                    {"duracion": "9m", "parada_tecnica": "15 dias si es anual", "presion": "alta"},
                    ensure_ascii=False,
                ),
            },
        )
        upsert_run(
            conn,
            hotel1_config,
            hotel1_scenario,
            {
                "codigo": "hotel1_vida_2026",
                "nombre": "Hotel 1 - campana laboratorio 2026",
                "fecha_inicio": "2026-06-01",
                "fecha_fin": "2027-02-28",
                "dias": 273,
                "estado": "preparada",
                "origen": "simulador_hotel1",
                "script_principal": "tools/simulador_vida_operativa_hotel1.py",
                "db_destino": "simulacion_hotel1_vida.db",
                "resumen_json": json.dumps({"nota": "Corrida aislada fuera de pepe4.db"}, ensure_ascii=False),
            },
        )

        alb_config = upsert_config(
            conn,
            {
                "empresa_id": 2,
                "centro_id": 8,
                "nombre": alb_name,
                "tipo_negocio": "hotel_gestion",
                "modo_aislamiento": "base_real_marcada",
                "db_laboratorio": "pepe4.db",
                "horizonte_defecto": "12m",
                "fecha_inicio_defecto": "2027-01-01",
                "estado": "activo",
                "nivel_riesgo": "medio",
                "permite_escritura_erp": 1,
                "descripcion": "Simulacion de gestion realista para estudiar rentabilidad de Hotel Albarracin.",
                "parametros_json": json.dumps(
                    {
                        "habitaciones": scalar(conn, "SELECT COUNT(*) FROM habitaciones_recepcion WHERE empresa_id=2 AND centro_id=8 AND COALESCE(activo,1)=1"),
                        "segmentos": ["imserso", "normal_ad", "semana_santa", "verano", "fines_semana"],
                        "regla": "datos marcados por origen y centro",
                    },
                    ensure_ascii=False,
                ),
            },
        )
        alb_scenario = upsert_scenario(
            conn,
            alb_config,
            {
                "codigo": "albarracin_ocupacion_2027",
                "nombre": "Ocupacion, Imserso y venta normal 2027",
                "tipo": "gestion_hotelera",
                "estado": "activo",
                "prioridad": 20,
                "descripcion": "Simula ocupacion anual, Imserso, fines de semana, Semana Santa, verano y oportunidades de restauracion.",
                "parametros_json": json.dumps(
                    {
                        "imserso_operador": "Mundiplan",
                        "imserso_pax_dia": 45.00,
                        "imserso_regimen": "pension_completa",
                        "imserso_bebidas_mesa": "agua y vino de la casa",
                        "imserso_suplemento_individual_dia": 25.00,
                        "imserso_adr_doble_equivalente": 90.00,
                        "equilibrio_pax_dia": 35.00,
                        "normal_ad_baja_pax_noche": 60.00,
                        "normal_ad_fin_semana_pax_noche": 70.00,
                        "normal_mp_fin_semana_pax_noche": 78.00,
                        "normal_alta_pax_noche": 85.00,
                        "semana_santa_pax_dia": 68,
                    },
                    ensure_ascii=False,
                ),
            },
        )
        alb_run = upsert_run(
            conn,
            alb_config,
            alb_scenario,
            {
                "codigo": "albarracin_2027_base",
                "nombre": "Hotel Albarracin - escenario base 2027",
                "fecha_inicio": "2027-01-01",
                "fecha_fin": "2027-12-31",
                "dias": 365,
                "estado": "ejecutada",
                "origen": "simulador_albarracin_ocupacion",
                "script_principal": "scripts/seed_albarracin_ocupacion_2027.py",
                "db_destino": "pepe4.db",
                "resumen_json": json.dumps(
                    {
                        "reservas": scalar(conn, "SELECT COUNT(*) FROM reservas_recepcion WHERE empresa_id=2 AND centro_id=8 AND origen='simulador_albarracin_ocupacion'"),
                        "servicios_restaurante": scalar(conn, "SELECT COUNT(*) FROM recepcion_reservas_restaurante WHERE empresa_id=2 AND centro_id=8 AND origen='simulador_albarracin_ocupacion'"),
                    },
                    ensure_ascii=False,
                ),
            },
        )
        alb_results = refresh_albarracin_results(conn, alb_run)
        conn.commit()

        summary = {
            "ok": True,
            "backup": str(backup) if backup else None,
            "configs": scalar(conn, "SELECT COUNT(*) FROM simulador_config_centros"),
            "escenarios": scalar(conn, "SELECT COUNT(*) FROM simulador_escenarios"),
            "corridas": scalar(conn, "SELECT COUNT(*) FROM simulador_corridas_generales"),
            "resultados_albarracin": alb_results,
        }
    finally:
        conn.close()

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
