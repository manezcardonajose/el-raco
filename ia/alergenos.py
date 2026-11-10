import re
import unicodedata


ALERGENOS_UE = [
    "Cereales con gluten",
    "Crustaceos",
    "Huevos",
    "Pescado",
    "Cacahuetes",
    "Soja",
    "Leche",
    "Frutos con cascara",
    "Apio",
    "Mostaza",
    "Sesamo",
    "Sulfitos",
    "Altramuces",
    "Moluscos",
]


def normalizar_texto(valor):
    texto = str(valor or "").lower()
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return " ".join(re.findall(r"[a-z0-9]+", texto))


def _contiene(texto, claves):
    padded = f" {texto} "
    for clave in claves:
        clave_norm = normalizar_texto(clave)
        if not clave_norm:
            continue
        if " " in clave_norm:
            if clave_norm in texto:
                return clave
        elif re.search(rf"\b{re.escape(clave_norm)}s?\b", padded):
            return clave
    return ""


REGLAS_ALERGENOS = {
    "Cereales con gluten": [
        "gluten", "trigo", "espelta", "kamut", "centeno", "cebada", "avena",
        "harina de trigo", "harina", "pan", "panko", "pan rallado", "pasta",
        "macarron", "macarrones", "espagueti", "spaghetti", "tallarin", "fideo",
        "cuscus", "cous cous", "bulgur", "seitan", "hojaldre", "masa quebrada",
        "pizza", "canelon", "lasana", "lasaña", "ravioli", "tortellini",
        "canelones", "canalon", "canalones",
        "croissant", "galleta", "bizcocho", "rebozado", "enharinado",
    ],
    "Crustaceos": [
        "crustaceo", "gamba", "gambon", "langostino", "langosta", "bogavante",
        "cangrejo", "centollo", "necora", "cigala", "camaron", "quisquilla",
        "carabinero",
    ],
    "Huevos": [
        "huevo", "huevos", "yema", "yemas", "clara", "claras", "ovoproducto",
        "mayonesa", "mahonesa", "alioli", "all i oli", "tortilla", "flan",
        "crema pastelera", "merengue",
    ],
    "Pescado": [
        "pescado", "merluza", "atun", "atún", "salmon", "salmón", "bacalao",
        "dorada", "lubina", "rape", "rodaballo", "lenguado", "sardina",
        "anchoa", "boqueron", "boquerón", "bonito", "trucha", "caballa",
        "gallo", "corvina", "fumet de pescado", "caldo de pescado",
    ],
    "Cacahuetes": ["cacahuete", "cacahuetes", "mani", "maní", "mantequilla de cacahuete"],
    "Soja": [
        "soja", "soya", "soy", "salsa de soja", "tamari", "tofu", "miso",
        "edamame", "tempeh", "lecitina de soja",
    ],
    "Leche": [
        "leche", "lacteo", "lácteo", "lactosa", "queso", "nata", "crema de leche",
        "mantequilla", "yogur", "yogurt", "bechamel", "besamel", "mozzarella",
        "mozarella", "parmesano", "gorgonzola", "ricotta", "mascarpone", "brie",
        "camembert", "emmental", "gruyere", "cheddar", "cuajada", "suero lacteo",
    ],
    "Frutos con cascara": [
        "almendra", "almendras", "avellana", "avellanas", "nuez", "nueces",
        "nuez de brasil", "nuez macadamia", "macadamia", "pistacho", "pistachos",
        "anacardo", "anacardos", "castaña de caju", "pacana", "pecana", "pinon",
        "piñon", "piñones", "pesto",
    ],
    "Apio": ["apio", "apionabo", "sal de apio"],
    "Mostaza": ["mostaza", "mostaza dijon", "grano de mostaza"],
    "Sesamo": ["sesamo", "sésamo", "ajonjoli", "ajonjolí", "tahini", "tahin"],
    "Sulfitos": [
        "sulfito", "sulfitos", "dioxido de azufre", "metabisulfito", "vino",
        "vino blanco", "vino tinto", "vinagre", "sidra", "cerveza", "brandy",
        "coñac", "cognac", "jerez", "oporto", "pasas", "orejones", "encurtido",
        "aceituna", "pepinillo", "alcaparra",
    ],
    "Altramuces": ["altramuz", "altramuces", "lupino", "lupin"],
    "Moluscos": [
        "molusco", "mejillon", "mejillón", "almeja", "berberecho", "navaja",
        "ostra", "vieira", "zamburiña", "calamar", "chipiron", "chipirón",
        "sepia", "choco", "pulpo", "pota",
    ],
}


