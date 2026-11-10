# -*- coding: utf-8 -*-
"""Cruza fuentes externas de vivienda/personal con tension RRHH interna.

La salida no pretende adivinar causas. Marca riesgo cuando hay senal externa
validada y datos internos compatibles: fichajes rotos, extras, ausencias o
coste laboral alto. Si falta medicion interna, lo deja visible.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generar riesgo RRHH por vivienda/rotacion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--empresa-id", type=int, default=EMPRESA_ID)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def latest_corrida(conn: sqlite3.Connection) -> sqlite3.Row | None:
    if not table_exists(conn, "simulador_vida_corridas"):
        return None
    return conn.execute("SELECT * FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_rrhh_vivienda_laboral (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            mes TEXT,
            fuentes_externas INTEGER DEFAULT 0,
            confianza_media REAL DEFAULT 0,
            dias_rrhh INTEGER DEFAULT 0,
            dias_tension INTEGER DEFAULT 0,
            sin_fichar INTEGER DEFAULT 0,
            fichaje_sin_turno INTEGER DEFAULT 0,
            salidas_anticipadas INTEGER DEFAULT 0,
            extras_pendientes INTEGER DEFAULT 0,
            minutos_extra_pendientes INTEGER DEFAULT 0,
            horas_extra_nomina REAL DEFAULT 0,
            ausencias_horas REAL DEFAULT 0,
            coste_laboral REAL DEFAULT 0,
            coste_riesgo REAL DEFAULT 0,
            riesgo_pct REAL DEFAULT 0,
            nivel TEXT,
            lectura TEXT,
            acciones TEXT,
            datos_faltantes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(corrida_id, empresa_id, centro_id, mes)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_rrhh_vivienda_laboral_resumen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            fuentes_externas INTEGER DEFAULT 0,
            meses INTEGER DEFAULT 0,
            meses_rojo INTEGER DEFAULT 0,
            meses_ambar INTEGER DEFAULT 0,
            coste_riesgo REAL DEFAULT 0,
            coste_laboral REAL DEFAULT 0,
            confianza_media REAL DEFAULT 0,
            lectura TEXT,
            accion_prioritaria TEXT,
            datos_faltantes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(corrida_id, empresa_id, centro_id)
        )
        """
    )


def fuente_externa(conn: sqlite3.Connection, empresa_id: int) -> dict:
    if not table_exists(conn, "asistente_fuentes_conocimiento"):
        return {"total": 0, "confianza": 0.0}
    row = conn.execute(
        """
        SELECT COUNT(*) total, COALESCE(AVG(confianza_pct), 0) confianza
        FROM asistente_fuentes_conocimiento
        WHERE empresa_id=?
          AND estado IN ('procesada_contraste','validada','procesada')
          AND (
              COALESCE(lectura,'') LIKE '%vivienda_laboral%'
              OR COALESCE(resumen,'') LIKE '%vivienda%'
              OR COALESCE(resumen,'') LIKE '%habitacional%'
              OR COALESCE(resumen,'') LIKE '%garaje%'
              OR COALESCE(titulo,'') LIKE '%vivienda%'
              OR COALESCE(titulo,'') LIKE '%trabajadores%'
          )
        """,
        (empresa_id,),
    ).fetchone()
    return {"total": int(row["total"] or 0), "confianza": float(row["confianza"] or 0)}


