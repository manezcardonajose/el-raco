# -*- coding: utf-8 -*-
"""Importa menus y fichas tecnicas de Hotel Albarracin sin pasar a produccion."""

from __future__ import annotations

import argparse
import html
import re
import sqlite3
import unicodedata
import zipfile
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "pepe4.db"
BASE = Path(r"D:\archivos de cocina\hotel albarracin")
EMPRESA_ID = 2
CENTRO_ID = 8

RUEDA_INSERSO = BASE / "RUEDA MENU INSERSO 2024.xlsx"
FICHAS_INSERSO = [
    BASE / "FICHAS TECNICAS INSERSO LUNES.xlsx",
    BASE / "FICHAS TECNICAS INSERSO MARTES.xlsx",
    BASE / "FICHAS TECNICAS INSERSO MIERCOLES.xlsx",
    BASE / "FICHAS TECNICAS INSERSO JUEVES.xlsx",
    BASE / "FICHAS TECNICAS INSERSO VIERNES.xlsx",
    BASE / "FICHAS TECNICAS INSERSO DOMINGO.xlsx",
]
MENU_SEMANA_SANTA = BASE / "menu semana santa alergenos.docx"
FICHAS_SEMANA_SANTA = BASE / "Fichas tecnicas alergenos H ALBARRACIN MENU SEMANA SANTA 2024.xlsx"
CARTA_CENAS = BASE / "CARTA CENAS.odt"
MENU_DIARIO_ALMUERZO = BASE / "MENU diario alergenos.docx"
MENU_DESAYUNO_BUFFET = BASE / "MENU DESAYUNO BUFFET.ods"

ALLERGEN_CODES = {
    "1": "Mostaza",
    "2": "Sésamo",
    "3.1.": "Gluten",
    "3.2.": "Gluten",
    "3.3.": "Gluten",
    "3.4.": "Gluten",
    "3.5.": "Gluten",
    "4": "Altramuces",
    "5.1.": "Frutos secos",
    "5.2.": "Frutos secos",
    "5.3.": "Frutos secos",
    "5.4.": "Frutos secos",
    "5.5.": "Frutos secos",
    "5.6.": "Frutos secos",
    "5.7.": "Frutos secos",
    "5.8.": "Frutos secos",
    "6": "Cacahuetes",
    "7": "Huevo",
    "8": "Pescado",
    "9": "Crustáceos",
    "10": "Moluscos",
    "11": "Leche",
    "12": "Soja",
    "13": "Apio",
    "14": "Sulfitos",
}

_PRODUCT_CACHE: dict[tuple[int, int, str], int] = {}
_RECIPE_CACHE: dict[tuple[int, int], list[tuple[int, str]]] = {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Importar menus Albarracin.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=EMPRESA_ID)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--semana-inicio", default="2027-02-15", help="Lunes de la semana plantilla Imserso.")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def norm(text: str | None) -> str:
    text = str(text or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean(text: str | None) -> str:
    text = str(text or "").replace("\n", " ").strip()
    return re.sub(r"\s+", " ", text)


def clean_menu_dish(text: str | None) -> str:
    text = clean(text)
    text = re.sub(r"^[.\-•\s]+", "", text)
    text = re.sub(r"[.…]{2,}.*$", "", text)
    text = re.sub(r"\s*\d+[,.]\d+\s*€.*$", "", text)
    return text.strip(" .-")


def meaningful_tokens(text: str) -> set[str]:
    stop = {"de", "del", "la", "el", "los", "las", "con", "y", "a", "al", "en"}
    return {token for token in norm(text).split() if len(token) > 2 and token not in stop}


def ensure_schema(conn: sqlite3.Connection) -> None:
    receta_cols = {row["name"] for row in conn.execute("PRAGMA table_info(recetas)").fetchall()}
    if "escandallo_fiable" not in receta_cols:
        conn.execute("ALTER TABLE recetas ADD COLUMN escandallo_fiable INTEGER DEFAULT 1")
    if "origen_escandallo" not in receta_cols:
        conn.execute("ALTER TABLE recetas ADD COLUMN origen_escandallo TEXT")
    if "coste_validado" not in receta_cols:
        conn.execute("ALTER TABLE recetas ADD COLUMN coste_validado INTEGER DEFAULT 0")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS importacion_albarracin_menus (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER,
            centro_id INTEGER,
            origen TEXT,
            tipo TEXT,
            referencia TEXT,
            registros INTEGER DEFAULT 0,
            observaciones TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(empresa_id, centro_id, origen, tipo, referencia)
        )
        """
    )


def log_import(conn: sqlite3.Connection, empresa_id: int, centro_id: int, origen: str, tipo: str, referencia: str, registros: int, obs: str = "") -> None:
    conn.execute(
        """
        INSERT INTO importacion_albarracin_menus
        (empresa_id, centro_id, origen, tipo, referencia, registros, observaciones)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(empresa_id, centro_id, origen, tipo, referencia)
        DO UPDATE SET registros=excluded.registros, observaciones=excluded.observaciones, created_at=CURRENT_TIMESTAMP
        """,
        (empresa_id, centro_id, origen, tipo, referencia, registros, obs),
    )


def get_dep_pv(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> tuple[int, int | None]:
    dep = conn.execute(
        """
        SELECT id FROM departamentos
        WHERE empresa_id=? AND centro_id=? AND UPPER(nombre)=UPPER('SALA / RESTAURANTE') AND COALESCE(activo,1)=1
        """,
        (empresa_id, centro_id),
    ).fetchone()
    if not dep:
        raise RuntimeError("Falta departamento SALA / RESTAURANTE en Hotel Albarracin.")
    pv = conn.execute(
        """
        SELECT id FROM puntos_venta
        WHERE empresa_id=? AND centro_id=? AND LOWER(nombre)=LOWER('Restaurante') AND COALESCE(activo,1)=1
        """,
        (empresa_id, centro_id),
    ).fetchone()
    return int(dep["id"]), int(pv["id"]) if pv else None


def get_menu_subdepartamento(conn: sqlite3.Connection, empresa_id: int, centro_id: int, dep_id: int) -> int:
    row = conn.execute(
        """
        SELECT id FROM subdepartamentos
        WHERE empresa_id=? AND centro_id=? AND departamento_id=? AND LOWER(nombre)=LOWER('Restaurante')
        """,
        (empresa_id, centro_id, dep_id),
    ).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO subdepartamentos (nombre, departamento_id, descripcion, activo, empresa_id, centro_id)
        VALUES ('Restaurante', ?, 'Anclaje tecnico para menus; no usado como subdepartamento operativo visible.', 0, ?, ?)
        """,
        (dep_id, empresa_id, centro_id),
    )
    return int(cur.lastrowid)


