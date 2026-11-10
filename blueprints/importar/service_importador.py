import os
import pandas as pd
from database import get_db

EXTENSIONES_VALIDAS = (".xlsx", ".xls", ".xlsm")


def recorrer_carpeta(ruta):
    archivos = []
    if not ruta or not os.path.exists(ruta):
        return archivos

    for root, _, files in os.walk(ruta):
        for file in files:
            if file.lower().endswith(EXTENSIONES_VALIDAS):
                archivos.append(os.path.join(root, file))
    return archivos


def limpiar_texto(texto):
    if texto is None:
        return ""

    # pandas NaN
    try:
        if pd.isna(texto):
            return ""
    except Exception:
        pass

    texto = str(texto).strip()

    if texto.lower() in ("", "nan", "none", "null"):
        return ""

    return texto


def normalizar_texto(texto):
    return limpiar_texto(texto).lower()


def receta_existe(nombre):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM recetas WHERE LOWER(nombre) = ?",
        (normalizar_texto(nombre),)
    )
    row = cursor.fetchone()
    conn.close()
    return row["id"] if row else None


def actualizar_alergenos_si_faltan(receta_id, alergenos):
    alergenos = limpiar_texto(alergenos)
    if not receta_id or not alergenos:
        return False

    conn = get_db()
    receta = conn.execute(
        "SELECT alergenos FROM recetas WHERE id = ?",
        (receta_id,)
    ).fetchone()

    if not receta or limpiar_texto(receta["alergenos"]):
        conn.close()
        return False

    conn.execute(
        "UPDATE recetas SET alergenos = ? WHERE id = ?",
        (alergenos, receta_id)
    )
    conn.commit()
    conn.close()
    return True


def obtener_o_crear_producto(nombre, conn):
    nombre_limpio = limpiar_texto(nombre)
    if not nombre_limpio:
        return None

    cursor = conn.cursor()

    cursor.execute(
        "SELECT id FROM productos WHERE LOWER(nombre) = ?",
        (normalizar_texto(nombre_limpio),)
    )
    row = cursor.fetchone()

    if row:
        return row["id"]

    cursor.execute(
        """
        INSERT INTO productos (nombre, activo, empresa_id)
        VALUES (?, 1, 1)
        """,
        (nombre_limpio,)
    )
    return cursor.lastrowid


