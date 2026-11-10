import os
import unicodedata
import pandas as pd
from database import get_db


DIAS_VALIDOS = {
    "LUNES": "LUNES",
    "MARTES": "MARTES",
    "MIERCOLES": "MIERCOLES",
    "MIÉRCOLES": "MIERCOLES",
    "JUEVES": "JUEVES",
    "VIERNES": "VIERNES",
    "SABADO": "SABADO",
    "SÁBADO": "SABADO",
    "DOMINGO": "DOMINGO",
}

DIAS_DETECCION = {"LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"}


def limpiar(texto):
    if texto is None:
        return ""
    t = str(texto).strip()
    if not t or t.lower() == "nan":
        return ""
    return t


def quitar_acentos(texto):
    if not texto:
        return ""
    return "".join(
        c for c in unicodedata.normalize("NFD", str(texto))
        if unicodedata.category(c) != "Mn"
    )


def normalizar(texto):
    t = limpiar(texto)
    if not t:
        return ""
    t = quitar_acentos(t)
    t = " ".join(t.split())
    return t.lower()


def nombre_visible(texto):
    t = limpiar(texto)
    if not t:
        return ""
    t = " ".join(t.split())
    if t.isupper():
        return t.title()
    return t


def obtener_columnas_tabla(conn, tabla):
    rows = conn.execute(f"PRAGMA table_info({tabla})").fetchall()
    return [r["name"] for r in rows]


def interpretar_hoja(nombre_hoja):
    t = normalizar(nombre_hoja).upper()

    semana = 1
    if "2" in t:
        semana = 2

    if "DESAYUNO" in t:
        servicio = "Desayuno"
    elif "SNACK" in t:
        servicio = "Snack"
    elif "CENA" in t:
        servicio = "Cena"
    else:
        servicio = "Almuerzo"

    if "POSTRE" in t:
        bloque = "Postres"
        es_postre = True
    elif "FRIO" in t:
        bloque = "Cuarto frío"
        es_postre = False
    elif "CALIENTE" in t:
        bloque = "Cocina caliente"
        es_postre = False
    elif "DESAYUNO" in t:
        bloque = "Desayunos"
        es_postre = False
    elif "SNACK" in t:
        bloque = "Snack"
        es_postre = False
    else:
        bloque = "General"
        es_postre = False

    return semana, servicio, bloque, es_postre


def prioridad_bloque(nombre_bloque):
    n = normalizar(nombre_bloque)

    if "frio" in n:
        return 1000
    if "caliente" in n:
        return 2000
    if "postre" in n:
        return 3000
    if "desayun" in n:
        return 1000
    if "snack" in n:
        return 1000
    return 9000


def buscar_id_por_nombre_normalizado(conn, tabla, nombre, empresa_id=1, campo_nombre="nombre", extras=None):
    if extras is None:
        extras = {}

    nombre_norm = normalizar(nombre)
    where = []
    params = []

    if "empresa_id" in obtener_columnas_tabla(conn, tabla):
        where.append("empresa_id = ?")
        params.append(empresa_id)

    for campo, valor in extras.items():
        where.append(f"{campo} = ?")
        params.append(valor)

    sql = f"SELECT * FROM {tabla}"
    if where:
        sql += " WHERE " + " AND ".join(where)

    rows = conn.execute(sql, params).fetchall()

    for row in rows:
        if normalizar(row[campo_nombre]) == nombre_norm:
            return row["id"]

    return None


def obtener_departamento_cocina(conn, empresa_id=1):
    existente = buscar_id_por_nombre_normalizado(
        conn=conn,
        tabla="departamentos",
        nombre="COCINA CENTRAL",
        empresa_id=empresa_id,
        campo_nombre="nombre",
    )
    if existente:
        return existente

    columnas = obtener_columnas_tabla(conn, "departamentos")
    datos = {}

    if "nombre" in columnas:
        datos["nombre"] = "COCINA CENTRAL"
    if "descripcion" in columnas:
        datos["descripcion"] = "Cocina principal y preparaciones"
    if "activo" in columnas:
        datos["activo"] = 1
    if "empresa_id" in columnas:
        datos["empresa_id"] = empresa_id

    cols = ", ".join(datos.keys())
    vals = ", ".join(["?"] * len(datos))
    cur = conn.execute(f"INSERT INTO departamentos ({cols}) VALUES ({vals})", list(datos.values()))
    return cur.lastrowid