def tipo_menu(conn: sqlite3.Connection, empresa_id: int, centro_id: int, nombre: str, punto_venta_id: int | None) -> int:
    row = conn.execute(
        """
        SELECT id FROM tipos_menu
        WHERE empresa_id=? AND centro_id=? AND LOWER(nombre)=LOWER(?)
        """,
        (empresa_id, centro_id, nombre),
    ).fetchone()
    if row:
        conn.execute("UPDATE tipos_menu SET activo=1, punto_venta_id=? WHERE id=?", (punto_venta_id, row["id"]))
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO tipos_menu (nombre, punto_venta_id, activo, empresa_id, centro_id)
        VALUES (?, ?, 1, ?, ?)
        """,
        (nombre, punto_venta_id, empresa_id, centro_id),
    )
    return int(cur.lastrowid)


def infer_allergens_from_name(name: str) -> list[str]:
    n = norm(name)
    allergens = set()
    if any(x in n for x in ("pan", "pasta", "macarron", "canelon", "croquet", "tagliatelle", "brown", "tarta", "torrija")):
        allergens.add("Gluten")
    if any(x in n for x in ("leche", "queso", "yogur", "nata", "carbonara", "crema", "helado", "flan", "natilla")):
        allergens.add("Leche")
    if any(x in n for x in ("huevo", "tortilla", "flan", "carbonara", "rebozad", "empanad")):
        allergens.add("Huevo")
    if any(x in n for x in ("pescado", "tilapia", "merluza", "salmon", "bacalao", "atun", "rape", "fogonero", "marmitaco")):
        allergens.add("Pescado")
    if any(x in n for x in ("gambon", "gamba", "langost", "crustace")):
        allergens.add("Crustáceos")
    if any(x in n for x in ("mejillon", "almeja", "sepia", "calamar", "molus")):
        allergens.add("Moluscos")
    if any(x in n for x in ("apio", "ensaladilla")):
        allergens.add("Apio")
    if any(x in n for x in ("mostaza",)):
        allergens.add("Mostaza")
    if any(x in n for x in ("soja",)):
        allergens.add("Soja")
    if any(x in n for x in ("frutos secos", "almendra", "nuez", "pistacho")):
        allergens.add("Frutos secos")
    if any(x in n for x in ("vino", "px", "sulfito")):
        allergens.add("Sulfitos")
    return sorted(allergens)


def upsert_product(conn: sqlite3.Connection, empresa_id: int, centro_id: int, name: str, allergens: list[str] | None = None) -> int:
    name = clean(name).upper()
    key = (empresa_id, centro_id, norm(name))
    if key in _PRODUCT_CACHE:
        return _PRODUCT_CACHE[key]
    if not any(k[0] == empresa_id and k[1] == centro_id for k in _PRODUCT_CACHE):
        for existing in conn.execute(
            "SELECT id, nombre FROM productos WHERE empresa_id=? AND centro_id=?",
            (empresa_id, centro_id),
        ).fetchall():
            _PRODUCT_CACHE[(empresa_id, centro_id, norm(existing["nombre"]))] = int(existing["id"])
        if key in _PRODUCT_CACHE:
            return _PRODUCT_CACHE[key]
    row = conn.execute(
        """
        SELECT id FROM productos
        WHERE empresa_id=? AND centro_id=? AND LOWER(nombre)=LOWER(?)
        """,
        (empresa_id, centro_id, name),
    ).fetchone()
    alergenos = ", ".join(sorted(set(allergens or infer_allergens_from_name(name))))
    if row:
        _PRODUCT_CACHE[key] = int(row["id"])
        if alergenos:
            conn.execute("UPDATE productos SET alergenos=?, alergenos_validado=0, activo=1 WHERE id=?", (alergenos, row["id"]))
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO productos
        (nombre, unidad, tipo_conservacion, familia, precio, stock_minimo, activo,
         empresa_id, centro_id, visible_en_compras, visible_en_produccion,
         permite_compra_directa, alergenos, alergenos_validado, observaciones)
        VALUES (?, 'kg', 'seco', 'ingrediente', 0, 0, 1, ?, ?, 1, 1, 1, ?, 0, ?)
        """,
        (name, empresa_id, centro_id, alergenos, "Creado desde fichas tecnicas Albarracin. Precio pendiente."),
    )
    product_id = int(cur.lastrowid)
    _PRODUCT_CACHE[key] = product_id
    return product_id