def rrhh_mensual(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> dict[str, dict]:
    if not table_exists(conn, "simulador_rrhh_control_diario"):
        return {}
    rows = conn.execute(
        """
        SELECT substr(fecha, 1, 7) mes,
               COUNT(*) dias_rrhh,
               SUM(CASE WHEN nivel IN ('critica','alta') THEN 1 ELSE 0 END) dias_tension,
               SUM(sin_fichar) sin_fichar,
               SUM(fichaje_sin_turno) fichaje_sin_turno,
               SUM(salidas_anticipadas) salidas_anticipadas,
               SUM(extras_pendientes) extras_pendientes,
               SUM(minutos_extra_pendientes) minutos_extra_pendientes,
               SUM(coste_riesgo_estimado) coste_riesgo
        FROM simulador_rrhh_control_diario
        WHERE corrida_id=? AND centro_id=?
        GROUP BY substr(fecha, 1, 7)
        ORDER BY mes
        """,
        (corrida_id, centro_id),
    ).fetchall()
    return {row["mes"]: dict(row) for row in rows}


def prenomina_mensual(conn: sqlite3.Connection, empresa_id: int) -> dict[str, dict]:
    if not table_exists(conn, "resumen_prenomina"):
        return {}
    rows = conn.execute(
        """
        SELECT printf('%04d-%02d', anio, mes) mes,
               SUM(coste_estimado) coste_laboral,
               SUM(COALESCE(horas_extra_aprobadas,0) + COALESCE(horas_extra_no_aprobadas,0) + COALESCE(horas_extra,0)) horas_extra_nomina,
               SUM(COALESCE(horas_ausencia_retribuidas,0) + COALESCE(horas_ausencia_no_retribuidas,0) + COALESCE(ausencias_horas,0)) ausencias_horas
        FROM resumen_prenomina
        WHERE empresa_id=?
        GROUP BY anio, mes
        ORDER BY anio, mes
        """,
        (empresa_id,),
    ).fetchall()
    return {row["mes"]: dict(row) for row in rows}


def evaluar_mes(row: dict, nomina: dict, fuentes: dict) -> dict:
    dias = int(row.get("dias_rrhh") or 0)
    tension = int(row.get("dias_tension") or 0)
    sin_fichar = int(row.get("sin_fichar") or 0)
    sin_turno = int(row.get("fichaje_sin_turno") or 0)
    salidas = int(row.get("salidas_anticipadas") or 0)
    extras_pendientes = int(row.get("extras_pendientes") or 0)
    minutos_extra = int(row.get("minutos_extra_pendientes") or 0)
    coste_riesgo = float(row.get("coste_riesgo") or 0)
    horas_extra = float(nomina.get("horas_extra_nomina") or 0)
    ausencias = float(nomina.get("ausencias_horas") or 0)
    coste_laboral = float(nomina.get("coste_laboral") or 0)
    senal_externa = min(25, int(fuentes["total"]) * 7)
    tension_pct = (tension / dias) if dias else 0
    riesgo = 0
    riesgo += senal_externa
    riesgo += min(25, tension_pct * 35)
    riesgo += min(18, (sin_fichar + salidas) * 1.6)
    riesgo += min(14, sin_turno * 1.1)
    riesgo += min(10, extras_pendientes * 0.8 + minutos_extra / 480)
    riesgo += min(8, horas_extra / 80 + ausencias / 70)
    riesgo_pct = round(min(100, riesgo), 1)
    nivel = "rojo" if riesgo_pct >= 70 else "ambar" if riesgo_pct >= 45 else "controlado"
    datos_faltantes = [
        "motivo_baja_renuncia",
        "motivo_no_presentacion",
        "necesidad_alojamiento_empleado",
        "coste_alojamiento_plantilla",
        "tiempo_cobertura_vacante",
    ]
    if fuentes["total"] <= 0:
        lectura = "Sin fuentes externas procesadas sobre vivienda laboral. La lectura queda solo interna."
    elif nivel == "rojo":
        lectura = "Riesgo alto: la senal externa de vivienda/personal coincide con tension interna de RRHH."
    elif nivel == "ambar":
        lectura = "Vigilancia: hay senal externa y sintomas internos suficientes para preparar medidas."
    else:
        lectura = "Controlado: la senal externa existe, pero la operativa interna no muestra rotura fuerte."
    acciones = [
        "Medir bajas y renuncias por vivienda antes de culpar al mercado.",
        "Cruzar vacantes dificiles con fichajes rotos, extras y quejas por departamento.",
        "Simular coste de alojamiento o transporte frente a coste de rotacion.",
    ]
    return {
        "fuentes_externas": int(fuentes["total"]),
        "confianza_media": round(float(fuentes["confianza"] or 0), 1),
        "dias_rrhh": dias,
        "dias_tension": tension,
        "sin_fichar": sin_fichar,
        "fichaje_sin_turno": sin_turno,
        "salidas_anticipadas": salidas,
        "extras_pendientes": extras_pendientes,
        "minutos_extra_pendientes": minutos_extra,
        "horas_extra_nomina": round(horas_extra, 2),
        "ausencias_horas": round(ausencias, 2),
        "coste_laboral": round(coste_laboral, 2),
        "coste_riesgo": round(coste_riesgo, 2),
        "riesgo_pct": riesgo_pct,
        "nivel": nivel,
        "lectura": lectura,
        "acciones": " | ".join(acciones),
        "datos_faltantes": " | ".join(datos_faltantes),
    }


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        corrida = latest_corrida(conn)
        if not corrida:
            raise SystemExit("No hay corrida de simulacion.")
        corrida_id = args.corrida_id or int(corrida["id"])
        fuentes = fuente_externa(conn, args.empresa_id)
        rrhh = rrhh_mensual(conn, corrida_id, args.centro_id)
        nomina = prenomina_mensual(conn, args.empresa_id)
        meses = sorted(set(rrhh) | set(nomina))
        conn.execute(
            "DELETE FROM simulador_rrhh_vivienda_laboral WHERE corrida_id=? AND empresa_id=? AND centro_id=?",
            (corrida_id, args.empresa_id, args.centro_id),
        )
        resumen = {"meses": 0, "rojo": 0, "ambar": 0, "coste_riesgo": 0.0, "coste_laboral": 0.0}
        for mes in meses:
            data = evaluar_mes(rrhh.get(mes, {"mes": mes}), nomina.get(mes, {}), fuentes)
            data.update({"corrida_id": corrida_id, "empresa_id": args.empresa_id, "centro_id": args.centro_id, "mes": mes})
            conn.execute(
                """
                INSERT INTO simulador_rrhh_vivienda_laboral
                (corrida_id, empresa_id, centro_id, mes, fuentes_externas, confianza_media,
                 dias_rrhh, dias_tension, sin_fichar, fichaje_sin_turno, salidas_anticipadas,
                 extras_pendientes, minutos_extra_pendientes, horas_extra_nomina, ausencias_horas,
                 coste_laboral, coste_riesgo, riesgo_pct, nivel, lectura, acciones, datos_faltantes)
                VALUES (:corrida_id, :empresa_id, :centro_id, :mes, :fuentes_externas, :confianza_media,
                        :dias_rrhh, :dias_tension, :sin_fichar, :fichaje_sin_turno, :salidas_anticipadas,
                        :extras_pendientes, :minutos_extra_pendientes, :horas_extra_nomina, :ausencias_horas,
                        :coste_laboral, :coste_riesgo, :riesgo_pct, :nivel, :lectura, :acciones, :datos_faltantes)
                """,
                data,
            )
            resumen["meses"] += 1
            resumen["rojo"] += 1 if data["nivel"] == "rojo" else 0
            resumen["ambar"] += 1 if data["nivel"] == "ambar" else 0
            resumen["coste_riesgo"] += data["coste_riesgo"]
            resumen["coste_laboral"] += data["coste_laboral"]
        lectura = "Sin meses evaluables de RRHH."
        accion = "Generar primero turnos, fichajes y pre-nomina."
        if resumen["meses"]:
            if resumen["rojo"]:
                lectura = "Hay meses donde vivienda/personal puede estar afectando estabilidad y coste."
                accion = "Medir motivo de baja/renuncia y simular alojamiento, transporte o retencion."
            elif resumen["ambar"]:
                lectura = "Hay senales preventivas. Conviene actuar antes de que suba la rotacion."
                accion = "Preparar indicadores de vivienda y cobertura de vacantes por departamento."
            else:
                lectura = "No hay rotura fuerte, pero la senal externa debe seguir monitorizada."
                accion = "Mantener seguimiento mensual y completar datos faltantes."
        conn.execute(
            """
            INSERT INTO simulador_rrhh_vivienda_laboral_resumen
            (corrida_id, empresa_id, centro_id, fuentes_externas, meses, meses_rojo,
             meses_ambar, coste_riesgo, coste_laboral, confianza_media, lectura,
             accion_prioritaria, datos_faltantes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(corrida_id, empresa_id, centro_id) DO UPDATE SET
                fuentes_externas=excluded.fuentes_externas,
                meses=excluded.meses,
                meses_rojo=excluded.meses_rojo,
                meses_ambar=excluded.meses_ambar,
                coste_riesgo=excluded.coste_riesgo,
                coste_laboral=excluded.coste_laboral,
                confianza_media=excluded.confianza_media,
                lectura=excluded.lectura,
                accion_prioritaria=excluded.accion_prioritaria,
                datos_faltantes=excluded.datos_faltantes,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                corrida_id,
                args.empresa_id,
                args.centro_id,
                int(fuentes["total"]),
                resumen["meses"],
                resumen["rojo"],
                resumen["ambar"],
                round(resumen["coste_riesgo"], 2),
                round(resumen["coste_laboral"], 2),
                round(float(fuentes["confianza"] or 0), 1),
                lectura,
                accion,
                "motivo_baja_renuncia | motivo_no_presentacion | necesidad_alojamiento_empleado | coste_alojamiento_plantilla | tiempo_cobertura_vacante",
            ),
        )
        conn.commit()
        print(
            "Riesgo vivienda laboral: "
            f"{resumen['meses']} meses, {resumen['rojo']} rojos, "
            f"{resumen['ambar']} ambar, fuentes {fuentes['total']}."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