def obtener_subdepto(conn, nombre, empresa_id=1):
    depto_id = obtener_departamento_cocina(conn, empresa_id=empresa_id)
    nombre_ok = nombre_visible(nombre)

    existente = buscar_id_por_nombre_normalizado(
        conn=conn,
        tabla="subdepartamentos",
        nombre=nombre_ok,
        empresa_id=empresa_id,
        campo_nombre="nombre",
        extras={"departamento_id": depto_id},
    )
    if existente:
        return existente

    columnas = obtener_columnas_tabla(conn, "subdepartamentos")
    datos = {}

    if "departamento_id" in columnas:
        datos["departamento_id"] = depto_id
    if "nombre" in columnas:
        datos["nombre"] = nombre_ok
    if "descripcion" in columnas:
        datos["descripcion"] = "Creado automáticamente al importar menús"
    if "activo" in columnas:
        datos["activo"] = 1
    if "empresa_id" in columnas:
        datos["empresa_id"] = empresa_id

    cols = ", ".join(datos.keys())
    vals = ", ".join(["?"] * len(datos))
    cur = conn.execute(f"INSERT INTO subdepartamentos ({cols}) VALUES ({vals})", list(datos.values()))
    return cur.lastrowid


def obtener_tipo_menu_por_servicio(conn, servicio, empresa_id=1):
    servicio_ok = nombre_visible(servicio)

    existente = buscar_id_por_nombre_normalizado(
        conn=conn,
        tabla="tipos_menu",
        nombre=servicio_ok,
        empresa_id=empresa_id,
        campo_nombre="nombre",
    )
    if existente:
        return existente

    columnas = obtener_columnas_tabla(conn, "tipos_menu")
    datos = {}

    if "nombre" in columnas:
        datos["nombre"] = servicio_ok
    if "punto_venta_id" in columnas:
        datos["punto_venta_id"] = None
    if "activo" in columnas:
        datos["activo"] = 1
    if "empresa_id" in columnas:
        datos["empresa_id"] = empresa_id

    cols = ", ".join(datos.keys())
    vals = ", ".join(["?"] * len(datos))
    cur = conn.execute(f"INSERT INTO tipos_menu ({cols}) VALUES ({vals})", list(datos.values()))
    return cur.lastrowid


def obtener_menu(conn, semana, dia, servicio, empresa_id=1, observaciones=""):
    fecha = f"SEMANA {semana} - {dia}"
    tipo_menu_id = obtener_tipo_menu_por_servicio(conn, servicio, empresa_id=empresa_id)

    row = conn.execute(
        """
        SELECT id
        FROM menus_diarios
        WHERE fecha = ?
          AND tipo_menu_id = ?
          AND empresa_id = ?
        LIMIT 1
        """,
        (fecha, tipo_menu_id, empresa_id),
    ).fetchone()

    if row:
        return row["id"]

    columnas = obtener_columnas_tabla(conn, "menus_diarios")
    datos = {}

    if "fecha" in columnas:
        datos["fecha"] = fecha
    if "tipo_menu_id" in columnas:
        datos["tipo_menu_id"] = tipo_menu_id
    if "observaciones" in columnas:
        datos["observaciones"] = observaciones
    if "empresa_id" in columnas:
        datos["empresa_id"] = empresa_id

    cols = ", ".join(datos.keys())
    vals = ", ".join(["?"] * len(datos))
    cur = conn.execute(f"INSERT INTO menus_diarios ({cols}) VALUES ({vals})", list(datos.values()))
    return cur.lastrowid


def obtener_receta(conn, nombre, empresa_id=1):
    nombre_ok = nombre_visible(nombre)
    nombre_norm = normalizar(nombre_ok)

    rows = conn.execute(
        """
        SELECT id, nombre
        FROM recetas
        WHERE empresa_id = ?
        """,
        (empresa_id,),
    ).fetchall()

    for r in rows:
        if normalizar(r["nombre"]) == nombre_norm:
            return r["id"]

    columnas = obtener_columnas_tabla(conn, "recetas")
    datos = {}

    if "nombre" in columnas:
        datos["nombre"] = nombre_ok
    if "descripcion" in columnas:
        datos["descripcion"] = ""
    if "alergenos" in columnas:
        datos["alergenos"] = ""
    if "categoria" in columnas:
        datos["categoria"] = ""
    if "activo" in columnas:
        datos["activo"] = 1
    if "empresa_id" in columnas:
        datos["empresa_id"] = empresa_id

    cols = ", ".join(datos.keys())
    vals = ", ".join(["?"] * len(datos))
    cur = conn.execute(f"INSERT INTO recetas ({cols}) VALUES ({vals})", list(datos.values()))
    return cur.lastrowid


def limpiar_estructura_menu(conn, menu_id):
    conn.execute(
        """
        DELETE FROM menu_platos
        WHERE menu_subdepartamento_id IN (
            SELECT id
            FROM menu_subdepartamentos
            WHERE menu_diario_id = ?
        )
        """,
        (menu_id,),
    )

    conn.execute(
        """
        DELETE FROM menu_subdepartamentos
        WHERE menu_diario_id = ?
        """,
        (menu_id,),
    )


