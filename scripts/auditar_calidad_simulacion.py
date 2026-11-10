# -*- coding: utf-8 -*-
"""Audita coherencia de la simulacion.

No genera ni borra datos operativos. Registra incidencias de calidad para saber
si el simulador esta creando una realidad creible o si hay datos que no cuadran.
"""

from __future__ import annotations

import argparse
import calendar
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auditar calidad de datos de la simulacion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--empresa-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=3)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def latest_corrida(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "simulador_vida_corridas"):
        return 0
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_calidad_datos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            area TEXT,
            severidad TEXT,
            codigo TEXT,
            titulo TEXT,
            detalle TEXT,
            evidencia_json TEXT,
            estado TEXT DEFAULT 'abierta',
            empresa_id INTEGER,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_calidad_resumen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            total INTEGER DEFAULT 0,
            criticas INTEGER DEFAULT 0,
            altas INTEGER DEFAULT 0,
            medias INTEGER DEFAULT 0,
            bajas INTEGER DEFAULT 0,
            score_confianza REAL DEFAULT 0,
            lectura TEXT,
            empresa_id INTEGER,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def add(issues: list[dict], area: str, severidad: str, codigo: str, titulo: str, detalle: str, evidencia: dict | list | None = None) -> None:
    issues.append({
        "area": area,
        "severidad": severidad,
        "codigo": codigo,
        "titulo": titulo,
        "detalle": detalle,
        "evidencia": evidencia or {},
    })