def guardar_receta(nombre, ingredientes, elaboracion, alergenos=""):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO recetas (nombre, descripcion, alergenos, empresa_id)
        VALUES (?, ?, ?, 1)
        """,
        (limpiar_texto(nombre), limpiar_texto(elaboracion), limpiar_texto(alergenos))
    )
    receta_id = cursor.lastrowid

    for ingrediente in ingredientes:
        producto_id = obtener_o_crear_producto(ingrediente, conn)
        if not producto_id:
            continue

        cursor.execute(
            """
            INSERT INTO recetas_ingredientes (receta_id, producto_id, cantidad)
            VALUES (?, ?, ?)
            """,
            (receta_id, producto_id, 0)
        )

    conn.commit()
    conn.close()
    return receta_id


def extraer_receta_de_hoja(df, nombre_hoja=""):
    nombre_receta = limpiar_texto(str(nombre_hoja))
    ingredientes = []
    elaboracion = ""
    alergenos_detectados = set()

    # Mapeo de códigos a nombres de alérgenos
    mapa_alergenos = {
        "1": "Mostaza",
        "2": "Sésamo",
        "3": "Cereales con gluten",
        "3.1": "Trigo",
        "3.2": "Centeno",
        "3.3": "Cebada",
        "3.4": "Avena",
        "3.5": "Espelta",
        "4": "Altramuces",
        "5": "Frutos con cáscara",
        "5.1": "Almendra",
        "5.2": "Avellana",
        "5.3": "Nuez",
        "5.4": "Anacardo",
        "5.5": "Pacana",
        "5.6": "Nuez de Brasil",
        "5.7": "Pistacho",
        "5.8": "Macadamia",
        "6": "Cacahuete",
        "7": "Huevo",
        "8": "Pescado",
        "9": "Crustáceos",
        "10": "Moluscos",
        "11": "Leche",
        "12": "Soja",
        "13": "Apio",
        "14": "Sulfitos",
    }

    fila_ingredientes = None
    fila_fin_ingredientes = None
    fila_codigos_alergenos = 8   # fila 9 visual en Excel (índice 0-based)

    # 1. Buscar fila de inicio de ingredientes
    for i in range(len(df)):
        valor = limpiar_texto(df.iloc[i, 0] if len(df.columns) > 0 else "")
        if "INGREDIENTES" in valor.upper():
            fila_ingredientes = i + 1
            break

    if fila_ingredientes is None:
        fila_ingredientes = 9

    # 2. Buscar final de ingredientes
    for i in range(fila_ingredientes, len(df)):
        valor = limpiar_texto(df.iloc[i, 0] if len(df.columns) > 0 else "")
        valor_upper = valor.upper()

        if (
            valor_upper.startswith("1. MOSTAZA")
            or valor_upper.startswith("2. SÉSAMO")
            or valor_upper.startswith("2. SESAMO")
            or valor_upper.startswith("3. CEREALES")
            or "ELABORACIÓN" in valor_upper
            or "ELABORACION" in valor_upper
        ):
            fila_fin_ingredientes = i
            break

    if fila_fin_ingredientes is None:
        fila_fin_ingredientes = min(fila_ingredientes + 20, len(df))

    # 3. Leer ingredientes y alérgenos por fila
    for i in range(fila_ingredientes, fila_fin_ingredientes):
        valor = limpiar_texto(df.iloc[i, 0] if len(df.columns) > 0 else "")
        if not valor:
            continue

        valor_upper = valor.upper()

        if (
            valor_upper == "INGREDIENTES"
            or valor_upper.startswith("1.")
            or valor_upper.startswith("2.")
            or valor_upper.startswith("3.")
            or valor_upper.startswith("4.")
            or valor_upper.startswith("5.")
            or valor_upper.startswith("6.")
            or valor_upper.startswith("7.")
            or valor_upper.startswith("8.")
            or valor_upper.startswith("9.")
            or valor_upper.startswith("10.")
            or valor_upper.startswith("11.")
            or valor_upper.startswith("12.")
            or valor_upper.startswith("13.")
            or valor_upper.startswith("14.")
        ):
            continue

        ingredientes.append(valor)

        # Leer marcas X / T en columnas C a AA aprox
        for col in range(2, min(27, len(df.columns))):
            try:
                marca = limpiar_texto(df.iloc[i, col]).upper()
            except Exception:
                marca = ""

            if marca in ("X", "T"):
                try:
                    codigo = limpiar_texto(df.iloc[fila_codigos_alergenos, col])
                except Exception:
                    codigo = ""

                if codigo in mapa_alergenos:
                    alergenos_detectados.add(mapa_alergenos[codigo])

            # Agrupadores generales
                if codigo.startswith("3"):
                    alergenos_detectados.add("Cereales con gluten")
                if codigo.startswith("5"):
                    alergenos_detectados.add("Frutos con cáscara")
                    

    # 4. Quitar duplicados de ingredientes conservando orden
    ingredientes_limpios = []
    vistos = set()
    for ing in ingredientes:
        clave = ing.lower()
        if clave not in vistos:
            vistos.add(clave)
            ingredientes_limpios.append(ing)

    ingredientes = ingredientes_limpios

    # 5. Buscar elaboración
    for i in range(len(df)):
        fila_textos = []
        for j in range(min(6, len(df.columns))):
            try:
                v = limpiar_texto(df.iloc[i, j])
            except Exception:
                v = ""
            if v:
                fila_textos.append(v)

        fila_unida = " ".join(fila_textos).strip()
        if not fila_unida:
            continue

        fila_upper = fila_unida.upper()

        if "ELABORACIÓN" in fila_upper or "ELABORACION" in fila_upper:
            lineas_elaboracion = []
            for k in range(i + 1, min(i + 15, len(df))):
                valores = []
                for c in range(min(6, len(df.columns))):
                    try:
                        vv = limpiar_texto(df.iloc[k, c])
                    except Exception:
                        vv = ""
                    if vv:
                        valores.append(vv)

                linea = " ".join(valores).strip()
                if linea:
                    lineas_elaboracion.append(linea)

            elaboracion = " ".join(lineas_elaboracion).strip()
            break

    alergenos = ", ".join(sorted(alergenos_detectados))
    return nombre_receta, ingredientes, elaboracion, alergenos

    print("RECETA:", nombre)
    print("ALERGENOS:", alergenos)
    print("INGREDIENTES:", ingredientes)

def procesar_archivo(ruta_archivo, stats):
    try:
        if ruta_archivo.lower().endswith(".xls"):
            xls = pd.ExcelFile(ruta_archivo, engine="xlrd")
        else:
            xls = pd.ExcelFile(ruta_archivo)

        stats["archivos_procesados"] += 1
        print("PROCESANDO:", ruta_archivo)

        for hoja in xls.sheet_names:
            try:
                df = xls.parse(sheet_name=hoja, header=None)
                stats["hojas_procesadas"] += 1

                nombre, ingredientes, elaboracion, alergenos = extraer_receta_de_hoja(df, str(hoja))

                if not nombre:
                    stats["errores"].append(f"Sin nombre de receta: {ruta_archivo} / {hoja}")
                    continue

                receta_id_existente = receta_existe(nombre)
                if receta_id_existente:
                    if actualizar_alergenos_si_faltan(receta_id_existente, alergenos):
                        stats["alergenos_actualizados"] = stats.get("alergenos_actualizados", 0) + 1
                    stats["recetas_omitidas"] += 1
                    continue
                print("  HOJA:", hoja)

                guardar_receta(nombre, ingredientes, elaboracion, alergenos)
                stats["recetas_nuevas"] += 1
                stats["ingredientes_total"] += len(ingredientes)

            except Exception as e:
                stats["errores"].append(f"Error en hoja {hoja} de {ruta_archivo}: {e}")

    except Exception as e:
        stats["errores"].append(f"Error abriendo archivo {ruta_archivo}: {e}")  
        print("    RECETA GUARDADA:", nombre)


def importar_desde_carpeta(ruta):
    archivos = recorrer_carpeta(ruta)

    print("RUTA:", ruta)
    print("ARCHIVOS ENCONTRADOS:", archivos)

    stats = {
        "archivos_encontrados": len(archivos),
        "archivos_procesados": 0,
        "hojas_procesadas": 0,
        "recetas_nuevas": 0,
        "recetas_omitidas": 0,
        "alergenos_actualizados": 0,
        "ingredientes_total": 0,
        "errores": []
    }

    for archivo in archivos:
        procesar_archivo(archivo, stats)

    return stats
