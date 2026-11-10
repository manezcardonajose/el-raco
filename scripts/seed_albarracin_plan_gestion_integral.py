# -*- coding: utf-8 -*-
"""Crea checklist de gestion integral para Hotel Albarracin."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "pepe4.db"
EMPRESA_ID = 2
CENTRO_ID = 8


TASKS = [
    ("Auditoria inicial", "Finanzas", "Revisar balances, deudas, cobros pendientes, pagos pendientes y compromisos.", "alta", "Direccion", 1, 30),
    ("Auditoria inicial", "Finanzas", "Separar ingresos por segmento: Imserso, directo, OTA, grupos, eventos y restaurante.", "alta", "Direccion / Revenue", 1, 30),
    ("Auditoria inicial", "Finanzas", "Medir ADR, RevPAR, ocupacion, GOP estimado, coste laboral, coste comida y suministros.", "alta", "Direccion / Administracion", 1, 30),
    ("Auditoria inicial", "Tecnica", "Inspeccion de habitaciones, zonas comunes, cocina, restaurante, cafeteria y mantenimiento.", "alta", "Mantenimiento", 1, 30),
    ("Auditoria inicial", "Tecnica", "Revisar licencias, PCI, legionella, APPCC, PRL y accesibilidad.", "critica", "Direccion / PRL / APPCC", 1, 30),
    ("Auditoria inicial", "Inventario", "Registrar activos, lenceria, menaje, maquinaria, productos y stocks iniciales.", "alta", "Economato / Pisos / Cocina", 1, 30),
    ("Auditoria inicial", "Tecnologia", "Revisar PMS/ERP, TPV, channel manager, pasarelas de pago, backups, red y correo.", "alta", "Direccion / Sistemas", 1, 30),
    ("Legalizacion", "Contrato", "Definir management agreement: fijo, variable por GOP, limites de gasto y reporting.", "critica", "Propiedad / Direccion", 31, 60),
    ("Legalizacion", "Poderes", "Preparar poderes operativos para bancos, contratos, proveedores y representacion.", "critica", "Propiedad / Legal", 31, 60),
    ("Legalizacion", "Terceros", "Notificar cambio de gestion a proveedores, OTAs, pasarelas y agencias.", "alta", "Administracion", 31, 60),
    ("Legalizacion", "Compras", "Revisar condiciones de proveedores: tarifas, dias de entrega, vencimientos y alternativas.", "alta", "Compras / Direccion", 31, 60),
    ("Talento", "RRHH", "Entrevistar equipo actual y detectar perfiles clave y riesgo de salida.", "alta", "Direccion / RRHH", 1, 45),
    ("Talento", "RRHH", "Revisar cuadrantes, horas reales, fichajes, absentismo y productividad.", "alta", "RRHH / Direccion", 1, 45),
    ("Operaciones", "SOP", "Crear SOP recepcion: check-in, cobros, documentacion, viajeros, quejas y upselling.", "media", "Recepcion", 15, 75),
    ("Operaciones", "SOP", "Crear SOP pisos: limpieza, lenceria, bloqueos, mantenimiento y objetos olvidados.", "media", "Gobernanta / Pisos", 15, 75),
    ("Operaciones", "SOP", "Crear SOP cocina: produccion, escandallos, APPCC, mermas, pedidos y reutilizacion segura.", "alta", "Cocina", 15, 75),
    ("Operaciones", "SOP", "Crear SOP sala/cafeteria: servicio, reservas, carta, caja y control de consumos.", "media", "Sala", 15, 75),
    ("Operaciones", "Mantenimiento", "Plan preventivo: habitaciones, legionella, electricidad, fontaneria y maquinaria critica.", "alta", "Mantenimiento", 15, 90),
    ("Revenue", "Mercado", "Analizar competencia, temporadas, segmentos y buyer persona.", "alta", "Revenue / Direccion", 15, 60),
    ("Revenue", "Precios", "Definir tarifas por segmento: Imserso, AD, Semana Santa, verano, fines de semana y grupos.", "alta", "Revenue", 15, 60),
    ("Revenue", "Canales", "Potenciar venta directa y controlar comisiones OTA.", "media", "Revenue / Recepcion", 30, 90),
    ("Restauracion", "Imserso", "Controlar presupuesto comida por pax/dia y compararlo contra coste real.", "critica", "Cocina / Direccion", 1, 365),
    ("Restauracion", "Fines de semana", "Probar cenas de fin de semana con cupo, reserva previa y menu cerrado.", "media", "Sala / Cocina", 30, 180),
    ("Reporting", "Propiedad", "Emitir reporte mensual: ocupacion, ADR, RevPAR, ingresos, costes, GOP, quejas y CapEx.", "alta", "Direccion", 30, 365),
    ("Reporting", "CapEx", "Separar urgencias de inversion estrategica y documentar impacto esperado.", "alta", "Direccion / Propiedad", 1, 365),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed plan gestion integral Albarracin.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=EMPRESA_ID)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plan_gestion_integral_hotel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER,
            centro_id INTEGER,
            fase TEXT,
            area TEXT,
            tarea TEXT,
            prioridad TEXT DEFAULT 'media',
            responsable TEXT,
            dia_inicio INTEGER DEFAULT 1,
            dia_limite INTEGER DEFAULT 30,
            estado TEXT DEFAULT 'pendiente',
            avance_pct REAL DEFAULT 0,
            evidencia TEXT,
            observaciones TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(empresa_id, centro_id, fase, area, tarea)
        )
        """
    )


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        for fase, area, tarea, prioridad, responsable, inicio, limite in TASKS:
            conn.execute(
                """
                INSERT INTO plan_gestion_integral_hotel
                (empresa_id, centro_id, fase, area, tarea, prioridad, responsable,
                 dia_inicio, dia_limite, estado, avance_pct, observaciones)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', 0, ?)
                ON CONFLICT(empresa_id, centro_id, fase, area, tarea)
                DO UPDATE SET prioridad=excluded.prioridad,
                    responsable=excluded.responsable,
                    dia_inicio=excluded.dia_inicio,
                    dia_limite=excluded.dia_limite,
                    observaciones=excluded.observaciones,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    args.empresa_id,
                    args.centro_id,
                    fase,
                    area,
                    tarea,
                    prioridad,
                    responsable,
                    inicio,
                    limite,
                    "Plan base para toma de gestion integral Hotel Albarracin.",
                ),
            )
        conn.commit()
        print({"tareas_plan": len(TASKS)})
    finally:
        conn.close()


if __name__ == "__main__":
    main()
