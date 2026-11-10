# -*- coding: utf-8 -*-
"""
Simulador de vida operativa para Hotel 1.

Trabaja siempre sobre una base de laboratorio copiada desde pepe4.db, nunca
sobre la base real. Genera una campana larga con reservas, viajeros, quejas,
averias, APPCC, compras con fallos, pisos, fichajes y vacaciones/parada tecnica.
"""

from __future__ import annotations

import argparse
import calendar
import json
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from simulador_hotel_desde_cero import (  # noqa: E402
    EMPRESA_ID,
    REAL_DB,
    columns,
    connect,
    dinero,
    filter_values,
    insert_row,
    reset_db,
    safe_db_path,
    table_exists,
    upsert_by_keys,
)


DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
OUT_PATH = ROOT / "publicaciones" / "el_raco" / "simulacion_vida_hotel1_resumen.json"

GASTOS_FIJOS_MENSUALES_HOTEL1 = [
    ("electricidad", "Electricidad y energia", 18500),
    ("agua", "Agua, saneamiento y piscina", 4800),
    ("seguros", "Seguros multirriesgo y RC", 3200),
    ("ibi", "IBI y tributos municipales", 6100),
    ("tasas", "Basuras, terrazas, licencias y tasas", 2100),
    ("telecom", "Telefonia, internet, TV y sistemas", 1800),
    ("mantenimiento_contratado", "Contratos mantenimiento, ascensores y climatizacion", 9400),
    ("asesoria", "Asesoria, legal, laboral y fiscal", 2600),
    ("software", "Software, PMS, channel manager y pasarelas", 2400),
    ("seguridad", "Seguridad, PRL y vigilancia tecnica", 1900),
]

SEGMENTOS = [
    {
        "nombre": "familias",
        "peso": 32,
        "nacionalidades": ["ES", "FR", "GB", "DE"],
        "adultos": [2, 2, 2],
        "menores": [1, 1, 2, 0],
        "regimenes": ["media_pension", "pension_completa", "todo_incluido"],
        "adr": 128,
        "canales": ["web", "booking", "tour_operador"],
    },
    {
        "nombre": "imserso",
        "peso": 16,
        "nacionalidades": ["ES"],
        "adultos": [2, 2, 1],
        "menores": [0],
        "regimenes": ["pension_completa"],
        "adr": 74,
        "canales": ["tour_operador", "agencia"],
    },
    {
        "nombre": "fin_de_curso",
        "peso": 10,
        "nacionalidades": ["ES", "FR"],
        "adultos": [2, 3, 4],
        "menores": [0, 0, 1],
        "regimenes": ["media_pension", "pension_completa"],
        "adr": 92,
        "canales": ["grupo", "agencia"],
    },
    {
        "nombre": "vip",
        "peso": 8,
        "nacionalidades": ["ES", "GB", "DE", "IT"],
        "adultos": [2, 2],
        "menores": [0, 0, 1],
        "regimenes": ["alojamiento_desayuno", "media_pension"],
        "adr": 214,
        "canales": ["web", "directo"],
    },
    {
        "nombre": "repetidores",
        "peso": 18,
        "nacionalidades": ["ES", "GB", "DE"],
        "adultos": [2, 2, 1],
        "menores": [0, 0, 1],
        "regimenes": ["media_pension", "todo_incluido"],
        "adr": 116,
        "canales": ["directo", "web"],
    },
    {
        "nombre": "jovenes_evento",
        "peso": 8,
        "nacionalidades": ["ES", "IT", "FR"],
        "adultos": [2, 3],
        "menores": [0],
        "regimenes": ["solo_alojamiento", "alojamiento_desayuno"],
        "adr": 88,
        "canales": ["grupo", "booking"],
    },
    {
        "nombre": "corporativo",
        "peso": 8,
        "nacionalidades": ["ES", "GB", "DE"],
        "adultos": [1, 1, 2],
        "menores": [0],
        "regimenes": ["alojamiento_desayuno"],
        "adr": 136,
        "canales": ["empresa", "directo"],
    },
]

NOMBRES = [
    "Juan", "Maria", "Jose", "Laura", "Pablo", "Ana", "Carlos", "Fisa",
    "Sofia", "Hugo", "Lucia", "Marta", "Marc", "Elena", "Hans", "Emma",
    "Giulia", "Luca", "Claire", "Oliver",
]
APELLIDOS = [
    "Sanchez", "Garcia", "Camacho", "Sancho", "Jimenez", "Lopez",
    "Medina", "Martin", "Cardona", "Herrera", "Muller", "Smith",
    "Rossi", "Dubois", "Perez", "Navarro",
]

BAR_CATEGORIAS = [
    ("Aguas", "bebidas", "#0ea5e9"),
    ("Refrescos", "bebidas", "#2563eb"),
    ("Zumos naturales", "zumos", "#22c55e"),
    ("Cafes e infusiones", "cafeteria", "#92400e"),
    ("Cervezas", "cervezas", "#f59e0b"),
    ("Vinos y espumosos", "vinos", "#7c2d12"),
    ("Cocteles", "cocteles", "#db2777"),
    ("Combinados", "combinados", "#4f46e5"),
    ("Brandis y licores", "licores", "#991b1b"),
    ("Snacks bar", "snacks", "#16a34a"),
]

