from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import flash, redirect, render_template, request, session, url_for

from blueprints.auth.routes import login_required, _registrar_auditoria_seguridad
from database import get_database_path
from helpers.centros_helper import centro_activo_id
from . import demo_bp


ROOT = Path(__file__).resolve().parents[2]
DEMO_DB = ROOT / "simulacion_el_raco.db"
VIDA_DB = ROOT / "simulacion_hotel1_vida.db"
REAL_DB = ROOT / "pepe4.db"
DEMO_PORT = 5002
REPORT_PATH = ROOT / "publicaciones" / "el_raco" / "simulacion_desde_cero_resumen.json"
VIDA_REPORT_PATH = ROOT / "publicaciones" / "el_raco" / "simulacion_vida_hotel1_resumen.json"
LOG_DIR = ROOT / "publicaciones" / "el_raco"
LOG_PATH = LOG_DIR / "simulador_erp.log"
VIDA_LOG_PATH = LOG_DIR / "simulador_vida_hotel1.log"
SERVER_LOG_PATH = LOG_DIR / "servidor_demo_5002.log"


ROLES_DEMO = {
    "admin",
    "administrador",
    "direccion",
    "director",
    "gerente",
    "propietario",
    "contabilidad",
    "rrhh",
}


def _rol_actual():
    return (session.get("user_rol") or "").strip().lower()


def _puede_demo():
    return session.get("user_es_global") in (1, True, "1", "true", "True") or _rol_actual() in ROLES_DEMO


