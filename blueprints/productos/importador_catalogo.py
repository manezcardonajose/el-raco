from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from ia.alergenos import detectar_alergenos


ALIASES = {
    "codigo_producto": [
        "codigo_producto", "codigo", "codigo interno", "sku", "referencia",
        "ref producto", "ref_producto", "cod producto", "cod_producto",
    ],
    "nombre_producto": [
        "nombre_producto", "producto", "nombre", "articulo", "articulo/servicio",
        "descripcion", "descripcion producto", "concepto", "item",
    ],
    "familia": ["familia", "grupo", "categoria", "category", "family"],
    "unidad": ["unidad", "ud", "unidad_base", "unit", "medida", "uom"],
    "tipo_conservacion": [
        "tipo_conservacion", "conservacion", "temperatura", "tipo almacen",
        "almacen", "frio", "conservacion producto",
    ],
    "precio_compra": [
        "precio_compra", "precio", "coste", "costo", "ultimo precio",
        "precio ultimo", "precio proveedor", "pvp compra",
    ],
    "stock_minimo": ["stock_minimo", "stock minimo", "minimo", "stock min", "min"],
    "proveedor": [
        "proveedor", "proveedor principal", "supplier", "acreedor",
        "nombre proveedor", "proveedor_nombre",
    ],
    "nif_proveedor": [
        "nif_proveedor", "cif proveedor", "nif", "cif", "vat",
        "tax id", "tax_id",
    ],
    "email_proveedor": ["email_proveedor", "email proveedor", "email"],
    "telefono_proveedor": ["telefono_proveedor", "telefono proveedor", "telefono"],
    "direccion_proveedor": ["direccion_proveedor", "direccion proveedor", "direccion"],
    "contacto_proveedor": ["contacto_proveedor", "contacto proveedor", "contacto"],
    "codigo_proveedor": [
        "codigo_proveedor", "referencia_proveedor", "ref proveedor",
        "ref_proveedor", "codigo proveedor", "referencia proveedor",
    ],
    "proveedor_principal": [
        "proveedor_principal", "principal", "preferente", "es_principal",
        "proveedor preferente",
    ],
    "precio_venta": ["precio_venta", "precio venta", "pvp", "venta"],
    "visible_venta": ["visible_venta", "venta_tpv", "tpv", "visible en venta"],
    "control_lote": ["control_lote", "lote", "control lote"],
    "control_caducidad": ["control_caducidad", "caducidad", "control caducidad"],
    "formato_venta": ["formato_venta", "formato", "presentacion"],
    "observaciones": ["observaciones", "notas", "comentarios"],
    "alergenos": ["alergenos", "alérgenos", "allergens", "alergenos_producto", "alergenos producto"],
    "alergenos_validado": [
        "alergenos_validado", "alérgenos_validado", "alergenos revisados",
        "alergenos_revisados", "validado alergenos",
    ],
}


FAMILY_KEYWORDS = {
    "limpieza": ["lejia", "desinfect", "detergente", "limpia", "desengras", "bayeta", "guante", "bolsa basura", "secamanos"],
    "lavanderia": ["sabana", "toalla", "lavander", "suavizante", "quitamanchas", "blanqueante", "funda almohada"],
    "papeleria": ["folio", "toner", "tinta", "boligrafo", "grapa", "papel termico", "etiqueta", "archivador"],
    "mantenimiento": ["bombilla", "pila", "filtro aire", "mando tv", "latiguillo", "sifon", "silicona", "teflon", "aireador"],
    "amenities": ["champu", "gel ducha", "jabon", "amenity", "vaso carton", "bolsa lavanderia"],
    "carnes": ["carne", "cerdo", "ternera", "pollo", "pavo", "hamburgues", "chuleta", "salchicha", "jamon"],
    "pescados": ["pescado", "tilapia", "dorado", "merluza", "salmon", "atun", "bacalao"],
    "mariscos": ["gamba", "mejillon", "marisco", "langostino", "calamar", "pulpo", "sepia"],
    "verduras": ["cebolla", "ajo", "zanahoria", "patata", "pimiento", "verdura", "lechuga", "tomate", "calabacin"],
    "frutas": ["manzana", "platano", "pera", "naranja", "melon", "sandia", "kiwi", "fruta"],
    "lacteos": ["leche", "queso", "nata", "yogur", "mantequilla"],
    "huevos": ["huevo", "tortilla"],
    "bebidas_agua": ["agua"],
    "bebidas_refrescos": ["refresco", "cola", "fanta", "sprite", "tonica", "zumo"],
    "bebidas_cerveza": ["cerveza", "barril"],
    "bebidas_premium": ["vino", "cava", "champagne"],
    "bebidas_licores": ["ron", "whisky", "vodka", "ginebra", "licor"],
    "cafeteria": ["cafe", "infusion", "te "],
    "snacks": ["snack", "patata frita", "gominola", "frutos secos"],
    "helados": ["helado"],
    "aceites_salsas": ["aceite", "vinagre", "salsa", "ketchup", "mayonesa"],
    "pastas_arroces": ["arroz", "pasta", "macarron", "fideo"],
    "legumbres": ["lenteja", "garbanzo", "alubia"],
    "panaderia": ["harina", "pan", "levadura", "masa"],
}


