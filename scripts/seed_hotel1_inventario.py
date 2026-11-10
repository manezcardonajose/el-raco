import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "pepe4.db"
EMPRESA_ID = 1
CENTRO_NOMBRE = "Hotel 1"


def row(conn, query, params=()):
    return conn.execute(query, params).fetchone()


def ensure_subdepartamento(conn, nombre, departamento_id, centro_id):
    existing = row(
        conn,
        """
        SELECT id FROM subdepartamentos
        WHERE empresa_id = ? AND centro_id = ? AND departamento_id = ?
          AND lower(trim(nombre)) = lower(trim(?))
        """,
        (EMPRESA_ID, centro_id, departamento_id, nombre),
    )
    if existing:
        return existing["id"]
    cur = conn.execute(
        """
        INSERT INTO subdepartamentos (nombre, departamento_id, descripcion, activo, empresa_id, centro_id)
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (nombre, departamento_id, f"Subdepartamento creado para inventario operativo {CENTRO_NOMBRE}.", EMPRESA_ID, centro_id),
    )
    return cur.lastrowid


def ensure_zona(conn, nombre, tipo, departamento_id, subdepartamento_id, punto_venta_id=None, prioridad="media", appcc=1):
    existing = row(
        conn,
        """
        SELECT id FROM pisos_zonas
        WHERE empresa_id = ? AND lower(trim(nombre)) = lower(trim(?))
        """,
        (EMPRESA_ID, nombre),
    )
    instrucciones = (
        "Zona inventariada para Hotel 1. Revisar limpieza, roturas, puntos de contacto, "
        "humedades/goteras si aplica y comunicar incidencias a mantenimiento."
    )
    if existing:
        conn.execute(
            """
            UPDATE pisos_zonas
            SET tipo = ?, prioridad = ?, instrucciones = ?, appcc_critico = ?, activo = 1,
                departamento_id = ?, subdepartamento_id = ?, punto_venta_id = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (tipo, prioridad, instrucciones, appcc, departamento_id, subdepartamento_id, punto_venta_id, existing["id"], EMPRESA_ID),
        )
        return existing["id"]
    cur = conn.execute(
        """
        INSERT INTO pisos_zonas
        (nombre, tipo, bloque, planta, frecuencia, prioridad, productos_previstos, instrucciones,
         appcc_critico, activo, empresa_id, fecha_creacion, departamento_id, subdepartamento_id, punto_venta_id)
        VALUES (?, ?, '', '', 'diaria', ?, 'Desinfectante superficies; limpiador banos; control visual',
                ?, ?, 1, ?, CURRENT_TIMESTAMP, ?, ?, ?)
        """,
        (nombre, tipo, prioridad, instrucciones, appcc, EMPRESA_ID, departamento_id, subdepartamento_id, punto_venta_id),
    )
    return cur.lastrowid