ALIAS_CANONICOS = {
    "crustaceo": "Crustaceos",
    "crustaceos": "Crustaceos",
    "crust ceos": "Crustaceos",
    "huevo": "Huevos",
    "huevos": "Huevos",
    "cacahuete": "Cacahuetes",
    "cacahuetes": "Cacahuetes",
    "frutos secos": "Frutos con cascara",
    "frutos con cascara": "Frutos con cascara",
    "frutos de cascara": "Frutos con cascara",
    "frutos con c scara": "Frutos con cascara",
    "lacteos": "Leche",
    "lacteo": "Leche",
    "sesamo": "Sesamo",
    "s samo": "Sesamo",
    "sulfito": "Sulfitos",
    "sulfitos": "Sulfitos",
    "altramuz": "Altramuces",
}


def analizar_alergenos(*valores):
    texto_original = " ".join(str(v or "") for v in valores if v is not None)
    texto = normalizar_texto(texto_original)
    evidencias = {}
    for alergeno in ALERGENOS_UE:
        clave = _contiene(texto, REGLAS_ALERGENOS.get(alergeno, []))
        if clave:
            evidencias.setdefault(alergeno, set()).add(clave)

    # Evita falsos positivos frecuentes en textos dieteticos.
    if "sin gluten" in texto and evidencias.get("Cereales con gluten") == {"gluten"}:
        evidencias.pop("Cereales con gluten", None)

    harinas_sin_gluten_por_nombre = [
        "harina garbanzo",
        "harina de garbanzo",
        "harina maiz",
        "harina de maiz",
        "harina arroz",
        "harina de arroz",
        "harina almendra",
        "harina de almendra",
        "harina coco",
        "harina de coco",
    ]
    if evidencias.get("Cereales con gluten") == {"harina"} and any(clave in texto for clave in harinas_sin_gluten_por_nombre):
        evidencias.pop("Cereales con gluten", None)

    contexto_dulce_choco = [
        "bizcocho choco",
        "chocolate",
        "chocolat",
        "choco blanco",
        "choco negro",
        "choco leche",
        "flan choco",
        "mousse choco",
        "helado choco",
        "tarta choco",
    ]
    if evidencias.get("Moluscos") == {"choco"} and any(clave in texto for clave in contexto_dulce_choco):
        evidencias.pop("Moluscos", None)

    return [
        {"alergeno": alergeno, "evidencias": sorted(evidencias[alergeno])}
        for alergeno in ALERGENOS_UE
        if alergeno in evidencias
    ]


def detectar_alergenos(*valores):
    return [item["alergeno"] for item in analizar_alergenos(*valores)]


def unir_alergenos(items):
    normalizados = {}
    for item in items:
        for parte in re.split(r"[,;/|]+", str(item or "")):
            parte = parte.strip()
            if parte:
                clave = normalizar_texto(parte)
                canonico = ALIAS_CANONICOS.get(clave)
                if canonico:
                    clave = normalizar_texto(canonico)
                    parte = canonico
                normalizados[clave] = parte
    resultado = []
    for alergeno in ALERGENOS_UE:
        clave = normalizar_texto(alergeno)
        if clave in normalizados:
            resultado.append(normalizados.pop(clave))
    resultado.extend(v for _, v in sorted(normalizados.items()))
    return ", ".join(resultado)