FAMILY_SUPPLIER_HINT = {
    "limpieza": "Proveedor Limpieza",
    "lavanderia": "Proveedor Lavanderia",
    "papeleria": "Proveedor Papeleria",
    "mantenimiento": "Proveedor Mantenimiento",
    "amenities": "Proveedor Amenities",
}


@dataclass
class ImportRecord:
    fila: int
    codigo_producto: str = ""
    nombre_producto: str = ""
    familia: str = ""
    unidad: str = ""
    tipo_conservacion: str = ""
    precio_compra: float = 0.0
    stock_minimo: float = 0.0
    proveedor: str = ""
    nif_proveedor: str = ""
    email_proveedor: str = ""
    telefono_proveedor: str = ""
    direccion_proveedor: str = ""
    contacto_proveedor: str = ""
    codigo_proveedor: str = ""
    proveedor_principal: bool = False
    precio_venta: float = 0.0
    visible_venta: int = 0
    control_lote: int = 0
    control_caducidad: int = 0
    formato_venta: str = ""
    observaciones: str = ""
    alergenos: str = ""
    alergenos_validado: int = 0
    origen: str = ""
    errores: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def _columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    return {row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}


def _safe_add_column(cur: sqlite3.Cursor, table: str, column_sql: str) -> None:
    name = column_sql.strip().split()[0]
    if name not in _columns(cur, table):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column_sql}")


def _ensure_import_schema(cur: sqlite3.Cursor) -> None:
    _safe_add_column(cur, "productos", "alergenos TEXT")
    _safe_add_column(cur, "productos", "alergenos_validado INTEGER DEFAULT 0")


