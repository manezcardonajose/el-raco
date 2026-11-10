import math
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "pepe4.db"
EMPRESA_ID = 1
CENTRO_NOMBRE = "Hotel 1"
DIAS = range(7)


def clean(value):
    return (value or "").strip()


def row(conn, query, params=()):
    return conn.execute(query, params).fetchone()


def add_col(conn, table, ddl):
    name = ddl.split()[0]
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def ensure_schema(conn):
    for ddl in [
        "hora_inicio TEXT",
        "hora_fin TEXT",
        "tipo_turno TEXT",
        "horas_dia REAL DEFAULT 0",
        "plantilla_objetivo REAL DEFAULT 0",
        "cobertura_libranzas TEXT",
        "etiqueta_operativa TEXT",
    ]:
        add_col(conn, "necesidades_operativas", ddl)


def dep_id(conn, centro_id, contains):
    contains = contains.upper()
    r = row(
        conn,
        """
        SELECT id FROM departamentos
        WHERE empresa_id = ? AND centro_id = ? AND activo = 1 AND upper(nombre) LIKE ?
        ORDER BY id LIMIT 1
        """,
        (EMPRESA_ID, centro_id, f"%{contains}%"),
    )
    return r["id"] if r else None


def ensure_dep(conn, centro_id, nombre, tipo_operativo="general"):
    r = row(
        conn,
        """
        SELECT id FROM departamentos
        WHERE empresa_id = ? AND centro_id = ?
          AND lower(trim(nombre)) = lower(trim(?))
        ORDER BY activo DESC, id LIMIT 1
        """,
        (EMPRESA_ID, centro_id, nombre),
    )
    if r:
        conn.execute(
            """
            UPDATE departamentos
            SET activo = 1, tipo_operativo = COALESCE(NULLIF(tipo_operativo, ''), ?)
            WHERE id = ? AND empresa_id = ?
            """,
            (tipo_operativo, r["id"], EMPRESA_ID),
        )
        return r["id"]
    cur = conn.execute(
        """
        INSERT INTO departamentos
        (nombre, descripcion, activo, empresa_id, tipo_operativo, centro_id)
        VALUES (?, ?, 1, ?, ?, ?)
        """,
        (nombre, f"Departamento operativo independiente de {CENTRO_NOMBRE}.", EMPRESA_ID, tipo_operativo, centro_id),
    )
    return cur.lastrowid