def _require_demo_admin(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _puede_demo():
            _registrar_auditoria_seguridad(
                "acceso_denegado",
                "denegado",
                modulo="demo",
                detalle={"endpoint": request.endpoint, "path": request.path},
            )
            flash("Solo direccion o administracion pueden gestionar la demo comercial.", "warning")
            return redirect(url_for("home"))
        return func(*args, **kwargs)

    return wrapper


def _port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


def _read_json(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _db_info(path):
    if not path.exists():
        return {"existe": False, "ruta": str(path)}
    stat = path.stat()
    return {
        "existe": True,
        "ruta": str(path),
        "tamano_mb": round(stat.st_size / (1024 * 1024), 2),
        "modificado": datetime.fromtimestamp(stat.st_mtime).strftime("%d/%m/%Y %H:%M"),
    }


def _simulador_estado():
    report = _read_json(REPORT_PATH)
    return {
        "db": _db_info(DEMO_DB),
        "report": report,
        "health": _demo_health(DEMO_DB, report),
        "server_running": _port_open(DEMO_PORT),
        "real_db_activa": Path(get_database_path()).resolve() == REAL_DB.resolve(),
        "demo_port": DEMO_PORT,
        "demo_url": f"http://127.0.0.1:{DEMO_PORT}",
        "login": {"email": "admin@elraco.local", "password": "admin"},
        "log_tail": _tail(LOG_PATH),
    }


def _demo_table_count(conn, table):
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            return 0
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
    except Exception:
        return 0


def _demo_table_exists(conn, table):
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None
    except Exception:
        return False


def _real_conn():
    conn = sqlite3.connect(get_database_path())
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_simulador_centro_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS simulador_whatif_centro (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            centro_id INTEGER NOT NULL,
            anio INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            ocupacion_delta_pct REAL DEFAULT 0,
            precio_delta_pct REAL DEFAULT 0,
            coste_fijo_delta_pct REAL DEFAULT 0,
            laboral_delta_pct REAL DEFAULT 0,
            ingresos_base REAL DEFAULT 0,
            gastos_base REAL DEFAULT 0,
            resultado_base REAL DEFAULT 0,
            ingresos_simulados REAL DEFAULT 0,
            gastos_simulados REAL DEFAULT 0,
            resultado_simulado REAL DEFAULT 0,
            lectura TEXT,
            creado_por INTEGER,
            creado_en DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_simulador_whatif_centro
        ON simulador_whatif_centro(empresa_id, centro_id, anio, creado_en)
    """)


def _presupuesto_base_centro(conn, empresa_id, centro_id, anio):
    presupuesto = conn.execute(
        """
        SELECT id
        FROM contabilidad_presupuestos
        WHERE empresa_id=? AND COALESCE(centro_id,0)=COALESCE(?,0) AND anio=?
        ORDER BY actualizado_en DESC, id DESC
        LIMIT 1
        """,
        (empresa_id, centro_id, anio),
    ).fetchone() if _demo_table_exists(conn, "contabilidad_presupuestos") else None
    if not presupuesto or not _demo_table_exists(conn, "contabilidad_presupuesto_lineas"):
        return {
            "presupuesto_id": None,
            "ingresos": 0.0,
            "gastos": 0.0,
            "resultado": 0.0,
            "costes_fijos": 0.0,
            "personal": 0.0,
            "otros_gastos": 0.0,
        }
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN tipo='ingreso' THEN importe ELSE 0 END),0) AS ingresos,
            COALESCE(SUM(CASE WHEN tipo='gasto' THEN importe ELSE 0 END),0) AS gastos,
            COALESCE(SUM(CASE WHEN bloque='Costes fijos' THEN importe ELSE 0 END),0) AS costes_fijos,
            COALESCE(SUM(CASE WHEN bloque='Personal' THEN importe ELSE 0 END),0) AS personal
        FROM contabilidad_presupuesto_lineas
        WHERE presupuesto_id=?
        """,
        (presupuesto["id"],),
    ).fetchone()
    ingresos = float(row["ingresos"] or 0)
    gastos = float(row["gastos"] or 0)
    costes_fijos = float(row["costes_fijos"] or 0)
    personal = float(row["personal"] or 0)
    return {
        "presupuesto_id": presupuesto["id"],
        "ingresos": ingresos,
        "gastos": gastos,
        "resultado": ingresos - gastos,
        "costes_fijos": costes_fijos,
        "personal": personal,
        "otros_gastos": max(gastos - costes_fijos - personal, 0.0),
    }


def _presupuesto_mensual_centro(conn, empresa_id, centro_id, anio):
    if not _demo_table_exists(conn, "contabilidad_presupuestos") or not _demo_table_exists(conn, "contabilidad_presupuesto_lineas"):
        return []
    presupuesto = conn.execute(
        """
        SELECT id
        FROM contabilidad_presupuestos
        WHERE empresa_id=? AND COALESCE(centro_id,0)=COALESCE(?,0) AND anio=?
        ORDER BY actualizado_en DESC, id DESC
        LIMIT 1
        """,
        (empresa_id, centro_id, anio),
    ).fetchone()
    if not presupuesto:
        return []
    rows = conn.execute(
        """
        SELECT mes,
               COALESCE(SUM(CASE WHEN tipo='ingreso' THEN importe ELSE 0 END),0) AS ingresos,
               COALESCE(SUM(CASE WHEN tipo='gasto' THEN importe ELSE 0 END),0) AS gastos,
               COALESCE(SUM(CASE WHEN tipo='ingreso' AND concepto LIKE '%IMSERSO%' THEN importe ELSE 0 END),0) AS ingresos_imserso,
               COALESCE(SUM(CASE WHEN tipo='ingreso' AND concepto LIKE '%cliente normal%' THEN importe ELSE 0 END),0) AS ingresos_normal,
               COALESCE(SUM(CASE WHEN tipo='gasto' AND bloque='Personal' THEN importe ELSE 0 END),0) AS personal,
               COALESCE(SUM(CASE WHEN tipo='gasto' AND bloque='Costes fijos' THEN importe ELSE 0 END),0) AS fijos
        FROM contabilidad_presupuesto_lineas
        WHERE presupuesto_id=?
        GROUP BY mes
        ORDER BY mes
        """,
        (presupuesto["id"],),
    ).fetchall()
    nombres = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    resumen = []
    for row in rows:
        ingresos = float(row["ingresos"] or 0)
        gastos = float(row["gastos"] or 0)
        resumen.append({
            "mes": int(row["mes"] or 0),
            "nombre": nombres[int(row["mes"] or 1) - 1],
            "ingresos": ingresos,
            "gastos": gastos,
            "resultado": ingresos - gastos,
            "ingresos_imserso": float(row["ingresos_imserso"] or 0),
            "ingresos_normal": float(row["ingresos_normal"] or 0),
            "personal": float(row["personal"] or 0),
            "fijos": float(row["fijos"] or 0),
        })
    return resumen


def _simulador_centros_estado():
    conn = _real_conn()
    _ensure_simulador_centro_schema(conn)
    configs = []
    if _demo_table_exists(conn, "simulador_config_centros"):
        empresa_activa = _to_int(session.get("user_empresa_id") or session.get("empresa_id"), 1) or 1
        centro_activo = centro_activo_id()
        filtros = ["sc.empresa_id = ?"]
        params = [empresa_activa]
        if centro_activo:
            filtros.append("sc.centro_id = ?")
            params.append(centro_activo)
        filtro_empresa = "WHERE " + " AND ".join(filtros)
        rows = conn.execute(
            f"""
            SELECT sc.*, e.nombre AS empresa_nombre, c.nombre AS centro_nombre,
                   COUNT(DISTINCT se.id) AS escenarios,
                   COUNT(DISTINCT cg.id) AS corridas,
                   COUNT(DISTINCT rg.id) AS resultados
            FROM simulador_config_centros sc
            LEFT JOIN empresas e ON e.id=sc.empresa_id
            LEFT JOIN centros_trabajo c ON c.id=sc.centro_id
            LEFT JOIN simulador_escenarios se ON se.config_id=sc.id
            LEFT JOIN simulador_corridas_generales cg ON cg.config_id=sc.id
            LEFT JOIN simulador_resultados_generales rg ON rg.empresa_id=sc.empresa_id AND rg.centro_id=sc.centro_id
            {filtro_empresa}
            GROUP BY sc.id
            ORDER BY e.nombre, c.nombre
            """,
            params,
        ).fetchall()
        configs = [dict(row) for row in rows]
    for config in configs:
        try:
            config["parametros"] = json.loads(config.get("parametros_json") or "{}")
        except Exception:
            config["parametros"] = {}
        config["base"] = _presupuesto_base_centro(conn, config["empresa_id"], config["centro_id"], 2027)
        config["mensual"] = _presupuesto_mensual_centro(conn, config["empresa_id"], config["centro_id"], 2027)
        config["escenarios_rows"] = conn.execute(
            """
            SELECT *
            FROM simulador_escenarios
            WHERE config_id=?
            ORDER BY prioridad DESC, id
            """,
            (config["id"],),
        ).fetchall() if _demo_table_exists(conn, "simulador_escenarios") else []
        config["ultimos_whatif"] = conn.execute(
            """
            SELECT *
            FROM simulador_whatif_centro
            WHERE empresa_id=? AND centro_id=?
            ORDER BY creado_en DESC, id DESC
            LIMIT 5
            """,
            (config["empresa_id"], config["centro_id"]),
        ).fetchall()
    conn.close()
    return {"configs": configs}


def _demo_health(db_path, report=None):
    checks = [
        ("Usuarios y roles", ("usuarios", "roles"), "Login y permisos base"),
        ("Estructura hotel", ("departamentos", "subdepartamentos", "habitaciones_recepcion"), "Departamentos, subdepartamentos y habitaciones"),
        ("Reservas y viajeros", ("reservas_recepcion", "viajeros_recepcion"), "Ocupacion y registro de viajeros"),
        ("Productos y stock", ("productos", "proveedores", "almacenes", "lotes"), "Catalogo, proveedores, almacenes y lotes"),
        ("Compras", ("pedidos", "recepciones"), "Pedidos y recepcion de mercancia"),
        ("Produccion", ("menus_diarios", "partes_produccion", "lineas_produccion"), "Menus, partes y recetas planificadas"),
        ("Mantenimiento", ("equipos_mantenimiento", "ordenes_trabajo"), "Equipos y ordenes de trabajo"),
        ("TPV", ("puntos_venta", "tpv_terminales"), "Puntos de venta y terminales"),
        ("Contabilidad", ("contabilidad_documentos",), "Documentos economicos"),
    ]
    if not db_path.exists():
        return {"ok": False, "items": [], "smoke_ok": 0, "smoke_total": 0}
    conn = sqlite3.connect(db_path)
    try:
        items = []
        for titulo, tablas, detalle in checks:
            conteos = {tabla: _demo_table_count(conn, tabla) for tabla in tablas}
            ok = all(valor > 0 for valor in conteos.values())
            items.append({
                "titulo": titulo,
                "detalle": detalle,
                "ok": ok,
                "conteos": conteos,
            })
    finally:
        conn.close()
    smoke = (report or {}).get("smoke") or []
    smoke_total = len(smoke)
    smoke_ok = sum(1 for row in smoke if int(row.get("status") or 0) < 400)
    return {
        "ok": all(item["ok"] for item in items) and (smoke_total == 0 or smoke_ok == smoke_total),
        "items": items,
        "smoke_ok": smoke_ok,
        "smoke_total": smoke_total,
    }


def _tail(path, lines=12):
    if not path.exists():
        return []
    try:
        data = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return data[-lines:]
    except Exception:
        return []


def _fetch_all_dict(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _fetch_one_dict(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def _count_sql(conn, sql, params=()):
    try:
        row = conn.execute(sql, params).fetchone()
        return int((row[0] if row else 0) or 0)
    except Exception:
        return 0


def _origen_datos_estado(conn):
    reglas = [
        {
            "modulo": "Reservas",
            "tabla": "reservas_recepcion",
            "total": "1=1",
            "simulado": "codigo LIKE 'VIDA-H1-%' OR codigo LIKE 'LLENO-H1-%' OR codigo LIKE 'OB-H1-%'",
            "importado": "COALESCE(origen,'') IN ('booking','channel_manager','ota','importado')",
            "real": "NOT (codigo LIKE 'VIDA-H1-%' OR codigo LIKE 'LLENO-H1-%' OR codigo LIKE 'OB-H1-%') AND COALESCE(origen,'') NOT IN ('booking','channel_manager','ota','importado')",
            "detalle": "PMS: reservas reales/base frente a vida operativa simulada.",
        },
        {
            "modulo": "Compras",
            "tabla": "pedidos",
            "total": "1=1",
            "simulado": "numero_pedido LIKE 'COMPRA-H1-%' OR numero_pedido LIKE 'MISE-H1-%' OR numero_pedido LIKE 'PED-VIDA-H1-%' OR COALESCE(origen_documento,'') LIKE 'simulador%'",
            "importado": "COALESCE(origen_documento,'') LIKE '%ocr%' OR COALESCE(origen_documento,'') LIKE '%import%'",
            "real": "NOT (numero_pedido LIKE 'COMPRA-H1-%' OR numero_pedido LIKE 'MISE-H1-%' OR numero_pedido LIKE 'PED-VIDA-H1-%' OR COALESCE(origen_documento,'') LIKE 'simulador%' OR COALESCE(origen_documento,'') LIKE '%ocr%' OR COALESCE(origen_documento,'') LIKE '%import%')",
            "detalle": "Pedidos normales separados de compras generadas por ocupacion/mise.",
        },
        {
            "modulo": "Recepciones",
            "tabla": "recepciones",
            "total": "1=1",
            "simulado": "COALESCE(pedido_id,0) IN (SELECT id FROM pedidos WHERE numero_pedido LIKE 'COMPRA-H1-%' OR numero_pedido LIKE 'MISE-H1-%' OR numero_pedido LIKE 'PED-VIDA-H1-%' OR COALESCE(origen_documento,'') LIKE 'simulador%')",
            "importado": "COALESCE(tipo_recepcion,'')='directa' AND COALESCE(documento_adjunto_id,0)>0",
            "real": "COALESCE(pedido_id,0) NOT IN (SELECT id FROM pedidos WHERE numero_pedido LIKE 'COMPRA-H1-%' OR numero_pedido LIKE 'MISE-H1-%' OR numero_pedido LIKE 'PED-VIDA-H1-%' OR COALESCE(origen_documento,'') LIKE 'simulador%') AND NOT (COALESCE(tipo_recepcion,'')='directa' AND COALESCE(documento_adjunto_id,0)>0)",
            "detalle": "Entradas de mercancia por pedido simulado frente a recepcion/base.",
        },
        {
            "modulo": "Menus",
            "tabla": "menus_diarios",
            "total": "1=1",
            "simulado": "COALESCE(modo_servicio,'')='simulador_produccion'",
            "importado": "fecha LIKE 'SEMANA %' OR COALESCE(observaciones,'') LIKE '%Importado%'",
            "real": "NOT (COALESCE(modo_servicio,'')='simulador_produccion' OR fecha LIKE 'SEMANA %' OR COALESCE(observaciones,'') LIKE '%Importado%')",
            "detalle": "Menus simulados, ciclos importados y menus manuales.",
        },
        {
            "modulo": "Produccion",
            "tabla": "partes_produccion",
            "total": "1=1",
            "simulado": "COALESCE(observaciones,'') LIKE '%simulador%' OR COALESCE(regla_calculo,'') LIKE '%pax previstos%' OR centro_id=3",
            "importado": "COALESCE(observaciones,'') LIKE '%Desde planificacion%' OR COALESCE(observaciones,'') LIKE '%menu semanal%'",
            "real": "NOT (COALESCE(observaciones,'') LIKE '%simulador%' OR COALESCE(regla_calculo,'') LIKE '%pax previstos%' OR centro_id=3 OR COALESCE(observaciones,'') LIKE '%Desde planificacion%' OR COALESCE(observaciones,'') LIKE '%menu semanal%')",
            "detalle": "Partes reales/base frente a partes creados por el simulador Hotel 1.",
        },
        {
            "modulo": "RRHH turnos",
            "tabla": "turnos_rrhh",
            "total": "1=1",
            "simulado": "COALESCE(origen,'') LIKE 'simulador%'",
            "importado": "COALESCE(origen,'') LIKE '%import%'",
            "real": "COALESCE(origen,'') NOT LIKE 'simulador%' AND COALESCE(origen,'') NOT LIKE '%import%'",
            "detalle": "Cuadrantes simulados separados de turnos manuales/base.",
        },
        {
            "modulo": "Fichajes",
            "tabla": "fichajes",
            "total": "1=1",
            "simulado": "COALESCE(origen,'') LIKE 'simulador%'",
            "importado": "COALESCE(origen,'') LIKE '%import%'",
            "real": "COALESCE(origen,'') NOT LIKE 'simulador%' AND COALESCE(origen,'') NOT LIKE '%import%'",
            "detalle": "Fichajes simulados frente a fichajes manuales/base.",
        },
        {
            "modulo": "Quejas",
            "tabla": "experiencia_quejas",
            "total": "1=1",
            "simulado": "COALESCE(origen_modulo,'') LIKE 'simulador%'",
            "importado": "0=1",
            "real": "COALESCE(origen_modulo,'') NOT LIKE 'simulador%'",
            "detalle": "Experiencia cliente de simulacion frente a incidencias reales/base.",
        },
        {
            "modulo": "Mantenimiento",
            "tabla": "ordenes_trabajo",
            "total": "1=1",
            "simulado": "numero_orden LIKE 'OT-VIDA-H1-%' OR COALESCE(origen_modulo,'') LIKE 'simulador%'",
            "importado": "0=1",
            "real": "NOT (numero_orden LIKE 'OT-VIDA-H1-%' OR COALESCE(origen_modulo,'') LIKE 'simulador%')",
            "detalle": "Ordenes de prueba separadas de mantenimiento manual/base.",
        },
        {
            "modulo": "TPV",
            "tabla": "tpv_comandas",
            "total": "1=1",
            "simulado": "numero LIKE 'CMD-H1-%'",
            "importado": "0=1",
            "real": "numero NOT LIKE 'CMD-H1-%'",
            "detalle": "Comandas de simulador frente a comandas base/manuales.",
        },
        {
            "modulo": "Lotes",
            "tabla": "lotes",
            "total": "1=1",
            "simulado": "COALESCE(documento_origen,'')='simulador_produccion' OR COALESCE(responsable_recepcion,'') LIKE 'Simulador%' OR COALESCE(origen_lote,'') LIKE 'simulador%'",
            "importado": "COALESCE(documento_origen,'') LIKE '%Pedido%'",
            "real": "NOT (COALESCE(documento_origen,'')='simulador_produccion' OR COALESCE(responsable_recepcion,'') LIKE 'Simulador%' OR COALESCE(origen_lote,'') LIKE 'simulador%' OR COALESCE(documento_origen,'') LIKE '%Pedido%')",
            "detalle": "Trazabilidad de camara simulada frente a lotes de recepcion/base.",
        },
    ]
    filas = []
    for regla in reglas:
        table = regla["tabla"]
        if not _demo_table_exists(conn, table):
            continue
        total = _count_sql(conn, f"SELECT COUNT(*) FROM {table} WHERE {regla['total']}")
        sim_cond = f"({regla['simulado']})"
        import_cond = f"({regla['importado']}) AND NOT {sim_cond}"
        real_cond = f"({regla['real']}) AND NOT {sim_cond} AND NOT ({regla['importado']})"
        simulado = _count_sql(conn, f"SELECT COUNT(*) FROM {table} WHERE {sim_cond}")
        importado = _count_sql(conn, f"SELECT COUNT(*) FROM {table} WHERE {import_cond}")
        real = _count_sql(conn, f"SELECT COUNT(*) FROM {table} WHERE {real_cond}")
        indeterminado = max(total - simulado - importado - real, 0)
        filas.append({
            "modulo": regla["modulo"],
            "tabla": table,
            "total": total,
            "simulado": simulado,
            "real": real,
            "importado": importado,
            "indeterminado": indeterminado,
            "pct_simulado": round((simulado / total * 100), 1) if total else 0,
            "detalle": regla["detalle"],
        })
    resumen = {
        "total": sum(f["total"] for f in filas),
        "simulado": sum(f["simulado"] for f in filas),
        "real": sum(f["real"] for f in filas),
        "importado": sum(f["importado"] for f in filas),
        "indeterminado": sum(f["indeterminado"] for f in filas),
    }
    resumen["pct_simulado"] = round((resumen["simulado"] / resumen["total"] * 100), 1) if resumen["total"] else 0
    return {"resumen": resumen, "filas": filas}


def _vida_control_estado():
    report = _read_json(VIDA_REPORT_PATH)
    estado = {
        "db": _db_info(VIDA_DB),
        "report": report,
        "server_running": _port_open(DEMO_PORT),
        "demo_url": f"http://127.0.0.1:{DEMO_PORT}",
        "log_tail": _tail(VIDA_LOG_PATH),
        "corrida": None,
        "decisiones": [],
        "eventos_modulo": [],
        "alertas": [],
        "metricas": {},
        "radar_modulos": {"resumen": {}, "filas": []},
        "origen_datos": {"resumen": {}, "filas": []},
        "top_etiquetas": [],
        "top_inventario": [],
        "peores_dias": [],
        "contexto_externo": [],
        "diagnosticos_dia": [],
        "plan_accion": [],
        "calidad_resumen": {},
        "calidad_incidencias": [],
        "prueba_fuego_resumen": {},
        "prueba_fuego_fallos": [],
        "trazabilidad_resumen": {},
        "trazabilidad_alertas": [],
        "veredicto_brutal": {},
        "overbooking": [],
        "overbooking_prevencion": [],
        "overbooking_resoluciones": [],
        "rrhh_control": [],
        "rrhh_resumen": {},
        "rrhh_escenarios": [],
        "rrhh_vivienda_resumen": {},
        "rrhh_vivienda_meses": [],
        "prl_acciones_resumen": {},
        "prl_acciones": [],
        "seguridad_alimentaria_acciones_resumen": {},
        "seguridad_alimentaria_acciones": [],
        "jornada_resumen": {},
        "jornada_critica": [],
        "brechas_stock_resumen": {},
        "brechas_stock": [],
        "plan_compras_mise_resumen": {},
        "plan_compras_mise_semanas": [],
        "plan_compras_mise_proveedores": [],
        "tesoreria_mise_resumen": {},
        "tesoreria_mise_meses": [],
        "escenarios_mise_resumen": {},
        "escenarios_mise_recomendados": [],
        "escenarios_mise_detalle": [],
        "rescate_criticos_resumen": {},
        "rescate_criticos": [],
        "guardarrailes_resumen": {},
        "guardarrailes": [],
        "decision_financiera_resumen": {},
        "decision_financiera_meses": [],
        "stoploss_semanal_resumen": {},
        "stoploss_semanal": [],
        "escandallos_resumen": {},
        "escandallos_servicios": [],
        "escandallos_sobrantes_destino": [],
        "escandallos_top_coste": [],
        "escandallos_top_merma": [],
        "escandallos_riesgo": [],
        "recomendaciones_cocina_resumen": {},
        "recomendaciones_cocina": [],
        "impacto_cocina_resumen": {},
        "impacto_cocina_meses": [],
        "plan_integral_resumen": {},
        "plan_integral_meses": [],
        "auditoria_margen_resumen": {},
        "auditoria_margen_alertas": [],
        "resumen_ejecutivo_margen_resumen": {},
        "resumen_ejecutivo_margen": [],
        "cierre_resumen": {},
        "cierre_diario": [],
        "finanzas_diagnostico": [],
        "finanzas_acciones": [],
    }
    if not VIDA_DB.exists():
        return estado
    conn = sqlite3.connect(VIDA_DB)
    conn.row_factory = sqlite3.Row
    try:
        estado["origen_datos"] = _origen_datos_estado(conn)
        corrida = _fetch_one_dict(
            conn,
            """
            SELECT *
            FROM simulador_vida_corridas
            ORDER BY id DESC
            LIMIT 1
            """,
        )
        if not corrida:
            return estado
        corrida_id = corrida["id"]
        estado["corrida"] = corrida
        estado["radar_modulos"] = _radar_modulos_operativos(conn, corrida_id)
        estado["decisiones"] = _fetch_all_dict(
            conn,
            """
            SELECT *
            FROM simulador_vida_decisiones
            WHERE corrida_id=?
            ORDER BY CASE prioridad
                WHEN 'critica' THEN 1
                WHEN 'alta' THEN 2
                WHEN 'media' THEN 3
                ELSE 4
            END, modulo, id
            """,
            (corrida_id,),
        )
        estado["eventos_modulo"] = _fetch_all_dict(
            conn,
            """
            SELECT modulo, COUNT(*) total,
                   SUM(CASE WHEN severidad IN ('critica','alta') THEN 1 ELSE 0 END) AS altas,
                   SUM(CASE WHEN severidad='media' THEN 1 ELSE 0 END) AS medias
            FROM simulador_vida_eventos
            WHERE corrida_id=?
            GROUP BY modulo
            ORDER BY altas DESC, total DESC
            """,
            (corrida_id,),
        )
        estado["alertas"] = _fetch_all_dict(
            conn,
            """
            SELECT fecha_simulada, modulo, tipo, severidad, descripcion, tabla_ref, registro_id
            FROM simulador_vida_eventos
            WHERE corrida_id=? AND severidad IN ('critica','alta')
            ORDER BY fecha_simulada DESC, id DESC
            LIMIT 80
            """,
            (corrida_id,),
        )
        estado["top_etiquetas"] = _fetch_all_dict(
            conn,
            """
            SELECT nombre_plato, COUNT(*) total,
                   SUM(CASE WHEN incidencia LIKE '%sin alergenos%' THEN 1 ELSE 0 END) sin_alergenos,
                   SUM(CASE WHEN incidencia LIKE '%traduccion%' THEN 1 ELSE 0 END) traduccion
            FROM simulador_buffet_etiquetas
            WHERE corrida_id=? AND estado!='lista'
            GROUP BY receta_id, nombre_plato
            ORDER BY total DESC
            LIMIT 10
            """,
            (corrida_id,),
        )
        estado["top_inventario"] = _fetch_all_dict(
            conn,
            """
            SELECT ia.nombre, SUM(ABS(c.desviacion)) desviacion_total, COUNT(*) conteos
            FROM simulador_inventario_conteos c
            LEFT JOIN inventario_articulos ia ON ia.id=c.articulo_id
            WHERE c.corrida_id=?
            GROUP BY c.articulo_id
            ORDER BY desviacion_total DESC
            LIMIT 10
            """,
            (corrida_id,),
        )
        estado["peores_dias"] = _fetch_all_dict(
            conn,
            """
            SELECT fecha, habitaciones_ocupadas, pax, ingresos_habitaciones, ingresos_tpv,
                   coste_laboral_estimado, coste_compras_estimado, margen_estimado,
                   quejas, ordenes_mantenimiento, no_conformidades_appcc, alertas_altas,
                   riesgo_operativo, lectura
            FROM simulador_vida_kpis_diarios
            WHERE corrida_id=?
            ORDER BY riesgo_operativo DESC, margen_estimado ASC
            LIMIT 15
            """,
            (corrida_id,),
        )
        estado["contexto_externo"] = _fetch_all_dict(
            conn,
            """
            SELECT fecha_inicio, fecha_fin, tipo, titulo, intensidad, descripcion
            FROM simulador_contexto_externo
            WHERE corrida_id=?
            ORDER BY fecha_inicio
            """,
            (corrida_id,),
        )
        estado["diagnosticos_dia"] = _fetch_all_dict(
            conn,
            """
            SELECT fecha, nivel, titulo, diagnostico, causas_json, acciones_json,
                   margen_estimado, riesgo_operativo
            FROM simulador_vida_diagnosticos_dia
            WHERE corrida_id=?
            ORDER BY CASE nivel WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END,
                     margen_estimado ASC, riesgo_operativo DESC
            LIMIT 20
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_vida_diagnosticos_dia") else []
        estado["plan_accion"] = _fetch_all_dict(
            conn,
            """
            SELECT area, prioridad, titulo, problema, accion_recomendada,
                   indicador_objetivo, impacto_estimado, estado
            FROM simulador_vida_plan_accion
            WHERE corrida_id=?
            ORDER BY CASE prioridad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END,
                     area, id
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_vida_plan_accion") else []
        estado["calidad_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT *
            FROM simulador_calidad_resumen
            WHERE corrida_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_calidad_resumen") else {}
        estado["calidad_incidencias"] = _fetch_all_dict(
            conn,
            """
            SELECT area, severidad, codigo, titulo, detalle, evidencia_json, estado
            FROM simulador_calidad_datos
            WHERE corrida_id=?
            ORDER BY CASE severidad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 WHEN 'media' THEN 3 ELSE 4 END,
                     area, id
            LIMIT 30
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_calidad_datos") else []
        estado["prueba_fuego_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT *
            FROM simulador_prueba_fuego_resumen
            WHERE corrida_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_prueba_fuego_resumen") else {}
        estado["prueba_fuego_fallos"] = _fetch_all_dict(
            conn,
            """
            SELECT modulo, ruta, status_code, tiempo_ms, error
            FROM simulador_prueba_fuego_rutas
            WHERE corrida_id=? AND ok=0
            ORDER BY modulo, ruta
            LIMIT 30
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_prueba_fuego_rutas") else []
        estado["trazabilidad_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT *
            FROM simulador_trazabilidad_resumen
            WHERE corrida_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_trazabilidad_resumen") else {}
        estado["trazabilidad_alertas"] = _fetch_all_dict(
            conn,
            """
            SELECT area, severidad, codigo, titulo, detalle, evidencia_json, estado
            FROM simulador_trazabilidad_operativa
            WHERE corrida_id=?
            ORDER BY CASE severidad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 WHEN 'media' THEN 3 ELSE 4 END,
                     area, id
            LIMIT 30
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_trazabilidad_operativa") else []
        estado["veredicto_brutal"] = _fetch_one_dict(
            conn,
            """
            SELECT *
            FROM simulador_veredicto_brutal
            WHERE corrida_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_veredicto_brutal") else {}
        if estado["veredicto_brutal"]:
            for field in ("fortalezas_json", "roturas_json", "acciones_json", "metricas_json"):
                try:
                    estado["veredicto_brutal"][field.replace("_json", "")] = json.loads(estado["veredicto_brutal"].get(field) or "[]")
                except Exception:
                    estado["veredicto_brutal"][field.replace("_json", "")] = [] if field != "metricas_json" else {}
        estado["overbooking"] = _fetch_all_dict(
            conn,
            """
            SELECT ob.*, r.codigo AS reserva_codigo, rc.codigo AS conflicto_codigo,
                   h.numero AS habitacion_numero
            FROM simulador_overbooking_casos ob
            LEFT JOIN reservas_recepcion r ON r.id=ob.reserva_id
            LEFT JOIN reservas_recepcion rc ON rc.id=ob.reserva_conflicto_id
            LEFT JOIN habitaciones_recepcion h ON h.id=ob.habitacion_id
            WHERE ob.corrida_id=?
            ORDER BY CASE ob.severidad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END,
                     ob.fecha_entrada, ob.id
            LIMIT 40
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_overbooking_casos") else []
        estado["overbooking_prevencion"] = _fetch_all_dict(
            conn,
            """
            SELECT fecha, canal_codigo, canal_tipo, tipo_habitacion_id,
                   habitaciones_totales, bloqueadas, reservas_activas, cupo_canal,
                   colchon_habitaciones, habitaciones_disponibles_reales,
                   venta_maxima_recomendada, riesgo, accion_recomendada
            FROM simulador_overbooking_prevencion
            WHERE corrida_id=? AND riesgo IN ('critico','alto')
            ORDER BY CASE riesgo WHEN 'critico' THEN 1 WHEN 'alto' THEN 2 ELSE 3 END,
                     fecha, canal_codigo
            LIMIT 40
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_overbooking_prevencion") else []
        estado["overbooking_resoluciones"] = _fetch_all_dict(
            conn,
            """
            SELECT res.*, ob.tipo, ob.fecha_entrada, ob.fecha_salida, ob.severidad,
                   r.codigo AS reserva_codigo, rc.codigo AS conflicto_codigo,
                   ho.numero AS habitacion_origen, hd.numero AS habitacion_destino,
                   cd.nombre AS centro_destino, ha.nombre AS hotel_apoyo
            FROM simulador_overbooking_resoluciones res
            JOIN simulador_overbooking_casos ob ON ob.id=res.caso_id
            LEFT JOIN reservas_recepcion r ON r.id=res.reserva_id
            LEFT JOIN reservas_recepcion rc ON rc.id=res.reserva_conflicto_id
            LEFT JOIN habitaciones_recepcion ho ON ho.id=res.habitacion_origen_id
            LEFT JOIN habitaciones_recepcion hd ON hd.id=res.habitacion_destino_id
            LEFT JOIN centros_trabajo cd ON cd.id=res.centro_destino_id
            LEFT JOIN simulador_hoteles_apoyo ha ON ha.id=res.hotel_apoyo_id
            WHERE res.corrida_id=?
            ORDER BY CASE res.prioridad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END,
                     ob.fecha_entrada, res.id
            LIMIT 40
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_overbooking_resoluciones") else []
        estado["rrhh_control"] = _fetch_all_dict(
            conn,
            """
            SELECT fecha, nivel, turnos, empleados_con_turno, fichajes, empleados_fichados,
                   sin_fichar, sin_entrada, sin_salida, fichaje_sin_turno,
                   pausa_sin_reanudar, doble_turno, extras_pendientes,
                   minutos_extra_pendientes, salidas_anticipadas,
                   coste_riesgo_estimado, lectura
            FROM simulador_rrhh_control_diario
            WHERE corrida_id=?
            ORDER BY CASE nivel WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 WHEN 'media' THEN 3 ELSE 4 END,
                     coste_riesgo_estimado DESC, fecha
            LIMIT 20
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_rrhh_control_diario") else []
        estado["rrhh_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) dias,
                   SUM(CASE WHEN nivel IN ('critica','alta') THEN 1 ELSE 0 END) dias_tension,
                   SUM(sin_fichar) sin_fichar,
                   SUM(fichaje_sin_turno) fichaje_sin_turno,
                   SUM(extras_pendientes) extras_pendientes,
                   ROUND(SUM(coste_riesgo_estimado), 2) coste_riesgo
            FROM simulador_rrhh_control_diario
            WHERE corrida_id=?
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_rrhh_control_diario") else {}
        estado["rrhh_escenarios"] = _fetch_all_dict(
            conn,
            """
            SELECT modo, dias, dias_criticos, dias_altos, sin_fichar,
                   fichaje_sin_turno, salidas_anticipadas,
                   coste_riesgo_estimado, lectura
            FROM simulador_rrhh_escenarios_resumen
            WHERE corrida_id=?
            ORDER BY CASE modo WHEN 'normal' THEN 1 WHEN 'tensionado' THEN 2 ELSE 3 END
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_rrhh_escenarios_resumen") else []
        estado["rrhh_vivienda_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT *
            FROM simulador_rrhh_vivienda_laboral_resumen
            WHERE corrida_id=? AND empresa_id=1 AND centro_id=3
            ORDER BY id DESC
            LIMIT 1
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_rrhh_vivienda_laboral_resumen") else {}
        estado["rrhh_vivienda_meses"] = _fetch_all_dict(
            conn,
            """
            SELECT mes, fuentes_externas, confianza_media, dias_rrhh, dias_tension,
                   sin_fichar, fichaje_sin_turno, salidas_anticipadas,
                   extras_pendientes, horas_extra_nomina, ausencias_horas,
                   coste_laboral, coste_riesgo, riesgo_pct, nivel, lectura,
                   acciones, datos_faltantes
            FROM simulador_rrhh_vivienda_laboral
            WHERE corrida_id=? AND empresa_id=1 AND centro_id=3
            ORDER BY CASE nivel WHEN 'rojo' THEN 1 WHEN 'ambar' THEN 2 ELSE 3 END,
                     riesgo_pct DESC, mes
            LIMIT 12
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_rrhh_vivienda_laboral") else []
        estado["prl_acciones_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) total,
                   SUM(CASE WHEN estado!='cerrada' THEN 1 ELSE 0 END) abiertas,
                   SUM(CASE WHEN prioridad='critica' AND estado!='cerrada' THEN 1 ELSE 0 END) criticas,
                   SUM(CASE WHEN prioridad='alta' AND estado!='cerrada' THEN 1 ELSE 0 END) altas
            FROM prl_acciones
            WHERE empresa_id=1 AND centro_id=3
              AND COALESCE(evidencia,'') LIKE 'simulador_calidad:%'
            """,
        ) if _demo_table_exists(conn, "prl_acciones") else {}
        estado["prl_acciones"] = _fetch_all_dict(
            conn,
            """
            SELECT a.prioridad, a.estado, a.titulo, a.descripcion, a.responsable,
                   a.fecha_limite, a.evidencia, r.codigo AS riesgo_codigo
            FROM prl_acciones a
            LEFT JOIN prl_riesgos r ON r.id=a.riesgo_id
            WHERE a.empresa_id=1 AND a.centro_id=3
              AND COALESCE(a.evidencia,'') LIKE 'simulador_calidad:%'
            ORDER BY CASE a.prioridad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END,
                     date(a.fecha_limite), a.id DESC
            LIMIT 12
            """,
        ) if _demo_table_exists(conn, "prl_acciones") else []
        estado["seguridad_alimentaria_acciones_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) total,
                   SUM(CASE WHEN estado!='cerrada' THEN 1 ELSE 0 END) abiertas,
                   SUM(CASE WHEN prioridad='critica' AND estado!='cerrada' THEN 1 ELSE 0 END) criticas,
                   SUM(CASE WHEN prioridad='alta' AND estado!='cerrada' THEN 1 ELSE 0 END) altas
            FROM seguridad_alimentaria_acciones
            WHERE empresa_id=1 AND centro_id=3
              AND COALESCE(evidencia,'') LIKE 'simulador_calidad:%'
            """,
        ) if _demo_table_exists(conn, "seguridad_alimentaria_acciones") else {}
        estado["seguridad_alimentaria_acciones"] = _fetch_all_dict(
            conn,
            """
            SELECT prioridad, estado, area, titulo, descripcion, responsable,
                   fecha_limite, origen, evidencia
            FROM seguridad_alimentaria_acciones
            WHERE empresa_id=1 AND centro_id=3
              AND COALESCE(evidencia,'') LIKE 'simulador_calidad:%'
            ORDER BY CASE prioridad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END,
                     date(fecha_limite), id DESC
            LIMIT 12
            """,
        ) if _demo_table_exists(conn, "seguridad_alimentaria_acciones") else []
        estado["jornada_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) filas,
                   COUNT(DISTINCT fecha) dias,
                   SUM(entrada) entrada,
                   SUM(completadas) completadas,
                   SUM(bloqueadas_stock) bloqueadas_stock,
                   SUM(bloqueadas_capacidad) bloqueadas_capacidad,
                   SUM(pendientes_final) pendientes_final,
                   SUM(CASE WHEN riesgo='critica' THEN 1 ELSE 0 END) criticas,
                   SUM(CASE WHEN riesgo='alta' THEN 1 ELSE 0 END) altas
            FROM simulador_jornada_operativa
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_jornada_operativa") else {}
        estado["jornada_critica"] = _fetch_all_dict(
            conn,
            """
            SELECT fecha, area, entrada, completadas, bloqueadas_stock,
                   bloqueadas_capacidad, pendientes_final, riesgo, lectura,
                   accion_direccion
            FROM simulador_jornada_operativa
            WHERE corrida_id=? AND centro_id=?
              AND riesgo IN ('critica','alta')
            ORDER BY CASE riesgo WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END,
                     pendientes_final DESC, fecha
            LIMIT 20
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_jornada_operativa") else []
        estado["brechas_stock_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) productos,
                   SUM(avisos) avisos,
                   ROUND(SUM(cantidad_bloqueada), 2) cantidad_bloqueada,
                   ROUND(SUM(cantidad_pedida_mise), 2) cantidad_pedida,
                   ROUND(SUM(brecha_estimada), 2) brecha,
                   ROUND(SUM(coste_brecha_estimado), 2) coste,
                   SUM(CASE WHEN prioridad='critica' THEN 1 ELSE 0 END) criticos,
                   SUM(CASE WHEN prioridad='alta' THEN 1 ELSE 0 END) altos
            FROM simulador_mise_brechas_stock
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_mise_brechas_stock") else {}
        estado["brechas_stock"] = _fetch_all_dict(
            conn,
            """
            SELECT producto, familia, unidad, proveedor, departamento, avisos,
                   cantidad_bloqueada, cantidad_stock, cantidad_pedida_mise,
                   cantidad_recibida_mise, brecha_estimada, coste_brecha_estimado,
                   primera_fecha, ultima_fecha, prioridad, accion, lectura
            FROM simulador_mise_brechas_stock
            WHERE corrida_id=? AND centro_id=?
            ORDER BY CASE prioridad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END,
                     coste_brecha_estimado DESC
            LIMIT 20
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_mise_brechas_stock") else []
        estado["plan_compras_mise_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) lineas,
                   COUNT(DISTINCT semana_inicio) semanas,
                   COUNT(DISTINCT proveedor_id) proveedores,
                   ROUND(SUM(cantidad_planificada), 2) cantidad,
                   ROUND(SUM(coste_estimado), 2) coste,
                   SUM(CASE WHEN riesgo IN ('critico','alto') THEN 1 ELSE 0 END) riesgos
            FROM simulador_plan_compras_mise
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_plan_compras_mise") else {}
        estado["plan_compras_mise_semanas"] = _fetch_all_dict(
            conn,
            """
            SELECT semana_inicio, MIN(fecha_pedido) primer_pedido,
                   COUNT(*) lineas, COUNT(DISTINCT proveedor_id) proveedores,
                   ROUND(SUM(cantidad_planificada), 2) cantidad,
                   ROUND(SUM(coste_estimado), 2) coste,
                   SUM(CASE WHEN riesgo IN ('critico','alto') THEN 1 ELSE 0 END) riesgos
            FROM simulador_plan_compras_mise
            WHERE corrida_id=? AND centro_id=?
            GROUP BY semana_inicio
            ORDER BY semana_inicio
            LIMIT 16
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_plan_compras_mise") else []
        estado["plan_compras_mise_proveedores"] = _fetch_all_dict(
            conn,
            """
            SELECT proveedor, COUNT(*) lineas,
                   ROUND(SUM(cantidad_planificada), 2) cantidad,
                   ROUND(SUM(coste_estimado), 2) coste,
                   SUM(CASE WHEN riesgo IN ('critico','alto') THEN 1 ELSE 0 END) riesgos
            FROM simulador_plan_compras_mise
            WHERE corrida_id=? AND centro_id=?
            GROUP BY proveedor
            ORDER BY coste DESC
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_plan_compras_mise") else []
        estado["tesoreria_mise_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) meses,
                   ROUND(SUM(coste_plan_mise), 2) coste_plan,
                   ROUND(SUM(margen_original), 2) margen_original,
                   ROUND(SUM(margen_ajustado), 2) margen_ajustado,
                   SUM(CASE WHEN riesgo='critico' THEN 1 ELSE 0 END) criticos,
                   SUM(CASE WHEN riesgo='alto' THEN 1 ELSE 0 END) altos
            FROM simulador_tesoreria_plan_compras_mise
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_tesoreria_plan_compras_mise") else {}
        estado["tesoreria_mise_meses"] = _fetch_all_dict(
            conn,
            """
            SELECT mes, ingresos_total, margen_original, coste_compras_original,
                   coste_plan_mise, coste_compras_ajustado, margen_ajustado,
                   compras_pct_ingresos, caja_minima_recomendada, riesgo,
                   decision, condicion_control
            FROM simulador_tesoreria_plan_compras_mise
            WHERE corrida_id=? AND centro_id=?
            ORDER BY mes
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_tesoreria_plan_compras_mise") else []
        estado["escenarios_mise_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) escenarios,
                   SUM(CASE WHEN veredicto='recomendado' THEN 1 ELSE 0 END) recomendados,
                   SUM(CASE WHEN margen_escenario < 0 THEN 1 ELSE 0 END) negativos,
                   ROUND(MAX(margen_escenario), 2) mejor_margen
            FROM simulador_escenarios_mise_decision
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_escenarios_mise_decision") else {}
        estado["escenarios_mise_recomendados"] = _fetch_all_dict(
            conn,
            """
            WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY mes
                    ORDER BY CASE veredicto
                        WHEN 'recomendado' THEN 1
                        WHEN 'piloto_controlado' THEN 2
                        WHEN 'aplicar_con_cuidado' THEN 3
                        ELSE 4 END,
                        margen_escenario DESC
                ) rn
                FROM simulador_escenarios_mise_decision
                WHERE corrida_id=? AND centro_id=?
            )
            SELECT mes, codigo, nombre, descripcion, margen_original,
                   coste_plan_original, coste_escenario, ahorro_vs_plan,
                   margen_escenario, cobertura_operativa_pct, riesgo,
                   veredicto, decision, condicion_control
            FROM ranked
            WHERE rn=1
            ORDER BY mes
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_escenarios_mise_decision") else []
        estado["escenarios_mise_detalle"] = _fetch_all_dict(
            conn,
            """
            SELECT mes, codigo, nombre, coste_escenario, ahorro_vs_plan,
                   margen_escenario, cobertura_operativa_pct, riesgo, veredicto
            FROM simulador_escenarios_mise_decision
            WHERE corrida_id=? AND centro_id=?
            ORDER BY mes,
                     CASE veredicto WHEN 'recomendado' THEN 1 WHEN 'piloto_controlado' THEN 2 WHEN 'aplicar_con_cuidado' THEN 3 ELSE 4 END,
                     margen_escenario DESC
            LIMIT 45
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_escenarios_mise_decision") else []
        estado["rescate_criticos_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) escenarios,
                   COUNT(DISTINCT mes) meses,
                   SUM(CASE WHEN veredicto IN ('recomendado','piloto_controlado') THEN 1 ELSE 0 END) salvables,
                   ROUND(MAX(margen_estimado), 2) mejor_margen
            FROM simulador_rescate_meses_criticos
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_rescate_meses_criticos") else {}
        estado["rescate_criticos"] = _fetch_all_dict(
            conn,
            """
            SELECT mes, accion, descripcion, margen_base_mise, ingreso_delta,
                   coste_delta, ahorro_delta, margen_estimado, mejora_vs_base,
                   ocupacion_objetivo_pct, adr_objetivo, tpv_pax_objetivo,
                   riesgo, veredicto, decision, condicion_control
            FROM simulador_rescate_meses_criticos
            WHERE corrida_id=? AND centro_id=?
            ORDER BY mes,
                     CASE veredicto WHEN 'recomendado' THEN 1 WHEN 'piloto_controlado' THEN 2 ELSE 3 END,
                     margen_estimado DESC
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_rescate_meses_criticos") else []
        estado["guardarrailes_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) guardarrailes,
                   SUM(CASE WHEN estado_margen='verde' THEN 1 ELSE 0 END) verdes,
                   SUM(CASE WHEN estado_margen='ambar' THEN 1 ELSE 0 END) ambar,
                   SUM(CASE WHEN estado_margen='rojo' THEN 1 ELSE 0 END) rojos,
                   ROUND(SUM(brecha_seguridad), 2) brecha_seguridad
            FROM simulador_guardarrailes_no_arruinarse
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_guardarrailes_no_arruinarse") else {}
        estado["guardarrailes"] = _fetch_all_dict(
            conn,
            """
            SELECT mes, escenario, margen_base, margen_estimado,
                   margen_seguridad_objetivo, brecha_seguridad,
                   estado_margen, ocupacion_minima_pct, adr_minimo,
                   tpv_pax_minimo, ahorro_minimo, compra_maxima,
                   ingreso_extra_minimo, decision, condiciones_minimas,
                   parada_si, lectura
            FROM simulador_guardarrailes_no_arruinarse
            WHERE corrida_id=? AND centro_id=?
            ORDER BY CASE estado_margen WHEN 'rojo' THEN 1 WHEN 'ambar' THEN 2 ELSE 3 END,
                     brecha_seguridad DESC, mes
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_guardarrailes_no_arruinarse") else []
        estado["decision_financiera_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) meses,
                   SUM(CASE WHEN estado='verde' THEN 1 ELSE 0 END) verdes,
                   SUM(CASE WHEN estado='ambar' THEN 1 ELSE 0 END) ambar,
                   SUM(CASE WHEN estado='rojo' THEN 1 ELSE 0 END) rojos,
                   ROUND(SUM(margen_final_estimado), 2) margen_final
            FROM simulador_decision_financiera_mensual
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_decision_financiera_mensual") else {}
        estado["decision_financiera_meses"] = _fetch_all_dict(
            conn,
            """
            SELECT mes, ingresos_total, coste_total_base, margen_base,
                   margen_mise, escenario_mise, margen_rescate,
                   escenario_rescate, margen_final_estimado, estado,
                   causa_principal, decision, indicador_clave, condicion_control
            FROM simulador_decision_financiera_mensual
            WHERE corrida_id=? AND centro_id=?
            ORDER BY mes
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_decision_financiera_mensual") else []
        estado["stoploss_semanal_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) semanas,
                   SUM(CASE WHEN estado='verde' THEN 1 ELSE 0 END) verdes,
                   SUM(CASE WHEN estado='ambar' THEN 1 ELSE 0 END) ambar,
                   SUM(CASE WHEN estado='rojo' THEN 1 ELSE 0 END) rojos,
                   ROUND(SUM(margen), 2) margen
            FROM simulador_stoploss_semanal
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_stoploss_semanal") else {}
        estado["stoploss_semanal"] = _fetch_all_dict(
            conn,
            """
            SELECT mes, semana_inicio, dias, habitaciones_ocupadas, pax,
                   ocupacion_pct, ocupacion_minima_pct, adr, adr_minimo,
                   tpv_pax, tpv_pax_minimo, compras, compra_maxima,
                   margen, margen_minimo, quejas, incumplimientos,
                   estado, causa, accion
            FROM simulador_stoploss_semanal
            WHERE corrida_id=? AND centro_id=?
            ORDER BY CASE estado WHEN 'rojo' THEN 1 WHEN 'ambar' THEN 2 ELSE 3 END,
                     incumplimientos DESC, semana_inicio
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_stoploss_semanal") else []
        estado["produccion_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) dias,
                   SUM(partes) partes,
                   SUM(lineas) lineas,
                   SUM(ingredientes_reales) ingredientes,
                   ROUND(SUM(kg_carnes), 2) kg_carnes,
                   ROUND(SUM(kg_pescados), 2) kg_pescados,
                   SUM(movimientos_camara) movimientos_camara,
                   SUM(controles_appcc) controles_appcc,
                   SUM(incidencias) incidencias
            FROM simulador_produccion_resumen_diario
            WHERE corrida_id=?
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_produccion_resumen_diario") else {}
        estado["produccion_diaria"] = _fetch_all_dict(
            conn,
            """
            SELECT *
            FROM simulador_produccion_resumen_diario
            WHERE corrida_id=?
            ORDER BY incidencias DESC, fecha
            LIMIT 20
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_produccion_resumen_diario") else []
        estado["escandallos_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) platos,
                   COUNT(DISTINCT fecha) dias,
                   ROUND(SUM(coste_teorico), 2) coste_teorico,
                   ROUND(SUM(coste_real), 2) coste_real,
                   ROUND(SUM(desviacion_coste), 2) desviacion,
                   ROUND(SUM(coste_merma_estimado), 2) coste_merma,
                   SUM(CASE WHEN riesgo='alto' THEN 1 ELSE 0 END) altos,
                   SUM(CASE WHEN riesgo='medio' THEN 1 ELSE 0 END) medios
            FROM simulador_escandallos_diarios_plato
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_escandallos_diarios_plato") else {}
        estado["escandallos_top_coste"] = _fetch_all_dict(
            conn,
            """
            SELECT plato, COUNT(*) veces,
                   ROUND(SUM(raciones_reales), 1) raciones,
                   ROUND(SUM(coste_real), 2) coste,
                   ROUND(AVG(coste_racion_real), 2) coste_racion,
                   ROUND(SUM(coste_merma_estimado), 2) merma,
                   SUM(CASE WHEN riesgo='alto' THEN 1 ELSE 0 END) altos
            FROM simulador_escandallos_diarios_plato
            WHERE corrida_id=? AND centro_id=?
            GROUP BY receta_id, plato
            ORDER BY coste DESC
            LIMIT 12
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_escandallos_diarios_plato") else []
        estado["escandallos_top_merma"] = _fetch_all_dict(
            conn,
            """
            SELECT plato, COUNT(*) veces,
                   ROUND(SUM(sobrante_raciones), 1) sobrante,
                   ROUND(SUM(coste_merma_estimado), 2) merma,
                   ROUND(AVG(coste_racion_real), 2) coste_racion,
                   SUM(CASE WHEN riesgo='alto' THEN 1 ELSE 0 END) altos
            FROM simulador_escandallos_diarios_plato
            WHERE corrida_id=? AND centro_id=?
            GROUP BY receta_id, plato
            ORDER BY merma DESC
            LIMIT 12
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_escandallos_diarios_plato") else []
        estado["escandallos_riesgo"] = _fetch_all_dict(
            conn,
            """
            SELECT fecha, servicio, plato, raciones_reales, coste_real,
                   coste_racion_real, desviacion_pct, sobrante_raciones,
                   coste_merma_estimado, temperatura_fin, riesgo, lectura, accion
            FROM simulador_escandallos_diarios_plato
            WHERE corrida_id=? AND centro_id=? AND riesgo='alto'
            ORDER BY coste_merma_estimado DESC, coste_real DESC
            LIMIT 20
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_escandallos_diarios_plato") else []
        estado["escandallos_servicios"] = _fetch_all_dict(
            conn,
            """
            SELECT fecha,
                   servicio,
                   COUNT(*) platos,
                   ROUND(SUM(raciones_reales), 1) raciones,
                   ROUND(SUM(coste_real), 2) coste_real,
                   ROUND(
                       CASE
                           WHEN SUM(raciones_reales) > 0 THEN SUM(coste_real) / SUM(raciones_reales)
                           ELSE 0
                       END,
                       2
                   ) coste_racion,
                   ROUND(SUM(coste_merma_estimado), 2) merma,
                   ROUND(SUM(sobrante_raciones), 1) sobrante,
                   SUM(CASE WHEN riesgo='alto' THEN 1 ELSE 0 END) altos
            FROM simulador_escandallos_diarios_plato
            WHERE corrida_id=? AND centro_id=?
            GROUP BY fecha, servicio
            ORDER BY fecha DESC,
                     CASE servicio
                         WHEN 'DESAYUNO' THEN 1
                         WHEN 'ALMUERZO' THEN 2
                         WHEN 'CENA' THEN 3
                         WHEN 'SNACK' THEN 4
                         ELSE 9
                     END
            LIMIT 40
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_escandallos_diarios_plato") else []
        estado["escandallos_sobrantes_destino"] = _fetch_all_dict(
            conn,
            """
            SELECT pp.fecha,
                   COALESCE(pp.turno, 'Sin servicio') servicio,
                   COALESCE(pc.destino_sobrante, 'sin_sobrante') destino,
                   COUNT(*) lineas,
                   ROUND(SUM(COALESCE(pc.cantidad_sobrante, 0)), 1) sobrante
            FROM produccion_controles_appcc pc
            JOIN lineas_produccion lp ON lp.id=pc.linea_produccion_id
            JOIN partes_produccion pp ON pp.id=lp.parte_id
            WHERE pp.empresa_id=? AND pp.centro_id=?
              AND COALESCE(pc.cantidad_sobrante, 0) > 0
            GROUP BY pp.fecha, pp.turno, pc.destino_sobrante
            ORDER BY pp.fecha DESC,
                     CASE pp.turno
                         WHEN 'DESAYUNO' THEN 1
                         WHEN 'ALMUERZO' THEN 2
                         WHEN 'CENA' THEN 3
                         WHEN 'SNACK' THEN 4
                         ELSE 9
                     END,
                     sobrante DESC
            LIMIT 40
            """,
            (1, 3),
        ) if _demo_table_exists(conn, "produccion_controles_appcc") else []
        estado["recomendaciones_cocina_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) recomendaciones,
                   SUM(CASE WHEN prioridad='critica' THEN 1 ELSE 0 END) criticas,
                   SUM(CASE WHEN prioridad='alta' THEN 1 ELSE 0 END) altas,
                   SUM(CASE WHEN prioridad='media' THEN 1 ELSE 0 END) medias,
                   ROUND(SUM(ahorro_estimado), 2) ahorro_estimado
            FROM simulador_recomendaciones_cocina_margen
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_recomendaciones_cocina_margen") else {}
        estado["recomendaciones_cocina"] = _fetch_all_dict(
            conn,
            """
            SELECT prioridad, plato, recomendacion, decision_propuesta,
                   coste_total, coste_racion, merma_total, sobrante_raciones,
                   desviacion_total, riesgo_alto, ahorro_estimado,
                   condicion_control, servicios, veces
            FROM simulador_recomendaciones_cocina_margen
            WHERE corrida_id=? AND centro_id=?
            ORDER BY CASE prioridad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 WHEN 'media' THEN 3 ELSE 4 END,
                     ahorro_estimado DESC
            LIMIT 20
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_recomendaciones_cocina_margen") else []
        estado["impacto_cocina_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) meses,
                   ROUND(SUM(ahorro_estimado), 2) ahorro_estimado,
                   SUM(CASE WHEN estado_antes!='verde' AND estado_despues='verde' THEN 1 ELSE 0 END) meses_rescatados,
                   SUM(CASE WHEN estado_despues='rojo' THEN 1 ELSE 0 END) rojos_restantes,
                   SUM(CASE WHEN estado_despues='ambar' THEN 1 ELSE 0 END) ambar_restantes
            FROM simulador_impacto_cocina_mensual
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_impacto_cocina_mensual") else {}
        estado["impacto_cocina_meses"] = _fetch_all_dict(
            conn,
            """
            SELECT mes, platos_intervenidos, lineas_afectadas, coste_afectado,
                   merma_afectada, ahorro_estimado, margen_antes,
                   margen_despues, estado_antes, estado_despues,
                   top_platos, decision, condicion_control
            FROM simulador_impacto_cocina_mensual
            WHERE corrida_id=? AND centro_id=?
            ORDER BY mes
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_impacto_cocina_mensual") else []
        estado["plan_integral_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) planes,
                   ROUND(SUM(brecha_margen), 2) brecha_total,
                   SUM(habitaciones_extra) habitaciones_extra,
                   ROUND(AVG(ocupacion_objetivo_pct), 1) ocupacion_objetivo_media,
                   ROUND(AVG(adr_objetivo), 2) adr_objetivo_medio
            FROM simulador_plan_integral_margen
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_plan_integral_margen") else {}
        estado["plan_integral_meses"] = _fetch_all_dict(
            conn,
            """
            SELECT mes, estado_origen, margen_actual, margen_objetivo,
                   brecha_margen, ahorro_cocina, ocupacion_actual_pct,
                   ocupacion_objetivo_pct, habitaciones_extra,
                   adr_actual, adr_objetivo, tpv_pax_actual,
                   tpv_pax_objetivo, compra_maxima, palancas,
                   decision, condicion_control, stop_loss
            FROM simulador_plan_integral_margen
            WHERE corrida_id=? AND centro_id=?
            ORDER BY brecha_margen DESC, mes
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_plan_integral_margen") else []
        estado["auditoria_margen_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT *
            FROM simulador_auditoria_cerebro_margen_resumen
            WHERE corrida_id=? AND centro_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_auditoria_cerebro_margen_resumen") else {}
        estado["auditoria_margen_alertas"] = _fetch_all_dict(
            conn,
            """
            SELECT area, severidad, codigo, titulo, detalle, evidencia_json
            FROM simulador_auditoria_cerebro_margen
            WHERE corrida_id=? AND centro_id=?
            ORDER BY CASE severidad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 WHEN 'media' THEN 3 ELSE 4 END,
                     area, id
            LIMIT 20
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_auditoria_cerebro_margen") else []
        estado["resumen_ejecutivo_margen_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) acciones,
                   ROUND(SUM(impacto_estimado), 2) impacto_estimado
            FROM simulador_resumen_ejecutivo_margen
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_resumen_ejecutivo_margen") else {}
        estado["resumen_ejecutivo_margen"] = _fetch_all_dict(
            conn,
            """
            SELECT prioridad, area, titulo, problema, decision,
                   impacto_estimado, indicador_control, estado
            FROM simulador_resumen_ejecutivo_margen
            WHERE corrida_id=? AND centro_id=?
            ORDER BY prioridad, id
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_resumen_ejecutivo_margen") else []
        estado["menu_semanal_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) servicios,
                   COUNT(DISTINCT semana_inicio) semanas,
                   COUNT(DISTINCT fecha) dias,
                   ROUND(SUM(pax_previstos), 0) pax,
                   ROUND(SUM(kg_estimados), 2) kg_estimados,
                   ROUND(SUM(kg_pescado), 2) kg_pescado,
                   ROUND(SUM(kg_carne), 2) kg_carne,
                   SUM(alertas) alertas
            FROM simulador_planificacion_menu_semanal
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_planificacion_menu_semanal") else {}
        estado["menu_semanal_proximos"] = _fetch_all_dict(
            conn,
            """
            SELECT fecha, servicio, partes, lineas, pax_previstos, kg_pescado,
                   kg_carne, kg_congelado, alertas, lectura
            FROM simulador_planificacion_menu_semanal
            WHERE corrida_id=? AND centro_id=?
            ORDER BY fecha, CASE servicio WHEN 'desayuno' THEN 1 WHEN 'almuerzo' THEN 2 WHEN 'cena' THEN 3 ELSE 4 END
            LIMIT 21
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_planificacion_menu_semanal") else []
        estado["mise_alertas_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) total,
                   SUM(CASE WHEN prioridad='critica' THEN 1 ELSE 0 END) criticas,
                   SUM(CASE WHEN prioridad='alta' THEN 1 ELSE 0 END) altas,
                   SUM(CASE WHEN tipo='sacar_pescado' THEN 1 ELSE 0 END) pescado,
                   SUM(CASE WHEN tipo='sacar_carne' THEN 1 ELSE 0 END) carne,
                   SUM(CASE WHEN tipo='desinfectar' THEN 1 ELSE 0 END) desinfeccion
            FROM simulador_alertas_mise_en_place
            WHERE corrida_id=? AND centro_id=?
              AND COALESCE(estado,'pendiente') NOT IN ('completada','cancelada')
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_alertas_mise_en_place") else {}
        estado["mise_alertas"] = _fetch_all_dict(
            conn,
            """
            SELECT fecha_alerta, fecha_servicio, servicio, prioridad, tipo, estado,
                   producto, cantidad, unidad, accion
            FROM simulador_alertas_mise_en_place
            WHERE corrida_id=? AND centro_id=?
              AND COALESCE(estado,'pendiente') NOT IN ('completada','cancelada')
            ORDER BY CASE prioridad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END,
                     fecha_alerta, fecha_servicio
            LIMIT 30
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_alertas_mise_en_place") else []
        estado["pedidos_mise_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) pedidos,
                   COALESCE(SUM(lineas), 0) lineas,
                   ROUND(COALESCE(SUM(total), 0), 2) importe,
                   SUM(CASE WHEN estado='borrador' THEN 1 ELSE 0 END) borradores,
                   SUM(CASE WHEN estado='enviado' THEN 1 ELSE 0 END) enviados,
                   SUM(CASE WHEN estado='recibido' THEN 1 ELSE 0 END) recibidos,
                   SUM(CASE WHEN estado='recibido_parcial' THEN 1 ELSE 0 END) parciales,
                   SUM(CASE WHEN recepciones=0 THEN 1 ELSE 0 END) sin_recepcion,
                   COALESCE(SUM(recepciones), 0) recepciones,
                   COALESCE(SUM(economicas_validadas), 0) economicas_validadas,
                   COALESCE(SUM(pendiente_economica), 0) pendiente_economica,
                   COALESCE(SUM(documentos_compra), 0) documentos_compra,
                   COALESCE(SUM(documentos_contables), 0) documentos_contables,
                   ROUND(COALESCE(SUM(total_contable), 0), 2) total_contable,
                   ROUND(COALESCE(SUM(pagado_contable), 0), 2) pagado_contable,
                   ROUND(COALESCE(SUM(pendiente_contable), 0), 2) pendiente_contable,
                   COALESCE(SUM(contables_vencidos), 0) contables_vencidos,
                   COALESCE(SUM(contables_parciales), 0) contables_parciales,
                   COALESCE(SUM(pagos_tesoreria), 0) pagos_tesoreria,
                   COALESCE(SUM(incidencias), 0) incidencias,
                   COALESCE(SUM(lotes), 0) lotes
            FROM (
                SELECT p.id, p.estado,
                       COUNT(DISTINCT lp.id) lineas,
                       ROUND(COALESCE(SUM(lp.cantidad_solicitada * lp.precio_unitario), 0), 2) total,
                       COUNT(DISTINCT r.id) recepciones,
                       COUNT(DISTINCT CASE WHEN COALESCE(r.validada_economicamente,0)=1 THEN r.id END) economicas_validadas,
                       COUNT(DISTINCT CASE WHEN r.id IS NOT NULL AND COALESCE(r.validada_economicamente,0)=0 THEN r.id END) pendiente_economica,
                       COUNT(DISTINCT dc.id) documentos_compra,
                       COUNT(DISTINCT cd.id) documentos_contables,
                       COALESCE(SUM(DISTINCT cd.total), 0) total_contable,
                       COALESCE(SUM(DISTINCT cd.pagado), 0) pagado_contable,
                       COALESCE(SUM(DISTINCT cd.pendiente), 0) pendiente_contable,
                       COUNT(DISTINCT CASE WHEN cd.estado='vencido' THEN cd.id END) contables_vencidos,
                       COUNT(DISTINCT CASE WHEN cd.estado='pagado_parcial' THEN cd.id END) contables_parciales,
                       COUNT(DISTINCT pg.id) pagos_tesoreria,
                       COUNT(DISTINCT lr.incidencia_id) incidencias,
                       COUNT(DISTINCT lr.lote_id) lotes
                FROM pedidos p
                LEFT JOIN lineas_pedido lp ON lp.pedido_id=p.id
                LEFT JOIN recepciones r ON r.pedido_id=p.id
                LEFT JOIN lineas_recepcion lr ON lr.recepcion_id=r.id
                LEFT JOIN documentos_compra dc ON dc.recepcion_id=r.id AND dc.origen_subida='simulador_mise_economica'
                LEFT JOIN contabilidad_documentos cd
                       ON cd.origen_modulo='compras'
                      AND cd.origen_tipo='recepcion'
                      AND cd.origen_id=r.id
                LEFT JOIN contabilidad_pagos pg ON pg.documento_id=cd.id
                WHERE p.empresa_id=1
                  AND p.numero_pedido LIKE 'MISE-H1-%'
                GROUP BY p.id
            )
            """,
        ) if _demo_table_exists(conn, "pedidos") else {}
        estado["pedidos_mise"] = _fetch_all_dict(
            conn,
            """
            SELECT p.id, p.numero_pedido, p.estado, p.fecha_pedido, p.fecha_entrega,
                   pr.nombre proveedor_nombre, d.nombre departamento_nombre,
                   COUNT(lp.id) lineas,
                   ROUND(COALESCE(SUM(lp.cantidad_solicitada * lp.precio_unitario), 0), 2) total
            FROM pedidos p
            LEFT JOIN proveedores pr ON pr.id=p.proveedor_id
            LEFT JOIN departamentos d ON d.id=p.departamento_id
            LEFT JOIN lineas_pedido lp ON lp.pedido_id=p.id
            WHERE p.empresa_id=1
              AND p.numero_pedido LIKE 'MISE-H1-%'
            GROUP BY p.id
            ORDER BY p.fecha_pedido DESC, p.numero_pedido DESC
            LIMIT 12
            """,
        ) if _demo_table_exists(conn, "pedidos") else []
        estado["desviaciones_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT *
            FROM simulador_desviaciones_resumen
            WHERE corrida_id=? AND centro_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_desviaciones_resumen") else {}
        estado["desviaciones_operativas"] = _fetch_all_dict(
            conn,
            """
            SELECT *
            FROM simulador_desviaciones_operativas
            WHERE corrida_id=? AND centro_id=?
            ORDER BY CASE nivel WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 WHEN 'media' THEN 3 ELSE 4 END,
                     fecha, area
            LIMIT 20
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_desviaciones_operativas") else []
        estado["cierre_resumen"] = _fetch_one_dict(
            conn,
            """
            SELECT COUNT(*) dias,
                   SUM(CASE WHEN estado='bloqueado' THEN 1 ELSE 0 END) bloqueados,
                   SUM(CASE WHEN estado='revisar' THEN 1 ELSE 0 END) revisar,
                   SUM(CASE WHEN estado='cerrado' THEN 1 ELSE 0 END) cerrados,
                   ROUND(SUM(CASE WHEN margen_estimado < 0 THEN margen_estimado ELSE 0 END), 2) margen_negativo,
                   ROUND(AVG(score_riesgo), 1) riesgo_medio,
                   COALESCE(MAX(hora_corte), '01:00') hora_corte
            FROM simulador_cierre_diario
            WHERE corrida_id=?
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_cierre_diario") else {}
        estado["resultado_mensual"] = _fetch_all_dict(
            conn,
            """
            SELECT *
            FROM simulador_resultado_mensual
            WHERE corrida_id=?
            ORDER BY mes
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_resultado_mensual") else []
        estado["propuestas_ia_mensuales"] = _fetch_all_dict(
            conn,
            """
            SELECT *
            FROM simulador_ia_propuestas_mensuales
            WHERE corrida_id=?
            ORDER BY CASE prioridad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END,
                     mes, area
            LIMIT 40
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_ia_propuestas_mensuales") else []
        estado["finanzas_diagnostico"] = _fetch_all_dict(
            conn,
            """
            SELECT mes, estado, causa_principal, causa_secundaria, lectura,
                   ocupacion_pct, adr, revpar, tpv_pax, coste_laboral_pct,
                   coste_compras_pct, coste_fijo_pct, margen, brecha_diaria,
                   experimento_recomendado, condicion_no_arruinarse
            FROM simulador_finanzas_diagnostico_mensual
            WHERE corrida_id=? AND centro_id=?
            ORDER BY mes
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_finanzas_diagnostico_mensual") else []
        estado["finanzas_acciones"] = _fetch_all_dict(
            conn,
            """
            SELECT mes, accion, descripcion, ingresos_estimados, costes_estimados,
                   margen_estimado, mejora_margen, riesgo, veredicto,
                   condicion_control
            FROM simulador_finanzas_acciones_mensuales
            WHERE corrida_id=? AND centro_id=?
            ORDER BY CASE veredicto WHEN 'probar_controlado' THEN 1 WHEN 'mejora_pero_no_basta' THEN 2 ELSE 3 END,
                     mes, mejora_margen DESC
            LIMIT 36
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_finanzas_acciones_mensuales") else []
        estado["plan_accion_mensual_elegido"] = _fetch_all_dict(
            conn,
            """
            SELECT mes, estado_mes, margen_original, accion_elegida, descripcion,
                   margen_estimado, mejora_margen, riesgo, veredicto, decision,
                   condicion_control, prioridad, accion_refuerzo, margen_con_refuerzo,
                   brecha_restante, lectura_refuerzo
            FROM simulador_plan_accion_mensual_elegido
            WHERE corrida_id=? AND centro_id=?
            ORDER BY mes
            """,
            (corrida_id, 3),
        ) if _demo_table_exists(conn, "simulador_plan_accion_mensual_elegido") else []
        estado["cierre_diario"] = _fetch_all_dict(
            conn,
            """
            SELECT *
            FROM simulador_cierre_diario
            WHERE corrida_id=?
            ORDER BY CASE estado WHEN 'bloqueado' THEN 1 WHEN 'revisar' THEN 2 ELSE 3 END,
                     score_riesgo DESC, margen_estimado ASC, fecha
            LIMIT 20
            """,
            (corrida_id,),
        ) if _demo_table_exists(conn, "simulador_cierre_diario") else []
        metricas = {}
        for key, table, where in [
            ("reservas", "reservas_recepcion", "codigo LIKE 'VIDA-H1-%'"),
            ("pagos", "pagos_reserva_recepcion", "referencia LIKE 'PAY-VIDA-H1-%'"),
            ("facturas", "recepcion_facturas", "serie='SIMH1'"),
            ("comandas", "tpv_comandas", "numero LIKE 'CMD-H1-%'"),
            ("turnos", "turnos_rrhh", "origen='simulador_vida'"),
            ("fichajes", "fichajes", "origen='simulador_vida'"),
            ("quejas", "experiencia_quejas", "origen_modulo='simulador_vida'"),
            ("ordenes", "ordenes_trabajo", "numero_orden LIKE 'OT-VIDA-H1-%'"),
            ("pedidos_mise", "pedidos", "numero_pedido LIKE 'MISE-H1-%'"),
        ]:
            metricas[key] = _demo_table_count_where(conn, table, where)
        estado["metricas"] = metricas
    finally:
        conn.close()
    return estado


def _demo_table_count_where(conn, table, where):
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            return 0
        return int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0] or 0)
    except Exception:
        return 0


def _radar_modulos_operativos(conn, corrida_id):
    modulos = [
        {
            "modulo": "Reservas y ocupacion",
            "icono": "bed",
            "principal": ("reservas_recepcion", "codigo LIKE 'VIDA-H1-%' OR codigo LIKE 'OB-H1-%'"),
            "apoyos": [
                ("Viajeros", "viajeros_recepcion", "1=1"),
                ("Overbooking", "simulador_overbooking_casos", "corrida_id=%d" % corrida_id),
                ("Bloqueos/cupos", "simulador_overbooking_prevencion", "corrida_id=%d" % corrida_id),
            ],
            "riesgo": ("simulador_overbooking_casos", "corrida_id=%d AND severidad IN ('critica','alta')" % corrida_id),
            "lectura_ok": "Hay ocupacion, viajeros y control de sobreventa para tensionar recepcion.",
            "lectura_falta": "Faltan reservas/viajeros suficientes para probar recepcion como operacion real.",
            "accion": "Cruzar cupos, canales, bloqueos y habitaciones antes de vender mas.",
        },
        {
            "modulo": "Compras y proveedores",
            "icono": "truck-ramp-box",
            "principal": ("pedidos", "numero_pedido LIKE 'MISE-H1-%' OR numero_pedido LIKE 'PED-VIDA-H1-%' OR numero_pedido LIKE 'COMPRA-H1-%'"),
            "apoyos": [
                ("Recepciones", "recepciones", "1=1"),
                ("Lineas pedido", "lineas_pedido", "1=1"),
                ("Lotes", "lotes", "1=1"),
            ],
            "riesgo": ("recepciones", "COALESCE(estado,'') LIKE '%parcial%' OR COALESCE(incidencia_id,0)>0"),
            "lectura_ok": "Ya hay pedidos, recepciones y lotes para seguir compras desde necesidad hasta entrada.",
            "lectura_falta": "Faltan compras/recepciones trazables para saber si cocina y almacenes aguantan.",
            "accion": "Forzar faltas, entregas parciales, proveedor malo y validacion economica.",
        },
        {
            "modulo": "Cocina y produccion",
            "icono": "utensils",
            "principal": ("partes_produccion", "centro_id=3 OR COALESCE(observaciones,'') LIKE '%simulador%'"),
            "apoyos": [
                ("Lineas produccion", "lineas_produccion", "1=1"),
                ("Menu semanal", "simulador_planificacion_menu_semanal", "corrida_id=%d" % corrida_id),
                ("Mise en place", "simulador_alertas_mise_en_place", "corrida_id=%d" % corrida_id),
            ],
            "riesgo": ("simulador_alertas_mise_en_place", "corrida_id=%d AND prioridad IN ('critica','alta') AND COALESCE(estado,'pendiente') NOT IN ('completada','cancelada')" % corrida_id),
            "lectura_ok": "Produccion, menu semanal y mise ya generan trabajo medible.",
            "lectura_falta": "Falta presion real de cocina: partes, lineas, camaras y avisos previos.",
            "accion": "Comprobar pescados/carnes, descongelacion, cantidades y salida de camara.",
        },
        {
            "modulo": "RRHH y fichajes",
            "icono": "users-gear",
            "principal": ("fichajes", "origen='simulador_vida'"),
            "apoyos": [
                ("Turnos", "turnos_rrhh", "origen='simulador_vida'"),
                ("Control diario", "simulador_rrhh_control_diario", "corrida_id=%d" % corrida_id),
                ("Escenarios", "simulador_rrhh_escenarios_resumen", "corrida_id=%d" % corrida_id),
            ],
            "riesgo": ("simulador_rrhh_control_diario", "corrida_id=%d AND nivel IN ('critica','alta')" % corrida_id),
            "lectura_ok": "Ya se puede cruzar cuadrante, fichaje, ausencias y riesgo laboral.",
            "lectura_falta": "Sin fichajes y turnos suficientes no se puede medir rotacion ni coste real.",
            "accion": "Seguir metiendo ausencias, cambios, salidas anticipadas y extras pendientes.",
        },
        {
            "modulo": "Mantenimiento e infraestructura",
            "icono": "screwdriver-wrench",
            "principal": ("ordenes_trabajo", "numero_orden LIKE 'OT-VIDA-H1-%' OR COALESCE(origen_modulo,'')='simulador_vida'"),
            "apoyos": [
                ("Equipos", "equipos_mantenimiento", "1=1"),
                ("Incidencias", "incidencias", "1=1"),
                ("Habitaciones", "habitaciones_recepcion", "1=1"),
            ],
            "riesgo": ("ordenes_trabajo", "(numero_orden LIKE 'OT-VIDA-H1-%' OR COALESCE(origen_modulo,'')='simulador_vida') AND (COALESCE(prioridad,'') IN ('critica','alta') OR COALESCE(estado,'') IN ('pendiente','abierta','pendiente_capacidad'))"),
            "lectura_ok": "Hay ordenes y activos para ver si el edificio limita la venta.",
            "lectura_falta": "Faltan averias, preventivos o activos para medir impacto real.",
            "accion": "Tensionar goteras, climatizacion, ascensores, piscina y habitaciones bloqueadas.",
        },
        {
            "modulo": "TPV y puntos de venta",
            "icono": "cash-register",
            "principal": ("tpv_comandas", "numero LIKE 'CMD-H1-%'"),
            "apoyos": [
                ("Puntos venta", "puntos_venta", "1=1"),
                ("Terminales", "tpv_terminales", "1=1"),
                ("Lineas TPV", "tpv_lineas_comanda", "1=1"),
            ],
            "riesgo": ("tpv_comandas", "COALESCE(total,0)=0 OR COALESCE(estado,'') IN ('cancelada','incidencia')"),
            "lectura_ok": "Hay consumo extra para medir TPV por pax y rentabilidad de bares.",
            "lectura_falta": "Sin comandas no sabemos si bar, piscina y extras ayudan al margen.",
            "accion": "Crear carta, cocktails, combinados, cafes, agua, refrescos y venta por segmento.",
        },
        {
            "modulo": "Finanzas y cierre",
            "icono": "chart-line",
            "principal": ("simulador_resultado_mensual", "corrida_id=%d" % corrida_id),
            "apoyos": [
                ("Cierre diario", "simulador_cierre_diario", "corrida_id=%d" % corrida_id),
                ("Diagnostico", "simulador_finanzas_diagnostico_mensual", "corrida_id=%d" % corrida_id),
                ("Plan elegido", "simulador_plan_accion_mensual_elegido", "corrida_id=%d" % corrida_id),
            ],
            "riesgo": ("simulador_resultado_mensual", "corrida_id=%d AND margen < 0" % corrida_id),
            "lectura_ok": "Ya hay cuenta mensual, cierre diario y propuestas con control de riesgo.",
            "lectura_falta": "Falta cierre financiero para saber por que se gana o se pierde.",
            "accion": "Mirar meses negativos, brecha diaria, fijos, laboral, compras y precio medio.",
        },
        {
            "modulo": "Cliente y experiencia",
            "icono": "face-smile",
            "principal": ("experiencia_quejas", "origen_modulo='simulador_vida'"),
            "apoyos": [
                ("Eventos", "simulador_vida_eventos", "corrida_id=%d AND modulo='experiencia'" % corrida_id),
                ("Diagnosticos", "simulador_vida_diagnosticos_dia", "corrida_id=%d" % corrida_id),
                ("Decisiones", "simulador_vida_decisiones", "corrida_id=%d" % corrida_id),
            ],
            "riesgo": ("experiencia_quejas", "origen_modulo='simulador_vida' AND COALESCE(estado,'') NOT IN ('cerrada','resuelta')"),
            "lectura_ok": "Hay quejas/eventos para conectar satisfaccion con margen.",
            "lectura_falta": "Faltan quejas, preferencias o valoraciones para medir fidelizacion.",
            "accion": "Simular reclamaciones, repetidores, VIP, familias, senior e incidencias de servicio.",
        },
    ]

    filas = []
    resumen = {"ok": 0, "vigilar": 0, "flojo": 0, "riesgos": 0, "total_movimiento": 0}
    for item in modulos:
        tabla, where = item["principal"]
        principal = _demo_table_count_where(conn, tabla, where)
        apoyos = []
        apoyos_ok = 0
        for nombre, tabla_apoyo, where_apoyo in item["apoyos"]:
            total = _demo_table_count_where(conn, tabla_apoyo, where_apoyo)
            apoyos.append({"nombre": nombre, "total": total})
            if total > 0:
                apoyos_ok += 1
        riesgo_tabla, riesgo_where = item["riesgo"]
        riesgos = _demo_table_count_where(conn, riesgo_tabla, riesgo_where)
        movimiento = principal + sum(a["total"] for a in apoyos)
        if principal > 0 and apoyos_ok >= 2:
            estado = "vivo"
        elif principal > 0 or apoyos_ok:
            estado = "vigilar"
        else:
            estado = "flojo"
        resumen["ok" if estado == "vivo" else estado] += 1
        resumen["riesgos"] += riesgos
        resumen["total_movimiento"] += movimiento
        filas.append({
            "modulo": item["modulo"],
            "icono": item["icono"],
            "estado": estado,
            "principal": principal,
            "apoyos": apoyos,
            "riesgos": riesgos,
            "movimiento": movimiento,
            "lectura": item["lectura_ok"] if estado == "vivo" else item["lectura_falta"],
            "accion": item["accion"],
        })
    resumen["total"] = len(filas)
    resumen["pct_vivo"] = round((resumen["ok"] / max(resumen["total"], 1)) * 100, 1)
    return {"resumen": resumen, "filas": filas}


def _demo_steps():
    return [
        {
            "grupo": "1. Direccion",
            "titulo": "Inicio ejecutivo",
            "detalle": "Vista rapida de ocupacion, ventas, compras, incidencias, RRHH y trazabilidad.",
            "url": "/",
            "modulo": "inicio",
        },
        {
            "grupo": "2. Reserva y cliente",
            "titulo": "Recepcion y reservas",
            "detalle": "Regimenes, habitaciones, check-in, viajeros, avisos y prevision de servicios.",
            "url": "/recepcion/",
            "modulo": "recepcion",
        },
        {
            "grupo": "3. Operacion diaria",
            "titulo": "Pisos y mantenimiento",
            "detalle": "Trabajos por habitacion/zona, lavanderia, inventario, partes y equipos.",
            "url": "/pisos/",
            "modulo": "pisos",
        },
        {
            "grupo": "4. Compra y stock",
            "titulo": "Compras, recepcion y almacenes",
            "detalle": "Pedidos, recepcion, lotes, caducidades, ubicaciones y validacion economica.",
            "url": "/compras/pedidos",
            "modulo": "compras",
        },
        {
            "grupo": "5. Cocina y trazabilidad",
            "titulo": "Produccion y APPCC",
            "detalle": "Menus, recetas, descongelacion, etiquetas, aprovechamiento y controles.",
            "url": "/produccion/semanal",
            "modulo": "produccion",
        },
        {
            "grupo": "6. Personas",
            "titulo": "RRHH, fichajes y seleccion",
            "detalle": "Cuadrantes, jornada, costes, portal empleado, ofertas y escandallo de CV.",
            "url": "/rrhh/seleccion",
            "modulo": "rrhh",
        },
        {
            "grupo": "7. Cliente",
            "titulo": "Experiencia y RRPP",
            "detalle": "Quejas, comprobaciones, preferencias y cliente 360 para fidelizacion.",
            "url": "/experiencia/",
            "modulo": "experiencia",
        },
        {
            "grupo": "8. Venta",
            "titulo": "TPV y puntos de venta",
            "detalle": "Terminales, mesas, accesos, buffet, suplementos y cargos.",
            "url": "/tpv/",
            "modulo": "tpv",
        },
        {
            "grupo": "9. Gestion",
            "titulo": "Control economico",
            "detalle": "Facturas, albaranes, terceros, vencimientos, pagos, impuestos y explotacion.",
            "url": "/contabilidad/",
            "modulo": "contabilidad",
        },
        {
            "grupo": "10. Copiloto",
            "titulo": "Asistente IA 360",
            "detalle": "Lee datos, detecta riesgos y propone mejoras sin decidir por el equipo.",
            "url": "/asistente/diario",
            "modulo": "ia",
        },
    ]


@demo_bp.route("/")
@login_required
@_require_demo_admin
def dashboard():
    return render_template(
        "demo/dashboard.html",
        estado=_simulador_estado(),
        steps=_demo_steps(),
    )


@demo_bp.route("/guia")
@login_required
@_require_demo_admin
def guia():
    return render_template("demo/guia.html", steps=_demo_steps(), estado=_simulador_estado())


@demo_bp.route("/sala-control")
@login_required
@_require_demo_admin
def sala_control():
    return render_template("demo/sala_control.html", estado=_vida_control_estado())


@demo_bp.route("/simulador-centros", methods=["GET"])
@login_required
@_require_demo_admin
def simulador_centros():
    return render_template("demo/simulador_centros.html", estado=_simulador_centros_estado())


@demo_bp.route("/simulador-centros/whatif", methods=["POST"])
@login_required
@_require_demo_admin
def simulador_centros_whatif():
    def num(name):
        try:
            return float((request.form.get(name) or "0").replace(",", "."))
        except Exception:
            return 0.0

    empresa_id = int(request.form.get("empresa_id") or 0)
    centro_id = int(request.form.get("centro_id") or 0)
    empresa_activa = _to_int(session.get("user_empresa_id") or session.get("empresa_id"), 1) or 1
    centro_activo = centro_activo_id()
    if empresa_id != empresa_activa or (centro_activo and centro_id != centro_activo):
        flash("Simulacion bloqueada: el centro no pertenece al contexto activo.", "warning")
        return redirect(url_for("demo.simulador_centros"))

    anio = int(request.form.get("anio") or 2027)
    nombre = (request.form.get("nombre") or "Escenario rapido").strip()[:120]
    ocupacion_delta = num("ocupacion_delta_pct")
    precio_delta = num("precio_delta_pct")
    coste_fijo_delta = num("coste_fijo_delta_pct")
    laboral_delta = num("laboral_delta_pct")

    conn = _real_conn()
    _ensure_simulador_centro_schema(conn)
    base = _presupuesto_base_centro(conn, empresa_id, centro_id, anio)
    factor_ingresos = (1 + ocupacion_delta / 100.0) * (1 + precio_delta / 100.0)
    ingresos_simulados = round(base["ingresos"] * factor_ingresos, 2)
    costes_fijos = base["costes_fijos"] * (1 + coste_fijo_delta / 100.0)
    personal = base["personal"] * (1 + laboral_delta / 100.0)
    gastos_simulados = round(costes_fijos + personal + base["otros_gastos"], 2)
    resultado_simulado = round(ingresos_simulados - gastos_simulados, 2)
    mejora = resultado_simulado - base["resultado"]
    if not base["presupuesto_id"]:
        lectura = "Falta presupuesto operativo del centro. Genera primero Contabilidad > Presupuesto."
    elif resultado_simulado < 0:
        lectura = f"Sigue en negativo. Mejora {mejora:,.2f} EUR frente a base, pero no cubre costes."
    else:
        lectura = f"Escenario positivo. Mejora {mejora:,.2f} EUR frente a base."
    conn.execute(
        """
        INSERT INTO simulador_whatif_centro
        (empresa_id, centro_id, anio, nombre, ocupacion_delta_pct, precio_delta_pct,
         coste_fijo_delta_pct, laboral_delta_pct, ingresos_base, gastos_base, resultado_base,
         ingresos_simulados, gastos_simulados, resultado_simulado, lectura, creado_por)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            empresa_id, centro_id, anio, nombre, ocupacion_delta, precio_delta,
            coste_fijo_delta, laboral_delta, base["ingresos"], base["gastos"], base["resultado"],
            ingresos_simulados, gastos_simulados, resultado_simulado, lectura, session.get("user_id"),
        ),
    )
    conn.commit()
    conn.close()
    flash("Escenario rapido calculado para el centro.", "success")
    return redirect(url_for("demo.simulador_centros"))


def _seguridad_herramientas_estado():
    from scripts.erp_herramientas_seguridad import health_report, list_backups, network_report

    estado = {
        "db": _db_info(VIDA_DB),
        "health": {
            "auditoria_seguridad": {
                "total": 0,
                "login_ok": 0,
                "login_fallido": 0,
                "herramientas": 0,
                "denegados": 0,
            },
            "entorno": {
                "secret_key_pruebas": False,
                "erp_host": "-",
                "erp_port": "-",
                "erp_cookie_secure": False,
            },
            "inventario_db": [],
            "issues": [],
            "score": 0,
            "estado": "sin_datos",
            "tablas": 0,
            "integrity_check": "-",
            "incidencias": 0,
            "criticas": 0,
            "altas": 0,
        },
        "network": {
            "puerto_responde": False,
            "hostname": "-",
            "erp_host": "-",
            "erp_port": "-",
            "ips_locales": [],
            "listeners_erp": [],
            "issues": [],
        },
        "backups": {"total": 0, "backups": []},
        "ultimos_backups": [],
        "ultimas_salud": [],
        "auditoria_seguridad": [],
        "reportes": [],
    }
    if not VIDA_DB.exists():
        return estado
    try:
        estado["health"] = health_report(VIDA_DB, 1, 3, persist=False)
    except Exception as exc:
        estado["health"] = {"estado": "error", "issues": [{"severidad": "critica", "titulo": "No se pudo leer salud", "detalle": str(exc)}]}
    auditoria = estado["health"].setdefault("auditoria_seguridad", {})
    for clave in ("total", "login_ok", "login_fallido", "herramientas", "denegados"):
        auditoria.setdefault(clave, 0)
    entorno = estado["health"].setdefault("entorno", {})
    for clave, valor in {
        "secret_key_pruebas": False,
        "erp_host": "-",
        "erp_port": "-",
        "erp_cookie_secure": False,
    }.items():
        entorno.setdefault(clave, valor)
    estado["health"].setdefault("inventario_db", [])
    estado["health"].setdefault("issues", [])
    for clave, valor in {
        "score": 0,
        "estado": "sin_datos",
        "tablas": 0,
        "integrity_check": "-",
        "incidencias": 0,
        "criticas": 0,
        "altas": 0,
    }.items():
        estado["health"].setdefault(clave, valor)
    try:
        estado["network"] = network_report()
    except Exception as exc:
        estado["network"] = {"issues": [{"severidad": "media", "titulo": "No se pudo leer red", "detalle": str(exc)}]}
    for clave, valor in {
        "puerto_responde": False,
        "hostname": "-",
        "erp_host": "-",
        "erp_port": "-",
        "ips_locales": [],
        "listeners_erp": [],
        "issues": [],
    }.items():
        estado["network"].setdefault(clave, valor)
    try:
        estado["backups"] = list_backups(ROOT / "backups" / "erp")
    except Exception as exc:
        estado["backups"] = {"total": 0, "backups": [], "error": str(exc)}
    conn = sqlite3.connect(VIDA_DB)
    conn.row_factory = sqlite3.Row
    try:
        if _demo_table_exists(conn, "erp_herramientas_backups"):
            estado["ultimos_backups"] = _fetch_all_dict(
                conn,
                """
                SELECT fecha, archivo, sha256, tamano_bytes, motivo, estado, detalle
                FROM erp_herramientas_backups
                ORDER BY id DESC
                LIMIT 10
                """,
            )
        if _demo_table_exists(conn, "erp_herramientas_salud"):
            estado["ultimas_salud"] = _fetch_all_dict(
                conn,
                """
                SELECT fecha, score, estado, incidencias, criticas, altas
                FROM erp_herramientas_salud
                ORDER BY id DESC
                LIMIT 10
                """,
            )
        if _demo_table_exists(conn, "erp_seguridad_auditoria"):
            estado["auditoria_seguridad"] = _fetch_all_dict(
                conn,
                """
                SELECT fecha, usuario_email, modulo, accion, resultado, ip, detalle
                FROM erp_seguridad_auditoria
                ORDER BY id DESC
                LIMIT 15
                """,
            )
    finally:
        conn.close()
    report_dir = ROOT / "publicaciones" / "el_raco"
    if report_dir.exists():
        estado["reportes"] = [
            {"nombre": p.name, "ruta": str(p), "modificado": datetime.fromtimestamp(p.stat().st_mtime).strftime("%d/%m/%Y %H:%M")}
            for p in sorted(report_dir.glob("erp_seguridad_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:10]
        ]
    return estado


@demo_bp.route("/seguridad-herramientas", methods=["GET", "POST"])
@login_required
@_require_demo_admin
def seguridad_herramientas():
    if request.method == "POST":
        accion = request.form.get("accion")
        motivo = (request.form.get("motivo") or "manual desde ERP").strip()
        try:
            from scripts.erp_herramientas_seguridad import health_report, make_backup, network_report

            if accion == "health":
                resultado = health_report(VIDA_DB, 1, 3, persist=True)
                _registrar_auditoria_seguridad(
                    "seguridad_health",
                    "ok",
                    modulo="seguridad_backups",
                    detalle={
                        "db": str(VIDA_DB),
                        "estado": resultado.get("estado"),
                        "score": resultado.get("score"),
                        "incidencias": resultado.get("incidencias"),
                    },
                )
                flash(f"Salud registrada: {resultado.get('estado')} score {resultado.get('score')}.", "success")
            elif accion == "snapshot":
                backup = make_backup(VIDA_DB, ROOT / "backups" / "erp", motivo, 1, 3)
                resultado = health_report(VIDA_DB, 1, 3, persist=True)
                _registrar_auditoria_seguridad(
                    "seguridad_snapshot",
                    "ok",
                    modulo="seguridad_backups",
                    detalle={
                        "db": str(VIDA_DB),
                        "backup": Path(backup["backup"]).name,
                        "motivo": motivo,
                        "estado": resultado.get("estado"),
                        "score": resultado.get("score"),
                    },
                )
                flash(f"Snapshot creado. Backup {Path(backup['backup']).name}; salud {resultado.get('estado')} score {resultado.get('score')}.", "success")
            elif accion == "network":
                resultado = network_report()
                _registrar_auditoria_seguridad(
                    "seguridad_network",
                    "ok",
                    modulo="seguridad_backups",
                    detalle={
                        "host": resultado.get("erp_host"),
                        "port": resultado.get("erp_port"),
                        "puerto_responde": resultado.get("puerto_responde"),
                        "incidencias": len(resultado.get("issues") or []),
                    },
                )
                flash(f"Diagnostico de red ejecutado. Puerto responde: {'si' if resultado.get('puerto_responde') else 'no'}.", "success")
            else:
                _registrar_auditoria_seguridad(
                    "seguridad_accion_no_permitida",
                    "denegado",
                    modulo="seguridad_backups",
                    detalle={"accion": accion},
                )
                flash("Accion no permitida.", "warning")
        except Exception as exc:
            _registrar_auditoria_seguridad(
                "seguridad_herramienta_error",
                "error",
                modulo="seguridad_backups",
                detalle={"accion": accion, "error": str(exc)[:500]},
            )
            flash(f"No se pudo ejecutar la herramienta: {exc}", "danger")
        return redirect(url_for("demo.seguridad_herramientas"))
    return render_template("demo/seguridad_herramientas.html", estado=_seguridad_herramientas_estado())


@demo_bp.route("/generar-vida", methods=["POST"])
@login_required
@_require_demo_admin
def generar_vida():
    duracion = request.form.get("duracion") if request.form.get("duracion") in {"9m", "12m"} else "9m"
    inicio = request.form.get("inicio") or ("2026-01-01" if duracion == "12m" else "2026-06-01")
    reset = request.form.get("reset") == "1"
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "simulador_vida_operativa_hotel1.py"),
        "--db",
        str(VIDA_DB),
        "--duracion",
        duracion,
        "--centro-id",
        "3",
        "--inicio",
        inicio,
    ]
    if reset:
        cmd.append("--reset")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    inicio_log = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    try:
        resultado = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=420,
        )
        VIDA_LOG_PATH.write_text(
            f"[{inicio_log}] {' '.join(cmd)}\n\nSTDOUT\n{resultado.stdout}\n\nSTDERR\n{resultado.stderr}\n",
            encoding="utf-8",
        )
        if resultado.returncode == 0:
            post_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "gestionar_jornada_operativa_simulacion.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            post = subprocess.run(
                post_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO JORNADA\n"
                    + " ".join(post_cmd)
                    + f"\n\nSTDOUT\n{post.stdout}\n\nSTDERR\n{post.stderr}\n"
                )
            brecha_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "resumir_brechas_stock_mise_simulacion.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            brecha = subprocess.run(
                brecha_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO BRECHAS STOCK\n"
                    + " ".join(brecha_cmd)
                    + f"\n\nSTDOUT\n{brecha.stdout}\n\nSTDERR\n{brecha.stderr}\n"
                )
            plan_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "planificar_compras_brechas_mise_simulacion.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            plan = subprocess.run(
                plan_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO PLAN COMPRAS MISE\n"
                    + " ".join(plan_cmd)
                    + f"\n\nSTDOUT\n{plan.stdout}\n\nSTDERR\n{plan.stderr}\n"
                )
            caja_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "evaluar_tesoreria_plan_compras_mise_simulacion.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            caja = subprocess.run(
                caja_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO TESORERIA MISE\n"
                    + " ".join(caja_cmd)
                    + f"\n\nSTDOUT\n{caja.stdout}\n\nSTDERR\n{caja.stderr}\n"
                )
            escenarios_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "simular_escenarios_mise_decision.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            escenarios = subprocess.run(
                escenarios_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO ESCENARIOS MISE\n"
                    + " ".join(escenarios_cmd)
                    + f"\n\nSTDOUT\n{escenarios.stdout}\n\nSTDERR\n{escenarios.stderr}\n"
                )
            rescate_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "simular_rescate_meses_criticos.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            rescate = subprocess.run(
                rescate_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO RESCATE MESES CRITICOS\n"
                    + " ".join(rescate_cmd)
                    + f"\n\nSTDOUT\n{rescate.stdout}\n\nSTDERR\n{rescate.stderr}\n"
                )
            guard_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "generar_guardarrailes_no_arruinarse.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            guard = subprocess.run(
                guard_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO GUARDARRAILES NO ARRUINARSE\n"
                    + " ".join(guard_cmd)
                    + f"\n\nSTDOUT\n{guard.stdout}\n\nSTDERR\n{guard.stderr}\n"
                )
            decision_fin_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "generar_decision_financiera_mensual.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            decision_fin = subprocess.run(
                decision_fin_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO DECISION FINANCIERA MENSUAL\n"
                    + " ".join(decision_fin_cmd)
                    + f"\n\nSTDOUT\n{decision_fin.stdout}\n\nSTDERR\n{decision_fin.stderr}\n"
                )
            stoploss_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "generar_stoploss_semanal_simulacion.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            stoploss = subprocess.run(
                stoploss_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO STOPLOSS SEMANAL\n"
                    + " ".join(stoploss_cmd)
                    + f"\n\nSTDOUT\n{stoploss.stdout}\n\nSTDERR\n{stoploss.stderr}\n"
                )
            escandallo_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "generar_escandallos_diarios_produccion.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            escandallo = subprocess.run(
                escandallo_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO ESCANDALLOS DIARIOS PRODUCCION\n"
                    + " ".join(escandallo_cmd)
                    + f"\n\nSTDOUT\n{escandallo.stdout}\n\nSTDERR\n{escandallo.stderr}\n"
                )
            rec_cocina_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "generar_recomendaciones_cocina_margen.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            rec_cocina = subprocess.run(
                rec_cocina_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO RECOMENDACIONES COCINA MARGEN\n"
                    + " ".join(rec_cocina_cmd)
                    + f"\n\nSTDOUT\n{rec_cocina.stdout}\n\nSTDERR\n{rec_cocina.stderr}\n"
                )
            impacto_cocina_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "generar_impacto_recomendaciones_cocina.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            impacto_cocina = subprocess.run(
                impacto_cocina_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO IMPACTO RECOMENDACIONES COCINA\n"
                    + " ".join(impacto_cocina_cmd)
                    + f"\n\nSTDOUT\n{impacto_cocina.stdout}\n\nSTDERR\n{impacto_cocina.stderr}\n"
                )
            plan_integral_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "generar_plan_integral_margen.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            plan_integral = subprocess.run(
                plan_integral_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO PLAN INTEGRAL MARGEN\n"
                    + " ".join(plan_integral_cmd)
                    + f"\n\nSTDOUT\n{plan_integral.stdout}\n\nSTDERR\n{plan_integral.stderr}\n"
                )
            auditoria_margen_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "auditar_cerebro_margen_simulacion.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            auditoria_margen = subprocess.run(
                auditoria_margen_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO AUDITORIA CEREBRO MARGEN\n"
                    + " ".join(auditoria_margen_cmd)
                    + f"\n\nSTDOUT\n{auditoria_margen.stdout}\n\nSTDERR\n{auditoria_margen.stderr}\n"
                )
            resumen_margen_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "generar_resumen_ejecutivo_margen.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            resumen_margen = subprocess.run(
                resumen_margen_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO RESUMEN EJECUTIVO MARGEN\n"
                    + " ".join(resumen_margen_cmd)
                    + f"\n\nSTDOUT\n{resumen_margen.stdout}\n\nSTDERR\n{resumen_margen.stderr}\n"
                )
            prl_appcc_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "simular_formacion_prl_appcc_hotel1.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            prl_appcc = subprocess.run(
                prl_appcc_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO FORMACION PRL APPCC\n"
                    + " ".join(prl_appcc_cmd)
                    + f"\n\nSTDOUT\n{prl_appcc.stdout}\n\nSTDERR\n{prl_appcc.stderr}\n"
                )
            calidad_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "auditar_calidad_simulacion.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            calidad = subprocess.run(
                calidad_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO AUDITORIA CALIDAD\n"
                    + " ".join(calidad_cmd)
                    + f"\n\nSTDOUT\n{calidad.stdout}\n\nSTDERR\n{calidad.stderr}\n"
                )
            acciones_prl_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "generar_acciones_prl_desde_auditoria.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            acciones_prl = subprocess.run(
                acciones_prl_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO ACCIONES PRL DESDE AUDITORIA\n"
                    + " ".join(acciones_prl_cmd)
                    + f"\n\nSTDOUT\n{acciones_prl.stdout}\n\nSTDERR\n{acciones_prl.stderr}\n"
                )
            acciones_alimentaria_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "generar_acciones_seguridad_alimentaria_desde_auditoria.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            acciones_alimentaria = subprocess.run(
                acciones_alimentaria_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO ACCIONES SEGURIDAD ALIMENTARIA DESDE AUDITORIA\n"
                    + " ".join(acciones_alimentaria_cmd)
                    + f"\n\nSTDOUT\n{acciones_alimentaria.stdout}\n\nSTDERR\n{acciones_alimentaria.stderr}\n"
                )
            vivienda_rrhh_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "generar_riesgo_vivienda_laboral.py"),
                "--db",
                str(VIDA_DB),
                "--centro-id",
                "3",
            ]
            vivienda_rrhh = subprocess.run(
                vivienda_rrhh_cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            with VIDA_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n\nPOSTPROCESO RIESGO VIVIENDA LABORAL\n"
                    + " ".join(vivienda_rrhh_cmd)
                    + f"\n\nSTDOUT\n{vivienda_rrhh.stdout}\n\nSTDERR\n{vivienda_rrhh.stderr}\n"
                )
            flash("Simulacion de vida operativa generada correctamente.", "success")
        else:
            flash("La simulacion de vida operativa termino con errores. Revisa el log.", "danger")
    except subprocess.TimeoutExpired:
        VIDA_LOG_PATH.write_text(f"[{inicio_log}] Timeout generando vida operativa.\n", encoding="utf-8")
        flash("La simulacion ha tardado demasiado. Revisa el log antes de relanzarla.", "warning")
    except Exception as exc:
        VIDA_LOG_PATH.write_text(f"[{inicio_log}] Error: {exc}\n", encoding="utf-8")
        flash(f"No se pudo generar la simulacion de vida: {exc}", "danger")
    return redirect(url_for("demo.sala_control"))


@demo_bp.route("/generar", methods=["POST"])
@login_required
@_require_demo_admin
def generar():
    habitaciones = max(10, min(400, _to_int(request.form.get("habitaciones"), 80)))
    dias = max(7, min(180, _to_int(request.form.get("dias"), 45)))
    ocupacion = max(0.1, min(0.98, _to_float(request.form.get("ocupacion"), 0.72)))
    reset = request.form.get("reset") == "1"

    cmd = [
        sys.executable,
        str(ROOT / "tools" / "simulador_hotel_desde_cero.py"),
        "--db",
        str(DEMO_DB),
        "--habitaciones",
        str(habitaciones),
        "--dias",
        str(dias),
        "--ocupacion",
        str(ocupacion),
    ]
    if reset:
        cmd.append("--reset")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    inicio = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    try:
        resultado = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=240,
        )
        LOG_PATH.write_text(
            f"[{inicio}] {' '.join(cmd)}\n\nSTDOUT\n{resultado.stdout}\n\nSTDERR\n{resultado.stderr}\n",
            encoding="utf-8",
        )
        if resultado.returncode == 0:
            flash("Demo generada correctamente. Ya puedes abrirla en el puerto 5002.", "success")
        else:
            flash("El simulador termino con errores. Revisa el log en la pantalla.", "danger")
    except subprocess.TimeoutExpired:
        LOG_PATH.write_text(f"[{inicio}] Timeout generando demo.\n", encoding="utf-8")
        flash("El simulador ha tardado demasiado. Revisa el log antes de relanzarlo.", "warning")
    except Exception as exc:
        LOG_PATH.write_text(f"[{inicio}] Error: {exc}\n", encoding="utf-8")
        flash(f"No se pudo generar la demo: {exc}", "danger")
    return redirect(url_for("demo.dashboard"))


@demo_bp.route("/abrir-servidor", methods=["POST"])
@login_required
@_require_demo_admin
def abrir_servidor():
    if not DEMO_DB.exists():
        flash("Primero genera la base demo.", "warning")
        return redirect(url_for("demo.dashboard"))
    if _port_open(DEMO_PORT):
        flash("La demo ya esta escuchando en el puerto 5002.", "info")
        return redirect(url_for("demo.dashboard"))
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["ERP_DATABASE"] = str(DEMO_DB)
    env["ERP_PORT"] = str(DEMO_PORT)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    log_file = SERVER_LOG_PATH.open("a", encoding="utf-8")
    subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=str(ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    flash("Servidor demo arrancando en http://127.0.0.1:5002", "success")
    return redirect(url_for("demo.dashboard"))


def _to_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        return default


def _to_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default
