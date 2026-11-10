from __future__ import annotations

import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import DATABASE


FAMILIAS_PRESERVADAS = {
    "bebidas_agua",
    "bebidas_refrescos",
    "bebidas_cerveza",
    "bebidas_premium",
    "bebidas_licores",
    "cafeteria",
    "snacks",
    "helados",
    "buffet",
}

FAMILIAS_LEGACY = {
    "verduras frescas": "verduras",
    "verduras congeladas": "verduras",
    "aceites": "aceites_salsas",
    "encurtidos": "conservas",
    "5a gama": "quinta_gama",
    "5ª gama": "quinta_gama",
    "carne": "carnes",
}


KEYWORDS = {
    "pescados": [
        "pescado", "tilapia", "dorado", "merluza", "salmon", "atun",
        "bacalao", "calamar", "sepia", "pulpo",
    ],
    "mariscos": [
        "mejillon", "gamba", "langostino", "almeja", "marisco",
        "cangrejo", "surimi",
    ],
    "carnes": [
        "carne", "cerdo", "ternera", "pollo", "pavo", "ave", "bacon",
        "jamon", "hamburgues", "albondiga", "chuleta", "escalope",
        "hot dog", "hot dogt", "salchicha", "chorizo", "lomo", "costilla",
        "cordero", "nugget", "nuget",
    ],
    "lacteos": [
        "leche", "nata", "queso", "yogur", "mantequilla", "mozarella",
        "mozzarella", "edam", "gouda", "roquefort", "parmesano",
    ],
    "huevos": ["huevo", "tortilla"],
    "precocinados": [
        "canalon", "lasana", "lasaña", "gyoza", "mousakka", "moussaka",
        "gratinado", "frikadell", "croqueta", "empanadilla", "rollito",
        "frito cong",
    ],
    "verduras": [
        "puerro", "cebolla", "ajo", "judia", "zanahoria", "patata",
        "papa", "guisante", "pimiento", "espinaca", "acelga", "col ",
        "coliflor", "brocoli", "calabacin", "berenjena", "apio",
        "lechuga", "tomate natural", "tomate triturado", "menestra",
        "verdura", "champi", "seta", "maiz", "alcachofa", "alcahofa",
        "calabaza", "esparrago", "hinojo", "nabo", "jalapeno",
        "wakame", "alga", "guacamole",
    ],
    "frutas": [
        "manzana", "platano", "banana", "pera", "naranja", "limon",
        "melon", "sandia", "pina", "piña", "kiwi", "fresa", "fruta",
    ],
    "legumbres": [
        "lenteja", "garbanzo", "alubia", "judion", "soja texturizada",
        "tofu", "garafon", "garrafon",
    ],
    "pastas_arroces": [
        "arroz", "fideua", "pasta", "macarron", "espagueti", "fideo",
        "cous cous", "cuscus", "cus cus", "codos", "coditos", "helices",
        "lazos", "pajaritas", "cintas nido",
    ],
    "panaderia": [
        "harina", "levadura", "pan", "masa", "pizza", "bolleria",
        "croissant",
    ],
    "aceites_salsas": [
        "aceite", "vinagre", "ketchup", "salsa", "tomate salsa",
        "tomate frito", "demiglace", "mayonesa", "mostaza", "caldo",
        "soja", "knor", "knorr", "miso",
    ],
    "especias": [
        "sal", "pimienta", "comino", "oregano", "azucar", "canela",
        "pimenton", "colorante", "nuez moscada", "azafran", "curry",
        "laurel", "tomillo", "cilantro", "bicarb",
    ],
    "frutos_secos": ["almendra", "avellana", "nuez", "pistacho", "cacahuete"],
    "conservas": ["lata", "conserva", "encurtido", "aceituna"],
    "bebidas_agua": ["agua"],
    "bebidas_refrescos": [
        "refresco", "cola", "coca cola", "fanta", "sprite", "tonica",
        "aquarius", "nestea", "zumo",
    ],
    "bebidas_cerveza": ["cerveza", "radler"],
    "bebidas_premium": ["vino", "cava", "champagne", "sidra"],
    "bebidas_licores": [
        "ron", "whisky", "vodka", "ginebra", "licor", "brandy",
        "tequila", "vermut",
    ],
    "cafeteria": ["cafe", "infusion", "te ", "te verde", "chocolate"],
    "snacks": [
        "snack", "patata frita", "ruffles", "doritos", "gominola",
        "frutos secos", "kikos", "chips",
    ],
    "helados": ["helado"],
    "postres": ["postre", "flan", "tarta", "bizcocho", "natilla"],
}


def limpiar(txt: str | None) -> str:
    value = (txt or "").strip().lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"\s+", " ", value)
    return value


def contiene(txt: str, palabras: list[str]) -> bool:
    return any(palabra in txt for palabra in palabras)


def normalizar_unidad_actual(unidad: str | None) -> str | None:
    txt = limpiar(unidad)
    if not txt or txt == "none":
        return None
    if txt in {"litro", "litros", "l"}:
        return "l"
    if txt in {"kilo", "kilos", "kilogramo", "kilogramos", "kg"}:
        return "kg"
    if txt in {"unidad", "unidades", "ud", "uds"}:
        return "ud"
    if txt in {"botella", "lata", "bolsa", "servicio"}:
        return txt
    return txt