def ensure_subdep(conn, centro_id, departamento_id, nombre):
    r = row(
        conn,
        """
        SELECT id FROM subdepartamentos
        WHERE empresa_id = ? AND centro_id = ? AND departamento_id = ?
          AND lower(trim(nombre)) = lower(trim(?))
        """,
        (EMPRESA_ID, centro_id, departamento_id, nombre),
    )
    if r:
        return r["id"]
    cur = conn.execute(
        """
        INSERT INTO subdepartamentos (nombre, departamento_id, descripcion, activo, empresa_id, centro_id)
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (nombre, departamento_id, f"Base necesidades operativas {CENTRO_NOMBRE}.", EMPRESA_ID, centro_id),
    )
    return cur.lastrowid


def ensure_puesto(conn, centro_id, departamento_id, subdepartamento_id, nombre, descripcion=""):
    r = row(
        conn,
        """
        SELECT id FROM puestos_operativos
        WHERE empresa_id = ? AND centro_id = ?
          AND COALESCE(departamento_id,0) = COALESCE(?,0)
          AND COALESCE(subdepartamento_id,0) = COALESCE(?,0)
          AND lower(trim(nombre)) = lower(trim(?))
        """,
        (EMPRESA_ID, centro_id, departamento_id, subdepartamento_id, nombre),
    )
    if r:
        return r["id"]
    cur = conn.execute(
        """
        INSERT INTO puestos_operativos
        (nombre, descripcion, centro_id, departamento_id, subdepartamento_id, empresa_id, activo)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        """,
        (nombre, descripcion, centro_id, departamento_id, subdepartamento_id, EMPRESA_ID),
    )
    return cur.lastrowid


def plantilla_para(personas_dia):
    return math.ceil((float(personas_dia) * 7.0) / 5.0)


def upsert_need(conn, centro_id, departamento_id, subdepartamento_id, puesto_id, dia, personas, inicio, fin, tipo_turno, horas, plantilla, etiqueta, obs):
    existing = row(
        conn,
        """
        SELECT id FROM necesidades_operativas
        WHERE empresa_id = ? AND centro_id = ? AND departamento_id = ?
          AND COALESCE(subdepartamento_id,0) = COALESCE(?,0)
          AND COALESCE(puesto_id,0) = COALESCE(?,0)
          AND dia_semana = ?
          AND COALESCE(etiqueta_operativa,'') = ?
        LIMIT 1
        """,
        (EMPRESA_ID, centro_id, departamento_id, subdepartamento_id, puesto_id, dia, etiqueta),
    )
    data = (
        personas,
        obs,
        1,
        inicio,
        fin,
        tipo_turno,
        horas,
        plantilla,
        "2 dias libres por persona: la necesidad diaria ya incluye cobertura minima; plantilla objetivo calculada a 5 dias/semana.",
        etiqueta,
    )
    if existing:
        conn.execute(
            """
            UPDATE necesidades_operativas
            SET personas_necesarias = ?, observaciones = ?, activo = ?, hora_inicio = ?, hora_fin = ?,
                tipo_turno = ?, horas_dia = ?, plantilla_objetivo = ?, cobertura_libranzas = ?,
                etiqueta_operativa = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND empresa_id = ?
            """,
            data + (existing["id"], EMPRESA_ID),
        )
        return False
    conn.execute(
        """
        INSERT INTO necesidades_operativas
        (centro_id, departamento_id, subdepartamento_id, puesto_id, dia_semana, personas_necesarias,
         observaciones, empresa_id, activo, hora_inicio, hora_fin, tipo_turno, horas_dia,
         plantilla_objetivo, cobertura_libranzas, etiqueta_operativa)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (centro_id, departamento_id, subdepartamento_id, puesto_id, dia) + data[:2] + (EMPRESA_ID,) + data[2:],
    )
    return True


def add_daily(conn, ctx, subdep, puesto, personas, inicio, fin, tipo, horas, etiqueta, obs, plantilla=None):
    plantilla = plantilla if plantilla is not None else plantilla_para(personas)
    created = 0
    for dia in DIAS:
        if upsert_need(conn, ctx["centro"], ctx["dep"], subdep, puesto, dia, personas, inicio, fin, tipo, horas, plantilla, etiqueta, obs):
            created += 1
    return created


def ensure_office_planta_8(conn, centro_id, dep_pisos, sub_habitaciones):
    r = row(
        conn,
        """
        SELECT id FROM almacenes
        WHERE empresa_id = ? AND centro_id = ? AND lower(trim(nombre)) = 'office planta 8'
        """,
        (EMPRESA_ID, centro_id),
    )
    if r:
        conn.execute(
            "UPDATE almacenes SET activo = 1, departamento_id = ?, subdepartamento_id = ? WHERE id = ?",
            (dep_pisos, sub_habitaciones, r["id"]),
        )
        return False
    conn.execute(
        """
        INSERT INTO almacenes
        (nombre, tipo, ubicacion, responsable, activo, empresa_id, departamento_id, subdepartamento_id, centro_id)
        VALUES ('Office planta 8', 'seco', 'Planta 8', 'Pisos', 1, ?, ?, ?, ?)
        """,
        (EMPRESA_ID, dep_pisos, sub_habitaciones, centro_id),
    )
    return True


conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
ensure_schema(conn)

centro = row(conn, "SELECT id FROM centros_trabajo WHERE empresa_id = ? AND lower(nombre) = lower(?) AND activo = 1", (EMPRESA_ID, CENTRO_NOMBRE))
if not centro:
    raise SystemExit("No encuentro Hotel 1 activo.")
centro_id = centro["id"]

