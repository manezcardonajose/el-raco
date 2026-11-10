import unicodedata


def _clean(value, max_len=80):
    text = str(value or "").strip()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.replace("^", "").replace("~", "").replace("\n", " ")
    return text[:max_len]


def _mm_to_dots(mm, dpi=203):
    try:
        return max(int(float(mm or 0) * dpi / 25.4), 1)
    except Exception:
        return 1


def _row_get(row, key, default=""):
    try:
        if hasattr(row, "keys") and key not in row.keys():
            return default
        return row[key]
    except Exception:
        return default


def _zpl_text(x, y, text, height=24, width=24, max_len=80):
    return f"^FO{x},{y}^A0N,{height},{width}^FD{_clean(text, max_len)}^FS"


def _zpl_box(x, y, w, h, thickness=2):
    return f"^FO{x},{y}^GB{w},{h},{thickness}^FS"


def zpl_label(titulo, codigo, rows, config=None):
    config = config or {}
    width = _mm_to_dots(config.get("ancho_mm", 58))
    height = _mm_to_dots(config.get("alto_mm", 40))
    small = width < 560 or height < 360
    title_h = 30 if small else 36
    body_h = 20 if small else 24
    y = 18
    margin = 18
    code_box_h = 58 if small else 78
    lines = [
        "^XA",
        "^CI28",
        f"^PW{width}",
        f"^LL{height}",
        _zpl_text(margin, y, titulo, title_h, title_h, 36 if small else 52),
    ]
    y += 42 if small else 52
    lines.append(_zpl_box(margin, y, width - margin * 2, code_box_h, 2))
    lines.append(_zpl_text(margin + 10, y + 12, codigo, 28 if small else 36, 24 if small else 30, 34))
    y += code_box_h + 12

    max_rows = 5 if small else 8
    for label, value in rows[:max_rows]:
        if y > height - 34:
            break
        left = _clean(label, 18)
        right = _clean(value, 42 if small else 58)
        lines.append(_zpl_text(margin, y, f"{left}: {right}", body_h, body_h, 68))
        y += 24 if small else 30
    lines.append("^XZ")
    return "\n".join(lines)


def zpl_lote(lote, config=None):
    rows = [
        ("Familia", f"{_row_get(lote, 'familia', '-')} / {_row_get(lote, 'tipo_conservacion', '-')}"),
        ("Proveedor", _row_get(lote, "proveedor_nombre", "-")),
        ("Recepcion", _row_get(lote, "fecha_recepcion", "-")),
        ("Caducidad", _row_get(lote, "fecha_caducidad", "-")),
        ("Cantidad", f"{_row_get(lote, 'cantidad_actual', 0)} {_row_get(lote, 'unidad', '')}"),
        ("Almacen", _row_get(lote, "ubicacion_actual", "") or _row_get(lote, "almacen_id", "-")),
    ]
    return zpl_label(_row_get(lote, "producto_nombre", "Producto"), _row_get(lote, "codigo_lote", "-"), rows, config)


def zpl_etiquetas_recepcion(etiquetas, recepcion, config=None):
    bloques = []
    for etiqueta in etiquetas:
        linea = etiqueta["linea"]
        rows = [
            ("Envase", f"{etiqueta.get('copia')} / {etiqueta.get('total_copias')}"),
            ("Cantidad", f"{etiqueta.get('cantidad_envase', 0):.3f} {_row_get(linea, 'unidad', '')}"),
            ("Proveedor", _row_get(recepcion, "proveedor_nombre", "-")),
            ("Recepcion", _row_get(recepcion, "fecha_recepcion", "-")),
            ("Caducidad", _row_get(linea, "fecha_caducidad", "-")),
            ("Almacen", _row_get(linea, "almacen_nombre", "-")),
        ]
        bloques.append(zpl_label(_row_get(linea, "producto_nombre", "Producto"), etiqueta.get("codigo_lote", "-"), rows, config))
    return "\n".join(bloques)


def zpl_lote_elaborado(lote, ingredientes="", alergenos="", config=None):
    rows = [
        ("Servicio", f"{_row_get(lote, 'subdepartamento_nombre', '-')} / {_row_get(lote, 'turno', '-')}"),
        ("Produccion", _row_get(lote, "fecha_elaboracion", "") or _row_get(lote, "parte_fecha", "-")),
        ("Consumo", _row_get(lote, "fecha_limite_uso", "-")),
        ("Conserva", _row_get(lote, "metodo_conservacion", "-")),
        ("Cantidad", f"{_row_get(lote, 'cantidad', 0)} {_row_get(lote, 'unidad', '')}"),
        ("Ubicacion", _row_get(lote, "ubicacion", "-")),
        ("Alergenos", alergenos or "Pendiente"),
        ("Ingredientes", ingredientes or "Pendiente"),
    ]
    return zpl_label(_row_get(lote, "receta_nombre", "Elaboracion"), _row_get(lote, "codigo_lote", "-"), rows, config)