def obtener_seccion(conn, menu_id, sub_id, seccion, orden=0):
    seccion_ok = nombre_visible(seccion) if seccion else "General"
    seccion_norm = normalizar(seccion_ok)

    rows = conn.execute(
        """
        SELECT id, seccion, orden
        FROM menu_subdepartamentos
        WHERE menu_diario_id = ?
          AND subdepartamento_id = ?
        """,
        (menu_id, sub_id),
    ).fetchall()

    for r in rows:
        if normalizar(r["seccion"]) == seccion_norm:
            if r["orden"] is None or orden < r["orden"]:
                conn.execute(
                    "UPDATE menu_subdepartamentos SET orden = ? WHERE id = ?",
                    (orden, r["id"])
                )
            return r["id"]

    columnas = obtener_columnas_tabla(conn, "menu_subdepartamentos")
    datos = {}

    if "menu_diario_id" in columnas:
        datos["menu_diario_id"] = menu_id
    if "subdepartamento_id" in columnas:
        datos["subdepartamento_id"] = sub_id
    if "seccion" in columnas:
        datos["seccion"] = seccion_ok
    if "orden" in columnas:
        datos["orden"] = orden

    cols = ", ".join(datos.keys())
    vals = ", ".join(["?"] * len(datos))
    cur = conn.execute(f"INSERT INTO menu_subdepartamentos ({cols}) VALUES ({vals})", list(datos.values()))
    return cur.lastrowid


def insertar_plato(conn, seccion_id, receta_id, orden=0):
    row = conn.execute(
        """
        SELECT id
        FROM menu_platos
        WHERE menu_subdepartamento_id = ?
          AND receta_id = ?
          AND orden = ?
        LIMIT 1
        """,
        (seccion_id, receta_id, orden),
    ).fetchone()

    if row:
        return row["id"]

    columnas = obtener_columnas_tabla(conn, "menu_platos")
    datos = {}

    if "menu_subdepartamento_id" in columnas:
        datos["menu_subdepartamento_id"] = seccion_id
    if "receta_id" in columnas:
        datos["receta_id"] = receta_id
    if "orden" in columnas:
        datos["orden"] = orden

    cols = ", ".join(datos.keys())
    vals = ", ".join(["?"] * len(datos))
    cur = conn.execute(f"INSERT INTO menu_platos ({cols}) VALUES ({vals})", list(datos.values()))
    return cur.lastrowid


def detectar_fila_dias(df):
    max_filas = min(20, len(df))

    for i in range(max_filas):
        fila = [quitar_acentos(limpiar(x)).upper() for x in df.iloc[i]]

        hits = 0
        for val in fila:
            for d in DIAS_DETECCION:
                if d in val:
                    hits += 1
                    break

        if hits >= 3:
            return i

    return None


def detectar_columnas_dias(df, fila_dias):
    columnas = {}
    fila = [quitar_acentos(limpiar(x)).upper() for x in df.iloc[fila_dias]]

    for idx, val in enumerate(fila):
        for d in DIAS_VALIDOS:
            if d in val:
                columnas[idx] = DIAS_VALIDOS[d]
                break

    return columnas


def hoja_es_formato_tabla(df, fila_dias):
    if fila_dias is None:
        return False

    fila = [normalizar(x) for x in df.iloc[fila_dias].tolist()]
    joined = " | ".join(fila)

    if "departamento" in joined and "subdepartamento" in joined and "seccion" in joined:
        return True

    if fila_dias + 1 < len(df):
        fila_sig = [normalizar(x) for x in df.iloc[fila_dias + 1].tolist()]
        joined_sig = " | ".join(fila_sig)
        if "departamento" in joined_sig and "subdepartamento" in joined_sig and "seccion" in joined_sig:
            return True

    return False


def normalizar_subdepto_excel(subdepto_excel, bloque_hoja):
    sx = normalizar(subdepto_excel)
    if "frio" in sx:
        return "COCINA FRIA"
    if "caliente" in sx:
        return "COCINA CALIENTE"
    if "pasteler" in sx or "postre" in sx:
        return "Postres"
    if "desayun" in sx:
        return "DESAYUNOS"
    if "snack" in sx:
        return "Snack"
    return bloque_hoja


