# -*- coding: utf-8 -*-
"""Orquesta una vuelta de presion operativa sobre el simulador Hotel 1.

No resetea ni borra la campana. Encadena scripts existentes para generar mas
vida operativa y comprobar por donde se tensiona la ERP.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_DEFAULT = ROOT / "simulacion_hotel1_vida.db"
REPORT_DIR = ROOT / "publicaciones" / "el_raco"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apretar simulador Hotel 1 sin borrar datos.")
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--desde", default="", help="Fecha inicio YYYY-MM-DD. Si se omite usa 2026-08-01.")
    parser.add_argument("--dias", type=int, default=14)
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--centro-id", type=int, default=3)
    parser.add_argument("--sin-snapshot", action="store_true")
    parser.add_argument("--rapido", action="store_true", help="Salta pasos pesados y ejecuta solo presion controlada.")
    parser.add_argument("--saltar-produccion", action="store_true", help="No ejecuta la inyeccion masiva de produccion.")
    return parser.parse_args()


def connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def latest_corrida(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "simulador_vida_corridas"):
        return 0
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def choose_start(conn: sqlite3.Connection, explicit: str) -> str:
    if explicit:
        return explicit
    if table_exists(conn, "simulador_vida_kpis_diarios"):
        row = conn.execute(
            """
            SELECT fecha
            FROM simulador_vida_kpis_diarios
            WHERE fecha >= '2026-08-01'
            ORDER BY margen_estimado ASC, fecha ASC
            LIMIT 1
            """
        ).fetchone()
        if row and row["fecha"]:
            return row["fecha"]
    return "2026-08-01"


def run_step(name: str, cmd: list[str], timeout: int = 180) -> dict:
    started = datetime.now()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False)
    return {
        "name": name,
        "cmd": " ".join(cmd),
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "seconds": round((datetime.now() - started).total_seconds(), 2),
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
    }


def scalar(conn: sqlite3.Connection, sql: str, default=0):
    try:
        row = conn.execute(sql).fetchone()
        return row[0] if row and row[0] is not None else default
    except Exception:
        return default


def collect_summary(conn: sqlite3.Connection) -> dict:
    tables = [
        "reservas_recepcion",
        "pedidos",
        "recepciones",
        "partes_produccion",
        "lineas_produccion",
        "simulador_planificacion_menu_semanal",
        "simulador_alertas_mise_en_place",
        "simulador_jornada_operativa",
        "simulador_escandallos_diarios_plato",
        "simulador_cierre_diario",
        "simulador_vida_diagnosticos_dia",
        "simulador_vida_plan_accion",
        "simulador_vida_eventos",
        "simulador_crisis_operativa",
        "simulador_prueba_fuego_resumen",
        "simulador_prueba_fuego_detalle",
        "simulador_mejoras_erp_backlog",
        "asistente_memoria",
        "asistente_investigaciones",
        "asistente_fuentes_conocimiento",
    ]
    counts = {}
    for table in tables:
        counts[table] = int(scalar(conn, f"SELECT COUNT(*) FROM {table}", 0)) if table_exists(conn, table) else 0
    top_events = []
    if table_exists(conn, "simulador_vida_eventos"):
        top_events = [
            dict(row)
            for row in conn.execute(
                """
                SELECT modulo, severidad, COUNT(*) total
                FROM simulador_vida_eventos
                GROUP BY modulo, severidad
                ORDER BY total DESC
                LIMIT 12
                """
            )
        ]
    return {"counts": counts, "top_events": top_events}


def main() -> None:
    args = parse_args()
    db = Path(args.db).resolve()
    if not db.exists():
        raise SystemExit(f"No existe la base: {db}")

    conn = connect(db)
    try:
        corrida_id = args.corrida_id or latest_corrida(conn) or 1
        desde = choose_start(conn, args.desde)
    finally:
        conn.close()
    hasta = (date.fromisoformat(desde) + timedelta(days=max(1, args.dias) - 1)).isoformat()

    py = sys.executable
    steps: list[tuple[str, list[str], int]] = []
    if not args.sin_snapshot:
        steps.append((
            "snapshot_seguridad",
            [py, "scripts/erp_herramientas_seguridad.py", "snapshot", "--db", str(db), "--motivo", f"antes de apretar simulador {desde} {hasta}", "--centro-id", str(args.centro_id)],
            240,
        ))
    if not (args.rapido or args.saltar_produccion):
        steps.append(("inyectar_produccion", [py, "scripts/inyectar_produccion_operativa_hotel1.py", "--db", str(db), "--desde", desde, "--dias", str(args.dias), "--corrida-id", str(corrida_id)], 240))
    steps.extend([
        ("sanear_productos_compras", [py, "scripts/sanear_productos_compras_simulacion.py", "--db", str(db), "--centro-id", str(args.centro_id)], 120),
        ("planificacion_semanal", [py, "scripts/generar_planificacion_semanal_simulacion.py", "--db", str(db), "--desde", desde, "--dias", str(args.dias), "--corrida-id", str(corrida_id)], 180),
        ("pedidos_mise", [py, "scripts/generar_pedidos_mise_simulacion.py", "--db", str(db), "--desde", desde, "--hasta", hasta, "--corrida-id", str(corrida_id), "--centro-id", str(args.centro_id), "--estado", "enviado"], 240),
        ("pedidos_alertas_mise", [py, "scripts/generar_pedidos_alertas_mise_simulacion.py", "--db", str(db), "--desde", desde, "--hasta", hasta, "--corrida-id", str(corrida_id), "--centro-id", str(args.centro_id), "--estado", "enviado"], 240),
        ("pedidos_stock_minimo", [py, "scripts/generar_pedidos_stock_minimo_simulacion.py", "--db", str(db), "--fecha", desde, "--corrida-id", str(corrida_id), "--centro-id", str(args.centro_id), "--estado", "enviado"], 240),
        ("recepcionar_mise", [py, "scripts/recepcionar_pedidos_mise_simulacion.py", "--db", str(db), "--corrida-id", str(corrida_id)], 240),
        ("recepcionar_pendientes", [py, "scripts/recepcionar_pedidos_pendientes_simulacion.py", "--db", str(db), "--corrida-id", str(corrida_id), "--centro-id", str(args.centro_id), "--max-pedidos", "90"], 240),
        ("resolver_pedidos_parciales", [py, "scripts/resolver_pedidos_parciales_simulacion.py", "--db", str(db), "--corrida-id", str(corrida_id), "--centro-id", str(args.centro_id), "--max-pedidos", "120"], 180),
        ("recepcionar_rescates", [py, "scripts/recepcionar_pedidos_pendientes_simulacion.py", "--db", str(db), "--corrida-id", str(corrida_id), "--centro-id", str(args.centro_id), "--max-pedidos", "120", "--prefijos", "RESCATE-H1-"], 240),
        ("normalizar_compras", [py, "scripts/normalizar_estados_compras.py", "--db", str(db), "--json"], 120),
        ("crisis_operativa_cruzada", [py, "scripts/inyectar_crisis_operativa_simulacion.py", "--db", str(db), "--corrida-id", str(corrida_id), "--centro-id", str(args.centro_id), "--desde", desde, "--dias", str(min(max(args.dias, 1), 18)), "--intensidad", "alta"], 180),
        ("jornada_operativa", [py, "scripts/gestionar_jornada_operativa_simulacion.py", "--db", str(db), "--desde", desde, "--hasta", hasta, "--corrida-id", str(corrida_id), "--centro-id", str(args.centro_id)], 180),
        ("escandallos_diarios", [py, "scripts/generar_escandallos_diarios_produccion.py", "--db", str(db), "--corrida-id", str(corrida_id), "--centro-id", str(args.centro_id)], 240),
        ("plan_reduccion_merma", [py, "scripts/aplicar_plan_reduccion_merma_simulacion.py", "--db", str(db), "--desde", desde, "--corrida-id", str(corrida_id), "--centro-id", str(args.centro_id), "--merma-min", "5", "--limite", "2500" if not args.rapido else "250"], 240),
        ("escandallos_post_merma", [py, "scripts/generar_escandallos_diarios_produccion.py", "--db", str(db), "--corrida-id", str(corrida_id), "--centro-id", str(args.centro_id)], 240),
        ("cierre_diario", [py, "scripts/generar_cierre_diario_simulacion.py", "--db", str(db), "--corrida-id", str(corrida_id), "--hora-corte", "01:00"], 180),
        ("diagnostico_vida", [py, "scripts/diagnosticar_simulacion_vida.py", "--db", str(db)], 180),
        ("auditoria_calidad", [py, "scripts/auditar_calidad_simulacion.py", "--db", str(db)], 180),
        ("prueba_fuego", [py, "scripts/auditar_prueba_fuego_simulador.py", "--db", str(db), "--corrida-id", str(corrida_id), "--centro-id", str(args.centro_id), "--json"], 180),
        ("aprendizaje_ia", [py, "scripts/aprender_de_prueba_fuego_simulador.py", "--db", str(db), "--corrida-id", str(corrida_id), "--centro-id", str(args.centro_id), "--json"], 180),
        ("fuentes_externas_ia", [py, "scripts/buscar_fuentes_externas_simulador.py", "--db", str(db), "--max-investigaciones", "3", "--resultados", "2", "--json"], 180),
        ("veredicto", [py, "scripts/veredicto_brutal_simulacion.py", "--db", str(db)], 180),
    ])

    results = []
    for name, cmd, timeout in steps:
        results.append(run_step(name, cmd, timeout))

    conn = connect(db)
    try:
        summary = collect_summary(conn)
    finally:
        conn.close()

    payload = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "db": str(db),
        "corrida_id": corrida_id,
        "desde": desde,
        "hasta": hasta,
        "dias": args.dias,
        "ok": all(step["ok"] for step in results),
        "steps": results,
        "summary": summary,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"simulador_hotel1_apretar_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": payload["ok"], "desde": desde, "hasta": hasta, "corrida_id": corrida_id, "reporte": str(out), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