def ensure_activo(
    conn,
    codigo,
    nombre,
    tipo,
    familia,
    clase,
    ubicacion,
    departamento_id,
    subdepartamento_id=None,
    punto_venta_id=None,
    habitacion_id=None,
    zona_id=None,
    criticidad="media",
    requiere_appcc=0,
    categoria_operativa="inventario hotel 1",
):
    existing = row(
        conn,
        "SELECT id FROM equipos_mantenimiento WHERE empresa_id = ? AND codigo = ?",
        (EMPRESA_ID, codigo),
    )
    params = (
        nombre,
        tipo,
        familia,
        categoria_operativa,
        clase,
        criticidad,
        1,
        requiere_appcc,
        "hotel1_inventario",
        ubicacion,
        departamento_id,
        subdepartamento_id,
        punto_venta_id,
        habitacion_id,
        zona_id,
        CENTRO_ID,
    )
    if existing:
        conn.execute(
            """
            UPDATE equipos_mantenimiento
            SET nombre = ?, tipo = ?, familia = ?, categoria_operativa = ?, clase_activo = ?,
                criticidad = ?, inventariable = ?, requiere_appcc = ?, origen_modulo = ?,
                ubicacion = ?, departamento_id = ?, subdepartamento_id = ?, punto_venta_id = ?,
                habitacion_id = ?, zona_id = ?, centro_id = ?, activo = 1
            WHERE id = ? AND empresa_id = ?
            """,
            params + (existing["id"], EMPRESA_ID),
        )
        return existing["id"], False
    cur = conn.execute(
        """
        INSERT INTO equipos_mantenimiento
        (codigo, nombre, tipo, familia, categoria_operativa, clase_activo, criticidad,
         inventariable, requiere_appcc, origen_modulo, ubicacion, departamento_id,
         subdepartamento_id, punto_venta_id, habitacion_id, zona_id, centro_id, activo, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (codigo,) + params + (EMPRESA_ID,),
    )
    return cur.lastrowid, True


def ensure_appcc_control(
    conn,
    codigo,
    area,
    etapa,
    tipo_control,
    peligro,
    medida,
    limite,
    frecuencia,
    responsable,
    accion,
    verificacion,
    departamento_id=None,
    subdepartamento_id=None,
):
    existing = row(
        conn,
        "SELECT id FROM appcc_controles_plan WHERE empresa_id = ? AND codigo = ? AND centro_id = ?",
        (EMPRESA_ID, codigo, CENTRO_ID),
    )
    data = (
        area,
        etapa,
        tipo_control,
        peligro,
        medida,
        limite,
        frecuencia,
        responsable,
        accion,
        verificacion,
        "hotel1_inventario",
        1,
        departamento_id,
        subdepartamento_id,
    )
    if existing:
        conn.execute(
            """
            UPDATE appcc_controles_plan
            SET area = ?, etapa = ?, tipo_control = ?, peligro = ?, medida_preventiva = ?,
                limite_critico = ?, frecuencia = ?, responsable = ?, accion_correctiva = ?,
                verificacion = ?, modulo_origen = ?, activo = ?, departamento_id = ?, subdepartamento_id = ?
            WHERE id = ? AND empresa_id = ?
            """,
            data + (existing["id"], EMPRESA_ID),
        )
        return existing["id"], False
    cur = conn.execute(
        """
        INSERT INTO appcc_controles_plan
        (empresa_id, codigo, area, etapa, tipo_control, peligro, medida_preventiva,
         limite_critico, frecuencia, responsable, accion_correctiva, verificacion,
         modulo_origen, activo, fecha_creacion, centro_id, departamento_id, subdepartamento_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?)
        """,
        (EMPRESA_ID, codigo) + data[:-2] + (CENTRO_ID, departamento_id, subdepartamento_id),
    )
    return cur.lastrowid, True


def upsert_habitacion_equipamiento(conn, habitacion):
    capacidad = int(habitacion["capacidad"] or 2)
    camas = 4 if capacidad >= 4 or "cuad" in (habitacion["tipo"] or "").lower() else 2
    extra = {
        "bano": "Bano completo",
        "mesa_escritorio": 1,
        "silla_escritorio": 1,
        "terraza_balcon": "Todas las habitaciones tienen balcon o terraza",
        "terraza_mobiliario": "1 mesita redonda PVC + 4 sillas PVC",
        "cortinas": "1 opaca + 1 decorativa",
        "climatizacion": "Fan coil / aire acondicionado",
        "contexto": "Hotel viejo reformado en 2004; vigilar goteras y humedades.",
    }
    existing = row(
        conn,
        "SELECT id FROM habitaciones_equipamiento WHERE habitacion_id = ? AND empresa_id = ?",
        (habitacion["id"], EMPRESA_ID),
    )
    data = (
        "individuales",
        0,
        camas,
        0,
        0,
        0,
        0,
        0,
        1,
        0,
        "fan coil",
        1,
        2,
        5,
        2,
        0,
        0,
        0,
        1,
        json.dumps(extra, ensure_ascii=False),
        "Equipamiento base cargado para Hotel 1: camas individuales, mesillas, armario, escritorio, bano completo, terraza/balcon con mobiliario PVC y cortinas.",
        "Carga inicial Hotel 1",
        datetime.now().isoformat(timespec="seconds"),
    )
    if existing:
        conn.execute(
            """
            UPDATE habitaciones_equipamiento
            SET tipo_cama = ?, camas_matrimonio = ?, camas_individuales = ?, camas_juntas = ?,
                sofa_cama = ?, supletoria_posible = ?, supletorias_max = ?, cuna_posible = ?,
                tv = ?, minibar = ?, aire_tipo = ?, armario = ?, mesas = ?, sillas = ?,
                mesillas = ?, tumbonas = ?, tendedero = ?, caja_fuerte = ?, terraza = ?,
                inventario_extra = ?, observaciones = ?, revisado_por = ?, revisado_at = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND empresa_id = ?
            """,
            data + (existing["id"], EMPRESA_ID),
        )
        return False
    conn.execute(
        """
        INSERT INTO habitaciones_equipamiento
        (habitacion_id, tipo_cama, camas_matrimonio, camas_individuales, camas_juntas,
         sofa_cama, supletoria_posible, supletorias_max, cuna_posible, tv, minibar,
         aire_tipo, armario, mesas, sillas, mesillas, tumbonas, tendedero, caja_fuerte,
         terraza, inventario_extra, observaciones, revisado_por, revisado_at, empresa_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (habitacion["id"],) + data + (EMPRESA_ID,),
    )
    return True


conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys = ON")

centro = row(
    conn,
    "SELECT id FROM centros_trabajo WHERE empresa_id = ? AND lower(trim(nombre)) = lower(trim(?)) AND activo = 1",
    (EMPRESA_ID, CENTRO_NOMBRE),
)
if not centro:
    raise SystemExit("No encuentro Hotel 1 activo en centros_trabajo.")
CENTRO_ID = centro["id"]

deps = {
    r["nombre"].strip().upper(): r["id"]
    for r in conn.execute("SELECT id, nombre FROM departamentos WHERE empresa_id = ? AND centro_id = ? AND activo = 1", (EMPRESA_ID, CENTRO_ID))
}
dep_cocina = deps.get("COCINA CENTRAL")
dep_recepcion = deps.get("RECEPCION")
dep_bares = deps.get("BARES/RESTAURANTES")
dep_pisos = deps.get("PISOS")
dep_mantenimiento = deps.get("MATENIMIENTO") or deps.get("MATENIMIENTO") or deps.get("MATENIMIENTO")
dep_mantenimiento = dep_mantenimiento or deps.get("MATENIMIENTO") or deps.get("MATENIMIENTO") or deps.get("MATENIMIENTO")
if not dep_mantenimiento:
    dep_mantenimiento = next((v for k, v in deps.items() if "MATEN" in k or "MANTEN" in k), None)

needed = [dep_recepcion, dep_bares, dep_pisos, dep_mantenimiento]
if any(v is None for v in needed):
    raise SystemExit(f"Faltan departamentos base en Hotel 1. Encontrados: {deps}")

sub_habitaciones = ensure_subdepartamento(conn, "Habitaciones", dep_pisos, CENTRO_ID)
sub_zonas = ensure_subdepartamento(conn, "Zonas comunes", dep_pisos, CENTRO_ID)
sub_buffet = ensure_subdepartamento(conn, "Buffet", dep_bares, CENTRO_ID)
sub_bar = ensure_subdepartamento(conn, "Bar piscina y sala de fiestas", dep_bares, CENTRO_ID)
sub_piscina = ensure_subdepartamento(conn, "Piscina", dep_bares, CENTRO_ID)
sub_recepcion = ensure_subdepartamento(conn, "Recepcion y hall", dep_recepcion, CENTRO_ID)
sub_wc = ensure_subdepartamento(conn, "WCs clientes", dep_pisos, CENTRO_ID)
sub_aguas = ensure_subdepartamento(conn, "Aguas y legionella", dep_mantenimiento, CENTRO_ID)

pv_buffet = row(conn, "SELECT id FROM puntos_venta WHERE empresa_id = ? AND centro_id = ? AND lower(nombre) LIKE '%buffet%' LIMIT 1", (EMPRESA_ID, CENTRO_ID))
pv_bar = row(conn, "SELECT id FROM puntos_venta WHERE empresa_id = ? AND centro_id = ? AND lower(nombre) LIKE '%bar%' LIMIT 1", (EMPRESA_ID, CENTRO_ID))
pv_buffet_id = pv_buffet["id"] if pv_buffet else None
pv_bar_id = pv_bar["id"] if pv_bar else None

zonas = {
    "buffet": ensure_zona(conn, "Hotel 1 - Zona buffet", "buffet", dep_bares, sub_buffet, pv_buffet_id, "alta", 1),
    "recepcion": ensure_zona(conn, "Hotel 1 - Recepcion y hall", "zona_comun", dep_recepcion, sub_recepcion, None, "media", 1),
    "bar": ensure_zona(conn, "Hotel 1 - Bar piscina y sala de fiestas", "bar", dep_bares, sub_bar, pv_bar_id, "alta", 1),
    "wc_bar_h": ensure_zona(conn, "Hotel 1 - WC bar hombres", "bano", dep_pisos, sub_wc, None, "alta", 1),
    "wc_bar_m": ensure_zona(conn, "Hotel 1 - WC bar mujeres", "bano", dep_pisos, sub_wc, None, "alta", 1),
    "wc_rec_h": ensure_zona(conn, "Hotel 1 - WC recepcion hombres", "bano", dep_pisos, sub_wc, None, "alta", 1),
    "wc_rec_m": ensure_zona(conn, "Hotel 1 - WC recepcion mujeres", "bano", dep_pisos, sub_wc, None, "alta", 1),
    "piscina": ensure_zona(conn, "Hotel 1 - Piscina", "piscina", dep_bares, sub_piscina, None, "alta", 1),
}

habitaciones = conn.execute(
    """
    SELECT * FROM habitaciones_recepcion
    WHERE empresa_id = ? AND centro_id = ? AND activo = 1
    ORDER BY numero
    """,
    (EMPRESA_ID, CENTRO_ID),
).fetchall()

equip_insertados = equip_actualizados = fan_insertados = 0
for h in habitaciones:
    if upsert_habitacion_equipamiento(conn, h):
        equip_insertados += 1
    else:
        equip_actualizados += 1
    _, created = ensure_activo(
        conn,
        f"H1-FC-{h['numero']}",
        f"Fan coil habitacion {h['numero']}",
        "fan_coil",
        "climatizacion",
        "habitacion",
        f"Habitacion {h['numero']} - planta {h['planta'] or '-'} - bloque {h['bloque'] or '-'}",
        dep_mantenimiento,
        sub_habitaciones,
        habitacion_id=h["id"],
        criticidad="media",
        categoria_operativa="climatizacion habitaciones",
    )
    fan_insertados += 1 if created else 0

asset_specs = [
    ("H1-BUF-MESAS-70", "Buffet - 70 mesas cuadradas 4 pax", "mesa", "mobiliario", "mobiliario", "Zona buffet", dep_bares, sub_buffet, pv_buffet_id, zonas["buffet"], "media", 0),
    ("H1-BUF-SILLAS-280", "Buffet - 280 sillas comedor", "silla", "mobiliario", "mobiliario", "Zona buffet", dep_bares, sub_buffet, pv_buffet_id, zonas["buffet"], "media", 0),
    ("H1-REC-SOFAS-2", "Recepcion y hall - 2 sofas", "sofa", "mobiliario", "mobiliario", "Recepcion y hall", dep_recepcion, sub_recepcion, None, zonas["recepcion"], "media", 0),
    ("H1-BAR-SOFAS-14", "Bar - 14 sofas de pared", "sofa", "mobiliario", "mobiliario", "Bar piscina y sala de fiestas", dep_bares, sub_bar, pv_bar_id, zonas["bar"], "media", 0),
    ("H1-BAR-MESAS-90", "Bar - 90 mesas con sillas", "mesa", "mobiliario", "mobiliario", "Bar piscina y sala de fiestas", dep_bares, sub_bar, pv_bar_id, zonas["bar"], "media", 0),
    ("H1-BAR-SILLAS-360", "Bar - 360 sillas asociadas a mesas", "silla", "mobiliario", "mobiliario", "Bar piscina y sala de fiestas", dep_bares, sub_bar, pv_bar_id, zonas["bar"], "media", 0),
    ("H1-BAR-ESCENARIO", "Escenario animacion bar", "escenario", "animacion", "equipo", "Bar piscina y sala de fiestas", dep_bares, sub_bar, pv_bar_id, zonas["bar"], "alta", 0),
    ("H1-BAR-LUCES-DISCO", "Iluminacion discoteca - focos varios", "iluminacion", "animacion", "equipo", "Escenario bar", dep_mantenimiento, sub_bar, pv_bar_id, zonas["bar"], "alta", 0),
    ("H1-BAR-SONIDO", "Equipo de sonido animacion", "sonido", "animacion", "equipo", "Escenario bar", dep_mantenimiento, sub_bar, pv_bar_id, zonas["bar"], "alta", 0),
    ("H1-PISCINA-VASO", "Piscina principal", "piscina", "appcc", "equipo_control", "Zona piscina", dep_mantenimiento, sub_piscina, None, zonas["piscina"], "critica", 1),
    ("H1-PISCINA-DEPURADORA", "Sistema depuradora piscina", "depuradora", "appcc", "equipo", "Sala tecnica piscina", dep_mantenimiento, sub_piscina, None, zonas["piscina"], "critica", 1),
    ("H1-PISCINA-TUMBONAS-40", "Piscina - 40 tumbonas PVC", "tumbona", "mobiliario", "mobiliario", "Zona piscina", dep_bares, sub_piscina, None, zonas["piscina"], "media", 0),
    ("H1-CUBIERTA-GOTERAS", "Cubierta y puntos de goteras Hotel 1", "cubierta", "edificio", "equipo", "Hotel viejo reformado 2004 - revisar goteras", dep_mantenimiento, sub_zonas, None, None, "alta", 0),
    ("H1-RED-AGUA-SANITARIA", "Red agua sanitaria Hotel 1", "agua_sanitaria", "appcc", "equipo_control", "Hotel completo", dep_mantenimiento, sub_aguas, None, None, "critica", 1),
]

for i in range(1, 5):
    asset_specs.append((f"H1-ASC-{i:02d}", f"Ascensor {i} Hotel 1", "ascensor", "elevacion", "equipo", f"Nucleo ascensores {i}", dep_mantenimiento, sub_zonas, None, None, "critica", 0))

wc_specs = [
    ("BAR-H", "WC bar hombres", zonas["wc_bar_h"], "4 urinarios, 3 tazas, 2 lavamanos"),
    ("BAR-M", "WC bar mujeres", zonas["wc_bar_m"], "3 tazas, 2 lavamanos"),
    ("REC-H", "WC recepcion hombres", zonas["wc_rec_h"], "4 urinarios, 3 tazas, 2 lavamanos"),
    ("REC-M", "WC recepcion mujeres", zonas["wc_rec_m"], "3 tazas, 2 lavamanos"),
]
for code, nombre, zona_id, detalle in wc_specs:
    asset_specs.append((f"H1-WC-{code}", f"{nombre} - {detalle}", "wc_publico", "banos", "equipo", nombre, dep_mantenimiento, sub_wc, None, zona_id, "alta", 1))

activos_creados = 0
for spec in asset_specs:
    codigo, nombre, tipo, familia, clase, ubicacion, dep, sub, pv, zona, criticidad, appcc = spec
    _, created = ensure_activo(
        conn,
        codigo,
        nombre,
        tipo,
        familia,
        clase,
        ubicacion,
        dep,
        sub,
        pv,
        zona_id=zona,
        criticidad=criticidad,
        requiere_appcc=appcc,
    )
    activos_creados += 1 if created else 0

controls = [
    ("H1-APPCC-PISC-PH", "Piscina", "Agua vaso piscina", "pH", "Riesgo quimico/irritacion", "Medicion y ajuste de pH", "Segun RD 742/2013 y normativa/autocontrol aplicable", "diaria", "Mantenimiento", "Ajustar tratamiento y repetir medicion", "Registro APPCC piscina", dep_mantenimiento, sub_piscina),
    ("H1-APPCC-PISC-CL-LIBRE", "Piscina", "Agua vaso piscina", "Cloro libre residual", "Desinfeccion insuficiente", "Control desinfectante residual", "Segun RD 742/2013 y normativa/autocontrol aplicable", "diaria", "Mantenimiento", "Corregir dosificacion y valorar cierre temporal si procede", "Registro APPCC piscina", dep_mantenimiento, sub_piscina),
    ("H1-APPCC-PISC-CL-COMB", "Piscina", "Agua vaso piscina", "Cloro combinado residual", "Subproductos/irritacion", "Control cloro combinado y renovacion", "Segun RD 742/2013", "diaria", "Mantenimiento", "Renovar agua, ajustar tratamiento y revisar carga", "Registro APPCC piscina", dep_mantenimiento, sub_piscina),
    ("H1-APPCC-PISC-TURB", "Piscina", "Agua vaso piscina", "Turbidez/transparencia", "Perdida de calidad del agua", "Control visual y/o instrumental", "Agua transparente y dentro de criterio legal", "diaria", "Mantenimiento", "Limpieza, filtracion, ajuste tratamiento", "Registro APPCC piscina", dep_mantenimiento, sub_piscina),
    ("H1-APPCC-PISC-E-COLI", "Piscina", "Analitica microbiologica", "Escherichia coli", "Contaminacion fecal", "Analitica laboratorio", "Segun RD 742/2013", "segun plan analitico", "Mantenimiento", "Cierre del vaso si procede, desinfeccion y nueva analitica", "Informe laboratorio", dep_mantenimiento, sub_piscina),
    ("H1-APPCC-PISC-PSEUDO", "Piscina", "Analitica microbiologica", "Pseudomonas aeruginosa", "Riesgo microbiologico", "Analitica laboratorio", "Segun RD 742/2013", "segun plan analitico", "Mantenimiento", "Desinfeccion, filtracion, cierre si procede y nueva analitica", "Informe laboratorio", dep_mantenimiento, sub_piscina),
    ("H1-APPCC-PISC-LEG", "Piscina", "Analitica microbiologica", "Legionella spp", "Legionelosis en vasos con aerosolizacion/climatizados", "Analitica laboratorio si aplica por instalacion/riesgo", "<100 UFC/L cuando aplique", "segun plan/riesgo", "Mantenimiento", "Aplicar protocolo legionella, desinfeccion y nueva analitica", "Informe laboratorio", dep_mantenimiento, sub_piscina),
    ("H1-APPCC-AGUA-LEG", "Agua sanitaria", "Red ACS/AFCH", "Legionella spp", "Legionelosis", "Programa de prevencion y control de legionella", "Segun RD 487/2022 y plan sanitario", "segun programa", "Mantenimiento", "Tratamiento, purga, choque termico/quimico si procede y nueva analitica", "Informe laboratorio", dep_mantenimiento, sub_aguas),
    ("H1-APPCC-AGUA-AEROBIOS", "Agua sanitaria", "Red ACS/AFCH", "Aerobios totales", "Indicador microbiologico", "Analitica de control cuando proceda", "Segun RD 487/2022 y plan sanitario", "segun programa", "Mantenimiento", "Investigar origen, limpiar, desinfectar y repetir analitica", "Informe laboratorio", dep_mantenimiento, sub_aguas),
    ("H1-APPCC-WC-LIMP", "WCs clientes", "Limpieza y desinfeccion", "Superficies y sanitarios", "Contaminacion por contacto", "Limpieza reforzada y reposicion consumibles", "Sin suciedad visible ni olores; consumibles disponibles", "varias veces al dia", "Pisos", "Repetir limpieza y abrir incidencia si hay averia", "Registro limpieza zonas", dep_pisos, sub_wc),
]
controles_creados = 0
for c in controls:
    _, created = ensure_appcc_control(conn, *c)
    controles_creados += 1 if created else 0

conn.commit()
print(
    json.dumps(
        {
            "centro_id": CENTRO_ID,
            "habitaciones": len(habitaciones),
            "equipamiento_insertado": equip_insertados,
            "equipamiento_actualizado": equip_actualizados,
            "fan_coils_nuevos": fan_insertados,
            "activos_zonas_nuevos": activos_creados,
            "controles_appcc_nuevos": controles_creados,
        },
        ensure_ascii=False,
        indent=2,
    )
)
conn.close()