def importar_hoja_formato_tabla(conn, df, archivo, hoja, semana, servicio, bloque, es_postre, menus_reiniciados, empresa_id=1):
    fila_dias = detectar_fila_dias(df)
    if fila_dias is None:
        print(f"  AVISO: no se detectó fila de días en {hoja}")
        return 0

    columnas = detectar_columnas_dias(df, fila_dias)
    if not columnas:
        print(f"  AVISO: no se detectaron columnas de días en {hoja}")
        return 0

    inicio_datos = fila_dias + 1
    fila_cabecera_real = [normalizar(x) for x in df.iloc[fila_dias].tolist()]
    if "departamento" in " | ".join(fila_cabecera_real):
        inicio_datos = fila_dias + 1
    elif fila_dias + 1 < len(df):
        fila_sig = [normalizar(x) for x in df.iloc[fila_dias + 1].tolist()]
        if "departamento" in " | ".join(fila_sig):
            inicio_datos = fila_dias + 2

    platos = 0

    for i in range(inicio_datos, len(df)):
        fila = [limpiar(x) for x in df.iloc[i].tolist()]
        if not any(fila):
            continue

        subdepto_excel = fila[1] if len(fila) > 1 else ""
        seccion = fila[3] if len(fila) > 3 else "General"

        subdepto_final = normalizar_subdepto_excel(subdepto_excel, bloque)
        sub_id = obtener_subdepto(conn, subdepto_final, empresa_id=empresa_id)
        base_orden = prioridad_bloque(subdepto_final)

        for col, dia in columnas.items():
            if col >= len(fila):
                continue

            plato = limpiar(fila[col])
            if not plato:
                continue

            semanas_a_usar = [semana]
            if es_postre:
                semanas_a_usar = [1, 2]

            for s in semanas_a_usar:
                menu_id = obtener_menu(
                    conn=conn,
                    semana=s,
                    dia=dia,
                    servicio=servicio,
                    empresa_id=empresa_id,
                    observaciones=f"Importado desde {archivo} | {hoja}",
                )

                if menu_id not in menus_reiniciados:
                    limpiar_estructura_menu(conn, menu_id)
                    menus_reiniciados.add(menu_id)

                orden_final = base_orden + i

                seccion_id = obtener_seccion(
                    conn=conn,
                    menu_id=menu_id,
                    sub_id=sub_id,
                    seccion=seccion,
                    orden=orden_final,
                )

                receta_id = obtener_receta(
                    conn=conn,
                    nombre=plato,
                    empresa_id=empresa_id,
                )

                insertar_plato(
                    conn=conn,
                    seccion_id=seccion_id,
                    receta_id=receta_id,
                    orden=i,
                )

                platos += 1

    return platos


def importar_excel(ruta, empresa_id=1):
    conn = get_db()

    if not os.path.exists(ruta):
        conn.close()
        raise FileNotFoundError(f"No existe la ruta: {ruta}")

    archivos = [f for f in os.listdir(ruta) if f.lower().endswith(".xlsx") or f.lower().endswith(".xls")]

    total_archivos = 0
    total_hojas = 0
    total_platos = 0
    menus_reiniciados = set()

    for archivo in archivos:
        total_archivos += 1
        print("IMPORTANDO:", archivo)

        ruta_archivo = os.path.join(ruta, archivo)

        try:
            xls = pd.ExcelFile(ruta_archivo)
        except Exception as e:
            print(f"  ERROR abriendo archivo {archivo}: {e}")
            continue

        for hoja in xls.sheet_names:
            total_hojas += 1
            print(" HOJA:", hoja)

            try:
                df = xls.parse(hoja, header=None)
            except Exception as e:
                print(f"  ERROR leyendo hoja {hoja}: {e}")
                continue

            if df.shape[0] == 0 or df.shape[1] == 0:
                print(f"  AVISO: hoja vacía {hoja}")
                continue

            semana, servicio, bloque, es_postre = interpretar_hoja(hoja)
            fila_dias = detectar_fila_dias(df)

            if hoja_es_formato_tabla(df, fila_dias):
                platos = importar_hoja_formato_tabla(
                    conn=conn,
                    df=df,
                    archivo=archivo,
                    hoja=hoja,
                    semana=semana,
                    servicio=servicio,
                    bloque=bloque,
                    es_postre=es_postre,
                    menus_reiniciados=menus_reiniciados,
                    empresa_id=empresa_id,
                )
                total_platos += platos
                conn.commit()
                continue

            print(f"  AVISO: formato no soportado todavía en {hoja}")

    menus_creados = conn.execute(
        """
        SELECT COUNT(*)
        FROM menus_diarios
        WHERE observaciones LIKE 'Importado desde%'
          AND empresa_id = ?
        """,
        (empresa_id,),
    ).fetchone()[0]

    conn.close()

    print("IMPORTACIÓN COMPLETA")
    print(f"ARCHIVOS: {total_archivos}")
    print(f"HOJAS: {total_hojas}")
    print(f"MENÚS IMPORTADOS: {menus_creados}")
    print(f"PLATOS PROCESADOS: {total_platos}")


if __name__ == "__main__":
    importar_excel("menus_importar", empresa_id=1)