def familia_sugerida(nombre_limpio: str, familia_actual: str | None) -> str:
    actual = limpiar(familia_actual)
    if actual in FAMILIAS_PRESERVADAS:
        return actual
    if actual in FAMILIAS_LEGACY:
        return FAMILIAS_LEGACY[actual]

    for familia, palabras in KEYWORDS.items():
        if contiene(nombre_limpio, palabras):
            return familia
    return "otros"


def unidad_sugerida(nombre_limpio: str, familia: str, unidad_actual: str | None) -> str:
    actual = normalizar_unidad_actual(unidad_actual)
    if "lata" in nombre_limpio:
        return "lata"
    if "botella" in nombre_limpio:
        return "botella"
    if "bolsa" in nombre_limpio:
        return "bolsa"
    if familia.startswith("bebidas_"):
        if actual and actual != "kg":
            return actual
        return "l"
    if familia in {"aceites_salsas"}:
        if actual and actual != "kg":
            return actual
        return "l"
    if familia in {"huevos", "helados", "postres", "buffet", "cafeteria"}:
        if actual and actual != "kg":
            return actual
        return "ud"
    if familia in {"snacks"}:
        if actual and actual != "kg":
            return actual
        return "bolsa"
    if familia in {"conservas"} and "lata" in nombre_limpio:
        return "lata"
    if actual:
        return actual
    return "kg"


def conservacion_sugerida(nombre_limpio: str, familia: str, conservacion_actual: str | None) -> str:
    actual = limpiar(conservacion_actual)
    if "cong" in nombre_limpio or "uco" in nombre_limpio or "preco" in nombre_limpio:
        return "congelado"
    if familia in {"carnes", "pescados", "mariscos", "helados"}:
        return "congelado"
    if familia == "precocinados":
        return "congelado"
    if familia in {"lacteos", "huevos"}:
        return "refrigerado"
    if familia in {"verduras", "frutas"}:
        if any(word in nombre_limpio for word in ["lata", "conserva", "tomate frito"]):
            return "seco"
        return "refrigerado"
    if actual in {"seco", "refrigerado", "congelado"}:
        return actual
    return "seco"


def main() -> None:
    db_path = ROOT / DATABASE
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS productos_normalizacion_demo_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            producto_id INTEGER NOT NULL,
            nombre TEXT,
            familia_anterior TEXT,
            familia_nueva TEXT,
            unidad_anterior TEXT,
            unidad_nueva TEXT,
            conservacion_anterior TEXT,
            conservacion_nueva TEXT,
            fecha TEXT NOT NULL
        )
        """
    )

    run_id = datetime.now().strftime("%Y%m%d%H%M%S")
    rows = cur.execute(
        "SELECT id, nombre, familia, unidad, tipo_conservacion FROM productos"
    ).fetchall()

    cambios = []
    for row in rows:
        nombre_limpio = limpiar(row["nombre"])
        familia = familia_sugerida(nombre_limpio, row["familia"])
        unidad = unidad_sugerida(nombre_limpio, familia, row["unidad"])
        conservacion = conservacion_sugerida(
            nombre_limpio, familia, row["tipo_conservacion"]
        )

        familia_actual = (row["familia"] or "").strip()
        unidad_actual = row["unidad"]
        conservacion_actual = (row["tipo_conservacion"] or "").strip()

        if (
            familia_actual != familia
            or normalizar_unidad_actual(unidad_actual) != unidad
            or conservacion_actual != conservacion
        ):
            cambios.append((row, familia, unidad, conservacion))

    for row, familia, unidad, conservacion in cambios:
        cur.execute(
            """
            INSERT INTO productos_normalizacion_demo_log (
                run_id, producto_id, nombre,
                familia_anterior, familia_nueva,
                unidad_anterior, unidad_nueva,
                conservacion_anterior, conservacion_nueva,
                fecha
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                row["id"],
                row["nombre"],
                row["familia"],
                familia,
                row["unidad"],
                unidad,
                row["tipo_conservacion"],
                conservacion,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        cur.execute(
            """
            UPDATE productos
            SET familia = ?, unidad = ?, tipo_conservacion = ?
            WHERE id = ?
            """,
            (familia, unidad, conservacion, row["id"]),
        )

    conn.commit()

    familias = Counter(
        row[0] for row in cur.execute("SELECT familia FROM productos").fetchall()
    )
    unidades = Counter(
        row[0] for row in cur.execute("SELECT unidad FROM productos").fetchall()
    )
    conservaciones = Counter(
        row[0]
        for row in cur.execute("SELECT tipo_conservacion FROM productos").fetchall()
    )
    conn.close()

    print(f"Run: {run_id}")
    print(f"Productos revisados: {len(rows)}")
    print(f"Productos actualizados: {len(cambios)}")
    print("Familias:", dict(familias.most_common()))
    print("Unidades:", dict(unidades.most_common()))
    print("Conservacion:", dict(conservaciones.most_common()))


if __name__ == "__main__":
    main()