def upsert_recipe(conn: sqlite3.Connection, empresa_id: int, centro_id: int, name: str, categoria: str, allergens: list[str], source: str, ingredients: list[str], validated: bool) -> int:
    name = clean(name)
    row = conn.execute(
        """
        SELECT id FROM recetas
        WHERE empresa_id=? AND centro_id=? AND LOWER(nombre)=LOWER(?)
        """,
        (empresa_id, centro_id, name),
    ).fetchone()
    alergenos = ", ".join(sorted(set(allergens)))
    desc = f"Fuente: {source}. {'Ficha tecnica importada.' if validated else 'Receta pendiente de ficha tecnica; alergenos inferidos/revisar.'}"
    if row:
        receta_id = int(row["id"])
        conn.execute(
            """
            UPDATE recetas
            SET categoria=?, alergenos=?, descripcion=?, raciones_base=1,
                escandallo_fiable=0, origen_escandallo='ficha_alergenos_sin_cantidades',
                coste_validado=0
            WHERE id=?
            """,
            (categoria, alergenos, desc, receta_id),
        )
        conn.execute("DELETE FROM recetas_ingredientes WHERE receta_id=?", (receta_id,))
    else:
        cur = conn.execute(
            """
            INSERT INTO recetas
            (nombre, descripcion, alergenos, dificultad, categoria, empresa_id, raciones_base,
             centro_id, escandallo_fiable, origen_escandallo, coste_validado)
            VALUES (?, ?, ?, 'pendiente coste', ?, ?, 1, ?, 0, 'ficha_alergenos_sin_cantidades', 0)
            """,
            (name, desc, alergenos, categoria, empresa_id, centro_id),
        )
        receta_id = int(cur.lastrowid)
    cache_key = (empresa_id, centro_id)
    if cache_key in _RECIPE_CACHE:
        _RECIPE_CACHE[cache_key] = [(rid, rname) for rid, rname in _RECIPE_CACHE[cache_key] if rid != receta_id]
        _RECIPE_CACHE[cache_key].append((receta_id, name))

    for ing in ingredients:
        product_id = upsert_product(conn, empresa_id, centro_id, ing)
        conn.execute(
            """
            INSERT INTO recetas_ingredientes (receta_id, producto_id, cantidad)
            VALUES (?, ?, 0)
            """,
            (receta_id, product_id),
        )
    return receta_id


def recipe_by_name(conn: sqlite3.Connection, empresa_id: int, centro_id: int, name: str) -> int | None:
    target = norm(name)
    if not target:
        return None
    cache_key = (empresa_id, centro_id)
    if cache_key not in _RECIPE_CACHE:
        _RECIPE_CACHE[cache_key] = [
            (int(row["id"]), row["nombre"])
            for row in conn.execute(
                "SELECT id, nombre FROM recetas WHERE empresa_id=? AND centro_id=?",
                (empresa_id, centro_id),
            ).fetchall()
        ]
    best = None
    for receta_id, receta_name in _RECIPE_CACHE[cache_key]:
        rn = norm(receta_name)
        if rn == target:
            return receta_id
        contained = (
            target in rn and len(target) / max(len(rn), 1) >= 0.65
        ) or (
            rn in target and len(rn) / max(len(target), 1) >= 0.65
        )
        target_tokens = meaningful_tokens(target)
        recipe_tokens = meaningful_tokens(rn)
        token_match = False
        if target_tokens and recipe_tokens:
            overlap = len(target_tokens & recipe_tokens) / max(len(target_tokens | recipe_tokens), 1)
            token_match = overlap >= 0.67
        if contained or token_match:
            score = abs(len(rn) - len(target))
            if best is None or score < best[0]:
                best = (score, receta_id)
    return best[1] if best else None