BAR_ARTICULOS = [
    ("Agua 0.5L", "Aguas", 1.80, "producto"),
    ("Agua 1.5L", "Aguas", 3.00, "producto"),
    ("Agua con gas", "Aguas", 2.20, "producto"),
    ("Coca Cola", "Refrescos", 2.80, "producto"),
    ("Fanta naranja", "Refrescos", 2.80, "producto"),
    ("Sprite", "Refrescos", 2.80, "producto"),
    ("Nestea limon", "Refrescos", 3.00, "producto"),
    ("Zumo natural naranja", "Zumos naturales", 4.50, "elaborado"),
    ("Zumo detox verde", "Zumos naturales", 5.20, "elaborado"),
    ("Zumo sandia y lima", "Zumos naturales", 5.00, "elaborado"),
    ("Cafe solo", "Cafes e infusiones", 1.60, "producto"),
    ("Cafe con leche", "Cafes e infusiones", 2.00, "producto"),
    ("Cappuccino", "Cafes e infusiones", 2.80, "producto"),
    ("Infusion", "Cafes e infusiones", 2.20, "producto"),
    ("Cerveza cana", "Cervezas", 2.50, "producto"),
    ("Cerveza pinta", "Cervezas", 4.20, "producto"),
    ("Cerveza sin alcohol", "Cervezas", 3.00, "producto"),
    ("Copa vino blanco", "Vinos y espumosos", 4.00, "producto"),
    ("Copa vino tinto", "Vinos y espumosos", 4.00, "producto"),
    ("Copa cava", "Vinos y espumosos", 4.80, "producto"),
    ("Mojito", "Cocteles", 8.50, "elaborado"),
    ("Caipirinha", "Cocteles", 8.50, "elaborado"),
    ("Pina colada", "Cocteles", 8.90, "elaborado"),
    ("Daiquiri fresa", "Cocteles", 8.90, "elaborado"),
    ("Aperol spritz", "Cocteles", 8.00, "elaborado"),
    ("Gin tonic nacional", "Combinados", 8.50, "producto"),
    ("Gin tonic premium", "Combinados", 11.50, "producto"),
    ("Ron cola", "Combinados", 8.50, "producto"),
    ("Whisky cola", "Combinados", 8.80, "producto"),
    ("Vodka limon", "Combinados", 8.50, "producto"),
    ("Brandy", "Brandis y licores", 6.00, "producto"),
    ("Licor hierbas", "Brandis y licores", 4.50, "producto"),
    ("Baileys", "Brandis y licores", 5.20, "producto"),
    ("Sandwich mixto", "Snacks bar", 5.50, "elaborado"),
    ("Nachos con queso", "Snacks bar", 7.50, "elaborado"),
    ("Patatas chips", "Snacks bar", 2.50, "producto"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simula una campana operativa larga para Hotel 1.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Base de laboratorio destino.")
    parser.add_argument('--origen', default=str(REAL_DB), help='Base existente desde la que copiar el laboratorio; nunca se modifica.')
    parser.add_argument("--reset", action="store_true", help="Recrea la base de laboratorio desde pepe4.db.")
    parser.add_argument("--duracion", choices=["9m", "12m"], default="9m", help="9 meses de temporada o 12 meses con parada tecnica.")
    parser.add_argument("--centro-id", type=int, default=3, help="Centro/hotel a simular. Hotel 1 = 3.")
    parser.add_argument("--inicio", default=None, help="Fecha inicio YYYY-MM-DD. Por defecto 2026-06-01.")
    parser.add_argument("--seed", type=int, default=20260526, help="Semilla aleatoria.")
    return parser.parse_args()


def clone_real_db(target: Path, source: Path = REAL_DB) -> None:
    source = source.resolve()
    target = target.resolve()
    if source == target:
        raise RuntimeError('Origen y laboratorio deben ser bases distintas.')
    if not source.is_file():
        raise RuntimeError(f'No existe la base origen: {source}. Indicala con --origen; no se creara una base vacia.')
    src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    try:
        requeridas = {'habitaciones_recepcion', 'recepcion_tipos_habitacion', 'reservas_recepcion'}
        faltantes = sorted(t for t in requeridas if not table_exists(src, t))
        if faltantes:
            raise RuntimeError('Base origen incompleta. Faltan tablas: ' + ', '.join(faltantes))
        reset_db(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def ensure_trace_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_vida_corridas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT,
            fecha_inicio TEXT,
            fecha_fin TEXT,
            duracion TEXT,
            centro_id INTEGER,
            empresa_id INTEGER,
            config_json TEXT,
            resumen_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_vida_eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            fecha_simulada TEXT,
            modulo TEXT,
            tipo TEXT,
            severidad TEXT,
            descripcion TEXT,
            tabla_ref TEXT,
            registro_id INTEGER,
            impacto_json TEXT,
            empresa_id INTEGER,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_buffet_etiquetas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            fecha TEXT,
            servicio TEXT,
            receta_id INTEGER,
            nombre_plato TEXT,
            estacion_buffet TEXT,
            alergenos TEXT,
            idiomas_json TEXT,
            estado TEXT,
            incidencia TEXT,
            empresa_id INTEGER,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_inventario_conteos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            fecha TEXT,
            articulo_id INTEGER,
            cantidad_sistema REAL,
            cantidad_fisica REAL,
            desviacion REAL,
            motivo TEXT,
            empresa_id INTEGER,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_vida_decisiones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            modulo TEXT,
            prioridad TEXT,
            titulo TEXT,
            diagnostico TEXT,
            decision_recomendada TEXT,
            impacto_estimado TEXT,
            datos_soporte_json TEXT,
            estado TEXT DEFAULT 'pendiente',
            empresa_id INTEGER,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_contexto_externo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            fecha_inicio TEXT,
            fecha_fin TEXT,
            tipo TEXT,
            titulo TEXT,
            intensidad REAL,
            descripcion TEXT,
            impacto_previsto_json TEXT,
            empresa_id INTEGER,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_vida_kpis_diarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            fecha TEXT,
            habitaciones_ocupadas INTEGER,
            pax INTEGER,
            ingresos_habitaciones REAL,
            ingresos_tpv REAL,
            coste_laboral_estimado REAL,
            coste_compras_estimado REAL,
            coste_fijo_estimado REAL DEFAULT 0,
            coste_total_estimado REAL DEFAULT 0,
            margen_estimado REAL,
            quejas INTEGER,
            ordenes_mantenimiento INTEGER,
            no_conformidades_appcc INTEGER,
            alertas_altas INTEGER,
            riesgo_operativo REAL,
            lectura TEXT,
            empresa_id INTEGER,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in ["coste_fijo_estimado REAL DEFAULT 0", "coste_total_estimado REAL DEFAULT 0"]:
        name = col.split()[0]
        if name not in columns(conn, "simulador_vida_kpis_diarios"):
            conn.execute(f"ALTER TABLE simulador_vida_kpis_diarios ADD COLUMN {col}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_costes_fijos_mensuales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            centro_id INTEGER DEFAULT 3,
            mes TEXT,
            concepto TEXT,
            descripcion TEXT,
            importe_mensual REAL DEFAULT 0,
            tipo TEXT DEFAULT 'estructura',
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, mes, concepto)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_resultado_mensual (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            centro_id INTEGER DEFAULT 3,
            mes TEXT,
            dias_calendario INTEGER DEFAULT 0,
            dias_simulados INTEGER DEFAULT 0,
            dias_negativos INTEGER DEFAULT 0,
            ingresos_total REAL DEFAULT 0,
            coste_laboral REAL DEFAULT 0,
            coste_compras REAL DEFAULT 0,
            coste_fijo REAL DEFAULT 0,
            coste_total REAL DEFAULT 0,
            margen REAL DEFAULT 0,
            precio_medio_habitacion REAL DEFAULT 0,
            punto_muerto_diario REAL DEFAULT 0,
            lectura TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(corrida_id, centro_id, mes)
        )
        """
    )


def log_event(
    conn: sqlite3.Connection,
    corrida_id: int,
    fecha_simulada: date | str,
    modulo: str,
    tipo: str,
    severidad: str,
    descripcion: str,
    tabla_ref: str | None = None,
    registro_id: int | None = None,
    impacto: dict | None = None,
    centro_id: int = 3,
) -> None:
    fecha_txt = fecha_simulada.isoformat() if isinstance(fecha_simulada, date) else str(fecha_simulada)
    insert_row(
        conn,
        "simulador_vida_eventos",
        {
            "corrida_id": corrida_id,
            "fecha_simulada": fecha_txt,
            "modulo": modulo,
            "tipo": tipo,
            "severidad": severidad,
            "descripcion": descripcion,
            "tabla_ref": tabla_ref,
            "registro_id": registro_id,
            "impacto_json": json.dumps(impacto or {}, ensure_ascii=False),
            "empresa_id": EMPRESA_ID,
            "centro_id": centro_id,
        },
    )


def date_add_months(base: date, months: int) -> date:
    month = base.month - 1 + months
    year = base.year + month // 12
    month = month % 12 + 1
    day = min(base.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def campaign_plan(start: date, duracion: str) -> tuple[date, list[dict], tuple[date, date] | None]:
    months = 12 if duracion == "12m" else 9
    occupancies = [1.00, 0.90, 0.45, 0.68, 0.82, 0.96, 0.88, 0.73, 0.56, 0.62, 0.70, 0.84]
    names = [
        "arranque completo", "normal 90", "caida 45", "recuperacion",
        "verano fuerte", "pico demanda", "post pico", "normalizacion",
        "baja tension", "invierno activo", "puente y grupos", "cierre fuerte",
    ]
    scenarios = []
    for idx in range(months):
        inicio = date_add_months(start, idx)
        fin = date_add_months(start, idx + 1)
        scenarios.append(
            {
                "mes": idx + 1,
                "nombre": names[idx],
                "inicio": inicio,
                "fin": fin,
                "ocupacion": occupancies[idx],
            }
        )
    parada = None
    if duracion == "12m":
        parada_fin = scenarios[-1]["fin"]
        parada_inicio = parada_fin - timedelta(days=15)
        parada = (parada_inicio, parada_fin)
    return scenarios[-1]["fin"], scenarios, parada


def scenario_for(day: date, scenarios: list[dict]) -> dict:
    for scenario in scenarios:
        if scenario["inicio"] <= day < scenario["fin"]:
            return scenario
    return scenarios[-1]


def weighted_segment() -> dict:
    return random.choices(SEGMENTOS, weights=[s["peso"] for s in SEGMENTOS], k=1)[0]


def rand_name() -> tuple[str, str]:
    return random.choice(NOMBRES), f"{random.choice(APELLIDOS)} {random.choice(APELLIDOS)}"


def get_rooms(conn: sqlite3.Connection, centro_id: int) -> list[sqlite3.Row]:
    if 'centro_id' not in columns(conn, 'habitaciones_recepcion'):
        raise RuntimeError('Las habitaciones requieren centro_id antes de simular.')
    centro_filter = 'AND h.centro_id=?'
    params: list = [EMPRESA_ID]
    if centro_filter:
        params.append(centro_id)
    return conn.execute(
        f"""
        SELECT h.*, th.codigo AS tipo_codigo
        FROM habitaciones_recepcion h
        LEFT JOIN recepcion_tipos_habitacion th ON th.id=h.tipo_habitacion_id
        WHERE h.empresa_id=? AND COALESCE(h.activo,1)=1 {centro_filter}
        ORDER BY h.numero
        """,
        params,
    ).fetchall()


def get_canales(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    if not table_exists(conn, "recepcion_canales_reserva"):
        return []
    return conn.execute(
        "SELECT id, codigo, comision_pct FROM recepcion_canales_reserva WHERE empresa_id=? AND COALESCE(activo,1)=1",
        (EMPRESA_ID,),
    ).fetchall()


def pick_canal(canales: list[sqlite3.Row], segment: dict) -> sqlite3.Row | None:
    if not canales:
        return None
    preferred = [c for c in canales if str(c["codigo"] or "").lower() in segment["canales"]]
    return random.choice(preferred or canales)


def is_in_stop(day: date, parada: tuple[date, date] | None) -> bool:
    return bool(parada and parada[0] <= day < parada[1])


def generate_reservations(
    conn: sqlite3.Connection,
    corrida_id: int,
    centro_id: int,
    start: date,
    end: date,
    scenarios: list[dict],
    parada: tuple[date, date] | None,
) -> tuple[int, Counter]:
    rooms = get_rooms(conn, centro_id)
    canales = get_canales(conn)
    if not rooms:
        return 0, Counter()

    used: dict[int, list[tuple[date, date]]] = {int(room["id"]): [] for room in rooms}
    created = 0
    segment_counter: Counter = Counter()
    day = start
    while day < end:
        scenario = scenario_for(day, scenarios)
        if is_in_stop(day, parada):
            day += timedelta(days=1)
            continue
        target_rooms = int(len(rooms) * float(scenario["ocupacion"]))
        already = sum(1 for room in rooms for s, e in used[int(room["id"])] if s <= day < e)
        to_create = max(0, target_rooms - already)
        free_rooms = [
            room for room in rooms
            if not any(s <= day < e for s, e in used[int(room["id"])])
        ]
        random.shuffle(free_rooms)
        for room in free_rooms[:to_create]:
            segment = weighted_segment()
            nights = random.randint(2, 7)
            if segment["nombre"] in {"imserso", "fin_de_curso"}:
                nights = random.randint(5, 14)
            salida = min(day + timedelta(days=nights), end)
            if parada and day < parada[0] < salida:
                salida = parada[0]
            if salida <= day:
                continue
            room_id = int(room["id"])
            if any(not (salida <= s or day >= e) for s, e in used[room_id]):
                continue
            canal = pick_canal(canales, segment)
            adults = min(int(room["capacidad"] or 2), random.choice(segment["adultos"]))
            minors = max(0, min(int(room["capacidad"] or 2) - adults, random.choice(segment["menores"])))
            pax = max(1, adults + minors)
            regimen = random.choice(segment["regimenes"])
            adr = segment["adr"] * random.uniform(0.86, 1.24)
            if float(scenario["ocupacion"]) >= 0.95:
                adr *= 1.12
            if float(scenario["ocupacion"]) <= 0.50:
                adr *= 0.82
            total = dinero(max(1, (salida - day).days) * adr)
            nombre, apellidos = rand_name()
            created += 1
            codigo = f"VIDA-H1-{day.strftime('%Y%m%d')}-{created:05d}"
            estado = "checkout" if salida < date.today() else "alojado" if day <= date.today() < salida else "confirmada"
            values = {
                "codigo": codigo,
                "habitacion_id": room_id,
                "tipo_habitacion_id": room["tipo_habitacion_id"],
                "centro_id": centro_id,
                "fecha_entrada": day.isoformat(),
                "fecha_salida": salida.isoformat(),
                "hora_llegada": random.choice(["12:30", "14:00", "16:30", "19:00"]),
                "hora_salida": "11:00",
                "regimen": regimen,
                "adultos": adults,
                "menores": minors,
                "estado": estado,
                "origen": (canal["codigo"].lower() if canal else random.choice(segment["canales"])),
                "titular_nombre": f"{nombre} {apellidos}",
                "titular_email": f"vida{created}@hotel1.demo",
                "titular_telefono": "+34 600 000 000",
                "precio_total": total,
                "deposito": dinero(total * 0.25),
                "saldo": dinero(total * 0.75),
                "observaciones": f"Simulacion vida operativa: {segment['nombre']}",
                "observaciones_operativas": f"segmento={segment['nombre']}; escenario={scenario['nombre']}",
                "alergias_resumen": random.choice(["", "", "", "Gluten", "Lacteos", "Frutos secos"]),
                "empresa_id": EMPRESA_ID,
                "canal_id": canal["id"] if canal else None,
                "localizador_externo": f"VIDA-{created:06d}",
                "comision_canal": dinero(total * float(canal["comision_pct"] or 0) / 100) if canal else 0,
                "politica_credito": "abierto" if segment["nombre"] != "jovenes_evento" else "fianza_virtual",
                "limite_credito": 200 if segment["nombre"] == "vip" else 75,
            }
            reserva_id = insert_row(conn, "reservas_recepcion", values)
            insert_travelers(conn, reserva_id, adults, minors, segment, created)
            used[room_id].append((day, salida))
            segment_counter[segment["nombre"]] += 1
            log_event(
                conn, corrida_id, day, "recepcion", "reserva", "info",
                f"Reserva {codigo} {segment['nombre']} {pax} pax {regimen}",
                "reservas_recepcion", reserva_id,
                {"segmento": segment["nombre"], "importe": total, "ocupacion_objetivo": scenario["ocupacion"]},
                centro_id,
            )
        day += timedelta(days=1)
    return created, segment_counter


def insert_travelers(conn: sqlite3.Connection, reserva_id: int, adults: int, minors: int, segment: dict, serial: int) -> None:
    total = adults + minors
    for idx in range(total):
        nombre, apellidos = rand_name()
        menor = idx >= adults
        age = random.randint(4, 15) if menor else random.randint(24, 78)
        nacionalidad = random.choice(segment["nacionalidades"])
        insert_row(
            conn,
            "viajeros_recepcion",
            {
                "reserva_id": reserva_id,
                "nombre": nombre,
                "apellidos": apellidos,
                "fecha_nacimiento": f"{date.today().year - age}-06-10",
                "nacionalidad": nacionalidad,
                "sexo": random.choice(["M", "F"]),
                "tipo_documento": "DNI" if nacionalidad == "ES" else "PASAPORTE",
                "numero_documento": f"SIMV{serial:05d}{idx}",
                "parentesco": "Titular" if idx == 0 else "Acompanante",
                "telefono": "+34 600 000 000" if idx == 0 else "",
                "email": f"viajero{serial}_{idx}@hotel1.demo" if idx == 0 else "",
                "pais": "Espana" if nacionalidad == "ES" else nacionalidad,
                "es_titular": 1 if idx == 0 else 0,
                "alergias_intolerancias": random.choice(["", "", "", "Gluten", "Lacteos", "Huevo"]),
                "avisos_departamentos": "cocina" if random.random() < 0.04 else "",
                "registro_viajeros_estado": "pendiente",
                "empresa_id": EMPRESA_ID,
            },
        )


def generate_complaints(conn: sqlite3.Connection, corrida_id: int, centro_id: int, start: date, end: date) -> int:
    if not table_exists(conn, "experiencia_quejas"):
        return 0
    reservas = conn.execute(
        """
        SELECT id, titular_nombre, habitacion_id, fecha_entrada, fecha_salida, observaciones_operativas
        FROM reservas_recepcion
        WHERE empresa_id=? AND fecha_entrada>=? AND fecha_entrada<?
        ORDER BY RANDOM()
        """,
        (EMPRESA_ID, start.isoformat(), end.isoformat()),
    ).fetchall()
    total = max(1, int(len(reservas) * 0.018))
    categorias = [
        ("limpieza", "Habitacion no lista al llegar", "media", "pisos"),
        ("mantenimiento", "Fan coil hace ruido y molesta por la noche", "alta", "mantenimiento"),
        ("buffet", "Cola larga y reposicion lenta en cena", "media", "cocina"),
        ("ruido", "Grupo joven hace ruido en pasillo", "media", "recepcion"),
        ("recepcion", "Espera excesiva en check-in", "baja", "recepcion"),
        ("appcc", "Cliente avisa de rotulo de alergeno confuso", "alta", "cocina"),
    ]
    created = 0
    for idx, reserva in enumerate(reservas[:total], start=1):
        cat, desc, gravedad, responsable = random.choice(categorias)
        fecha = date.fromisoformat(reserva["fecha_entrada"]) + timedelta(days=random.randint(0, max(0, (date.fromisoformat(reserva["fecha_salida"]) - date.fromisoformat(reserva["fecha_entrada"])).days - 1)))
        q_id = insert_row(
            conn,
            "experiencia_quejas",
            {
                "empresa_id": EMPRESA_ID,
                "fecha": fecha.isoformat(),
                "canal": random.choice(["recepcion", "email", "encuesta", "comentario_online"]),
                "origen_modulo": "simulador_vida",
                "reserva_id": reserva["id"],
                "titular_nombre": reserva["titular_nombre"],
                "habitacion": habitacion_numero(conn, reserva["habitacion_id"]),
                "tipo": "queja",
                "categoria": cat,
                "subcategoria": responsable,
                "descripcion": desc,
                "gravedad": gravedad,
                "impacto": random.choice(["servicio", "reputacion", "operacion", "seguridad_alimentaria"]),
                "estado": random.choice(["abierta", "en_revision", "cerrada"]),
                "responsable": responsable,
                "requiere_compensacion": 1 if gravedad == "alta" and random.random() < 0.55 else 0,
                "compensacion_tipo": "detalle_habitacion" if gravedad == "alta" else "",
                "compensacion_importe": 25 if gravedad == "alta" and random.random() < 0.35 else 0,
                "requiere_mantenimiento": 1 if cat == "mantenimiento" else 0,
                "requiere_appcc": 1 if cat == "appcc" else 0,
                "acciones_inmediatas": "Aviso a responsable y seguimiento con cliente",
                "aprendizaje": "Dato simulado para analizar decisiones operativas",
                "fecha_creacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        created += 1
        log_event(conn, corrida_id, fecha, "experiencia", "queja", gravedad, desc, "experiencia_quejas", q_id, {"categoria": cat}, centro_id)
    return created


def habitacion_numero(conn: sqlite3.Connection, habitacion_id: int | None) -> str:
    if not habitacion_id:
        return ""
    row = conn.execute("SELECT numero FROM habitaciones_recepcion WHERE id=?", (habitacion_id,)).fetchone()
    return str(row["numero"]) if row else ""


def department_id(conn: sqlite3.Connection, name_like: str, centro_id: int) -> int | None:
    if not table_exists(conn, "departamentos"):
        return None
    row = conn.execute(
        "SELECT id FROM departamentos WHERE empresa_id=? AND centro_id=? AND UPPER(nombre) LIKE UPPER(?) ORDER BY id LIMIT 1",
        (EMPRESA_ID, centro_id, f"%{name_like}%"),
    ).fetchone()
    return int(row["id"]) if row else None


def product_id_for_sale(conn: sqlite3.Connection, name: str, familia: str, price: float, centro_id: int) -> int | None:
    if not table_exists(conn, "productos"):
        return None
    row = conn.execute(
        """
        SELECT id FROM productos
        WHERE empresa_id=? AND LOWER(nombre)=LOWER(?) AND centro_id=?
        ORDER BY CASE WHEN centro_id=? THEN 0 ELSE 1 END, id
        LIMIT 1
        """,
        (EMPRESA_ID, name, centro_id, centro_id),
    ).fetchone()
    values = {
        "nombre": name,
        "cantidad": random.randint(24, 180),
        "unidad": "ud",
        "tipo_conservacion": "ambiente" if familia not in {"zumos", "snacks"} else "refrigerado",
        "familia": familia,
        "precio": dinero(price * 0.32),
        "stock_minimo": random.choice([12, 24, 36, 48]),
        "codigo_interno": f"BAR-H1-{abs(hash(name)) % 100000:05d}",
        "tipo_producto": "venta_bar",
        "unidad_base": "ud",
        "control_lote": 1,
        "control_caducidad": 1,
        "visible_en_compras": 1,
        "visible_en_produccion": 1 if familia in {"zumos", "snacks"} else 0,
        "visible_en_venta": 1,
        "permite_compra_directa": 1,
        "formato_venta": "unidad",
        "precio_venta": price,
        "empresa_id": EMPRESA_ID,
        "centro_id": centro_id,
    }
    if row:
        conn.execute(
            """
            UPDATE productos
            SET visible_en_venta=1, precio_venta=?, stock_minimo=COALESCE(NULLIF(stock_minimo, 0), ?)
            WHERE id=?
            """,
            (price, values["stock_minimo"], row["id"]),
        )
        return int(row["id"])
    return insert_row(conn, "productos", values)


def ensure_bar_catalogue(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> tuple[int, int]:
    if not (table_exists(conn, "puntos_venta") and table_exists(conn, "tpv_categorias") and table_exists(conn, "tpv_articulos")):
        return 0, 0
    dept_bar = department_id(conn, "BARES", centro_id) or department_id(conn, "RESTAUR", centro_id)
    sub_bar = None
    if table_exists(conn, "subdepartamentos"):
        row = conn.execute(
            """
            SELECT id FROM subdepartamentos
            WHERE empresa_id=? AND UPPER(nombre) LIKE '%BAR%'
            ORDER BY id LIMIT 1
            """,
            (EMPRESA_ID,),
        ).fetchone()
        sub_bar = int(row["id"]) if row else None
    pv_id = upsert_by_keys(
        conn,
        "puntos_venta",
        ["nombre", "empresa_id", "centro_id"],
        {
            "nombre": "Hotel 1 - Bar piscina",
            "tipo": "bar",
            "activo": 1,
            "departamento_id": dept_bar,
            "subdepartamento_id": sub_bar,
            "imprimir_comanda": 1,
            "empresa_id": EMPRESA_ID,
            "tpv_tipo": "bar",
            "tpv_control_buffet": 0,
            "tpv_admite_cargo_habitacion": 1,
            "tpv_permite_todo_incluido": 1,
            "tpv_bebidas_incluidas": 0,
            "centro_id": centro_id,
        },
    )
    categorias: dict[str, int] = {}
    for order, (name, tipo, color) in enumerate(BAR_CATEGORIAS, start=1):
        categorias[name] = upsert_by_keys(
            conn,
            "tpv_categorias",
            ["punto_venta_id", "nombre", "empresa_id"],
            {
                "punto_venta_id": pv_id,
                "nombre": name,
                "tipo": tipo,
                "color": color,
                "orden": order * 10,
                "activo": 1,
                "empresa_id": EMPRESA_ID,
            },
        )

    created_or_updated = 0
    for order, (name, categoria, price, tipo_articulo) in enumerate(BAR_ARTICULOS, start=1):
        producto_id = product_id_for_sale(conn, name, categorias_tipo(categoria), price, centro_id)
        upsert_by_keys(
            conn,
            "tpv_articulos",
            ["punto_venta_id", "nombre", "empresa_id"],
            {
                "punto_venta_id": pv_id,
                "categoria_id": categorias[categoria],
                "producto_id": producto_id,
                "nombre": name,
                "tipo": tipo_articulo,
                "precio": price,
                "incluido_regimenes": "ti" if categoria in {"Aguas", "Refrescos", "Cafes e infusiones"} else "",
                "suplemento": 0 if categoria in {"Aguas", "Refrescos", "Cafes e infusiones"} else price,
                "destino_preparacion": "barra",
                "visible_tpv": 1,
                "visible_comandero": 1,
                "orden": order,
                "activo": 1,
                "empresa_id": EMPRESA_ID,
                "tipo_cobro": "cobro",
            },
        )
        created_or_updated += 1
    log_event(conn, corrida_id, date.today(), "tpv", "carta_bar", "info", f"Carta de bar generada/actualizada con {created_or_updated} articulos", "puntos_venta", pv_id, {"categorias": len(categorias)}, centro_id)
    return len(categorias), created_or_updated


def categorias_tipo(categoria: str) -> str:
    mapping = {
        "Aguas": "aguas",
        "Refrescos": "refrescos",
        "Zumos naturales": "zumos",
        "Cafes e infusiones": "cafeteria",
        "Cervezas": "cervezas",
        "Vinos y espumosos": "vinos",
        "Cocteles": "cocteleria",
        "Combinados": "destilados",
        "Brandis y licores": "licores",
        "Snacks bar": "snacks",
    }
    return mapping.get(categoria, "bar")


def generate_maintenance(conn: sqlite3.Connection, corrida_id: int, centro_id: int, start: date, end: date) -> int:
    if not table_exists(conn, "ordenes_trabajo"):
        return 0
    rooms = get_rooms(conn, centro_id)
    equipos = conn.execute("SELECT * FROM equipos_mantenimiento WHERE empresa_id=? AND COALESCE(activo,1)=1 ORDER BY RANDOM() LIMIT 80", (EMPRESA_ID,)).fetchall() if table_exists(conn, "equipos_mantenimiento") else []
    dept_mant = department_id(conn, "MANTEN", centro_id)
    incidencias = [
        ("goteras", "Goteras detectadas tras lluvia fuerte", "alta"),
        ("climatizacion", "Fan coil no enfria correctamente", "media"),
        ("ascensor", "Ascensor con parada intermitente", "alta"),
        ("fontaneria", "Perdida de agua en bano", "media"),
        ("iluminacion", "Foco de zona comun fundido", "baja"),
        ("piscina", "Revision extraordinaria de bomba de piscina", "alta"),
    ]
    months = max(1, (end.year - start.year) * 12 + end.month - start.month)
    target = months * 18
    created = 0
    for idx in range(target):
        fecha = start + timedelta(days=random.randint(0, max(1, (end - start).days - 1)))
        tipo, desc, prioridad = random.choice(incidencias)
        room = random.choice(rooms) if rooms and tipo in {"goteras", "climatizacion", "fontaneria"} else None
        equipo = random.choice(equipos) if equipos else None
        incidencia_id = None
        if table_exists(conn, "incidencias"):
            incidencia_id = insert_row(
                conn,
                "incidencias",
                {
                    "tipo": tipo,
                    "fecha": fecha.isoformat(),
                    "usuario": "Simulador vida",
                    "descripcion": desc,
                    "gravedad": prioridad,
                    "estado": random.choice(["abierta", "en_proceso", "resuelta"]),
                    "empresa_id": EMPRESA_ID,
                    "origen_modulo": "simulador_vida",
                    "destino_modulo": "mantenimiento",
                    "categoria_operativa": "mantenimiento",
                    "departamento_id": dept_mant,
                    "habitacion_id": room["id"] if room else None,
                    "equipo_mantenimiento_id": equipo["id"] if equipo else None,
                    "requiere_mantenimiento": 1,
                },
            )
        estado_ot = random.choice(["abierta", "asignada", "en_proceso", "cerrada"])
        apertura_hora = random.choice(["08:10:00", "12:30:00", "18:45:00"])
        fecha_apertura = f"{fecha.isoformat()} {apertura_hora}"
        fecha_cierre = None
        horas_reales = None
        coste_real = None
        cerrado_por = None
        if estado_ot == "cerrada":
            horas_reales = random.choice([1.0, 1.5, 2.0, 3.0, 4.0])
            fecha_cierre = f"{fecha.isoformat()} {random.choice(['11:30:00','16:45:00','21:10:00'])}"
            coste_real = dinero(random.choice([45, 80, 135, 310]))
            cerrado_por = "Mantenimiento simulado"
        ot_id = insert_row(
            conn,
            "ordenes_trabajo",
            {
                "numero_orden": f"OT-VIDA-H1-{fecha.strftime('%Y%m%d')}-{idx + 1:04d}",
                "equipo_id": equipo["id"] if equipo else None,
                "tipo": "correctivo" if prioridad != "baja" else "preventivo",
                "prioridad": prioridad,
                "estado": estado_ot,
                "fecha_apertura": fecha_apertura,
                "fecha_inicio": fecha_apertura if estado_ot in {"en_proceso", "cerrada"} else None,
                "fecha_fin": fecha_cierre,
                "descripcion": desc,
                "tecnico_asignado": random.choice(["Tecnico 1", "Tecnico 2", "Tecnico 3", "Segundo mantenimiento"]),
                "horas_estimadas": random.choice([1.5, 2, 3, 4]),
                "horas_reales": horas_reales,
                "coste_estimado": dinero(random.choice([35, 65, 120, 280])),
                "coste_real": coste_real,
                "incidencia_id": incidencia_id,
                "cerrado_por": cerrado_por,
                "fecha_cierre": fecha_cierre,
                "empresa_id": EMPRESA_ID,
                "origen_modulo": "simulador_vida",
                "departamento_id": dept_mant,
                "habitacion_id": room["id"] if room else None,
                "requiere_appcc": 1 if tipo == "piscina" else 0,
            },
        )
        created += 1
        log_event(conn, corrida_id, fecha, "mantenimiento", "orden_trabajo", prioridad, desc, "ordenes_trabajo", ot_id, {"tipo": tipo}, centro_id)
    return created


def generate_housekeeping(conn: sqlite3.Connection, corrida_id: int, centro_id: int, start: date, end: date) -> int:
    if not table_exists(conn, "pisos_trabajos"):
        return 0
    reservas = conn.execute(
        """
        SELECT r.id, r.habitacion_id, r.fecha_entrada, r.fecha_salida, h.numero, h.planta, h.bloque
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON h.id=r.habitacion_id
        WHERE r.empresa_id=? AND r.fecha_entrada>=? AND r.fecha_entrada<?
          AND r.habitacion_id IS NOT NULL
        """,
        (EMPRESA_ID, start.isoformat(), end.isoformat()),
    ).fetchall()
    created = 0
    for reserva in reservas:
        entrada = date.fromisoformat(reserva["fecha_entrada"])
        salida = date.fromisoformat(reserva["fecha_salida"])
        for fecha, tarea, prioridad in [
            (entrada, "Repaso entrada habitacion", "media"),
            (salida, "Salida completa con cambio de sabanas", "alta"),
        ]:
            t_id = insert_row(
                conn,
                "pisos_trabajos",
                {
                    "fecha": fecha.isoformat(),
                    "origen": "simulador_vida",
                    "target_tipo": "habitacion",
                    "habitacion_id": reserva["habitacion_id"],
                    "reserva_id": reserva["id"],
                    "tarea_codigo": "SALIDA" if fecha == salida else "ENTRADA",
                    "tarea_nombre": tarea,
                    "categoria": "habitacion",
                    "estado": random.choice(["pendiente", "asignada", "cerrada"]),
                    "prioridad": prioridad,
                    "bloque": reserva["bloque"],
                    "planta": reserva["planta"],
                    "asignado_a": random.choice(["Equipo pisos planta", "Gobernanta", "Refuerzo pisos"]),
                    "sabanas_previstas": 2,
                    "toallas_previstas": 4,
                    "alfombrines_previstos": 1,
                    "observaciones": "Trabajo generado por simulador de vida operativa",
                    "empresa_id": EMPRESA_ID,
                    "fecha_creacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            created += 1
            if prioridad == "alta" and random.random() < 0.03:
                log_event(conn, corrida_id, fecha, "pisos", "incidencia_limpieza", "media", "Habitacion sale tarde y tensiona el planning", "pisos_trabajos", t_id, {"habitacion": reserva["numero"]}, centro_id)
    return created


def generate_purchases(conn: sqlite3.Connection, corrida_id: int, centro_id: int, start: date, end: date) -> int:
    if not (table_exists(conn, "pedidos") and table_exists(conn, "lineas_pedido")):
        return 0
    proveedores = conn.execute("SELECT id, nombre FROM proveedores WHERE empresa_id=? ORDER BY RANDOM() LIMIT 20", (EMPRESA_ID,)).fetchall()
    productos = conn.execute("SELECT id, nombre, precio FROM productos WHERE empresa_id=? ORDER BY RANDOM() LIMIT 120", (EMPRESA_ID,)).fetchall()
    if not proveedores or not productos:
        return 0
    dept_cocina = department_id(conn, "COCINA", centro_id) or department_id(conn, "BARES", centro_id)
    created = 0
    current = start
    while current < end:
        for n in range(random.randint(4, 8)):
            fecha = current + timedelta(days=random.randint(0, 26))
            if fecha >= end:
                continue
            prov = random.choice(proveedores)
            fallo = random.random() < 0.22
            estado_pedido = "parcial" if fallo else random.choice(["enviado", "recibido", "cerrado"])
            fecha_entrega = fecha + timedelta(days=random.randint(1, 5))
            pedido_id = insert_row(
                conn,
                "pedidos",
                {
                    "numero_pedido": f"PED-VIDA-H1-{fecha.strftime('%Y%m')}-{created + 1:04d}",
                    "proveedor_id": prov["id"],
                    "fecha_pedido": fecha.isoformat(),
                    "fecha_entrega": fecha_entrega.isoformat(),
                    "estado": estado_pedido,
                    "observaciones": "Pedido generado por simulador vida operativa",
                    "creado_por": "Simulador vida",
                    "empresa_id": EMPRESA_ID,
                    "departamento_id": dept_cocina,
                    "departamento_solicitante_id": dept_cocina,
                    "estado_recepcion": "parcial" if fallo else ("recibido" if estado_pedido in {"recibido", "cerrado"} else "pendiente"),
                    "fecha_recepcion": fecha_entrega.isoformat() if estado_pedido in {"recibido", "cerrado"} else None,
                    "cerrado": 1 if estado_pedido == "cerrado" else 0,
                    "fecha_cierre": fecha_entrega.isoformat() if estado_pedido == "cerrado" else None,
                    "solicitado_por": "Simulador vida",
                },
            )
            created += 1
            line_products = random.sample(productos, k=min(len(productos), random.randint(3, 8)))
            for producto in line_products:
                qty = random.choice([6, 12, 24, 36, 48, 80])
                received = qty * random.choice([0.0, 0.35, 0.60, 0.80]) if fallo and random.random() < 0.5 else qty
                insert_row(
                    conn,
                    "lineas_pedido",
                    {
                        "pedido_id": pedido_id,
                        "producto_id": producto["id"],
                        "cantidad_solicitada": qty,
                        "cantidad_recibida": received,
                        "precio_unitario": producto["precio"] or 0,
                    },
                )
            crear_recepcion = table_exists(conn, "recepciones") and (estado_pedido in {"recibido", "cerrado"} or random.random() < 0.75)
            if crear_recepcion:
                rec_id = insert_row(
                    conn,
                    "recepciones",
                    {
                        "pedido_id": pedido_id,
                        "proveedor_id": prov["id"],
                        "fecha_recepcion": fecha_entrega.isoformat(),
                        "numero_albaran": f"ALB-VIDA-{pedido_id:05d}",
                        "observaciones": "Recepcion parcial: falta mercancia" if fallo else "Recepcion correcta",
                        "recibido_por": "Economato simulado",
                        "empresa_id": EMPRESA_ID,
                        "tipo_recepcion": "albaran",
                        "departamento_id": dept_cocina,
                        "departamento_receptor_id": dept_cocina,
                        "validada_operativamente": 0 if fallo else 1,
                        "validada_economicamente": 0,
                        "stock_aplicado": 1,
                        "estado_facturacion": "pendiente_factura",
                    },
                )
                if fallo:
                    log_event(conn, corrida_id, fecha, "compras", "proveedor_falla", "alta", f"{prov['nombre']} sirve parcial y obliga a reajustar menu/stock", "recepciones", rec_id, {"pedido_id": pedido_id}, centro_id)
            if fallo:
                log_event(conn, corrida_id, fecha, "compras", "falta_producto", "media", "Faltas en pedido simulado; revisar sustituciones y coste real", "pedidos", pedido_id, {"proveedor": prov["nombre"]}, centro_id)
        current = date_add_months(current, 1)
    return created


def generate_auto_replenishment(conn: sqlite3.Connection, corrida_id: int, centro_id: int, start: date, end: date) -> int:
    if not (table_exists(conn, "productos") and table_exists(conn, "pedidos") and table_exists(conn, "lineas_pedido")):
        return 0
    productos = conn.execute(
        """
        SELECT id, nombre, cantidad, stock_minimo, precio, proveedor_id, familia
        FROM productos
        WHERE empresa_id=? AND COALESCE(activo,1)=1
          AND COALESCE(visible_en_compras,1)=1
          AND COALESCE(stock_minimo,0) > 0
        ORDER BY RANDOM()
        LIMIT 160
        """,
        (EMPRESA_ID,),
    ).fetchall()
    if not productos:
        return 0
    proveedores = conn.execute("SELECT id, nombre FROM proveedores WHERE empresa_id=? ORDER BY RANDOM()", (EMPRESA_ID,)).fetchall()
    dept_cocina = department_id(conn, "COCINA", centro_id) or department_id(conn, "BARES", centro_id)
    created = 0
    current = start
    while current < end:
        fecha = current + timedelta(days=random.randint(0, 26))
        needs = []
        for producto in random.sample(productos, k=min(len(productos), random.randint(18, 42))):
            consumo = random.uniform(0.35, 1.75) * float(producto["stock_minimo"] or 0)
            nueva_cantidad = max(0, float(producto["cantidad"] or 0) - consumo)
            conn.execute("UPDATE productos SET cantidad=? WHERE id=?", (dinero(nueva_cantidad), producto["id"]))
            if nueva_cantidad <= float(producto["stock_minimo"] or 0) * random.uniform(0.75, 1.25):
                needs.append(producto)
        if not needs:
            current = date_add_months(current, 1)
            continue
        grouped: dict[int, list[sqlite3.Row]] = defaultdict(list)
        for producto in needs:
            prov_id = producto["proveedor_id"] or (random.choice(proveedores)["id"] if proveedores else None)
            if prov_id:
                grouped[int(prov_id)].append(producto)
        for prov_id, lineas in grouped.items():
            pedido_id = insert_row(
                conn,
                "pedidos",
                {
                    "numero_pedido": f"AUTO-H1-{fecha.strftime('%Y%m')}-{created + 1:04d}",
                    "proveedor_id": prov_id,
                    "fecha_pedido": fecha.isoformat(),
                    "fecha_entrega": (fecha + timedelta(days=random.randint(1, 4))).isoformat(),
                    "estado": "enviado",
                    "observaciones": "Pedido automatico por stock bajo simulado",
                    "creado_por": "Simulador automatico",
                    "empresa_id": EMPRESA_ID,
                    "departamento_id": dept_cocina,
                    "departamento_solicitante_id": dept_cocina,
                    "estado_recepcion": "pendiente",
                    "solicitado_por": "Motor automatico stock",
                    "tipo_pedido": "automatico_stock",
                    "origen_documento": "simulador_vida",
                },
            )
            created += 1
            for producto in lineas[:12]:
                minimo = float(producto["stock_minimo"] or 1)
                qty = max(minimo * 2, minimo - float(producto["cantidad"] or 0) + minimo)
                insert_row(
                    conn,
                    "lineas_pedido",
                    {
                        "pedido_id": pedido_id,
                        "producto_id": producto["id"],
                        "cantidad_solicitada": dinero(qty),
                        "cantidad_recibida": 0,
                        "precio_unitario": producto["precio"] or 0,
                    },
                )
            log_event(conn, corrida_id, fecha, "compras", "pedido_automatico", "info", f"Pedido automatico por stock bajo con {len(lineas[:12])} lineas", "pedidos", pedido_id, {"proveedor_id": prov_id}, centro_id)
        current = date_add_months(current, 1)
    return created


def generate_buffet_labels(conn: sqlite3.Connection, corrida_id: int, centro_id: int, start: date, end: date, parada: tuple[date, date] | None) -> int:
    if not table_exists(conn, "simulador_buffet_etiquetas"):
        return 0
    recetas = conn.execute(
        """
        SELECT id, nombre, alergenos, nombre_en, nombre_fr, nombre_de, nombre_it
        FROM recetas
        WHERE empresa_id=? AND COALESCE(nombre,'') <> ''
        ORDER BY RANDOM()
        LIMIT 220
        """,
        (EMPRESA_ID,),
    ).fetchall() if table_exists(conn, "recetas") else []
    if not recetas:
        return 0
    estaciones = ["Cocina caliente", "Sopas y cremas", "Veganos/guarniciones", "Postres", "Buffet general", "Showcooking"]
    servicios = ["DESAYUNO", "ALMUERZO", "CENA"]
    created = 0
    day = start
    while day < end:
        if is_in_stop(day, parada):
            day += timedelta(days=1)
            continue
        if day.day not in {1, 8, 15, 22}:
            day += timedelta(days=1)
            continue
        for servicio in servicios:
            for receta in random.sample(recetas, k=min(len(recetas), random.randint(10, 18))):
                missing_allergen = not (receta["alergenos"] or "").strip()
                missing_translation = not all((receta[k] or "").strip() for k in ["nombre_en", "nombre_fr", "nombre_de", "nombre_it"] if k in receta.keys())
                estado = "pendiente_revision" if missing_allergen or missing_translation or random.random() < 0.04 else "lista"
                incidencia = []
                if missing_allergen:
                    incidencia.append("sin alergenos")
                if missing_translation:
                    incidencia.append("traduccion incompleta")
                label_id = insert_row(
                    conn,
                    "simulador_buffet_etiquetas",
                    {
                        "corrida_id": corrida_id,
                        "fecha": day.isoformat(),
                        "servicio": servicio,
                        "receta_id": receta["id"],
                        "nombre_plato": receta["nombre"],
                        "estacion_buffet": random.choice(estaciones),
                        "alergenos": receta["alergenos"] or "",
                        "idiomas_json": json.dumps(
                            {
                                "es": receta["nombre"],
                                "en": receta["nombre_en"] if "nombre_en" in receta.keys() else "",
                                "fr": receta["nombre_fr"] if "nombre_fr" in receta.keys() else "",
                                "de": receta["nombre_de"] if "nombre_de" in receta.keys() else "",
                                "it": receta["nombre_it"] if "nombre_it" in receta.keys() else "",
                            },
                            ensure_ascii=False,
                        ),
                        "estado": estado,
                        "incidencia": ", ".join(incidencia),
                        "empresa_id": EMPRESA_ID,
                        "centro_id": centro_id,
                    },
                )
                created += 1
                if estado != "lista":
                    log_event(conn, corrida_id, day, "buffet", "etiqueta_pendiente", "alta" if missing_allergen else "media", f"Etiqueta pendiente: {receta['nombre']} ({', '.join(incidencia)})", "simulador_buffet_etiquetas", label_id, {"servicio": servicio}, centro_id)
        day += timedelta(days=1)
    return created


def generate_inventory_deviations(conn: sqlite3.Connection, corrida_id: int, centro_id: int, start: date, end: date) -> int:
    if not (table_exists(conn, "inventario_existencias") and table_exists(conn, "inventario_articulos")):
        return 0
    rows = conn.execute(
        """
        SELECT ie.*, ia.nombre, ia.rompible, ia.requiere_reposicion_buffet, ia.valor_unitario
        FROM inventario_existencias ie
        JOIN inventario_articulos ia ON ia.id=ie.articulo_id
        WHERE ie.empresa_id=? AND (ie.centro_id=? OR ia.centro_id=?)
        ORDER BY RANDOM()
        """,
        (EMPRESA_ID, centro_id, centro_id),
    ).fetchall()
    if not rows:
        return 0
    created = 0
    current = start
    while current < end:
        fecha = current + timedelta(days=random.randint(0, 26))
        sample = random.sample(rows, k=min(len(rows), random.randint(10, 22)))
        for row in sample:
            sistema = float(row["cantidad"] or 0)
            if sistema <= 0:
                continue
            pct = random.choice([-0.12, -0.08, -0.04, -0.02, 0.02, 0.05])
            if row["rompible"] and random.random() < 0.25:
                pct -= random.uniform(0.04, 0.11)
            fisica = max(0, round(sistema * (1 + pct)))
            desviacion = dinero(fisica - sistema)
            if abs(desviacion) < 1:
                continue
            conn.execute(
                "UPDATE inventario_existencias SET cantidad=?, updated_at=? WHERE id=?",
                (fisica, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), row["id"]),
            )
            if table_exists(conn, "inventario_movimientos"):
                insert_row(
                    conn,
                    "inventario_movimientos",
                    {
                        "articulo_id": row["articulo_id"],
                        "existencia_id": row["id"],
                        "tipo": "conteo_simulado",
                        "cantidad": fisica,
                        "cantidad_anterior": sistema,
                        "cantidad_nueva": fisica,
                        "motivo": "Revista simulada con desviacion",
                        "usuario": "Simulador vida",
                        "empresa_id": EMPRESA_ID,
                        "centro_id": centro_id,
                        "fecha": f"{fecha.isoformat()} 09:00:00",
                    },
                )
            conteo_id = insert_row(
                conn,
                "simulador_inventario_conteos",
                {
                    "corrida_id": corrida_id,
                    "fecha": fecha.isoformat(),
                    "articulo_id": row["articulo_id"],
                    "cantidad_sistema": sistema,
                    "cantidad_fisica": fisica,
                    "desviacion": desviacion,
                    "motivo": random.choice(["rotura", "perdida", "traspaso no registrado", "conteo mal anterior", "merma operativa"]),
                    "empresa_id": EMPRESA_ID,
                    "centro_id": centro_id,
                },
            )
            created += 1
            severity = "alta" if abs(desviacion) >= max(8, sistema * 0.08) else "media"
            log_event(conn, corrida_id, fecha, "inventario", "desviacion", severity, f"Desviacion en {row['nombre']}: sistema {sistema}, fisico {fisica}", "simulador_inventario_conteos", conteo_id, {"valor_estimado": dinero(abs(desviacion) * float(row["valor_unitario"] or 0))}, centro_id)
        current = date_add_months(current, 1)
    return created


def reservas_en_casa(conn: sqlite3.Connection, fecha: date, limit: int = 0) -> list[sqlite3.Row]:
    sql = """
        SELECT r.*, h.numero AS habitacion_numero
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON h.id=r.habitacion_id
        WHERE r.empresa_id=? AND r.fecha_entrada <= ? AND r.fecha_salida > ?
          AND r.estado IN ('confirmada','alojado','checkin')
        ORDER BY RANDOM()
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, (EMPRESA_ID, fecha.isoformat(), fecha.isoformat())).fetchall()


def generate_reception_economics(conn: sqlite3.Connection, corrida_id: int, centro_id: int, start: date, end: date) -> tuple[int, int]:
    pagos = 0
    facturas = 0
    if not table_exists(conn, "reservas_recepcion"):
        return pagos, facturas
    reservas = conn.execute(
        """
        SELECT *
        FROM reservas_recepcion
        WHERE empresa_id=? AND fecha_entrada>=? AND fecha_entrada<?
        ORDER BY fecha_entrada, id
        """,
        (EMPRESA_ID, start.isoformat(), end.isoformat()),
    ).fetchall()
    for idx, reserva in enumerate(reservas, start=1):
        total = float(reserva["precio_total"] or 0)
        if table_exists(conn, "pagos_reserva_recepcion") and total > 0 and random.random() < 0.78:
            importe = dinero(total if random.random() < 0.62 else total * random.uniform(0.25, 0.75))
            pago_id = insert_row(
                conn,
                "pagos_reserva_recepcion",
                {
                    "reserva_id": reserva["id"],
                    "fecha": reserva["fecha_entrada"],
                    "concepto": random.choice(["Pago estancia", "Deposito reserva", "Liquidacion salida"]),
                    "importe": importe,
                    "forma_pago": random.choice(["tarjeta", "efectivo", "transferencia", "cargo_agencia"]),
                    "referencia": f"PAY-VIDA-H1-{idx:06d}",
                    "estado": "confirmado",
                    "observaciones": "Pago simulado por vida operativa",
                    "empresa_id": EMPRESA_ID,
                    "usuario_id": 1,
                },
            )
            pagos += 1
            if random.random() < 0.015:
                log_event(conn, corrida_id, reserva["fecha_entrada"], "recepcion", "pago_revisar", "media", "Pago parcial o forma de pago a revisar", "pagos_reserva_recepcion", pago_id, {"importe": importe}, centro_id)
        salida = date.fromisoformat(reserva["fecha_salida"])
        if table_exists(conn, "recepcion_facturas") and total > 0 and random.random() < 0.42:
            base = dinero(total / 1.10)
            tax = dinero(total - base)
            pagado = total if random.random() < 0.84 else dinero(total * 0.5)
            saldo = dinero(max(0, total - pagado))
            factura_id = insert_row(
                conn,
                "recepcion_facturas",
                {
                    "reserva_id": reserva["id"],
                    "tipo": "factura",
                    "serie": "SIMH1",
                    "ejercicio": salida.year,
                    "numero": idx,
                    "numero_completo": f"SIMH1-{salida.year}-{idx:06d}",
                    "fecha": salida.isoformat(),
                    "cliente_nombre": reserva["titular_nombre"],
                    "cliente_email": reserva["titular_email"],
                    "base": base,
                    "impuestos": tax,
                    "total": total,
                    "pagado": pagado,
                    "saldo": saldo,
                    "estado": "cobrada" if saldo <= 0.05 else random.choice(["emitida", "pendiente_cobro"]),
                    "observaciones": "Factura simulada de estancia",
                    "empresa_id": EMPRESA_ID,
                    "usuario_id": 1,
                    "idioma": random.choice(["es", "en", "fr", "de"]),
                    "impuesto_jurisdiccion": "IVA",
                },
            )
            facturas += 1
            if table_exists(conn, "recepcion_factura_lineas"):
                insert_row(
                    conn,
                    "recepcion_factura_lineas",
                    {
                        "factura_id": factura_id,
                        "origen": "reserva",
                        "origen_id": reserva["id"],
                        "concepto": "Estancia hotelera simulada",
                        "cantidad": 1,
                        "precio_unitario": base,
                        "base": base,
                        "impuesto_pct": 10,
                        "impuesto": tax,
                        "total": total,
                        "orden": 1,
                        "empresa_id": EMPRESA_ID,
                        "impuesto_codigo": "IVA10",
                        "impuesto_nombre": "IVA reducido",
                    },
                )
    return pagos, facturas


def generate_tpv_operations(conn: sqlite3.Connection, corrida_id: int, centro_id: int, start: date, end: date, parada: tuple[date, date] | None) -> tuple[int, int, int, int]:
    if not (table_exists(conn, "tpv_articulos") and table_exists(conn, "tpv_comandas") and table_exists(conn, "tpv_cuentas")):
        return 0, 0, 0, 0
    pv = conn.execute(
        "SELECT * FROM puntos_venta WHERE empresa_id=? AND centro_id=? AND LOWER(nombre)=LOWER(?) LIMIT 1",
        (EMPRESA_ID, centro_id, "Hotel 1 - Bar piscina"),
    ).fetchone()
    if not pv:
        return 0, 0, 0, 0
    terminal = conn.execute("SELECT * FROM tpv_terminales WHERE empresa_id=? AND punto_venta_id=? LIMIT 1", (EMPRESA_ID, pv["id"])).fetchone() if table_exists(conn, "tpv_terminales") else None
    terminal_id = terminal["id"] if terminal else None
    articulos = conn.execute(
        "SELECT * FROM tpv_articulos WHERE empresa_id=? AND punto_venta_id=? AND COALESCE(activo,1)=1 ORDER BY RANDOM()",
        (EMPRESA_ID, pv["id"]),
    ).fetchall()
    if not articulos:
        return 0, 0, 0, 0
    comandas = 0
    lineas = 0
    cajas = 0
    buffet_entries = 0
    day = start
    while day < end:
        if is_in_stop(day, parada):
            day += timedelta(days=1)
            continue
        reservas = reservas_en_casa(conn, day, 30)
        ocupacion = min(1.0, len(reservas) / 80.0)
        tickets = random.randint(8, 18) + int(ocupacion * random.randint(22, 48))
        efectivo_teorico = 0.0
        tarjeta_teorico = 0.0
        for n in range(tickets):
            reserva = random.choice(reservas) if reservas and random.random() < 0.48 else None
            selected = random.sample(articulos, k=min(len(articulos), random.randint(1, 5)))
            subtotal = 0.0
            comanda_id = insert_row(
                conn,
                "tpv_comandas",
                {
                    "numero": f"CMD-H1-{day.strftime('%Y%m%d')}-{n + 1:04d}",
                    "punto_venta_id": pv["id"],
                    "terminal_id": terminal_id,
                    "mesa_nombre": random.choice(["Bar piscina", "Terraza", "Sofa pared", "Mostrador"]),
                    "modo_servicio": "bar",
                    "reserva_id": reserva["id"] if reserva else None,
                    "habitacion": reserva["habitacion_numero"] if reserva else "",
                    "cliente": reserva["titular_nombre"] if reserva else "Cliente externo",
                    "pax": random.randint(1, 4),
                    "estado": "cerrada",
                    "usuario": random.choice(["Camarero bar 1", "Camarero bar 2", "Camarero bar partido"]),
                    "empresa_id": EMPRESA_ID,
                    "fecha_creacion": f"{day.isoformat()} {random.choice(['11:20:00','14:45:00','18:30:00','23:10:00'])}",
                    "camarero": random.choice(["Juan barra", "Maria barra", "Carlos barra"]),
                    "prioridad": random.choice(["normal", "normal", "alta"]),
                },
            )
            for art in selected:
                qty = random.choice([1, 1, 1, 2, 3])
                total = dinero(qty * float(art["precio"] or 0))
                subtotal += total
                insert_row(
                    conn,
                    "tpv_comanda_lineas",
                    {
                        "comanda_id": comanda_id,
                        "punto_venta_id": pv["id"],
                        "articulo_id": art["id"],
                        "producto_id": art["producto_id"],
                        "receta_id": art["receta_id"],
                        "nombre": art["nombre"],
                        "cantidad": qty,
                        "precio": art["precio"],
                        "suplemento": art["suplemento"] or 0,
                        "total": total,
                        "destino_preparacion": art["destino_preparacion"] or "barra",
                        "estado": "servido",
                        "incluido": 0,
                        "usuario": "Simulador TPV",
                        "empresa_id": EMPRESA_ID,
                        "fecha_creacion": f"{day.isoformat()} 12:00:00",
                        "prioridad": "normal",
                    },
                )
                lineas += 1
            forma_pago = random.choice(["tarjeta", "tarjeta", "efectivo", "cargo_habitacion"])
            if forma_pago == "efectivo":
                efectivo_teorico += subtotal
            elif forma_pago == "tarjeta":
                tarjeta_teorico += subtotal
            cuenta_id = insert_row(
                conn,
                "tpv_cuentas",
                {
                    "numero": f"CTA-H1-{day.strftime('%Y%m%d')}-{n + 1:04d}",
                    "punto_venta_id": pv["id"],
                    "terminal_id": terminal_id,
                    "mesa_nombre": "Bar piscina",
                    "modo_servicio": "bar",
                    "reserva_id": reserva["id"] if reserva and forma_pago == "cargo_habitacion" else None,
                    "habitacion": reserva["habitacion_numero"] if reserva and forma_pago == "cargo_habitacion" else "",
                    "cliente": reserva["titular_nombre"] if reserva else "Cliente externo",
                    "pax": random.randint(1, 4),
                    "estado": "cerrada",
                    "subtotal": dinero(subtotal),
                    "descuento": 0,
                    "total": dinero(subtotal),
                    "cargo_habitacion": 1 if forma_pago == "cargo_habitacion" else 0,
                    "forma_pago": forma_pago,
                    "observaciones": "Cuenta simulada bar",
                    "usuario_apertura": "Simulador TPV",
                    "usuario_cierre": "Simulador TPV",
                    "empresa_id": EMPRESA_ID,
                    "fecha_apertura": f"{day.isoformat()} 12:00:00",
                    "fecha_cierre": f"{day.isoformat()} 12:45:00",
                    "importe_entregado": dinero(subtotal) if forma_pago == "efectivo" else 0,
                    "cambio": 0,
                },
            )
            conn.execute("UPDATE tpv_comandas SET subtotal=?, total=?, cuenta_id=? WHERE id=?", (dinero(subtotal), dinero(subtotal), cuenta_id, comanda_id))
            comandas += 1
        if table_exists(conn, "tpv_caja_sesiones"):
            diferencia = dinero(random.choice([-12, -5, -2, 0, 0, 0, 3, 7, 15]))
            caja_id = insert_row(
                conn,
                "tpv_caja_sesiones",
                {
                    "punto_venta_id": pv["id"],
                    "terminal_id": terminal_id,
                    "fecha": day.isoformat(),
                    "turno": "bar_piscina",
                    "estado": "cerrada",
                    "saldo_inicial": 200,
                    "efectivo_teorico": dinero(efectivo_teorico),
                    "efectivo_contado": dinero(efectivo_teorico + diferencia),
                    "tarjeta_teorico": dinero(tarjeta_teorico),
                    "otros_teorico": 0,
                    "diferencia": diferencia,
                    "usuario_apertura": "Simulador TPV",
                    "usuario_cierre": "Simulador TPV",
                    "observaciones": "Arqueo simulado",
                    "empresa_id": EMPRESA_ID,
                    "fecha_apertura": f"{day.isoformat()} 10:00:00",
                    "fecha_cierre": f"{day.isoformat()} 01:00:00",
                },
            )
            cajas += 1
            if abs(diferencia) >= 10:
                log_event(conn, corrida_id, day, "tpv", "descuadre_caja", "alta", f"Descuadre de caja en bar piscina: {diferencia} EUR", "tpv_caja_sesiones", caja_id, {"diferencia": diferencia}, centro_id)
        if table_exists(conn, "tpv_buffet_entradas"):
            for servicio in ["DESAYUNO", "ALMUERZO", "CENA"]:
                for reserva in reservas[: random.randint(8, min(25, max(8, len(reservas))))]:
                    entry_id = insert_row(
                        conn,
                        "tpv_buffet_entradas",
                        {
                            "punto_venta_id": pv["id"],
                            "terminal_id": terminal_id,
                            "fecha": day.isoformat(),
                            "servicio": servicio,
                            "reserva_id": reserva["id"],
                            "habitacion_id": reserva["habitacion_id"],
                            "habitacion": reserva["habitacion_numero"],
                            "titular": reserva["titular_nombre"],
                            "regimen": reserva["regimen"],
                            "adultos": reserva["adultos"],
                            "menores": reserva["menores"],
                            "observaciones": "Entrada buffet simulada",
                            "usuario": "Control buffet",
                            "empresa_id": EMPRESA_ID,
                            "fecha_registro": f"{day.isoformat()} 09:00:00",
                        },
                    )
                    buffet_entries += 1
                    if random.random() < 0.004:
                        log_event(conn, corrida_id, day, "buffet", "entrada_discutida", "media", "Cliente discute regimen o acceso buffet", "tpv_buffet_entradas", entry_id, {"servicio": servicio}, centro_id)
        day += timedelta(days=1)
    return comandas, lineas, cajas, buffet_entries


def generate_turnos_rrhh(conn: sqlite3.Connection, corrida_id: int, centro_id: int, start: date, end: date, parada: tuple[date, date] | None) -> int:
    if not (table_exists(conn, "turnos_rrhh") and table_exists(conn, "empleados")):
        return 0
    empleados = active_employees(conn, centro_id)
    if not empleados:
        return 0
    by_role: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for emp in empleados:
        by_role[(emp["rol"] or "operativo").lower()].append(emp)
    created = 0
    day = start
    while day < end:
        if is_in_stop(day, parada):
            day += timedelta(days=1)
            continue
        selected = []
        for role, pool in by_role.items():
            if not pool:
                continue
            ratio = 0.75 if any(k in role for k in ["recepcion", "cocina", "buffet", "bar", "pisos"]) else 0.55
            k = max(1, min(len(pool), int(len(pool) * ratio)))
            selected.extend(random.sample(pool, k=k))
        for emp in selected:
            role = (emp["rol"] or "").lower()
            if "recepcion" in role:
                inicio, fin = random.choice([("07:00", "15:00"), ("15:00", "23:00"), ("23:00", "07:00")])
            elif "bar" in role:
                inicio, fin = random.choice([("10:00", "18:00"), ("17:00", "01:00"), ("11:30", "15:30")])
            elif "buffet" in role or "bufetero" in role:
                inicio, fin = random.choice([("07:00", "15:00"), ("11:30", "15:30"), ("18:00", "22:00")])
            elif "pisos" in role or "gobernanta" in role:
                inicio, fin = random.choice([("08:00", "16:00"), ("10:00", "18:00"), ("22:00", "06:00")])
            elif "mantenimiento" in role:
                inicio, fin = random.choice([("08:00", "16:00"), ("14:00", "22:00")])
            else:
                inicio, fin = random.choice([("08:00", "16:00"), ("14:30", "22:30")])
            insert_row(
                conn,
                "turnos_rrhh",
                {
                    "empleado_id": emp["id"],
                    "fecha": day.isoformat(),
                    "hora_inicio": inicio,
                    "hora_fin": fin,
                    "tipo": "trabajo",
                    "departamento_id": emp["departamento_id"],
                    "subdepartamento_id": emp["subdepartamento_id"],
                    "observaciones": "Turno generado por simulador vida",
                    "empresa_id": EMPRESA_ID,
                    "centro_id": centro_id,
                    "estado": random.choice(["publicado", "publicado", "borrador"]),
                    "origen": "simulador_vida",
                    "motivo_cambio": "Planificacion automatica de prueba",
                },
            )
            created += 1
        if random.random() < 0.02:
            log_event(conn, corrida_id, day, "rrhh", "cuadrante_tensionado", "media", "Cuadrante tensionado por descansos, horarios partidos o baja cobertura", "turnos_rrhh", None, {"turnos_dia": len(selected)}, centro_id)
        day += timedelta(days=1)
    return created


def generate_external_context(conn: sqlite3.Connection, corrida_id: int, centro_id: int, start: date, end: date) -> int:
    if not table_exists(conn, "simulador_contexto_externo"):
        return 0
    candidates = [
        ("meteorologia", "Ola de calor", 0.85, "Sube consumo de bar, piscina y quejas por climatizacion", {"tpv": 0.18, "mantenimiento": 0.12, "quejas": 0.08}),
        ("mercado", "Competidor baja precios", 0.65, "Presion sobre ADR y reservas directas", {"adr": -0.08, "canal_directo": -0.05}),
        ("transporte", "Huelga parcial aeropuerto", 0.75, "Retrasos, cancelaciones y llegadas fuera de hora", {"cancelaciones": 0.10, "recepcion": 0.18}),
        ("destino", "Evento local de alta demanda", 0.90, "Incremento de demanda y oportunidad de subir precio", {"adr": 0.14, "ocupacion": 0.08}),
        ("tour_operador", "Tour operador pide cupo agresivo", 0.70, "Riesgo de llenar barato y tensionar buffet", {"ocupacion": 0.12, "margen": -0.06}),
        ("reputacion", "Oleada de comentarios sobre buffet", 0.60, "Conviene revisar etiquetas, reposicion y variedad", {"quejas": 0.09, "buffet": 0.15}),
        ("proveedores", "Tension de suministros frescos", 0.72, "Puede subir coste y fallar entregas", {"compras": 0.16, "coste": 0.07}),
    ]
    created = 0
    total_days = max(1, (end - start).days)
    for idx, (tipo, titulo, intensidad, descripcion, impacto) in enumerate(random.sample(candidates, k=min(5, len(candidates))), start=1):
        inicio = start + timedelta(days=random.randint(0, max(1, total_days - 18)))
        fin = min(end, inicio + timedelta(days=random.randint(4, 16)))
        ctx_id = insert_row(
            conn,
            "simulador_contexto_externo",
            {
                "corrida_id": corrida_id,
                "fecha_inicio": inicio.isoformat(),
                "fecha_fin": fin.isoformat(),
                "tipo": tipo,
                "titulo": titulo,
                "intensidad": intensidad,
                "descripcion": descripcion,
                "impacto_previsto_json": json.dumps(impacto, ensure_ascii=False),
                "empresa_id": EMPRESA_ID,
                "centro_id": centro_id,
            },
        )
        created += 1
        log_event(conn, corrida_id, inicio, "contexto", tipo, "alta" if intensidad >= 0.75 else "media", f"{titulo}: {descripcion}", "simulador_contexto_externo", ctx_id, impacto, centro_id)
    return created


def _sum_tpv_day(conn: sqlite3.Connection, day: date) -> float:
    if not table_exists(conn, "tpv_cuentas"):
        return 0.0
    row = conn.execute(
        """
        SELECT SUM(COALESCE(total,0)) total
        FROM tpv_cuentas
        WHERE empresa_id=? AND fecha_apertura >= ? AND fecha_apertura < ?
        """,
        (EMPRESA_ID, f"{day.isoformat()} 00:00:00", f"{(day + timedelta(days=1)).isoformat()} 00:00:00"),
    ).fetchone()
    return float(row["total"] or 0)


def seed_costes_fijos_mensuales(conn: sqlite3.Connection, corrida_id: int, centro_id: int, start: date, end: date) -> None:
    actual = date(start.year, start.month, 1)
    while actual < end:
        mes = actual.strftime("%Y-%m")
        month = actual.month
        for concepto, descripcion, importe in GASTOS_FIJOS_MENSUALES_HOTEL1:
            factor_temporada = 1.0
            if month in {7, 8} and concepto in {"electricidad", "agua", "mantenimiento_contratado"}:
                factor_temporada = 1.18
            elif month in {11, 12, 1, 2} and concepto in {"electricidad", "mantenimiento_contratado"}:
                factor_temporada = 0.82
            conn.execute(
                """
                INSERT INTO simulador_costes_fijos_mensuales
                (corrida_id, centro_id, mes, concepto, descripcion, importe_mensual, tipo, activo, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, 'estructura', 1, ?)
                ON CONFLICT(corrida_id, centro_id, mes, concepto) DO UPDATE SET
                    descripcion=excluded.descripcion,
                    importe_mensual=excluded.importe_mensual,
                    activo=1
                """,
                (corrida_id, centro_id, mes, concepto, descripcion, dinero(importe * factor_temporada), EMPRESA_ID),
            )
        if actual.month == 12:
            actual = date(actual.year + 1, 1, 1)
        else:
            actual = date(actual.year, actual.month + 1, 1)


def coste_fijo_diario(conn: sqlite3.Connection, corrida_id: int, centro_id: int, day: date) -> float:
    mes = day.strftime("%Y-%m")
    row = conn.execute(
        """
        SELECT COALESCE(SUM(importe_mensual), 0) total
        FROM simulador_costes_fijos_mensuales
        WHERE corrida_id=? AND centro_id=? AND mes=? AND COALESCE(activo,1)=1
        """,
        (corrida_id, centro_id, mes),
    ).fetchone()
    return dinero(float(row["total"] or 0) / calendar.monthrange(day.year, day.month)[1])


def _count_table_day(conn: sqlite3.Connection, table: str, date_column: str, day: date, where_extra: str = "1=1") -> int:
    if not table_exists(conn, table):
        return 0
    row = conn.execute(
        f"""
        SELECT COUNT(*) total
        FROM {table}
        WHERE empresa_id=? AND {date_column} >= ? AND {date_column} < ? AND ({where_extra})
        """,
        (EMPRESA_ID, day.isoformat(), (day + timedelta(days=1)).isoformat()),
    ).fetchone()
    return int(row["total"] or 0)


def generate_daily_kpis(conn: sqlite3.Connection, corrida_id: int, centro_id: int, start: date, end: date, parada: tuple[date, date] | None) -> int:
    if not table_exists(conn, "simulador_vida_kpis_diarios"):
        return 0
    conn.execute("DELETE FROM simulador_vida_kpis_diarios WHERE corrida_id=?", (corrida_id,))
    seed_costes_fijos_mensuales(conn, corrida_id, centro_id, start, end)
    created = 0
    day = start
    while day < end:
        reservas = reservas_en_casa(conn, day)
        habitaciones = len({r["habitacion_id"] for r in reservas if r["habitacion_id"]})
        pax = sum(int(r["adultos"] or 0) + int(r["menores"] or 0) for r in reservas)
        ingresos_hab = 0.0
        for r in reservas:
            noches = max(1, (date.fromisoformat(r["fecha_salida"]) - date.fromisoformat(r["fecha_entrada"])).days)
            ingresos_hab += float(r["precio_total"] or 0) / noches
        ingresos_tpv = _sum_tpv_day(conn, day)
        turnos = _count_table_day(conn, "turnos_rrhh", "fecha", day, "origen='simulador_vida'")
        coste_laboral = dinero(turnos * 8 * 14.5)
        pedidos_dia = _count_table_day(conn, "pedidos", "fecha_pedido", day, "numero_pedido LIKE 'AUTO-H1-%' OR numero_pedido LIKE 'PED-VIDA-H1-%'")
        coste_compras = dinero(pedidos_dia * random.uniform(280, 950))
        coste_fijo = coste_fijo_diario(conn, corrida_id, centro_id, day)
        coste_total = dinero(coste_laboral + coste_compras + coste_fijo)
        quejas = _count_table_day(conn, "experiencia_quejas", "fecha", day, "origen_modulo='simulador_vida'")
        ordenes = _count_table_day(conn, "ordenes_trabajo", "fecha_apertura", day, "numero_orden LIKE 'OT-VIDA-H1-%'")
        appcc = _count_table_day(conn, "appcc_registros_plan", "fecha", day, "resultado='no_conforme'")
        alertas_altas = _count_table_day(conn, "simulador_vida_eventos", "fecha_simulada", day, f"corrida_id={int(corrida_id)} AND severidad IN ('alta','critica')")
        margen = dinero(ingresos_hab + ingresos_tpv - coste_total)
        riesgo = min(100, round((quejas * 10) + (ordenes * 4) + (appcc * 14) + (alertas_altas * 2) + max(0, -margen / 500), 1))
        if is_in_stop(day, parada):
            lectura = "Parada tecnica: no vender, usar dias para vacaciones, mantenimiento y limpieza profunda."
        elif margen < 0:
            lectura = "Dia con margen negativo estimado: revisar precio, compras, plantilla y mix de clientes."
        elif riesgo >= 70:
            lectura = "Dia de alto riesgo operativo: direccion debe revisar alertas antes del cierre."
        elif habitaciones >= 180:
            lectura = "Alta ocupacion: vigilar buffet, pisos, caja y mantenimiento."
        else:
            lectura = "Operacion dentro de parametros simulados."
        kpi_id = insert_row(
            conn,
            "simulador_vida_kpis_diarios",
            {
                "corrida_id": corrida_id,
                "fecha": day.isoformat(),
                "habitaciones_ocupadas": habitaciones,
                "pax": pax,
                "ingresos_habitaciones": dinero(ingresos_hab),
                "ingresos_tpv": dinero(ingresos_tpv),
                "coste_laboral_estimado": coste_laboral,
                "coste_compras_estimado": coste_compras,
                "coste_fijo_estimado": coste_fijo,
                "coste_total_estimado": coste_total,
                "margen_estimado": margen,
                "quejas": quejas,
                "ordenes_mantenimiento": ordenes,
                "no_conformidades_appcc": appcc,
                "alertas_altas": alertas_altas,
                "riesgo_operativo": riesgo,
                "lectura": lectura,
                "empresa_id": EMPRESA_ID,
                "centro_id": centro_id,
            },
        )
        created += 1
        if margen < 0:
            log_event(conn, corrida_id, day, "direccion", "margen_negativo", "alta", lectura, "simulador_vida_kpis_diarios", kpi_id, {"margen": margen, "riesgo": riesgo}, centro_id)
        elif riesgo >= 80:
            log_event(conn, corrida_id, day, "direccion", "riesgo_operativo_alto", "alta", lectura, "simulador_vida_kpis_diarios", kpi_id, {"margen": margen, "riesgo": riesgo}, centro_id)
        day += timedelta(days=1)
    return created


def insert_decision(
    conn: sqlite3.Connection,
    corrida_id: int,
    centro_id: int,
    modulo: str,
    prioridad: str,
    titulo: str,
    diagnostico: str,
    decision: str,
    impacto: str,
    soporte: dict,
) -> int:
    return insert_row(
        conn,
        "simulador_vida_decisiones",
        {
            "corrida_id": corrida_id,
            "modulo": modulo,
            "prioridad": prioridad,
            "titulo": titulo,
            "diagnostico": diagnostico,
            "decision_recomendada": decision,
            "impacto_estimado": impacto,
            "datos_soporte_json": json.dumps(soporte, ensure_ascii=False),
            "estado": "pendiente",
            "empresa_id": EMPRESA_ID,
            "centro_id": centro_id,
        },
    )


def count_event(conn: sqlite3.Connection, corrida_id: int, modulo: str, tipo: str | None = None, severidad: str | None = None) -> int:
    where = ["corrida_id=?", "modulo=?"]
    params: list = [corrida_id, modulo]
    if tipo:
        where.append("tipo=?")
        params.append(tipo)
    if severidad:
        where.append("severidad=?")
        params.append(severidad)
    row = conn.execute(
        f"SELECT COUNT(*) AS total FROM simulador_vida_eventos WHERE {' AND '.join(where)}",
        params,
    ).fetchone()
    return int(row["total"] or 0)


def top_inventory_deviations(conn: sqlite3.Connection, corrida_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT ia.nombre, SUM(ABS(c.desviacion)) AS desviacion_total, COUNT(*) AS conteos
        FROM simulador_inventario_conteos c
        LEFT JOIN inventario_articulos ia ON ia.id=c.articulo_id
        WHERE c.corrida_id=?
        GROUP BY c.articulo_id
        ORDER BY desviacion_total DESC
        LIMIT 8
        """,
        (corrida_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def top_buffet_label_issues(conn: sqlite3.Connection, corrida_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT nombre_plato, COUNT(*) AS total,
               SUM(CASE WHEN incidencia LIKE '%sin alergenos%' THEN 1 ELSE 0 END) AS sin_alergenos,
               SUM(CASE WHEN incidencia LIKE '%traduccion%' THEN 1 ELSE 0 END) AS traduccion
        FROM simulador_buffet_etiquetas
        WHERE corrida_id=? AND estado!='lista'
        GROUP BY receta_id, nombre_plato
        ORDER BY total DESC
        LIMIT 8
        """,
        (corrida_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def generate_decision_proposals(conn: sqlite3.Connection, corrida_id: int, centro_id: int, created: dict) -> int:
    if not table_exists(conn, "simulador_vida_decisiones"):
        return 0
    conn.execute("DELETE FROM simulador_vida_decisiones WHERE corrida_id=?", (corrida_id,))
    decisions = 0

    no_conformidades = count_event(conn, corrida_id, "appcc", "no_conformidad")
    if no_conformidades:
        decisions += 1
        insert_decision(
            conn, corrida_id, centro_id, "appcc", "critica" if no_conformidades > 25 else "alta",
            "APPCC con no conformidades repetidas",
            f"La simulacion ha generado {no_conformidades} no conformidades APPCC.",
            "Crear panel diario de APPCC por responsable, bloquear servicio si hay control critico abierto y exigir accion correctiva antes de cerrar turno.",
            "Reduce riesgo sanitario, sanciones y quejas por seguridad alimentaria.",
            {"no_conformidades": no_conformidades},
        )

    etiquetas_pendientes = count_event(conn, corrida_id, "buffet", "etiqueta_pendiente")
    if etiquetas_pendientes:
        decisions += 1
        insert_decision(
            conn, corrida_id, centro_id, "buffet", "critica" if etiquetas_pendientes > 250 else "alta",
            "Etiquetas de buffet insuficientes",
            f"Hay {etiquetas_pendientes} eventos de etiquetas pendientes por alergeno o traduccion.",
            "Antes de imprimir tarjetas, forzar validacion de alergenos y nombres multiidioma; si falta alergeno, no permitir publicar el plato en buffet.",
            "Evita errores con clientes alergicos y mejora imagen internacional.",
            {"pendientes": etiquetas_pendientes, "top_platos": top_buffet_label_issues(conn, corrida_id)},
        )

    pedidos_auto = int(created.get("pedidos_automaticos") or 0)
    proveedores_fallan = count_event(conn, corrida_id, "compras", "proveedor_falla")
    if pedidos_auto or proveedores_fallan:
        decisions += 1
        insert_decision(
            conn, corrida_id, centro_id, "compras", "alta" if proveedores_fallan else "media",
            "Compras necesita piloto automatico con control humano",
            f"El sistema genero {pedidos_auto} pedidos automaticos y detecto {proveedores_fallan} fallos de proveedor.",
            "Agrupar pedidos automaticos por proveedor y riesgo, pedir aprobacion al responsable y proponer sustitutos de menu si el proveedor falla.",
            "Reduce roturas de stock, urgencias de compra y cambios improvisados de menu.",
            {"pedidos_automaticos": pedidos_auto, "proveedores_fallan": proveedores_fallan},
        )

    desviaciones = count_event(conn, corrida_id, "inventario", "desviacion")
    if desviaciones:
        decisions += 1
        insert_decision(
            conn, corrida_id, centro_id, "inventario", "alta" if desviaciones > 80 else "media",
            "Inventario con desviaciones de menaje y utillaje",
            f"Se han detectado {desviaciones} desviaciones en revistas de inventario.",
            "Activar revista semanal para menaje rompible y articulos de buffet; cuando la desviacion supere umbral, abrir incidencia y reposicion sugerida.",
            "Controla perdidas, roturas y compras ocultas que luego afectan servicio.",
            {"desviaciones": desviaciones, "top_articulos": top_inventory_deviations(conn, corrida_id)},
        )

    no_presenta = count_event(conn, corrida_id, "rrhh", "no_se_presenta")
    retrasos = count_event(conn, corrida_id, "rrhh", "retraso")
    if no_presenta or retrasos:
        decisions += 1
        insert_decision(
            conn, corrida_id, centro_id, "rrhh", "critica" if no_presenta > 60 else "alta",
            "Riesgo de servicio por absentismo y retrasos",
            f"Hay {no_presenta} no presentaciones y {retrasos} retrasos simulados.",
            "Crear bolsa de retencion/refuerzo por departamento, medir clima por turno y cruzar fichajes con quejas y tiempos de servicio.",
            "Protege satisfaccion del cliente sin disparar coste laboral a ciegas.",
            {"no_presenta": no_presenta, "retrasos": retrasos},
        )

    quejas_altas = count_event(conn, corrida_id, "experiencia", "queja", "alta")
    quejas_total = count_event(conn, corrida_id, "experiencia", "queja")
    if quejas_total:
        decisions += 1
        insert_decision(
            conn, corrida_id, centro_id, "experiencia", "alta" if quejas_altas else "media",
            "Quejas conectadas a operacion",
            f"Se han generado {quejas_total} quejas, {quejas_altas} de gravedad alta.",
            "Cruzar quejas por categoria con ocupacion, fichajes, mantenimiento y buffet; convertir cada queja repetida en una regla preventiva.",
            "Menos compensaciones, mejor reputacion y aprendizaje operativo real.",
            {"quejas_total": quejas_total, "quejas_altas": quejas_altas},
        )

    mantenimiento_alto = count_event(conn, corrida_id, "mantenimiento", "orden_trabajo", "alta")
    if mantenimiento_alto:
        decisions += 1
        insert_decision(
            conn, corrida_id, centro_id, "mantenimiento", "alta",
            "Mantenimiento tensiona la experiencia",
            f"Hay {mantenimiento_alto} ordenes de mantenimiento de prioridad alta.",
            "Separar averias repetidas por familia de activo, reservar presupuesto preventivo y bloquear venta de habitaciones con averia critica abierta.",
            "Reduce quejas, compensaciones y dano reputacional por instalaciones viejas.",
            {"ordenes_altas": mantenimiento_alto},
        )

    descuadres = count_event(conn, corrida_id, "tpv", "descuadre_caja")
    if descuadres:
        decisions += 1
        insert_decision(
            conn, corrida_id, centro_id, "tpv", "alta",
            "Caja y TPV necesitan cierre vigilado",
            f"Se han generado {descuadres} descuadres de caja relevantes.",
            "Exigir arqueo por turno, foto/adjunto de cierre y regla de revision cuando la diferencia supere el umbral.",
            "Reduce fugas de caja, errores de cobro y discusiones al cierre.",
            {"descuadres": descuadres, "comandas": created.get("tpv_comandas", 0), "cajas": created.get("tpv_cajas", 0)},
        )

    turnos_tensionados = count_event(conn, corrida_id, "rrhh", "cuadrante_tensionado")
    if turnos_tensionados:
        decisions += 1
        insert_decision(
            conn, corrida_id, centro_id, "rrhh", "media",
            "Cuadrantes con tension operativa",
            f"Se han detectado {turnos_tensionados} dias con cuadrante tensionado.",
            "Comparar turnos publicados contra ocupacion, llegadas/salidas, buffet y eventos antes de validar la semana.",
            "Evita sobrecoste y falta de cobertura en los picos reales.",
            {"turnos_tensionados": turnos_tensionados, "turnos_generados": created.get("turnos_rrhh", 0)},
        )

    margen_negativo = count_event(conn, corrida_id, "direccion", "margen_negativo")
    riesgo_alto = count_event(conn, corrida_id, "direccion", "riesgo_operativo_alto")
    if margen_negativo or riesgo_alto:
        decisions += 1
        insert_decision(
            conn, corrida_id, centro_id, "direccion", "critica" if margen_negativo else "alta",
            "Dias con margen o riesgo fuera de control",
            f"Hay {margen_negativo} dias con margen negativo y {riesgo_alto} dias con riesgo operativo alto.",
            "Crear cierre diario de direccion: precio, compras, plantilla, quejas, APPCC y mantenimiento deben verse juntos antes del dia siguiente.",
            "Convierte datos dispersos en decisiones diarias y evita que una mala semana se descubra tarde.",
            {"margen_negativo": margen_negativo, "riesgo_alto": riesgo_alto, "kpis_diarios": created.get("kpis_diarios", 0)},
        )

    contexto = count_event(conn, corrida_id, "contexto")
    if contexto:
        decisions += 1
        insert_decision(
            conn, corrida_id, centro_id, "estrategia", "media",
            "Contexto externo conectado a la operacion",
            f"Se han simulado {contexto} senales externas de mercado/destino/proveedores.",
            "Cruzar cada senal externa con ocupacion, ADR, cancelaciones, compras, quejas y caja antes de modificar precios o cupos.",
            "Ayuda a anticiparse al mercado sin inventar decisiones ni bajar precios a ciegas.",
            {"senales_externas": contexto},
        )

    return decisions


def generate_appcc(conn: sqlite3.Connection, corrida_id: int, centro_id: int, start: date, end: date, parada: tuple[date, date] | None) -> int:
    if not (table_exists(conn, "appcc_controles_plan") and table_exists(conn, "appcc_registros_plan")):
        return 0
    controles = conn.execute(
        """
        SELECT id, codigo, area, tipo_control, limite_critico
        FROM appcc_controles_plan
        WHERE empresa_id=? AND COALESCE(activo,1)=1
        ORDER BY RANDOM()
        LIMIT 12
        """,
        (EMPRESA_ID,),
    ).fetchall()
    if not controles:
        return 0
    created = 0
    day = start
    while day < end:
        if is_in_stop(day, parada):
            day += timedelta(days=1)
            continue
        for control in random.sample(controles, k=min(len(controles), random.randint(2, 5))):
            anomaly = random.random() < 0.035
            valor = random.choice(["OK", "Correcto", "3.5 C", "75 C", "0.6 ppm"])
            if anomaly:
                valor = random.choice(["9.8 C", "58 C", "Legionella: repetir muestra", "Cloro bajo"])
            reg_id = insert_row(
                conn,
                "appcc_registros_plan",
                {
                    "empresa_id": EMPRESA_ID,
                    "control_id": control["id"],
                    "fecha": day.isoformat(),
                    "valor": valor,
                    "resultado": "no_conforme" if anomaly else "conforme",
                    "realizado_por": "Simulador APPCC",
                    "observaciones": "Control simulado de campana",
                    "accion_correctiva": "Bloquear uso, repetir control y avisar responsable" if anomaly else "",
                    "codigo_operacion": f"APPCC-VIDA-{created + 1:06d}",
                },
            )
            created += 1
            if anomaly:
                log_event(conn, corrida_id, day, "appcc", "no_conformidad", "alta", f"No conformidad APPCC en {control['area'] or control['codigo']}: {valor}", "appcc_registros_plan", reg_id, {"control_id": control["id"]}, centro_id)
        day += timedelta(days=1)
    return created


def active_employees(conn: sqlite3.Connection, centro_id: int) -> list[sqlite3.Row]:
    if not table_exists(conn, "empleados"):
        return []
    if 'centro_id' not in columns(conn, 'empleados'):
        raise RuntimeError('Los empleados requieren centro_id antes de simular.')
    centro_filter = 'AND centro_id=?'
    params: list = [EMPRESA_ID]
    if centro_filter:
        params.append(centro_id)
    return conn.execute(
        f"""
        SELECT id, nombre, apellidos, rol, departamento_id, subdepartamento_id
        FROM empleados
        WHERE empresa_id=? AND COALESCE(activo,1)=1 {centro_filter}
        ORDER BY id
        """,
        params,
    ).fetchall()


def generate_rrhh(
    conn: sqlite3.Connection,
    corrida_id: int,
    centro_id: int,
    start: date,
    end: date,
    parada: tuple[date, date] | None,
    duracion: str,
) -> tuple[int, int]:
    employees = active_employees(conn, centro_id)
    if not employees:
        return 0, 0
    vacaciones = 0
    if table_exists(conn, "vacaciones_rrhh"):
        if parada:
            vac_start, vac_end = parada
        else:
            vac_start, vac_end = end, end + timedelta(days=15)
        for emp in employees:
            insert_row(
                conn,
                "vacaciones_rrhh",
                {
                    "empleado_id": emp["id"],
                    "fecha_inicio": vac_start.isoformat(),
                    "fecha_fin": (vac_end - timedelta(days=1)).isoformat(),
                    "tipo": "vacaciones_empresa",
                    "estado": "aprobada",
                    "dias": (vac_end - vac_start).days,
                    "observaciones": "Vacaciones generadas por simulador de campana completa" if duracion == "12m" else "Vacaciones al terminar temporada simulada",
                    "empresa_id": EMPRESA_ID,
                    "origen": "simulador_vida",
                },
            )
            vacaciones += 1
        log_event(conn, corrida_id, vac_start, "rrhh", "vacaciones_empresa", "info", f"Vacaciones de plantilla: {vacaciones} empleados", "vacaciones_rrhh", None, {"dias": (vac_end - vac_start).days}, centro_id)

    fichajes = 0
    if table_exists(conn, "fichajes"):
        day = start
        while day < end:
            if is_in_stop(day, parada):
                day += timedelta(days=1)
                continue
            daily = random.sample(employees, k=min(len(employees), random.randint(18, 32)))
            for emp in daily:
                missing = random.random() < 0.012
                late = random.random() < 0.06
                if missing:
                    log_event(conn, corrida_id, day, "rrhh", "no_se_presenta", "alta", f"{emp['nombre']} {emp['apellidos'] or ''} no se presenta al turno", "empleados", emp["id"], {"rol": emp["rol"]}, centro_id)
                    continue
                entrada = datetime.combine(day, time(8, 0)) + timedelta(minutes=random.randint(-5, 35) if late else random.randint(-8, 8))
                salida = entrada + timedelta(hours=8, minutes=random.choice([0, 0, 15, 30]))
                for tipo, momento in [("entrada", entrada), ("salida", salida)]:
                    insert_row(
                        conn,
                        "fichajes",
                        {
                            "empleado_id": emp["id"],
                            "tipo": tipo,
                            "fecha_hora": momento.strftime("%Y-%m-%d %H:%M:%S"),
                            "origen": "simulador_vida",
                            "observaciones": "Fichaje simulado",
                            "empresa_id": EMPRESA_ID,
                            "estado": "pendiente" if late and tipo == "entrada" else "validado",
                            "minutos_desviacion": max(0, int((entrada - datetime.combine(day, time(8, 0))).total_seconds() / 60)) if tipo == "entrada" else 0,
                        },
                    )
                    fichajes += 1
                if late:
                    log_event(conn, corrida_id, day, "rrhh", "retraso", "media", f"{emp['nombre']} llega tarde y afecta al arranque del servicio", "empleados", emp["id"], {"minutos": max(0, int((entrada - datetime.combine(day, time(8, 0))).total_seconds() / 60))}, centro_id)
            if random.random() < 0.018:
                emp = random.choice(employees)
                log_event(conn, corrida_id, day, "rrhh", "retencion", "info", f"{emp['nombre']} valora quedarse por mejora de organizacion", "empleados", emp["id"], {"rol": emp["rol"]}, centro_id)
            day += timedelta(days=1)
    return fichajes, vacaciones


def summarize(conn: sqlite3.Connection, db_path: Path, corrida_id: int, start: date, end: date, created: dict, segmentos: Counter, parada: tuple[date, date] | None) -> dict:
    events = conn.execute(
        """
        SELECT modulo, tipo, severidad, COUNT(*) total
        FROM simulador_vida_eventos
        WHERE corrida_id=?
        GROUP BY modulo, tipo, severidad
        ORDER BY modulo, tipo, severidad
        """,
        (corrida_id,),
    ).fetchall()
    por_modulo: dict[str, int] = defaultdict(int)
    alertas: list[dict] = []
    for row in events:
        por_modulo[row["modulo"]] += int(row["total"])
        if row["severidad"] in {"alta", "critica"}:
            alertas.append({"modulo": row["modulo"], "tipo": row["tipo"], "total": row["total"]})
    return {
        "db": str(db_path),
        "fecha_inicio": start.isoformat(),
        "fecha_fin": end.isoformat(),
        "parada_tecnica": {"inicio": parada[0].isoformat(), "fin": parada[1].isoformat()} if parada else None,
        "creado": created,
        "segmentos_reservas": dict(segmentos),
        "eventos_por_modulo": dict(por_modulo),
        "alertas_altas": alertas[:20],
        "corrida_id": corrida_id,
    }


def update_corrida_summary(conn: sqlite3.Connection, corrida_id: int, summary: dict) -> None:
    conn.execute(
        "UPDATE simulador_vida_corridas SET resumen_json=? WHERE id=?",
        (json.dumps(summary, ensure_ascii=False), corrida_id),
    )


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    db_path = safe_db_path(args.db)
    origen = Path(args.origen).resolve()
    if db_path == origen:
        raise RuntimeError('El laboratorio no puede ser la base origen.')
    if args.reset or not db_path.exists():
        clone_real_db(db_path, origen)

    start = date.fromisoformat(args.inicio) if args.inicio else date(2026, 6, 1)
    end, scenarios, parada = campaign_plan(start, args.duracion)

    conn = connect(db_path)
    try:
        if not table_exists(conn, 'habitaciones_recepcion'):
            raise RuntimeError('El laboratorio no contiene habitaciones_recepcion. Recrealo con --reset --origen apuntando a una base valida.')
        if not get_rooms(conn, args.centro_id):
            raise RuntimeError(f'No hay habitaciones del centro {args.centro_id} en la base seleccionada.')
        ensure_trace_schema(conn)
        corrida_id = insert_row(
            conn,
            "simulador_vida_corridas",
            {
                "nombre": f"Hotel 1 vida operativa {args.duracion}",
                "fecha_inicio": start.isoformat(),
                "fecha_fin": end.isoformat(),
                "duracion": args.duracion,
                "centro_id": args.centro_id,
                "empresa_id": EMPRESA_ID,
                "config_json": json.dumps(
                    {
                        "seed": args.seed,
                        "escenarios": [
                            {
                                "mes": s["mes"],
                                "nombre": s["nombre"],
                                "inicio": s["inicio"].isoformat(),
                                "fin": s["fin"].isoformat(),
                                "ocupacion": s["ocupacion"],
                            }
                            for s in scenarios
                        ],
                        "parada_tecnica": [p.isoformat() for p in parada] if parada else None,
                    },
                    ensure_ascii=False,
                ),
            },
        )
        log_event(conn, corrida_id, start, "simulador", "inicio", "info", "Arranque de simulacion de vida operativa", "simulador_vida_corridas", corrida_id, {"duracion": args.duracion}, args.centro_id)

        contexto_externo = generate_external_context(conn, corrida_id, args.centro_id, start, end)
        bar_categorias, bar_articulos = ensure_bar_catalogue(conn, corrida_id, args.centro_id)
        reservas, segmentos = generate_reservations(conn, corrida_id, args.centro_id, start, end, scenarios, parada)
        compras = generate_purchases(conn, corrida_id, args.centro_id, start, end)
        pedidos_auto = generate_auto_replenishment(conn, corrida_id, args.centro_id, start, end)
        appcc = generate_appcc(conn, corrida_id, args.centro_id, start, end, parada)
        etiquetas_buffet = generate_buffet_labels(conn, corrida_id, args.centro_id, start, end, parada)
        desviaciones_inventario = generate_inventory_deviations(conn, corrida_id, args.centro_id, start, end)
        pisos = generate_housekeeping(conn, corrida_id, args.centro_id, start, end)
        mantenimiento = generate_maintenance(conn, corrida_id, args.centro_id, start, end)
        quejas = generate_complaints(conn, corrida_id, args.centro_id, start, end)
        pagos_recepcion, facturas_recepcion = generate_reception_economics(conn, corrida_id, args.centro_id, start, end)
        tpv_comandas, tpv_lineas, tpv_cajas, buffet_entradas = generate_tpv_operations(conn, corrida_id, args.centro_id, start, end, parada)
        turnos_rrhh = generate_turnos_rrhh(conn, corrida_id, args.centro_id, start, end, parada)
        fichajes, vacaciones = generate_rrhh(conn, corrida_id, args.centro_id, start, end, parada, args.duracion)
        kpis_diarios = generate_daily_kpis(conn, corrida_id, args.centro_id, start, end, parada)

        created = {
            "contexto_externo": contexto_externo,
            "bar_categorias": bar_categorias,
            "bar_articulos": bar_articulos,
            "reservas": reservas,
            "pedidos": compras,
            "pedidos_automaticos": pedidos_auto,
            "registros_appcc": appcc,
            "etiquetas_buffet": etiquetas_buffet,
            "desviaciones_inventario": desviaciones_inventario,
            "trabajos_pisos": pisos,
            "ordenes_mantenimiento": mantenimiento,
            "quejas": quejas,
            "pagos_recepcion": pagos_recepcion,
            "facturas_recepcion": facturas_recepcion,
            "tpv_comandas": tpv_comandas,
            "tpv_lineas": tpv_lineas,
            "tpv_cajas": tpv_cajas,
            "buffet_entradas": buffet_entradas,
            "turnos_rrhh": turnos_rrhh,
            "fichajes": fichajes,
            "vacaciones": vacaciones,
            "kpis_diarios": kpis_diarios,
        }
        decisiones = generate_decision_proposals(conn, corrida_id, args.centro_id, created)
        created["decisiones"] = decisiones
        summary = summarize(conn, db_path, corrida_id, start, end, created, segmentos, parada)
        update_corrida_summary(conn, corrida_id, summary)
        conn.commit()
    finally:
        conn.close()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\n[OK] Base de laboratorio: {db_path}")
    print(f"[OK] Resumen: {OUT_PATH}")
    print("[OK] Para verla en la ERP sin tocar la real:")
    print(f"     $env:ERP_DATABASE='{db_path}'")
    print("     $env:ERP_PORT='5002'")
    print("     python app.py")


if __name__ == "__main__":
    main()