def scalar(conn: sqlite3.Connection, sql: str, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return default
        return row[0] if row[0] is not None else default
    except Exception:
        return default


def audit_months(conn: sqlite3.Connection, corrida_id: int, issues: list[dict]) -> None:
    if not table_exists(conn, "simulador_resultado_mensual"):
        add(issues, "finanzas", "alta", "sin_resultado_mensual", "Falta resultado mensual", "No hay tabla de resultado mensual para auditar rentabilidad.")
        return
    for row in conn.execute("SELECT * FROM simulador_resultado_mensual WHERE corrida_id=?", (corrida_id,)):
        mes = row["mes"]
        try:
            year, month = [int(x) for x in str(mes).split("-")[:2]]
            dias_reales = calendar.monthrange(year, month)[1]
        except Exception:
            add(issues, "finanzas", "alta", "mes_invalido", "Mes con formato invalido", f"El mes {mes} no se puede interpretar.", dict(row))
            continue
        dias_cal = int(row["dias_calendario"] or 0)
        dias_sim = int(row["dias_simulados"] or 0)
        dias_neg = int(row["dias_negativos"] or 0)
        if dias_cal != dias_reales:
            add(issues, "finanzas", "critica", "dias_calendario_erroneos", "Dias de mes incorrectos", f"{mes} tiene {dias_cal} dias en datos, pero calendario real tiene {dias_reales}.", dict(row))
        if dias_sim > dias_reales:
            add(issues, "finanzas", "critica", "dias_simulados_exceso", "Mas dias simulados que reales", f"{mes} tiene {dias_sim} dias simulados y solo {dias_reales} dias reales.", dict(row))
        if dias_neg > dias_sim:
            add(issues, "finanzas", "critica", "dias_negativos_exceso", "Dias negativos imposibles", f"{mes} tiene {dias_neg} dias negativos sobre {dias_sim} dias simulados.", dict(row))
        ingresos = float(row["ingresos_total"] or 0)
        coste_total = float(row["coste_total"] or 0)
        margen = float(row["margen"] or 0)
        if round(ingresos - coste_total - margen, 2) != 0:
            add(issues, "finanzas", "alta", "margen_no_cuadra", "Margen mensual no cuadra", "Ingresos - coste total no coincide con margen.", dict(row))


def audit_daily_kpis(conn: sqlite3.Connection, corrida_id: int, centro_id: int, issues: list[dict]) -> None:
    if not table_exists(conn, "simulador_vida_kpis_diarios"):
        add(issues, "kpis", "alta", "sin_kpis_diarios", "Faltan KPIs diarios", "No hay KPIs diarios para contrastar la simulacion.")
        return
    rooms = int(scalar(conn, "SELECT COUNT(*) FROM habitaciones_recepcion WHERE centro_id=? AND empresa_id=? AND COALESCE(activa,1)=1", (centro_id, 1), 0))
    for row in conn.execute("SELECT * FROM simulador_vida_kpis_diarios WHERE corrida_id=?", (corrida_id,)):
        hab = int(row["habitaciones_ocupadas"] or 0)
        pax = int(row["pax"] or 0)
        ingresos_hab = float(row["ingresos_habitaciones"] or 0)
        ingresos_tpv = float(row["ingresos_tpv"] or 0)
        laboral = float(row["coste_laboral_estimado"] or 0)
        compras = float(row["coste_compras_estimado"] or 0)
        margen = float(row["margen_estimado"] or 0)
        if rooms and hab > rooms:
            add(issues, "ocupacion", "critica", "ocupacion_supera_habitaciones", "Ocupacion superior a habitaciones", f"{row['fecha']}: {hab} habitaciones ocupadas sobre {rooms} vendibles.", dict(row))
        if hab < 0 or pax < 0:
            add(issues, "kpis", "critica", "valores_negativos", "Valores negativos imposibles", f"{row['fecha']}: habitaciones o pax negativos.", dict(row))
        if hab > 0 and pax == 0:
            add(issues, "ocupacion", "alta", "habitaciones_sin_pax", "Habitaciones ocupadas sin pax", f"{row['fecha']}: hay habitaciones ocupadas pero 0 pax.", dict(row))
        if hab == 0 and ingresos_hab > 0:
            add(issues, "finanzas", "alta", "ingreso_sin_habitaciones", "Ingresos de habitaciones sin ocupacion", f"{row['fecha']}: ingresos habitacion con 0 habitaciones ocupadas.", dict(row))
        if ingresos_hab + ingresos_tpv > 0 and laboral + compras > (ingresos_hab + ingresos_tpv) * 1.8:
            add(issues, "finanzas", "media", "costes_muy_altos", "Costes muy por encima de ventas", f"{row['fecha']}: costes variables muy altos frente a ventas.", dict(row))
        if margen < -25000:
            add(issues, "finanzas", "media", "perdida_diaria_extrema", "Perdida diaria extrema", f"{row['fecha']}: perdida estimada de {margen:.2f} EUR; revisar si es real o formula.", dict(row))


def audit_structure(conn: sqlite3.Connection, empresa_id: int, centro_id: int, issues: list[dict]) -> None:
    if table_exists(conn, "empleados"):
        rows = conn.execute(
            """
            SELECT e.id, e.nombre, e.apellidos, e.rol, d.nombre departamento, s.nombre subdepartamento
            FROM empleados e
            LEFT JOIN departamentos d ON d.id=e.departamento_id
            LEFT JOIN subdepartamentos s ON s.id=e.subdepartamento_id
            WHERE e.empresa_id=? AND e.centro_id=?
              AND lower(COALESCE(e.rol,'') || ' ' || COALESCE(s.nombre,'')) LIKE '%anim%'
              AND lower(COALESCE(d.nombre,'')) LIKE '%bares%'
            """,
            (empresa_id, centro_id),
        ).fetchall()
        if rows:
            add(issues, "estructura", "alta", "animacion_en_bares", "Animacion depende de bares/restaurantes", "Animacion debe ser departamento propio o experiencia, no bares/restaurantes.", [dict(r) for r in rows])
        rows = conn.execute(
            """
            SELECT e.id, e.nombre, e.apellidos, e.rol, d.nombre departamento, s.nombre subdepartamento
            FROM empleados e
            LEFT JOIN departamentos d ON d.id=e.departamento_id
            LEFT JOIN subdepartamentos s ON s.id=e.subdepartamento_id
            WHERE e.empresa_id=? AND e.centro_id=?
              AND lower(COALESCE(d.nombre,'')) LIKE '%bares%'
              AND (
                lower(COALESCE(e.rol,'')) LIKE '%cocin%' OR
                lower(COALESCE(e.rol,'')) LIKE '%desay%' OR
                lower(COALESCE(e.rol,'')) LIKE '%partida%' OR
                lower(COALESCE(e.rol,'')) LIKE '%plonge%' OR
                lower(COALESCE(e.rol,'')) LIKE '%socor%'
              )
            """,
            (empresa_id, centro_id),
        ).fetchall()
        if rows:
            add(issues, "estructura", "alta", "rrhh_bares_mal_ubicado", "Personal no sala/bar dentro de bares/restaurantes", "Cocina, desayunos, plonge y socorristas no deben depender de bares/restaurantes.", [dict(r) for r in rows])
    if table_exists(conn, "subdepartamentos"):
        rows = conn.execute(
            """
            SELECT s.id, s.nombre, d.nombre departamento
            FROM subdepartamentos s
            LEFT JOIN departamentos d ON d.id=s.departamento_id
            WHERE s.empresa_id=? AND s.centro_id=?
              AND lower(s.nombre) LIKE '%anim%'
              AND lower(COALESCE(d.nombre,'')) LIKE '%bares%'
            """,
            (empresa_id, centro_id),
        ).fetchall()
        if rows:
            add(issues, "estructura", "alta", "subdep_animacion_en_bares", "Subdepartamento Animacion mal ubicado", "El subdepartamento de animacion sigue colgado de bares/restaurantes.", [dict(r) for r in rows])
        rows = conn.execute(
            """
            SELECT s.id, s.nombre, d.nombre departamento
            FROM subdepartamentos s
            LEFT JOIN departamentos d ON d.id=s.departamento_id
            WHERE s.empresa_id=? AND s.centro_id=?
              AND lower(COALESCE(d.nombre,'')) LIKE '%bares%'
              AND (
                lower(s.nombre) LIKE '%desay%' OR
                lower(s.nombre) LIKE '%cocina%' OR
                lower(s.nombre) LIKE '%plonge%' OR
                (lower(s.nombre) LIKE '%piscina%' AND lower(s.nombre) NOT LIKE '%bar%')
              )
            """,
            (empresa_id, centro_id),
        ).fetchall()
        if rows:
            add(issues, "estructura", "alta", "subdep_bares_mal_ubicado", "Subdepartamentos impropios bajo bares/restaurantes", "Bares/restaurantes debe quedarse para bar, buffet y sala.", [dict(r) for r in rows])


def audit_buffet(conn: sqlite3.Connection, issues: list[dict]) -> None:
    if table_exists(conn, "lineas_produccion"):
        rows = conn.execute(
            """
            SELECT lp.id, r.nombre, lp.estacion_buffet
            FROM lineas_produccion lp
            JOIN recetas r ON r.id=lp.receta_id
            WHERE (
                lower(r.nombre) LIKE '%napolit%' OR lower(r.nombre) LIKE '%croissant%' OR
                lower(r.nombre) LIKE '%queso%' OR lower(r.nombre) LIKE '%fiambre%'
            )
            AND (
                lower(COALESCE(lp.estacion_buffet,'')) LIKE '%caliente%' OR
                lower(COALESCE(lp.estacion_buffet,'')) IN ('buffet general', 'desayuno')
            )
            LIMIT 50
            """
        ).fetchall()
        if rows:
            add(issues, "buffet", "alta", "buffet_clasificacion_mala", "Platos mal clasificados en buffet", "Bolleria/quesos/fiambres aparecen en linea caliente o generica.", [dict(r) for r in rows])


def audit_production(conn: sqlite3.Connection, corrida_id: int, issues: list[dict]) -> None:
    if not table_exists(conn, "simulador_produccion_resumen_diario"):
        add(issues, "produccion", "media", "sin_produccion_simulada", "Falta produccion operativa", "No hay resumen de produccion para la corrida actual.")
        return
    for row in conn.execute("SELECT * FROM simulador_produccion_resumen_diario WHERE corrida_id=?", (corrida_id,)):
        lineas = int(row["lineas"] or 0)
        appcc = int(row["controles_appcc"] or 0)
        ingredientes = int(row["ingredientes_reales"] or 0)
        if lineas > 0 and ingredientes == 0:
            add(issues, "produccion", "alta", "produccion_sin_ingredientes", "Produccion sin ingredientes reales", f"{row['fecha']}: hay lineas sin ingredientes reales.", dict(row))
        if lineas > 0 and appcc == 0:
            add(issues, "appcc", "alta", "produccion_sin_appcc", "Produccion sin controles APPCC", f"{row['fecha']}: hay produccion sin controles APPCC.", dict(row))


def audit_coverage(conn: sqlite3.Connection, corrida_id: int, issues: list[dict]) -> None:
    dias_kpi = int(scalar(conn, "SELECT COUNT(*) FROM simulador_vida_kpis_diarios WHERE corrida_id=?", (corrida_id,), 0))
    if dias_kpi <= 0:
        return
    coverage_tables = [
        ("produccion", "simulador_produccion_resumen_diario", "Produccion operativa"),
        ("cierre", "simulador_cierre_diario", "Cierre diario"),
        ("rrhh", "simulador_rrhh_control_diario", "Control RRHH/fichajes"),
    ]
    for area, table, titulo in coverage_tables:
        if not table_exists(conn, table):
            add(issues, area, "alta", "cobertura_inexistente", f"{titulo} sin datos", f"La corrida tiene {dias_kpi} dias KPI y no hay tabla {table}.")
            continue
        dias = int(scalar(conn, f"SELECT COUNT(DISTINCT fecha) FROM {table} WHERE corrida_id=?", (corrida_id,), 0))
        ratio = dias / max(dias_kpi, 1)
        if ratio < 0.25:
            add(issues, area, "alta", "cobertura_muy_baja", f"{titulo} cubre pocos dias", f"{titulo}: {dias} dias sobre {dias_kpi} dias KPI. Para prueba fuerte debe cubrir casi toda la campana.", {"dias_cubiertos": dias, "dias_kpi": dias_kpi, "ratio": round(ratio, 3)})
        elif ratio < 0.80:
            add(issues, area, "media", "cobertura_parcial", f"{titulo} parcial", f"{titulo}: {dias} dias sobre {dias_kpi}. Vale para prueba parcial, no para validar toda la campana.", {"dias_cubiertos": dias, "dias_kpi": dias_kpi, "ratio": round(ratio, 3)})

    if table_exists(conn, "reservas_recepcion"):
        reservas = int(scalar(conn, "SELECT COUNT(*) FROM reservas_recepcion WHERE codigo LIKE 'VIDA-H1-%'", default=0))
        if reservas < dias_kpi * 20:
            add(issues, "reservas", "media", "volumen_reservas_bajo", "Volumen de reservas bajo para una campana larga", f"{reservas} reservas para {dias_kpi} dias puede ser poco si queremos estresar PMS, recepcion, limpieza y cobros.", {"reservas": reservas, "dias_kpi": dias_kpi})


def audit_cierre(conn: sqlite3.Connection, corrida_id: int, issues: list[dict]) -> None:
    if not table_exists(conn, "simulador_cierre_diario"):
        add(issues, "cierre", "media", "sin_cierre_diario", "Falta cierre diario", "No hay cierre diario de la corrida.")
        return
    rows = conn.execute(
        """
        SELECT *
        FROM simulador_cierre_diario
        WHERE corrida_id=? AND (
          COALESCE(hora_corte,'')='' OR COALESCE(cierre_desde,'')='' OR COALESCE(cierre_hasta,'')=''
        )
        LIMIT 50
        """,
        (corrida_id,),
    ).fetchall()
    if rows:
        add(issues, "cierre", "alta", "cierre_sin_ventana", "Cierres sin ventana operativa", "El cierre debe registrar hora de corte y ventana real.", [dict(r) for r in rows])


def _texto_empleado_area(row: sqlite3.Row) -> str:
    partes = [
        row["departamento"] if "departamento" in row.keys() else "",
        row["subdepartamento"] if "subdepartamento" in row.keys() else "",
        row["rol"] if "rol" in row.keys() else "",
    ]
    return " ".join(str(p or "").lower() for p in partes)


def audit_prl_alimentaria(conn: sqlite3.Connection, empresa_id: int, centro_id: int, issues: list[dict]) -> None:
    if not table_exists(conn, "empleados"):
        return
    empleados = conn.execute(
        """
        SELECT e.id, e.nombre, e.apellidos, e.rol, e.carnet_manipulador,
               e.fecha_caducidad_carnet, e.prl_realizada, e.fecha_prl,
               d.nombre AS departamento, sd.nombre AS subdepartamento
        FROM empleados e
        LEFT JOIN departamentos d ON d.id=e.departamento_id
        LEFT JOIN subdepartamentos sd ON sd.id=e.subdepartamento_id
        WHERE e.empresa_id=? AND e.centro_id=? AND COALESCE(e.activo,1)=1
        """,
        (empresa_id, centro_id),
    ).fetchall()
    manipuladores = []
    sin_carnet = []
    prl_pendiente = []
    for row in empleados:
        texto = _texto_empleado_area(row)
        manipula = any(p in texto for p in ["cocina", "bar", "bares", "restaurante", "sala", "buffet", "plonge", "chef", "camarer"])
        mantenimiento = "mantenimiento" in texto or "tecnico" in texto
        pisos = "piso" in texto or "gobernanta" in texto or "limpieza" in texto
        recepcion = any(p in texto for p in ["recepcion", "reservas", "administracion", "night audit", "auditor"])
        animacion = any(p in texto for p in ["animacion", "animador", "eventos", "monitor", "miniclub"])
        if manipula:
            manipuladores.append(row)
            fecha = (row["fecha_caducidad_carnet"] or "").strip()
            carnet_ok = int(row["carnet_manipulador"] or 0) == 1 and bool(fecha)
            if not carnet_ok:
                sin_carnet.append(dict(row))
            else:
                try:
                    if str(fecha)[:10] < "2026-06-02":
                        sin_carnet.append(dict(row))
                except Exception:
                    sin_carnet.append(dict(row))
        if manipula or mantenimiento or pisos or recepcion or animacion:
            if int(row["prl_realizada"] or 0) != 1:
                prl_pendiente.append(dict(row))
    if manipuladores and sin_carnet:
        severidad = "critica" if len(sin_carnet) / max(len(manipuladores), 1) > 0.25 else "alta"
        add(
            issues,
            "prl_appcc",
            severidad,
            "manipuladores_sin_carnet",
            "Manipuladores sin carnet/formacion vigente",
            f"{len(sin_carnet)} de {len(manipuladores)} trabajadores que manipulan alimentos no tienen carnet o fecha vigente.",
            sin_carnet[:40],
        )
    if prl_pendiente:
        severidad = "alta" if len(prl_pendiente) > 10 else "media"
        add(
            issues,
            "prl",
            severidad,
            "prl_pendiente_empleados_operativos",
            "Empleados operativos sin PRL realizada",
            f"{len(prl_pendiente)} empleados de areas operativas no tienen PRL marcada como realizada.",
            prl_pendiente[:40],
        )
    if table_exists(conn, "prl_formacion_empleados"):
        pendientes = int(scalar(conn, "SELECT COUNT(*) FROM prl_formacion_empleados WHERE empresa_id=? AND centro_id=? AND estado='pendiente'", (empresa_id, centro_id), 0))
        total = int(scalar(conn, "SELECT COUNT(*) FROM prl_formacion_empleados WHERE empresa_id=? AND centro_id=?", (empresa_id, centro_id), 0))
        if total and pendientes / max(total, 1) > 0.80:
            add(
                issues,
                "prl",
                "alta",
                "formacion_prl_masiva_pendiente",
                "Formacion PRL asignada pero no realizada",
                f"{pendientes} de {total} asignaciones PRL siguen pendientes. La simulacion debe probar realizacion, firma o bloqueo operativo.",
                {"pendientes": pendientes, "total": total},
            )
    else:
        add(issues, "prl", "media", "sin_formacion_prl_asignada", "No hay asignaciones PRL", "La simulacion no esta probando trazabilidad de formacion PRL.")


def score_from_issues(issues: list[dict]) -> tuple[float, str]:
    weights = {"critica": 18, "alta": 9, "media": 4, "baja": 1}
    penalty = sum(weights.get(i["severidad"], 2) for i in issues)
    score = max(0.0, 100.0 - penalty)
    if score >= 90:
        lectura = "Simulacion muy fiable para probar operativa."
    elif score >= 75:
        lectura = "Simulacion util, con algunos puntos a corregir."
    elif score >= 55:
        lectura = "Simulacion aprovechable, pero no tomar decisiones sin revisar incidencias."
    else:
        lectura = "Simulacion poco fiable: primero corregir incidencias criticas y altas."
    return round(score, 1), lectura


def persist(conn: sqlite3.Connection, corrida_id: int, empresa_id: int, centro_id: int, issues: list[dict]) -> None:
    conn.execute("DELETE FROM simulador_calidad_datos WHERE corrida_id=? AND empresa_id=? AND centro_id=?", (corrida_id, empresa_id, centro_id))
    conn.execute("DELETE FROM simulador_calidad_resumen WHERE corrida_id=? AND empresa_id=? AND centro_id=?", (corrida_id, empresa_id, centro_id))
    for i in issues:
        conn.execute(
            """
            INSERT INTO simulador_calidad_datos
            (corrida_id, area, severidad, codigo, titulo, detalle, evidencia_json, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrida_id,
                i["area"],
                i["severidad"],
                i["codigo"],
                i["titulo"],
                i["detalle"],
                json.dumps(i["evidencia"], ensure_ascii=False),
                empresa_id,
                centro_id,
            ),
        )
    score, lectura = score_from_issues(issues)
    counts = {sev: sum(1 for i in issues if i["severidad"] == sev) for sev in ("critica", "alta", "media", "baja")}
    conn.execute(
        """
        INSERT INTO simulador_calidad_resumen
        (corrida_id, total, criticas, altas, medias, bajas, score_confianza, lectura, empresa_id, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (corrida_id, len(issues), counts["critica"], counts["alta"], counts["media"], counts["baja"], score, lectura, empresa_id, centro_id),
    )
    conn.commit()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        corrida_id = args.corrida_id or latest_corrida(conn)
        if not corrida_id:
            raise SystemExit("No hay corrida para auditar.")
        issues: list[dict] = []
        audit_months(conn, corrida_id, issues)
        audit_daily_kpis(conn, corrida_id, args.centro_id, issues)
        audit_structure(conn, args.empresa_id, args.centro_id, issues)
        audit_buffet(conn, issues)
        audit_production(conn, corrida_id, issues)
        audit_coverage(conn, corrida_id, issues)
        audit_cierre(conn, corrida_id, issues)
        audit_prl_alimentaria(conn, args.empresa_id, args.centro_id, issues)
        persist(conn, corrida_id, args.empresa_id, args.centro_id, issues)
        score, lectura = score_from_issues(issues)
        print({
            "corrida_id": corrida_id,
            "incidencias": len(issues),
            "score_confianza": score,
            "lectura": lectura,
        })
    finally:
        conn.close()


if __name__ == "__main__":
    main()