def parse_ficha_workbook(path: Path, use_cell_name: bool = False) -> list[dict]:
    wb = load_workbook(path, read_only=True, data_only=True)
    parsed = []
    try:
        for ws in wb.worksheets:
            if norm(ws.title) in {"hoja limpia", "hoja1"}:
                continue
            cell_name = clean(ws.cell(8, 3).value)
            recipe_name = cell_name if use_cell_name and cell_name else clean(ws.title)
            if not recipe_name:
                continue
            service = clean(ws.cell(5, 2).value) or clean(ws.cell(5, 3).value)
            category = clean(ws.cell(6, 2).value) or clean(ws.cell(6, 3).value) or service or "Sin categoria"
            code_cols = {}
            for col in range(3, 28):
                code = clean(ws.cell(9, col).value)
                if code:
                    code_cols[col] = code
            ingredients = []
            allergens = set()
            for row in range(10, 26):
                ing = clean(ws.cell(row, 1).value)
                if not ing:
                    continue
                if norm(ing).startswith(("nota", "marcar")):
                    continue
                ingredients.append(ing)
                for col, code in code_cols.items():
                    mark = clean(ws.cell(row, col).value).upper()
                    if mark in {"X", "T"} and code in ALLERGEN_CODES:
                        allergens.add(ALLERGEN_CODES[code])
            if not ingredients:
                ingredients = [recipe_name]
            allergens.update(infer_allergens_from_name(recipe_name))
            parsed.append({
                "name": recipe_name,
                "sheet": ws.title,
                "category": category,
                "ingredients": ingredients,
                "allergens": sorted(allergens),
                "source": path.name,
            })
    finally:
        wb.close()
    return parsed