dep_pisos = dep_id(conn, centro_id, "PISOS")
dep_bares = dep_id(conn, centro_id, "BARES")
dep_cocina = dep_id(conn, centro_id, "COCINA")
dep_recepcion = dep_id(conn, centro_id, "RECEPC")
dep_mantenimiento = dep_id(conn, centro_id, "MATEN") or dep_id(conn, centro_id, "MANTEN")
dep_direccion = dep_id(conn, centro_id, "DIRECCION")
dep_animacion = ensure_dep(conn, centro_id, "ANIMACION", "animacion")
dep_piscina = ensure_dep(conn, centro_id, "PISCINA", "piscina")
missing = [name for name, val in {
    "pisos": dep_pisos, "bares": dep_bares, "cocina": dep_cocina,
    "recepcion": dep_recepcion, "mantenimiento": dep_mantenimiento, "direccion": dep_direccion,
    "animacion": dep_animacion, "piscina": dep_piscina,
}.items() if not val]
if missing:
    raise SystemExit(f"Faltan departamentos en Hotel 1: {missing}")

subs = {
    "habitaciones": ensure_subdep(conn, centro_id, dep_pisos, "Habitaciones"),
    "zonas": ensure_subdep(conn, centro_id, dep_pisos, "Zonas comunes"),
    "noche": ensure_subdep(conn, centro_id, dep_pisos, "Guardia noche pisos"),
    "gobernanta": ensure_subdep(conn, centro_id, dep_pisos, "Gobernanta"),
    "bar": ensure_subdep(conn, centro_id, dep_bares, "Bar piscina y sala de fiestas"),
    "buffet": ensure_subdep(conn, centro_id, dep_bares, "Buffet"),
    "desayunos": ensure_subdep(conn, centro_id, dep_cocina, "Desayunos"),
    "frio": ensure_subdep(conn, centro_id, dep_cocina, "Cuarto frio"),
    "caliente": ensure_subdep(conn, centro_id, dep_cocina, "Cocina caliente"),
    "plonge": ensure_subdep(conn, centro_id, dep_cocina, "Tren lavado y plonge"),
    "piscina": ensure_subdep(conn, centro_id, dep_piscina, "Piscina"),
    "animacion": ensure_subdep(conn, centro_id, dep_animacion, "Animacion"),
    "recepcion": ensure_subdep(conn, centro_id, dep_recepcion, "Recepcion 24h"),
    "direccion": ensure_subdep(conn, centro_id, dep_direccion, "Direccion operativa"),
    "mantenimiento": ensure_subdep(conn, centro_id, dep_mantenimiento, "Mantenimiento operativo"),
}

office8_created = ensure_office_planta_8(conn, centro_id, dep_pisos, subs["habitaciones"])

created = 0

# Pisos por planta: 20 habitaciones por planta, 2 camareras diarias, plantilla objetivo 3 por planta.
for planta in range(1, 9):
    puesto = ensure_puesto(conn, centro_id, dep_pisos, subs["habitaciones"], f"Camarera pisos planta {planta}", "Limpieza habitaciones y reposicion office.")
    obs = f"Planta {planta}: 20 habitaciones. Necesidad diaria 2 personas. Office planta {planta} para toallas, sabanas y amenities. Plantilla objetivo: 3 para cubrir 2 libranzas por persona."
    created += add_daily(conn, {"centro": centro_id, "dep": dep_pisos}, subs["habitaciones"], puesto, 2, "08:00", "16:00", "seguido", 8, f"H1-PISOS-PLANTA-{planta}", obs, plantilla=3)

created += add_daily(conn, {"centro": centro_id, "dep": dep_pisos}, subs["zonas"], ensure_puesto(conn, centro_id, dep_pisos, subs["zonas"], "Limpieza zonas comunes"), 3, "08:00", "16:00", "seguido", 8, "H1-PISOS-ZONAS-COMUNES", "Zonas comunes: 2-3 personas diarias. Se fija 3 para cobertura alta y hotel completo.", plantilla=5)
created += add_daily(conn, {"centro": centro_id, "dep": dep_pisos}, subs["noche"], ensure_puesto(conn, centro_id, dep_pisos, subs["noche"], "Guardia noche pisos"), 1, "23:00", "07:00", "noche", 8, "H1-PISOS-GUARDIA-NOCHE", "Una mujer/persona de guardia por la noche para incidencias, emergencias de limpieza y apoyo operativo.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_pisos}, subs["gobernanta"], ensure_puesto(conn, centro_id, dep_pisos, subs["gobernanta"], "Gobernanta"), 1, "08:00", "16:00", "seguido", 8, "H1-PISOS-GOBERNANTA", "Falta gobernanta. No debe quedar el departamento sin mando en salida/entrada.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_pisos}, subs["gobernanta"], ensure_puesto(conn, centro_id, dep_pisos, subs["gobernanta"], "Subgobernanta"), 1, "12:00", "20:00", "seguido", 8, "H1-PISOS-SUBGOBERNANTA", "Falta subgobernanta. Debe cubrir tarde y no librar el mismo dia que gobernanta.", plantilla=2)