def _norm(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")
    return text


def _norm_text(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text.lower())
    return text


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _to_float(value: Any, default: float = 0.0) -> float:
    text = _clean(value).replace(" ", "")
    if not text:
        return default
    text = text.replace(".", "").replace(",", ".") if "," in text else text
    try:
        return float(text)
    except Exception:
        return default


def _to_bool(value: Any) -> int:
    text = _norm_text(value)
    if text in {"1", "si", "sí", "s", "true", "x", "yes", "y", "principal", "preferente"}:
        return 1
    if text in {"0", "no", "false", "n"}:
        return 0
    return 0


def _infer_allergens(nombre: str) -> str:
    return ", ".join(detectar_alergenos(nombre))
    text = _norm_text(nombre)
    rules = [
        ("Cereales con gluten", ["gluten", "trigo", "harina", "pan", "pasta", "macarron", "espagueti", "fideo", "rebozado"]),
        ("Huevo", ["huevo", "mayonesa", "mahonesa", "alioli"]),
        ("Leche", ["leche", "queso", "nata", "mantequilla", "yogur", "bechamel"]),
        ("Pescado", ["pescado", "atun", "merluza", "salmon", "bacalao", "dorada", "lubina"]),
        ("Crustáceos", ["gamba", "langostino", "cangrejo", "camaron", "crustaceo"]),
        ("Moluscos", ["mejillon", "almeja", "calamar", "sepia", "pulpo"]),
        ("Soja", ["soja", "tofu"]),
        ("Mostaza", ["mostaza"]),
        ("Sésamo", ["sesamo", "tahini"]),
        ("Apio", ["apio"]),
        ("Frutos con cáscara", ["almendra", "avellana", "nuez", "pistacho", "anacardo", "pacana", "macadamia"]),
        ("Cacahuete", ["cacahuete", "mani"]),
        ("Sulfitos", ["sulfito", "vino", "vinagre", "aceituna", "pepinillo"]),
        ("Altramuces", ["altramuz", "altramuces"]),
    ]
    found = []
    for allergen, words in rules:
        if any(word in text for word in words):
            found.append(allergen)
    return ", ".join(dict.fromkeys(found))


def _get(row: dict[str, Any], key: str) -> str:
    for alias in ALIASES.get(key, []):
        value = row.get(_norm(alias))
        if _clean(value):
            return _clean(value)
    return ""


def _get_numbered(row: dict[str, Any], base: str, index: int) -> str:
    bases = {
        "proveedor": ["proveedor", "supplier"],
        "precio": ["precio", "precio_proveedor", "precio_compra", "coste"],
        "codigo": ["codigo_proveedor", "referencia_proveedor", "ref_proveedor"],
        "nif": ["nif_proveedor", "cif_proveedor"],
    }[base]
    for root in bases:
        for candidate in (f"{root}_{index}", f"{root}{index}", f"{root} {index}"):
            value = row.get(_norm(candidate))
            if _clean(value):
                return _clean(value)
    return ""


def _infer_family(nombre: str, familia: str) -> str:
    if familia:
        return _norm(familia)
    text = _norm_text(nombre)
    for family, words in FAMILY_KEYWORDS.items():
        if any(word in text for word in words):
            return family
    return "otros"


def _normalize_unit(unidad: str, familia: str, nombre: str) -> str:
    unit = _norm(unidad)
    if unit in {"litro", "litros"}:
        return "l"
    if unit in {"kilo", "kilos", "kilogramo", "kilogramos"}:
        return "kg"
    if unit in {"unidad", "unidades", "uds"}:
        return "ud"
    if unit:
        return unit
    text = _norm_text(nombre)
    if "lata" in text:
        return "lata"
    if "botella" in text:
        return "botella"
    if "bolsa" in text:
        return "bolsa"
    if familia.startswith("bebidas_") or familia in {"aceites_salsas", "limpieza", "lavanderia"}:
        return "l"
    if familia in {"papeleria", "mantenimiento", "amenities", "snacks", "helados"}:
        return "ud"
    return "kg"


def _normalize_conservation(value: str, familia: str, nombre: str) -> str:
    cons = _norm(value)
    text = _norm_text(f"{value} {nombre}")
    if cons in {"seco", "refrigerado", "congelado"}:
        return cons
    if any(word in text for word in ["cong", "congel", "freezer", "uco"]):
        return "congelado"
    if familia in {"carnes", "pescados", "mariscos", "helados"}:
        return "congelado"
    if familia in {"verduras", "frutas", "lacteos", "huevos"}:
        return "refrigerado"
    return "seco"


def _read_excel(path: Path) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    sheets = pd.read_excel(path, sheet_name=None, dtype=str)
    frames = []
    for name, frame in sheets.items():
        frame = frame.dropna(how="all")
        if frame.empty:
            continue
        normalized = {_norm(col) for col in frame.columns}
        required_any = {_norm(alias) for alias in ALIASES["nombre_producto"] + ALIASES["codigo_producto"] + ALIASES["proveedor"]}
        if not normalized.intersection(required_any):
            warnings.append(f"Hoja '{name}' ignorada: no parece una hoja de catalogo.")
            continue
        frame["__origen__"] = name
        frames.append(frame)
    if not frames:
        return pd.DataFrame(), ["El Excel no contiene filas con datos."]
    return pd.concat(frames, ignore_index=True), warnings


def _read_csv(path: Path) -> tuple[pd.DataFrame, list[str]]:
    sep = "\t" if path.suffix.lower() == ".tsv" else None
    frame = pd.read_csv(path, dtype=str, sep=sep, engine="python")
    frame["__origen__"] = path.name
    return frame, []


def _read_pdf(path: Path) -> tuple[pd.DataFrame, list[str]]:
    try:
        import pdfplumber
    except Exception:
        return pd.DataFrame(), ["No esta disponible pdfplumber para leer PDFs."]

    rows: list[dict[str, Any]] = []
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            for table_index, table in enumerate(page.extract_tables() or [], start=1):
                if not table or len(table) < 2:
                    continue
                headers = [str(h or "").strip() for h in table[0]]
                for values in table[1:]:
                    data = {headers[i] or f"col_{i+1}": values[i] if i < len(values) else "" for i in range(len(headers))}
                    data["__origen__"] = f"PDF pagina {page_number} tabla {table_index}"
                    rows.append(data)
    if not rows:
        return pd.DataFrame(), [
            "El PDF no trae tablas legibles. Si es escaneado necesitara OCR y revision manual."
        ]
    return pd.DataFrame(rows), []


def _load_frame(path: Path) -> tuple[pd.DataFrame, list[str]]:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        return _read_excel(path)
    if suffix in {".csv", ".tsv"}:
        return _read_csv(path)
    if suffix == ".pdf":
        return _read_pdf(path)
    return pd.DataFrame(), [f"Formato no soportado: {suffix}"]


def _records_from_file(path: Path) -> tuple[list[ImportRecord], list[str]]:
    frame, warnings = _load_frame(path)
    if frame.empty:
        return [], warnings

    normalized_columns = {_norm(col): col for col in frame.columns}
    frame = frame.rename(columns={original: normed for normed, original in normalized_columns.items()})
    records: list[ImportRecord] = []

    for idx, raw_row in frame.iterrows():
        row = {str(key): value for key, value in raw_row.to_dict().items()}
        fila = int(idx) + 2
        base = {
            "codigo_producto": _get(row, "codigo_producto"),
            "nombre_producto": _get(row, "nombre_producto"),
            "familia": _get(row, "familia"),
            "unidad": _get(row, "unidad"),
            "tipo_conservacion": _get(row, "tipo_conservacion"),
            "precio_compra": _get(row, "precio_compra"),
            "stock_minimo": _get(row, "stock_minimo"),
            "precio_venta": _get(row, "precio_venta"),
            "visible_venta": _get(row, "visible_venta"),
            "control_lote": _get(row, "control_lote"),
            "control_caducidad": _get(row, "control_caducidad"),
            "formato_venta": _get(row, "formato_venta"),
            "observaciones": _get(row, "observaciones"),
            "alergenos": _get(row, "alergenos"),
            "alergenos_validado": _get(row, "alergenos_validado"),
            "origen": _clean(row.get("origen") or row.get("__origen__") or row.get("source")),
        }
        base_supplier = {
            "proveedor": _get(row, "proveedor"),
            "nif_proveedor": _get(row, "nif_proveedor"),
            "email_proveedor": _get(row, "email_proveedor"),
            "telefono_proveedor": _get(row, "telefono_proveedor"),
            "direccion_proveedor": _get(row, "direccion_proveedor"),
            "contacto_proveedor": _get(row, "contacto_proveedor"),
            "codigo_proveedor": _get(row, "codigo_proveedor"),
            "precio_compra": base["precio_compra"],
            "proveedor_principal": _get(row, "proveedor_principal"),
        }
        numbered_suppliers = []
        for index in range(1, 6):
            proveedor = _get_numbered(row, "proveedor", index)
            if not proveedor:
                continue
            numbered_suppliers.append({
                "proveedor": proveedor,
                "nif_proveedor": _get_numbered(row, "nif", index),
                "email_proveedor": "",
                "telefono_proveedor": "",
                "direccion_proveedor": "",
                "contacto_proveedor": "",
                "codigo_proveedor": _get_numbered(row, "codigo", index),
                "precio_compra": _get_numbered(row, "precio", index) or base["precio_compra"],
                "proveedor_principal": "",
            })
        suppliers = []
        if base_supplier["proveedor"] or not numbered_suppliers:
            suppliers.append(base_supplier)
        suppliers.extend(numbered_suppliers)

        for supplier in suppliers:
            record = ImportRecord(
                fila=fila,
                codigo_producto=base["codigo_producto"],
                nombre_producto=base["nombre_producto"],
                familia=base["familia"],
                unidad=base["unidad"],
                tipo_conservacion=base["tipo_conservacion"],
                precio_compra=_to_float(supplier["precio_compra"]),
                stock_minimo=_to_float(base["stock_minimo"]),
                proveedor=supplier["proveedor"],
                nif_proveedor=supplier["nif_proveedor"],
                email_proveedor=supplier["email_proveedor"],
                telefono_proveedor=supplier["telefono_proveedor"],
                direccion_proveedor=supplier["direccion_proveedor"],
                contacto_proveedor=supplier["contacto_proveedor"],
                codigo_proveedor=supplier["codigo_proveedor"],
                proveedor_principal=bool(_to_bool(supplier["proveedor_principal"])),
                precio_venta=_to_float(base["precio_venta"]),
                visible_venta=_to_bool(base["visible_venta"]),
                control_lote=_to_bool(base["control_lote"]),
                control_caducidad=_to_bool(base["control_caducidad"]),
                formato_venta=base["formato_venta"],
                observaciones=base["observaciones"],
                alergenos=base["alergenos"],
                alergenos_validado=_to_bool(base["alergenos_validado"]),
                origen=base["origen"],
            )
            if not record.nombre_producto and not record.codigo_producto:
                record.errores.append("Falta nombre_producto o codigo_producto.")
            record.familia = _infer_family(record.nombre_producto, record.familia)
            record.unidad = _normalize_unit(record.unidad, record.familia, record.nombre_producto)
            record.tipo_conservacion = _normalize_conservation(
                record.tipo_conservacion, record.familia, record.nombre_producto
            )
            if not record.alergenos:
                record.alergenos = _infer_allergens(record.nombre_producto)
                if record.alergenos:
                    record.avisos.append("Alergenos inferidos por nombre; revisar antes de validar tarjetas.")
            if not record.proveedor:
                record.proveedor = FAMILY_SUPPLIER_HINT.get(record.familia, "PENDIENTE COMPRAS")
                record.avisos.append("Proveedor no indicado; asignado automaticamente.")
            if not record.control_lote and record.familia not in {"papeleria", "mantenimiento"}:
                record.control_lote = 1
            if not record.control_caducidad and (
                record.tipo_conservacion in {"refrigerado", "congelado"}
                or record.familia in {"limpieza", "lavanderia", "amenities"}
            ):
                record.control_caducidad = 1
            if record.errores:
                records.append(record)
            elif record.nombre_producto or record.codigo_producto:
                records.append(record)

    return records, warnings


def _product_key(record: ImportRecord) -> str:
    return f"codigo:{_norm(record.codigo_producto)}" if record.codigo_producto else f"nombre:{_norm(record.nombre_producto)}"


def _ensure_supplier(cur: sqlite3.Cursor, record: ImportRecord, empresa_id: int, aplicar: bool) -> tuple[int | None, str]:
    if record.nif_proveedor:
        row = cur.execute(
            """
            SELECT id FROM proveedores
            WHERE empresa_id = ? AND lower(COALESCE(nif, '')) = lower(?)
            LIMIT 1
            """,
            (empresa_id, record.nif_proveedor),
        ).fetchone()
        if row:
            return int(row["id"]), "actualizado"

    row = cur.execute(
        """
        SELECT id FROM proveedores
        WHERE empresa_id = ? AND lower(nombre) = lower(?)
        LIMIT 1
        """,
        (empresa_id, record.proveedor),
    ).fetchone()
    if row:
        return int(row["id"]), "actualizado"

    if not aplicar:
        return None, "creado"

    cur.execute(
        """
        INSERT INTO proveedores (
            nombre, nif, email, telefono, direccion, contacto,
            observaciones, activo, empresa_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            record.proveedor,
            record.nif_proveedor or None,
            record.email_proveedor or None,
            record.telefono_proveedor or None,
            record.direccion_proveedor or None,
            record.contacto_proveedor or None,
            "Creado por importador de catalogo.",
            empresa_id,
        ),
    )
    return int(cur.lastrowid), "creado"


def _find_product(cur: sqlite3.Cursor, record: ImportRecord, empresa_id: int) -> sqlite3.Row | None:
    if record.codigo_producto:
        row = cur.execute(
            """
            SELECT * FROM productos
            WHERE empresa_id = ? AND lower(COALESCE(codigo, '')) = lower(?)
            LIMIT 1
            """,
            (empresa_id, record.codigo_producto),
        ).fetchone()
        if row:
            return row

    return cur.execute(
        """
        SELECT * FROM productos
        WHERE empresa_id = ? AND lower(nombre) = lower(?)
        LIMIT 1
        """,
        (empresa_id, record.nombre_producto),
    ).fetchone()


def _upsert_product(
    cur: sqlite3.Cursor,
    record: ImportRecord,
    proveedor_id: int | None,
    empresa_id: int,
    aplicar: bool,
) -> tuple[int | None, str]:
    row = _find_product(cur, record, empresa_id)
    action = "actualizado" if row else "creado"
    if not aplicar:
        return (int(row["id"]) if row else None), action

    if row:
        product_id = int(row["id"])
        cur.execute(
            """
            UPDATE productos
            SET nombre = COALESCE(NULLIF(?, ''), nombre),
                codigo = COALESCE(NULLIF(?, ''), codigo),
                familia = ?,
                unidad = ?,
                unidad_base = ?,
                tipo_conservacion = ?,
                precio = CASE WHEN ? > 0 THEN ? ELSE precio END,
                stock_minimo = CASE WHEN ? > 0 THEN ? ELSE stock_minimo END,
                proveedor_id = COALESCE(?, proveedor_id),
                control_lote = ?,
                control_caducidad = ?,
                visible_en_compras = 1,
                visible_en_venta = CASE WHEN ? = 1 THEN 1 ELSE COALESCE(visible_en_venta, 0) END,
                permite_compra_directa = 1,
                formato_venta = COALESCE(NULLIF(?, ''), formato_venta),
                precio_venta = CASE WHEN ? > 0 THEN ? ELSE precio_venta END,
                observaciones = COALESCE(NULLIF(?, ''), observaciones),
                alergenos = CASE WHEN ? != '' OR ? = 1 THEN ? ELSE alergenos END,
                alergenos_validado = CASE WHEN ? != '' OR ? = 1 THEN ? ELSE COALESCE(alergenos_validado, 0) END
            WHERE id = ?
            """,
            (
                record.nombre_producto,
                record.codigo_producto,
                record.familia,
                record.unidad,
                record.unidad,
                record.tipo_conservacion,
                record.precio_compra,
                record.precio_compra,
                record.stock_minimo,
                record.stock_minimo,
                proveedor_id,
                record.control_lote,
                record.control_caducidad,
                record.visible_venta,
                record.formato_venta,
                record.precio_venta,
                record.precio_venta,
                record.observaciones,
                record.alergenos,
                record.alergenos_validado,
                record.alergenos,
                record.alergenos,
                record.alergenos_validado,
                record.alergenos_validado,
                product_id,
            ),
        )
        return product_id, action

    cur.execute(
        """
        INSERT INTO productos (
            nombre, codigo, cantidad, unidad, unidad_base, tipo_conservacion,
            familia, precio, stock_minimo, referencia_proveedor,
            proveedor_id, observaciones, activo, empresa_id,
            control_lote, control_caducidad, visible_en_compras,
            visible_en_produccion, visible_en_venta, permite_compra_directa,
            formato_venta, precio_venta, alergenos, alergenos_validado
        ) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 1, 1, ?, 1, ?, ?, ?, ?)
        """,
        (
            record.nombre_producto or record.codigo_producto,
            record.codigo_producto or None,
            record.unidad,
            record.unidad,
            record.tipo_conservacion,
            record.familia,
            record.precio_compra,
            record.stock_minimo,
            record.codigo_proveedor or None,
            proveedor_id,
            record.observaciones or "Creado por importador de catalogo.",
            empresa_id,
            record.control_lote,
            record.control_caducidad,
            record.visible_venta,
            record.formato_venta or None,
            record.precio_venta,
            record.alergenos or None,
            record.alergenos_validado,
        ),
    )
    product_id = int(cur.lastrowid)
    if not record.codigo_producto:
        codigo = f"IMP{product_id:05d}"
        cur.execute(
            "UPDATE productos SET codigo = ?, codigo_interno = ? WHERE id = ?",
            (codigo, codigo, product_id),
        )
    return product_id, action


def _upsert_relation(
    cur: sqlite3.Cursor,
    producto_id: int,
    proveedor_id: int,
    record: ImportRecord,
    principal: bool,
    aplicar: bool,
) -> str:
    row = cur.execute(
        """
        SELECT id FROM productos_proveedores
        WHERE producto_id = ? AND proveedor_id = ?
        LIMIT 1
        """,
        (producto_id, proveedor_id),
    ).fetchone()
    action = "actualizada" if row else "creada"
    if not aplicar:
        return action
    if principal:
        cur.execute(
            """
            UPDATE productos_proveedores
            SET es_principal = 0, preferente = 0
            WHERE producto_id = ?
            """,
            (producto_id,),
        )

    if row:
        cur.execute(
            """
            UPDATE productos_proveedores
            SET precio = CASE WHEN ? > 0 THEN ? ELSE precio END,
                precio_compra = CASE WHEN ? > 0 THEN ? ELSE precio_compra END,
                precio_ultimo = CASE WHEN ? > 0 THEN ? ELSE precio_ultimo END,
                precio_vigente = CASE WHEN ? > 0 THEN ? ELSE precio_vigente END,
                precio_medio = CASE WHEN ? > 0 THEN ? ELSE precio_medio END,
                codigo_proveedor = COALESCE(NULLIF(?, ''), codigo_proveedor),
                referencia_proveedor = COALESCE(NULLIF(?, ''), referencia_proveedor),
                es_principal = ?,
                preferente = ?,
                activo = 1,
                fecha_actualizacion = date('now')
            WHERE id = ?
            """,
            (
                record.precio_compra,
                record.precio_compra,
                record.precio_compra,
                record.precio_compra,
                record.precio_compra,
                record.precio_compra,
                record.precio_compra,
                record.precio_compra,
                record.precio_compra,
                record.precio_compra,
                record.codigo_proveedor,
                record.codigo_proveedor,
                int(principal),
                int(principal),
                row["id"],
            ),
        )
    else:
        cur.execute(
            """
            INSERT INTO productos_proveedores (
                producto_id, proveedor_id, codigo_proveedor, referencia_proveedor,
                precio, precio_compra, precio_ultimo, precio_vigente, precio_medio,
                es_principal, preferente, activo, fecha_actualizacion
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, date('now'))
            """,
            (
                producto_id,
                proveedor_id,
                record.codigo_proveedor or None,
                record.codigo_proveedor or None,
                record.precio_compra,
                record.precio_compra,
                record.precio_compra,
                record.precio_compra,
                record.precio_compra,
                int(principal),
                int(principal),
            ),
        )
    if principal:
        cur.execute(
            "UPDATE productos SET proveedor_id = ? WHERE id = ?",
            (proveedor_id, producto_id),
        )
    return action


def importar_catalogo_desde_archivo(path: str | Path, empresa_id: int, aplicar: bool = False) -> dict[str, Any]:
    path = Path(path)
    records, warnings = _records_from_file(path)
    grouped: dict[str, list[ImportRecord]] = defaultdict(list)
    for record in records:
        grouped[_product_key(record)].append(record)

    conn = sqlite3.connect(Path.cwd() / "pepe4.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    _ensure_import_schema(cur)

    resumen = {
        "archivo": path.name,
        "modo": "aplicado" if aplicar else "vista_previa",
        "filas_leidas": len(records),
        "productos_detectados": len(grouped),
        "proveedores_creados": 0,
        "proveedores_actualizados": 0,
        "productos_creados": 0,
        "productos_actualizados": 0,
        "relaciones_creadas": 0,
        "relaciones_actualizadas": 0,
        "errores": 0,
        "avisos": len(warnings),
    }
    preview: list[dict[str, Any]] = []
    errores: list[str] = []
    avisos = list(warnings)

    try:
        for product_records in grouped.values():
            valid_records = [r for r in product_records if not r.errores]
            for record in product_records:
                for error in record.errores:
                    errores.append(f"Fila {record.fila}: {error}")
                for aviso in record.avisos:
                    avisos.append(f"Fila {record.fila}: {aviso}")
            if not valid_records:
                continue

            principal_record = next((r for r in valid_records if r.proveedor_principal), None)
            if not principal_record:
                priced = [r for r in valid_records if r.precio_compra > 0]
                principal_record = min(priced, key=lambda r: r.precio_compra) if priced else valid_records[0]

            proveedor_id, proveedor_action = _ensure_supplier(cur, principal_record, empresa_id, aplicar)
            if proveedor_action == "creado":
                resumen["proveedores_creados"] += 1
            else:
                resumen["proveedores_actualizados"] += 1

            producto_id, producto_action = _upsert_product(
                cur, principal_record, proveedor_id, empresa_id, aplicar
            )
            if producto_action == "creado":
                resumen["productos_creados"] += 1
            else:
                resumen["productos_actualizados"] += 1

            supplier_rows: list[tuple[ImportRecord, int | None]] = [(principal_record, proveedor_id)]
            for record in valid_records:
                if record is principal_record:
                    continue
                supplier_id, supplier_action = _ensure_supplier(cur, record, empresa_id, aplicar)
                supplier_rows.append((record, supplier_id))
                if supplier_action == "creado":
                    resumen["proveedores_creados"] += 1
                else:
                    resumen["proveedores_actualizados"] += 1

            for record, supplier_id in supplier_rows:
                relation_action = "creada"
                if not aplicar and (not producto_id or not supplier_id):
                    relation_action = "creada"
                    resumen["relaciones_creadas"] += 1
                elif producto_id and supplier_id:
                    relation_action = _upsert_relation(
                        cur,
                        producto_id,
                        supplier_id,
                        record,
                        record is principal_record,
                        aplicar,
                    )
                    if relation_action == "creada":
                        resumen["relaciones_creadas"] += 1
                    else:
                        resumen["relaciones_actualizadas"] += 1
                preview.append({
                    "fila": record.fila,
                    "producto": record.nombre_producto or record.codigo_producto,
                    "codigo": record.codigo_producto,
                    "familia": record.familia,
                    "unidad": record.unidad,
                    "conservacion": record.tipo_conservacion,
                    "proveedor": record.proveedor,
                    "precio": record.precio_compra,
                    "alergenos": record.alergenos,
                    "alergenos_validado": record.alergenos_validado,
                    "principal": "si" if record is principal_record else "",
                    "accion_producto": producto_action,
                    "accion_relacion": relation_action,
                    "origen": record.origen,
                })

        resumen["errores"] = len(errores)
        resumen["avisos"] = len(avisos)
        if aplicar and not errores:
            conn.commit()
        elif aplicar and errores:
            conn.rollback()
            avisos.append("No se aplicaron cambios porque hay errores que revisar.")
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "resumen": resumen,
        "preview": preview[:80],
        "errores": errores[:80],
        "avisos": avisos[:80],
        "total_preview": len(preview),
    }


def crear_plantilla_importacion() -> BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "Catalogo"
    headers = [
        "codigo_producto", "nombre_producto", "familia", "unidad",
        "tipo_conservacion", "precio_compra", "stock_minimo", "proveedor",
        "nif_proveedor", "codigo_proveedor", "proveedor_principal",
        "precio_venta", "visible_venta", "control_lote", "control_caducidad",
        "formato_venta", "alergenos", "alergenos_validado", "observaciones",
    ]
    rows = [
        ["CAR-001", "Pechuga pollo congelada", "carnes", "kg", "congelado", 5.80, 25, "Nexora Carnes Congeladas", "B76010002", "POL-CONG-001", "si", "", "", "si", "si", "Caja 10 kg", "", "si", "Ejemplo cocina"],
        ["CAR-001", "Pechuga pollo congelada", "carnes", "kg", "congelado", 5.95, 25, "Proveedor Alternativo Carnes", "B76019998", "ALT-POL-001", "", "", "", "si", "si", "Caja 10 kg", "", "si", "Mismo producto con otro proveedor"],
        ["LIM-001", "Desinfectante banos 5 l", "limpieza", "l", "seco", 9.90, 4, "Limpieza Profesional Hotelera", "B76010007", "DES-BAN-5L", "si", "", "", "si", "si", "Garrafa 5 l", "", "", "Pisos y zonas comunes"],
        ["TPV-001", "Refresco cola lata 330 ml", "bebidas_refrescos", "lata", "seco", 0.42, 240, "Bebidas Hotel Canarias", "B76010006", "COLA-330", "si", 2.00, "si", "si", "si", "Lata 330 ml", "", "si", "Visible en TPV"],
        ["MAN-001", "Bombilla LED E27 10W", "mantenimiento", "ud", "seco", 2.40, 30, "Recambios y Mantenimiento Hotelero", "B76010010", "LED-E27-10", "si", "", "", "", "", "Unidad", "", "", "Repuesto mantenimiento"],
    ]
    ws.append(headers)
    for row in rows:
        ws.append(row)

    header_fill = PatternFill("solid", fgColor="FF6B00")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    widths = {
        1: 18, 2: 34, 3: 20, 4: 12, 5: 18, 6: 14, 7: 14,
        8: 32, 9: 16, 10: 20, 11: 18, 12: 14, 13: 14,
        14: 14, 15: 18, 16: 18, 17: 26, 18: 18, 19: 34,
    }
    for index, width in widths.items():
        ws.column_dimensions[get_column_letter(index)].width = width

    help_ws = wb.create_sheet("Ayuda")
    help_rows = [
        ["Campo", "Uso"],
        ["codigo_producto", "Opcional pero recomendable. Si se repite, se actualiza el mismo producto."],
        ["nombre_producto", "Obligatorio si no hay codigo_producto."],
        ["familia", "Ejemplos: carnes, pescados, verduras, limpieza, lavanderia, papeleria, mantenimiento, amenities."],
        ["unidad", "kg, l, ud, caja, pack, rollo, lata, botella, bolsa."],
        ["tipo_conservacion", "seco, refrigerado o congelado."],
        ["alergenos", "Opcional. Ejemplos: Apio, huevo, leche, frutos con cascara. Se usa para tarjetas de buffet desde produccion."],
        ["alergenos_validado", "Usar si/1/x cuando el producto ya esta revisado. Si queda vacio se marcara como pendiente."],
        ["proveedor", "Si se repite un producto con otro proveedor, se crea otra relacion proveedor-producto."],
        ["proveedor_principal", "Usar si/1/x para marcar el proveedor preferente. Si no, se elige el precio mas bajo."],
        ["PDF", "Solo se importan PDFs con tablas seleccionables. PDFs escaneados requieren OCR/revision."],
    ]
    for row in help_rows:
        help_ws.append(row)
    for cell in help_ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
    help_ws.column_dimensions["A"].width = 24
    help_ws.column_dimensions["B"].width = 90

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output