def import_recipes(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> dict:
    total = 0
    for path in FICHAS_INSERSO:
        recipes = parse_ficha_workbook(path, use_cell_name=False)
        for r in recipes:
            upsert_recipe(conn, empresa_id, centro_id, r["name"], r["category"], r["allergens"], r["source"], r["ingredients"], True)
        log_import(conn, empresa_id, centro_id, path.name, "recetas_fichas", "inserso", len(recipes))
        total += len(recipes)
    ss = parse_ficha_workbook(FICHAS_SEMANA_SANTA, use_cell_name=True)
    for r in ss:
        upsert_recipe(conn, empresa_id, centro_id, r["name"], r["category"], r["allergens"], r["source"], r["ingredients"], True)
    log_import(conn, empresa_id, centro_id, FICHAS_SEMANA_SANTA.name, "recetas_fichas", "semana_santa", len(ss))
    return {"fichas_recetas": total + len(ss), "inserso": total, "semana_santa": len(ss)}


def ensure_placeholder_recipe(conn: sqlite3.Connection, empresa_id: int, centro_id: int, name: str, categoria: str, source: str) -> int:
    name = clean_menu_dish(name)
    existing = recipe_by_name(conn, empresa_id, centro_id, name)
    if existing:
        row = conn.execute("SELECT descripcion FROM recetas WHERE id=?", (existing,)).fetchone()
        if row and "Receta pendiente de ficha tecnica" in (row["descripcion"] or ""):
            allergens = infer_allergens_from_name(name)
            conn.execute(
                "UPDATE recetas SET nombre=?, categoria=?, alergenos=? WHERE id=?",
                (name, categoria, ", ".join(allergens), existing),
            )
            cache_key = (empresa_id, centro_id)
            if cache_key in _RECIPE_CACHE:
                _RECIPE_CACHE[cache_key] = [
                    (rid, name if rid == existing else rname)
                    for rid, rname in _RECIPE_CACHE[cache_key]
                ]
        return existing
    allergens = infer_allergens_from_name(name)
    return upsert_recipe(conn, empresa_id, centro_id, name, categoria, allergens, source, [name], False)


def upsert_menu(conn: sqlite3.Connection, empresa_id: int, centro_id: int, fecha: str, tipo_menu_id: int, dep_id: int, pv_id: int | None, modo: str, obs: str) -> int:
    row = conn.execute(
        """
        SELECT id FROM menus_diarios
        WHERE empresa_id=? AND centro_id=? AND fecha=? AND tipo_menu_id=? AND modo_servicio=?
        """,
        (empresa_id, centro_id, fecha, tipo_menu_id, modo),
    ).fetchone()
    if row:
        menu_id = int(row["id"])
        for sec in conn.execute("SELECT id FROM menu_subdepartamentos WHERE menu_diario_id=?", (menu_id,)).fetchall():
            conn.execute("DELETE FROM menu_platos WHERE menu_subdepartamento_id=?", (sec["id"],))
        conn.execute("DELETE FROM menu_subdepartamentos WHERE menu_diario_id=?", (menu_id,))
        conn.execute(
            """
            UPDATE menus_diarios
            SET observaciones=?, departamento_id=?, punto_venta_id=?
            WHERE id=?
            """,
            (obs, dep_id, pv_id, menu_id),
        )
        return menu_id
    cur = conn.execute(
        """
        INSERT INTO menus_diarios
        (fecha, tipo_menu_id, observaciones, empresa_id, departamento_id,
         punto_venta_id, modo_servicio, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (fecha, tipo_menu_id, obs, empresa_id, dep_id, pv_id, modo, centro_id),
    )
    return int(cur.lastrowid)


def add_section(conn: sqlite3.Connection, menu_id: int, subdep_id: int, section_name: str, receta_ids: list[int], order: int) -> None:
    cur = conn.execute(
        """
        INSERT INTO menu_subdepartamentos (menu_diario_id, subdepartamento_id, seccion, orden)
        VALUES (?, ?, ?, ?)
        """,
        (menu_id, subdep_id, section_name, order),
    )
    sec_id = int(cur.lastrowid)
    for idx, receta_id in enumerate(receta_ids, start=1):
        conn.execute(
            "INSERT INTO menu_platos (menu_subdepartamento_id, receta_id, orden) VALUES (?, ?, ?)",
            (sec_id, receta_id, idx),
        )


def import_rueda_inserso(conn: sqlite3.Connection, empresa_id: int, centro_id: int, semana_inicio: str, dep_id: int, subdep_id: int, pv_id: int | None) -> dict:
    tipo_ids = {
        "ALMUERZO": tipo_menu(conn, empresa_id, centro_id, "INSERSO 2024 ALMUERZO", pv_id),
        "CENA": tipo_menu(conn, empresa_id, centro_id, "INSERSO 2024 CENA", pv_id),
    }
    wb = load_workbook(RUEDA_INSERSO, read_only=True, data_only=True)
    ws = wb.active
    start = date.fromisoformat(semana_inicio)
    date_by_day = {
        "LUNES": start,
        "MARTES": start + timedelta(days=1),
        "MIERCOLES": start + timedelta(days=2),
        "JUEVES": start + timedelta(days=3),
        "VIERNES": start + timedelta(days=4),
        "SABADO": start + timedelta(days=5),
        "DOMINGO": start + timedelta(days=6),
    }
    menus = platos = placeholders = 0
    try:
        for header_row, service, row_ini, row_fin in [(3, "ALMUERZO", 4, 14), (17, "CENA", 18, 28)]:
            days = {col: clean(ws.cell(header_row, col).value).upper() for col in range(3, 9)}
            for col, day in days.items():
                if not day or day not in date_by_day:
                    continue
                sections: dict[str, list[str]] = {}
                current = ""
                for row in range(row_ini, row_fin + 1):
                    sec = clean(ws.cell(row, 2).value)
                    if sec:
                        current = sec
                    dish = clean(ws.cell(row, col).value)
                    if not dish:
                        continue
                    section = f"{service} - {current or 'GENERAL'}"
                    sections.setdefault(section, []).append(dish)
                if not sections:
                    continue
                fecha = date_by_day[day].isoformat()
                menu_id = upsert_menu(
                    conn,
                    empresa_id,
                    centro_id,
                    fecha,
                    tipo_ids[service],
                    dep_id,
                    pv_id,
                    f"plantilla_inserso_{service.lower()}",
                    f"Importado desde {RUEDA_INSERSO.name}. Servicio {service}; rueda semanal Imserso; no genera produccion.",
                )
                order = 1
                for section, dishes in sections.items():
                    receta_ids = []
                    for dish in dishes:
                        rid = recipe_by_name(conn, empresa_id, centro_id, dish)
                        row = conn.execute("SELECT descripcion FROM recetas WHERE id=?", (rid,)).fetchone() if rid else None
                        if rid and row and "Receta pendiente de ficha tecnica" in (row["descripcion"] or ""):
                            rid = ensure_placeholder_recipe(conn, empresa_id, centro_id, dish, section, RUEDA_INSERSO.name)
                        elif not rid:
                            rid = ensure_placeholder_recipe(conn, empresa_id, centro_id, dish, section, RUEDA_INSERSO.name)
                            placeholders += 1
                        receta_ids.append(rid)
                        platos += 1
                    add_section(conn, menu_id, subdep_id, section, receta_ids, order)
                    order += 1
                menus += 1
    finally:
        wb.close()
    log_import(conn, empresa_id, centro_id, RUEDA_INSERSO.name, "menus", "inserso", menus, f"Platos {platos}; placeholders {placeholders}.")
    return {"menus_inserso": menus, "platos_inserso": platos, "recetas_pendientes_inserso": placeholders}


def docx_paragraphs(path: Path) -> list[str]:
    from docx import Document
    doc = Document(path)
    return [clean(p.text) for p in doc.paragraphs if clean(p.text)]


def import_semana_santa_menu(conn: sqlite3.Connection, empresa_id: int, centro_id: int, dep_id: int, subdep_id: int, pv_id: int | None) -> dict:
    tipo_id = tipo_menu(conn, empresa_id, centro_id, "Carta Semana Santa 2024", pv_id)
    paragraphs = docx_paragraphs(MENU_SEMANA_SANTA)
    sections = {"ENTRANTES": [], "PLATO PRINCIPAL": [], "POSTRE": []}
    current = None
    for p in paragraphs:
        up = p.upper()
        if up in sections:
            current = up
            continue
        if current and not up.startswith(("HOTEL", "GRUPO", "MENÚ", "MENU", "BEBIDAS")) and "€" not in p:
            sections[current].append(p)
    menu_id = upsert_menu(
        conn,
        empresa_id,
        centro_id,
        "2024-03-29",
        tipo_id,
        dep_id,
        pv_id,
        "carta_semana_santa",
        f"Importado desde {MENU_SEMANA_SANTA.name}. Carta/menu historico sin pasar a produccion.",
    )
    platos = placeholders = 0
    for order, (section, dishes) in enumerate(sections.items(), start=1):
        receta_ids = []
        for dish in dishes:
            rid = recipe_by_name(conn, empresa_id, centro_id, dish)
            row = conn.execute("SELECT descripcion FROM recetas WHERE id=?", (rid,)).fetchone() if rid else None
            if rid and row and "Receta pendiente de ficha tecnica" in (row["descripcion"] or ""):
                rid = ensure_placeholder_recipe(conn, empresa_id, centro_id, dish, section, MENU_SEMANA_SANTA.name)
            elif not rid:
                rid = ensure_placeholder_recipe(conn, empresa_id, centro_id, dish, section, MENU_SEMANA_SANTA.name)
                placeholders += 1
            receta_ids.append(rid)
            platos += 1
        add_section(conn, menu_id, subdep_id, section, receta_ids, order)
    log_import(conn, empresa_id, centro_id, MENU_SEMANA_SANTA.name, "menus", "semana_santa", 1, f"Platos {platos}; placeholders {placeholders}.")
    return {"menus_semana_santa": 1, "platos_semana_santa": platos, "recetas_pendientes_semana_santa": placeholders}


def odt_text_lines(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        xml = z.read("content.xml").decode("utf-8", errors="ignore")
    xml = re.sub(r"</text:p>|</text:h>", "\n", xml)
    text = re.sub(r"<[^>]+>", " ", xml)
    text = text.replace("&amp;", "&")
    return [clean(line) for line in text.splitlines() if clean(line)]


def import_carta_cenas(conn: sqlite3.Connection, empresa_id: int, centro_id: int, dep_id: int, subdep_id: int, pv_id: int | None) -> dict:
    if not CARTA_CENAS.exists():
        return {"menus_carta_cenas": 0, "platos_carta_cenas": 0, "recetas_pendientes_carta_cenas": 0}
    tipo_id = tipo_menu(conn, empresa_id, centro_id, "Carta Cenas", pv_id)
    lines = odt_text_lines(CARTA_CENAS)
    sections = {"PARA PICAR": [], "ENSALADAS": [], "CARNES": [], "PESCADOS": [], "POSTRE": []}
    current = None
    for line in lines:
        up = line.upper().strip(" .-")
        if up in sections:
            current = up
            continue
        if current:
            parts = re.split(r"\s\.\s|\s-\s| \u2022 ", line)
            for part in parts:
                part = clean_menu_dish(part)
                if len(part) > 3 and not part.upper().startswith(("HOTEL", "GRUPO")):
                    sections[current].append(part)
    menu_id = upsert_menu(
        conn,
        empresa_id,
        centro_id,
        "2024-01-22",
        tipo_id,
        dep_id,
        pv_id,
        "carta_cenas",
        f"Importado desde {CARTA_CENAS.name}. Carta sin fichas tecnicas: recetas pendientes de escandallo.",
    )
    platos = placeholders = 0
    for order, (section, dishes) in enumerate(sections.items(), start=1):
        receta_ids = []
        for dish in dishes:
            rid = recipe_by_name(conn, empresa_id, centro_id, dish)
            row = conn.execute("SELECT descripcion FROM recetas WHERE id=?", (rid,)).fetchone() if rid else None
            if rid and row and "Receta pendiente de ficha tecnica" in (row["descripcion"] or ""):
                rid = ensure_placeholder_recipe(conn, empresa_id, centro_id, dish, section, CARTA_CENAS.name)
            elif not rid:
                rid = ensure_placeholder_recipe(conn, empresa_id, centro_id, dish, section, CARTA_CENAS.name)
                placeholders += 1
            receta_ids.append(rid)
            platos += 1
        if receta_ids:
            add_section(conn, menu_id, subdep_id, section, receta_ids, order)
    log_import(conn, empresa_id, centro_id, CARTA_CENAS.name, "menus", "carta_cenas", 1, f"Platos {platos}; placeholders {placeholders}.")
    return {"menus_carta_cenas": 1, "platos_carta_cenas": platos, "recetas_pendientes_carta_cenas": placeholders}


def strip_embedded_allergen_paths(text: str) -> str:
    text = re.sub(r"[A-Z]:\\[^ ]+\\([^\\ ]+)\.png", " ", text, flags=re.IGNORECASE)
    return clean_menu_dish(text)


def import_menu_diario_almuerzo(conn: sqlite3.Connection, empresa_id: int, centro_id: int, dep_id: int, subdep_id: int, pv_id: int | None) -> dict:
    if not MENU_DIARIO_ALMUERZO.exists():
        return {"menus_almuerzo_diario": 0, "platos_almuerzo_diario": 0, "recetas_pendientes_almuerzo_diario": 0}
    tipo_id = tipo_menu(conn, empresa_id, centro_id, "Menú del día Almuerzo", pv_id)
    paragraphs = docx_paragraphs(MENU_DIARIO_ALMUERZO)
    sections = {"ENTRANTES": [], "PLATO PRINCIPAL": [], "POSTRE": []}
    current = None
    for p in paragraphs:
        p = strip_embedded_allergen_paths(p)
        up = p.upper()
        if not p:
            continue
        if up in sections:
            current = up
            continue
        if current and not up.startswith(("HOTEL", "GRUPO", "MENÚ", "MENU", "BEBIDAS")) and "€" not in p:
            sections[current].append(p)
    menu_id = upsert_menu(
        conn,
        empresa_id,
        centro_id,
        "2024-03-09",
        tipo_id,
        dep_id,
        pv_id,
        "almuerzo_diario",
        f"Importado desde {MENU_DIARIO_ALMUERZO.name}. Menu del dia almuerzo sin pasar a produccion.",
    )
    platos = placeholders = 0
    for order, (section, dishes) in enumerate(sections.items(), start=1):
        receta_ids = []
        for dish in dishes:
            rid = recipe_by_name(conn, empresa_id, centro_id, dish)
            row = conn.execute("SELECT descripcion FROM recetas WHERE id=?", (rid,)).fetchone() if rid else None
            if rid and row and "Receta pendiente de ficha tecnica" in (row["descripcion"] or ""):
                rid = ensure_placeholder_recipe(conn, empresa_id, centro_id, dish, section, MENU_DIARIO_ALMUERZO.name)
            elif not rid:
                rid = ensure_placeholder_recipe(conn, empresa_id, centro_id, dish, section, MENU_DIARIO_ALMUERZO.name)
                placeholders += 1
            receta_ids.append(rid)
            platos += 1
        if receta_ids:
            add_section(conn, menu_id, subdep_id, section, receta_ids, order)
    log_import(conn, empresa_id, centro_id, MENU_DIARIO_ALMUERZO.name, "menus", "almuerzo_diario", 1, f"Platos {platos}; placeholders {placeholders}.")
    return {"menus_almuerzo_diario": 1, "platos_almuerzo_diario": platos, "recetas_pendientes_almuerzo_diario": placeholders}


def ods_rows(path: Path) -> list[list[str]]:
    ns = {"table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0"}
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("content.xml"))
    table = root.find(".//table:table", ns)
    rows: list[list[str]] = []
    if table is None:
        return rows
    for row in table.findall("table:table-row", ns):
        values: list[str] = []
        for cell in list(row):
            if not (cell.tag.endswith("table-cell") or cell.tag.endswith("covered-table-cell")):
                continue
            repeated = int(cell.attrib.get("{urn:oasis:names:tc:opendocument:xmlns:table:1.0}number-columns-repeated", "1"))
            text = html.unescape(clean(" ".join(cell.itertext())))
            for _ in range(min(repeated, 20)):
                values.append(text)
        if any(values):
            rows.append(values)
    return rows


def split_desayuno_items(value: str) -> list[str]:
    value = clean_menu_dish(value)
    if not value:
        return []
    value = value.replace("YOGUR VARIADO CON Y SIN AZUCAR O FLAN", "YOGUR VARIADO CON Y SIN AZUCAR, FLAN")
    value = value.replace("PAN DE HOGAZA Y BOLLOS", "PAN DE HOGAZA, BOLLOS")
    value = value.replace(" ,", ",").replace(", ", ",")
    parts = [clean_menu_dish(part) for part in re.split(r",|;", value)]
    normalized = []
    for part in parts:
        if not part or part in {"2 TIPOS", "3 TIPOS"}:
            continue
        if part == "2 ZUMOS":
            part = "ZUMOS (2 TIPOS)"
        elif part == "3 TIPOS MERMELADAS":
            part = "MERMELADAS (3 TIPOS)"
        normalized.append(part)
    return normalized


def import_desayuno_buffet(conn: sqlite3.Connection, empresa_id: int, centro_id: int, semana_inicio: str, dep_id: int, subdep_id: int, pv_id: int | None) -> dict:
    if not MENU_DESAYUNO_BUFFET.exists():
        return {"menus_desayuno_buffet": 0, "platos_desayuno_buffet": 0, "recetas_pendientes_desayuno_buffet": 0}
    rows = ods_rows(MENU_DESAYUNO_BUFFET)
    if len(rows) < 2:
        return {"menus_desayuno_buffet": 0, "platos_desayuno_buffet": 0, "recetas_pendientes_desayuno_buffet": 0}
    tipo_id = tipo_menu(conn, empresa_id, centro_id, "Desayuno Buffet", pv_id)
    header_idx = next(
        (idx for idx, row in enumerate(rows) if {"LUNES", "MARTES", "MIERCOLES"}.issubset({clean(cell).upper() for cell in row})),
        -1,
    )
    if header_idx < 0:
        return {"menus_desayuno_buffet": 0, "platos_desayuno_buffet": 0, "recetas_pendientes_desayuno_buffet": 0}
    header = [clean(cell).upper() for cell in rows[header_idx]]
    start = date.fromisoformat(semana_inicio)
    date_by_day = {
        "LUNES": start,
        "MARTES": start + timedelta(days=1),
        "MIERCOLES": start + timedelta(days=2),
        "JUEVES": start + timedelta(days=3),
        "VIERNES": start + timedelta(days=4),
        "SABADO": start + timedelta(days=5),
        "DOMINGO": start + timedelta(days=6),
    }
    day_cols = [(idx, day) for idx, day in enumerate(header) if day in date_by_day]
    sections_by_day: dict[str, dict[str, list[str]]] = {day: {} for _, day in day_cols}
    current_section = ""
    for row in rows[header_idx + 1:]:
        if row and clean(row[0]):
            current_section = clean(row[0]).upper()
        if not current_section:
            continue
        for idx, day in day_cols:
            if idx >= len(row):
                continue
            for item in split_desayuno_items(row[idx]):
                sections_by_day[day].setdefault(current_section, []).append(item)
    menus = platos = placeholders = 0
    for _, day in day_cols:
        sections = sections_by_day.get(day) or {}
        if not sections:
            continue
        menu_id = upsert_menu(
            conn,
            empresa_id,
            centro_id,
            date_by_day[day].isoformat(),
            tipo_id,
            dep_id,
            pv_id,
            "desayuno_buffet",
            f"Importado desde {MENU_DESAYUNO_BUFFET.name}. Rueda semanal de desayuno buffet; no genera produccion.",
        )
        order = 1
        for section, dishes in sections.items():
            receta_ids = []
            seen = set()
            for dish in dishes:
                key = norm(dish)
                if not key or key in seen:
                    continue
                seen.add(key)
                rid = recipe_by_name(conn, empresa_id, centro_id, dish)
                row = conn.execute("SELECT descripcion FROM recetas WHERE id=?", (rid,)).fetchone() if rid else None
                if rid and row and "Receta pendiente de ficha tecnica" in (row["descripcion"] or ""):
                    rid = ensure_placeholder_recipe(conn, empresa_id, centro_id, dish, f"DESAYUNO - {section}", MENU_DESAYUNO_BUFFET.name)
                elif not rid:
                    rid = ensure_placeholder_recipe(conn, empresa_id, centro_id, dish, f"DESAYUNO - {section}", MENU_DESAYUNO_BUFFET.name)
                    placeholders += 1
                receta_ids.append(rid)
                platos += 1
            if receta_ids:
                add_section(conn, menu_id, subdep_id, f"DESAYUNO - {section}", receta_ids, order)
                order += 1
        menus += 1
    log_import(conn, empresa_id, centro_id, MENU_DESAYUNO_BUFFET.name, "menus", "desayuno_buffet", menus, f"Platos {platos}; placeholders {placeholders}.")
    return {"menus_desayuno_buffet": menus, "platos_desayuno_buffet": platos, "recetas_pendientes_desayuno_buffet": placeholders}


def cleanup_orphan_pending_recipes(conn: sqlite3.Connection, empresa_id: int, centro_id: int) -> int:
    cur = conn.execute(
        """
        DELETE FROM recetas
        WHERE empresa_id=? AND centro_id=?
          AND descripcion LIKE '%Receta pendiente de ficha tecnica%'
          AND id NOT IN (SELECT DISTINCT receta_id FROM menu_platos WHERE receta_id IS NOT NULL)
        """,
        (empresa_id, centro_id),
    )
    return int(cur.rowcount or 0)


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        dep_id, pv_id = get_dep_pv(conn, args.empresa_id, args.centro_id)
        subdep_id = get_menu_subdepartamento(conn, args.empresa_id, args.centro_id, dep_id)
        resumen = {}
        resumen.update(import_recipes(conn, args.empresa_id, args.centro_id))
        resumen.update(import_rueda_inserso(conn, args.empresa_id, args.centro_id, args.semana_inicio, dep_id, subdep_id, pv_id))
        resumen.update(import_semana_santa_menu(conn, args.empresa_id, args.centro_id, dep_id, subdep_id, pv_id))
        resumen.update(import_carta_cenas(conn, args.empresa_id, args.centro_id, dep_id, subdep_id, pv_id))
        resumen.update(import_menu_diario_almuerzo(conn, args.empresa_id, args.centro_id, dep_id, subdep_id, pv_id))
        resumen.update(import_desayuno_buffet(conn, args.empresa_id, args.centro_id, args.semana_inicio, dep_id, subdep_id, pv_id))
        resumen["recetas_pendientes_huerfanas_eliminadas"] = cleanup_orphan_pending_recipes(conn, args.empresa_id, args.centro_id)
        conn.commit()
        print(resumen)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