# Bar piscina / sala fiestas.
created += add_daily(conn, {"centro": centro_id, "dep": dep_bares}, subs["bar"], ensure_puesto(conn, centro_id, dep_bares, subs["bar"], "Camarero bar turno seguido"), 1, "17:00", "01:00", "seguido", 8, "H1-BAR-SEGUIDO", "Bar abre 10:00 y cierra 01:00. Turno seguido cubre cierre y tramo fuerte nocturno.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_bares}, subs["bar"], ensure_puesto(conn, centro_id, dep_bares, subs["bar"], "Camarero bar partido A"), 1, "10:00", "01:00", "partido", 8, "H1-BAR-PARTIDO-A", "Turno partido recomendado: 10:00-14:00 y 21:00-01:00.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_bares}, subs["bar"], ensure_puesto(conn, centro_id, dep_bares, subs["bar"], "Camarero bar partido B"), 1, "14:00", "00:00", "partido", 8, "H1-BAR-PARTIDO-B", "Turno partido recomendado: 14:00-18:00 y 20:00-00:00. Cubre hueco tarde y refuerzo noche.", plantilla=2)

# Buffet sala.
created += add_daily(conn, {"centro": centro_id, "dep": dep_bares}, subs["buffet"], ensure_puesto(conn, centro_id, dep_bares, subs["buffet"], "Jefe de rango buffet manana"), 1, "05:00", "13:00", "seguido", 8, "H1-BUFFET-JEFE-MANANA", "Jefe de rango entra 05:00 para preparacion y desayuno. Necesita sustituto en libranzas.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_bares}, subs["buffet"], ensure_puesto(conn, centro_id, dep_bares, subs["buffet"], "Jefe de rango buffet cena"), 1, "14:00", "22:00", "seguido", 8, "H1-BUFFET-JEFE-CENA", "Cena de 18:30 a 21:30 con jefe de rango. Si el jefe de manana no cubre cena, hace falta este rol.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_bares}, subs["buffet"], ensure_puesto(conn, centro_id, dep_bares, subs["buffet"], "Camarero buffet desayuno"), 4, "07:30", "10:20", "servicio", 2.83, "H1-BUFFET-DESAYUNO", "Desayuno 07:30-10:20. Base de 4 camareros a turno partido rotando con almuerzo/cena.", plantilla=6)
created += add_daily(conn, {"centro": centro_id, "dep": dep_bares}, subs["buffet"], ensure_puesto(conn, centro_id, dep_bares, subs["buffet"], "Camarero buffet almuerzo"), 3, "12:30", "15:00", "servicio", 2.5, "H1-BUFFET-ALMUERZO", "Almuerzo 12:30-15:00. Necesidad minima 3 camareros.", plantilla=5)
created += add_daily(conn, {"centro": centro_id, "dep": dep_bares}, subs["buffet"], ensure_puesto(conn, centro_id, dep_bares, subs["buffet"], "Camarero buffet cena"), 4, "18:30", "21:30", "servicio", 3, "H1-BUFFET-CENA", "Cena 18:30-21:30. Necesidad 4 camareros mas jefe de rango.", plantilla=6)

