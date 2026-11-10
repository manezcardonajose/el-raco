import re

def safe_float(v):
    try:
        return float(str(v).replace(',', '.'))
    except:
        return 0.0

def parse_ocr_text(texto):
    lineas = []
    filas = texto.split('\n')

    for fila in filas:
        fila = fila.strip()
        if not fila:
            continue

        # Buscar números en la línea
        numeros = re.findall(r'\d+[.,]?\d*', fila)

        if len(numeros) >= 2:
            try:
                cantidad = safe_float(numeros[0])
                importe = safe_float(numeros[-1])

                if cantidad > 0:
                    precio = round(importe / cantidad, 4)
                else:
                    precio = 0

                descripcion = fila

                lineas.append({
                    "descripcion": descripcion,
                    "cantidad": cantidad,
                    "precio": precio,
                    "importe": importe
                })

            except:
                continue

    return lineas