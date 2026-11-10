import re


def limpiar_texto(texto):
    texto = texto.replace(",", ".")
    texto = re.sub(r"\s+", " ", texto)
    return texto


def extraer_proveedor(lineas):
    for l in lineas[:5]:
        if "S.L" in l or "SL" in l or "S A" in l:
            return l.strip()
    return lineas[0].strip() if lineas else "Proveedor desconocido"


def extraer_numero(lineas):
    for l in lineas:
        m = re.search(r"\d{3}\\\d{3}-\d+", l)
        if m:
            return m.group(0)
    return None


def extraer_fecha(lineas):
    for l in lineas:
        m = re.search(r"\d{2}-\d{2}-\d{4}", l)
        if m:
            return m.group(0)
    return None


def parsear_linea(linea):
    """
    Busca:
    DESCRIPCION  CANTIDAD  PRECIO  IMPORTE
    """

    linea = limpiar_texto(linea)

    m = re.match(
        r"(.+?)\s+(\d+(?:\.\d+)?)\s+(\d+\.\d{2})\s+(\d+\.\d{2})",
        linea
    )

    if not m:
        return None

    try:
        descripcion = m.group(1).strip()
        cantidad = float(m.group(2))
        precio = float(m.group(3))
        importe = float(m.group(4))
    except:
        return None

    # filtro anti-basura
    if cantidad <= 0 or importe <= 0:
        return None

    return {
        "descripcion": descripcion,
        "cantidad": cantidad,
        "precio": precio,
        "importe": importe
    }


def extraer_lineas(texto):
    lineas = texto.split("\n")
    resultado = []

    for l in lineas:
        parsed = parsear_linea(l)
        if parsed:
            resultado.append(parsed)

    return resultado


def procesar_ticket(texto):
    lineas = [l.strip() for l in texto.split("\n") if l.strip()]

    proveedor = extraer_proveedor(lineas)
    numero = extraer_numero(lineas)
    fecha = extraer_fecha(lineas)
    lineas_ticket = extraer_lineas(texto)

    return {
        "proveedor": proveedor,
        "numero": numero,
        "fecha": fecha,
        "lineas": lineas_ticket
    }