# Cocina.
created += add_daily(conn, {"centro": centro_id, "dep": dep_cocina}, subs["desayunos"], ensure_puesto(conn, centro_id, dep_cocina, subs["desayunos"], "Jefe partida desayunos"), 1, "05:30", "13:30", "seguido", 8, "H1-COC-DES-JEFE", "Jefe partida desayunos con sustituto en libranzas.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_cocina}, subs["desayunos"], ensure_puesto(conn, centro_id, dep_cocina, subs["desayunos"], "Cocinero/ayudante desayunos"), 1, "06:00", "14:00", "seguido", 8, "H1-COC-DES-AYUDANTE", "Cocinero o ayudante desayunos con sustituto.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_cocina}, subs["frio"], ensure_puesto(conn, centro_id, dep_cocina, subs["frio"], "Jefa partida cuarto frio"), 1, "08:00", "16:00", "seguido", 8, "H1-COC-FRIO-JEFA", "Cuarto frio 08:00-16:00.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_cocina}, subs["frio"], ensure_puesto(conn, centro_id, dep_cocina, subs["frio"], "Ayudante cuarto frio"), 1, "08:00", "16:00", "seguido", 8, "H1-COC-FRIO-AYUDANTE", "Ayudante cuarto frio 08:00-16:00.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_cocina}, subs["caliente"], ensure_puesto(conn, centro_id, dep_cocina, subs["caliente"], "Jefe partida caliente"), 1, "08:00", "16:00", "seguido", 8, "H1-COC-CAL-JEFE", "Jefe partida caliente. Chef de cadena compartido no cuenta como cobertura fija diaria.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_cocina}, subs["caliente"], ensure_puesto(conn, centro_id, dep_cocina, subs["caliente"], "Cocinero caliente manana"), 1, "08:00", "16:00", "seguido", 8, "H1-COC-CAL-MANANA", "Cocinero caliente turno manana.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_cocina}, subs["caliente"], ensure_puesto(conn, centro_id, dep_cocina, subs["caliente"], "Cocinero caliente tarde"), 1, "14:30", "22:30", "seguido", 8, "H1-COC-CAL-TARDE", "Cocinero caliente turno tarde.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_cocina}, subs["caliente"], ensure_puesto(conn, centro_id, dep_cocina, subs["caliente"], "Segundo cocina partido"), 1, "11:30", "22:00", "partido", 8, "H1-COC-SEGUNDO-PARTIDO", "Segundo de cocina a turno partido: 11:30-15:30 y 18:00-22:00. Actualmente sin sustituto: riesgo operativo.", plantilla=1)
created += add_daily(conn, {"centro": centro_id, "dep": dep_cocina}, subs["caliente"], ensure_puesto(conn, centro_id, dep_cocina, subs["caliente"], "Bufetero cocina"), 3, "11:30", "22:00", "partido", 8, "H1-COC-BUFETEROS", "Bufeteros 3 servicios: 11:30-15:30 y 18:00-22:00. Contar libranzas y sustitucion.", plantilla=5)
created += add_daily(conn, {"centro": centro_id, "dep": dep_cocina}, subs["plonge"], ensure_puesto(conn, centro_id, dep_cocina, subs["plonge"], "Tren de lavado"), 1, "09:00", "23:00", "partido", 8, "H1-COC-TREN-LAVADO", "Tren lavado: 09:00-13:00 y 19:00-23:00.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_cocina}, subs["plonge"], ensure_puesto(conn, centro_id, dep_cocina, subs["plonge"], "Plonge / lava perolas"), 1, "09:00", "23:00", "partido", 8, "H1-COC-PLONGE", "Plonge/steward: 09:00-13:00 y 19:00-23:00.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_cocina}, subs["plonge"], ensure_puesto(conn, centro_id, dep_cocina, subs["plonge"], "Pinche apoyo limpieza cocina"), 1, "10:00", "18:00", "seguido", 8, "H1-COC-PINCHE-APOYO", "Apoyo a tren/plonge y limpieza cocina en horas fuertes. Turno seguido 10:00-18:00.", plantilla=2)

# Piscina, animacion, recepcion, direccion, mantenimiento.
created += add_daily(conn, {"centro": centro_id, "dep": dep_piscina}, subs["piscina"], ensure_puesto(conn, centro_id, dep_piscina, subs["piscina"], "Socorrista"), 1, "10:00", "18:00", "seguido", 8, "H1-PISC-SOCORRISTA", "Socorrista piscina. Departamento independiente de bares/restaurantes; con 2 dias libres necesita sustitucion.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_animacion}, subs["animacion"], ensure_puesto(conn, centro_id, dep_animacion, subs["animacion"], "Animacion"), 4, "16:00", "00:00", "seguido", 8, "H1-ANIMACION", "Equipo animacion base 4 personas. Departamento independiente de bares/restaurantes; coordina con bar solo cuando usa escenario o sala.", plantilla=6)
for inicio, fin, etiqueta, obs in [
    ("00:00", "08:00", "H1-REC-NOCHE", "Recepcion noche: 2 personas."),
    ("08:00", "16:00", "H1-REC-MANANA", "Recepcion manana: 2 personas."),
    ("16:00", "00:00", "H1-REC-TARDE", "Recepcion tarde: 2 personas."),
]:
    created += add_daily(conn, {"centro": centro_id, "dep": dep_recepcion}, subs["recepcion"], ensure_puesto(conn, centro_id, dep_recepcion, subs["recepcion"], f"Recepcion {inicio}-{fin}"), 2, inicio, fin, "seguido", 8, etiqueta, obs, plantilla=3)
created += add_daily(conn, {"centro": centro_id, "dep": dep_direccion}, subs["direccion"], ensure_puesto(conn, centro_id, dep_direccion, subs["direccion"], "Director"), 1, "09:00", "21:00", "partido", 8, "H1-DIR-DIRECTOR", "Director turno partido. Director y subdirector no deben librar el mismo dia; minimo uno en horas fuertes.", plantilla=1)
created += add_daily(conn, {"centro": centro_id, "dep": dep_direccion}, subs["direccion"], ensure_puesto(conn, centro_id, dep_direccion, subs["direccion"], "Subdirector"), 1, "10:00", "22:00", "partido", 8, "H1-DIR-SUBDIRECTOR", "Subdirector turno partido. Coordinar libranzas con director.", plantilla=1)
created += add_daily(conn, {"centro": centro_id, "dep": dep_mantenimiento}, subs["mantenimiento"], ensure_puesto(conn, centro_id, dep_mantenimiento, subs["mantenimiento"], "Segundo mantenimiento"), 1, "08:00", "16:00", "seguido", 8, "H1-MANT-SEGUNDO", "Jefe de mantenimiento de cadena compartido; segundo cubre operacion diaria.", plantilla=2)
created += add_daily(conn, {"centro": centro_id, "dep": dep_mantenimiento}, subs["mantenimiento"], ensure_puesto(conn, centro_id, dep_mantenimiento, subs["mantenimiento"], "Tecnico mantenimiento"), 2, "08:00", "16:00", "seguido", 8, "H1-MANT-TECNICOS", "Base 3 tecnicos para cubrir preventivo/correctivo y libranzas. Necesidad diaria 2.", plantilla=3)
created += add_daily(conn, {"centro": centro_id, "dep": dep_mantenimiento}, subs["mantenimiento"], ensure_puesto(conn, centro_id, dep_mantenimiento, subs["mantenimiento"], "Ayudante mantenimiento/piscina"), 2, "07:00", "15:00", "seguido", 8, "H1-MANT-AYUDANTES", "Base 3 ayudantes para piscina, exteriores, apoyo tecnico y libranzas. Necesidad diaria 2.", plantilla=3)

conn.commit()
total_h1 = row(conn, "SELECT COUNT(*) total FROM necesidades_operativas WHERE empresa_id=? AND centro_id=? AND activo=1", (EMPRESA_ID, centro_id))["total"]
plantilla = row(conn, "SELECT SUM(plantilla_objetivo) total FROM necesidades_operativas WHERE empresa_id=? AND centro_id=? AND activo=1 AND dia_semana=0", (EMPRESA_ID, centro_id))["total"]
print({
    "centro_id": centro_id,
    "office_planta_8_creado": office8_created,
    "necesidades_creadas_o_actualizadas": created,
    "necesidades_activas_hotel1": total_h1,
    "plantilla_objetivo_base_lunes": plantilla,
})
conn.close()
