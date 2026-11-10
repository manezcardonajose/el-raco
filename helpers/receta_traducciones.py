import re
import unicodedata


IDIOMAS_RECETA = ("en", "fr", "de", "it")


def _norm(texto):
    texto = str(texto or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"\s+", " ", texto)
    return texto


GLOSARIO = {
    "en": {
        "crema": "cream soup", "sopa": "soup", "ensalada": "salad", "arroz": "rice",
        "fideos": "noodles", "rollitos": "rolls", "primavera": "spring", "pato": "duck",
        "almendras": "almonds", "gambita": "small prawn", "con": "with", "de": "of",
        "pollo": "chicken", "cerdo": "pork", "ternera": "beef", "pescado": "fish",
        "merluza": "hake", "salmon": "salmon", "langostinos": "king prawns",
        "gambas": "prawns", "mejillon": "mussel", "calamar": "squid", "pulpo": "octopus",
        "verduras": "vegetables", "vegetal": "vegetable", "patata": "potato",
        "champiñones": "mushrooms", "champinones": "mushrooms", "setas": "mushrooms",
        "tomate": "tomato", "cebolla": "onion", "puerro": "leek", "zanahoria": "carrot",
        "espinaca": "spinach", "queso": "cheese", "jamon": "ham", "huevo": "egg",
        "tortilla": "omelette", "pasta": "pasta", "espaguetis": "spaghetti",
        "canelones": "cannelloni", "fideua": "fideua", "pizza": "pizza",
        "salsa": "sauce", "verde": "green", "blanca": "white", "agridulce": "sweet and sour",
        "asada": "roasted", "asado": "roasted", "grill": "grilled", "frito": "fried",
        "horno": "baked", "marinera": "seafood style", "bolognese": "bolognese",
        "postre": "dessert", "tarta": "cake", "flan": "flan", "natillas": "custard",
        "helado": "ice cream", "fruta": "fruit", "manzana": "apple", "chocolate": "chocolate",
    },
    "fr": {
        "crema": "veloute", "sopa": "soupe", "ensalada": "salade", "arroz": "riz",
        "fideos": "nouilles", "rollitos": "rouleaux", "primavera": "printemps", "pato": "canard",
        "almendras": "amandes", "gambita": "petite crevette", "con": "aux", "de": "de",
        "pollo": "poulet", "cerdo": "porc", "ternera": "boeuf", "pescado": "poisson",
        "merluza": "colin", "salmon": "saumon", "langostinos": "gambas",
        "gambas": "crevettes", "mejillon": "moule", "calamar": "calamar", "pulpo": "poulpe",
        "verduras": "legumes", "vegetal": "aux legumes", "patata": "pomme de terre",
        "champiñones": "champignons", "champinones": "champignons", "setas": "champignons",
        "tomate": "tomate", "cebolla": "oignon", "puerro": "poireau", "zanahoria": "carotte",
        "espinaca": "epinards", "queso": "fromage", "jamon": "jambon", "huevo": "oeuf",
        "tortilla": "omelette", "pasta": "pates", "espaguetis": "spaghettis",
        "canelones": "cannellonis", "fideua": "fideua", "pizza": "pizza",
        "salsa": "sauce", "verde": "verte", "blanca": "blanche", "agridulce": "aigre-douce",
        "asada": "rotie", "asado": "roti", "grill": "grille", "frito": "frit",
        "horno": "au four", "marinera": "mariniere", "bolognese": "bolognaise",
        "postre": "dessert", "tarta": "gateau", "flan": "flan", "natillas": "creme anglaise",
        "helado": "glace", "fruta": "fruit", "manzana": "pomme", "chocolate": "chocolat",
    },
    "de": {
        "crema": "cremesuppe", "sopa": "suppe", "ensalada": "salat", "arroz": "reis",
        "fideos": "nudeln", "rollitos": "rollen", "primavera": "fruhlings", "pato": "ente",
        "almendras": "mandeln", "gambita": "kleine garnele", "con": "mit", "de": "von",
        "pollo": "hahnchen", "cerdo": "schwein", "ternera": "rind", "pescado": "fisch",
        "merluza": "seehecht", "salmon": "lachs", "langostinos": "riesengarnelen",
        "gambas": "garnelen", "mejillon": "muschel", "calamar": "tintenfisch", "pulpo": "oktopus",
        "verduras": "gemuse", "vegetal": "gemuse", "patata": "kartoffel",
        "champiñones": "champignons", "champinones": "champignons", "setas": "pilze",
        "tomate": "tomate", "cebolla": "zwiebel", "puerro": "lauch", "zanahoria": "karotte",
        "espinaca": "spinat", "queso": "kase", "jamon": "schinken", "huevo": "ei",
        "tortilla": "omelett", "pasta": "pasta", "espaguetis": "spaghetti",
        "canelones": "cannelloni", "fideua": "fideua", "pizza": "pizza",
        "salsa": "sauce", "verde": "grun", "blanca": "weiss", "agridulce": "suss-sauer",
        "asada": "gerostet", "asado": "gerostet", "grill": "gegrillt", "frito": "frittiert",
        "horno": "aus dem ofen", "marinera": "nach meeresart", "bolognese": "bolognese",
        "postre": "dessert", "tarta": "kuchen", "flan": "flan", "natillas": "vanillecreme",
        "helado": "eis", "fruta": "obst", "manzana": "apfel", "chocolate": "schokolade",
    },
    "it": {
        "crema": "vellutata", "sopa": "zuppa", "ensalada": "insalata", "arroz": "riso",
        "fideos": "noodles", "rollitos": "involtini", "primavera": "primavera", "pato": "anatra",
        "almendras": "mandorle", "gambita": "gamberetto", "con": "con", "de": "di",
        "pollo": "pollo", "cerdo": "maiale", "ternera": "manzo", "pescado": "pesce",
        "merluza": "nasello", "salmon": "salmone", "langostinos": "gamberoni",
        "gambas": "gamberi", "mejillon": "cozza", "calamar": "calamaro", "pulpo": "polpo",
        "verduras": "verdure", "vegetal": "vegetale", "patata": "patata",
        "champiñones": "funghi", "champinones": "funghi", "setas": "funghi",
        "tomate": "pomodoro", "cebolla": "cipolla", "puerro": "porro", "zanahoria": "carota",
        "espinaca": "spinaci", "queso": "formaggio", "jamon": "prosciutto", "huevo": "uovo",
        "tortilla": "frittata", "pasta": "pasta", "espaguetis": "spaghetti",
        "canelones": "cannelloni", "fideua": "fideua", "pizza": "pizza",
        "salsa": "salsa", "verde": "verde", "blanca": "bianca", "agridulce": "agrodolce",
        "asada": "arrosto", "asado": "arrosto", "grill": "grigliato", "frito": "fritto",
        "horno": "al forno", "marinera": "alla marinara", "bolognese": "bolognese",
        "postre": "dessert", "tarta": "torta", "flan": "flan", "natillas": "crema", 
        "helado": "gelato", "fruta": "frutta", "manzana": "mela", "chocolate": "cioccolato",
    },
}


EXPRESIONES = {
    "crema champiñones": {"en": "mushroom cream soup", "fr": "veloute de champignons", "de": "champignoncremesuppe", "it": "vellutata di funghi"},
    "crema champinones": {"en": "mushroom cream soup", "fr": "veloute de champignons", "de": "champignoncremesuppe", "it": "vellutata di funghi"},
    "potage alubia blanca": {"en": "white bean soup", "fr": "potage aux haricots blancs", "de": "weisse bohnensuppe", "it": "zuppa di fagioli bianchi"},
    "arroz blanco": {"en": "white rice", "fr": "riz blanc", "de": "weisser reis", "it": "riso bianco"},
    "arroz 3 delicias": {"en": "three delights rice", "fr": "riz trois delices", "de": "reis drei kostlichkeiten", "it": "riso tre delizie"},
    "fideos de arroz": {"en": "rice noodles", "fr": "nouilles de riz", "de": "reisnudeln", "it": "noodles di riso"},
    "rollitos primavera": {"en": "spring rolls", "fr": "rouleaux de printemps", "de": "fruhlingsrollen", "it": "involtini primavera"},
    "pollo con almendras": {"en": "chicken with almonds", "fr": "poulet aux amandes", "de": "hahnchen mit mandeln", "it": "pollo alle mandorle"},
    "pato hoisin": {"en": "duck with hoisin sauce", "fr": "canard sauce hoisin", "de": "ente mit hoisin-sauce", "it": "anatra con salsa hoisin"},
    "pescado con gambita": {"en": "fish with small prawns", "fr": "poisson aux petites crevettes", "de": "fisch mit kleinen garnelen", "it": "pesce con gamberetti"},
    "patata asada": {"en": "baked potato", "fr": "pomme de terre au four", "de": "ofenkartoffel", "it": "patata al forno"},
    "pizza margarita": {"en": "margherita pizza", "fr": "pizza margherita", "de": "pizza margherita", "it": "pizza margherita"},
    "pizza de jamon": {"en": "ham pizza", "fr": "pizza au jambon", "de": "schinkenpizza", "it": "pizza al prosciutto"},
}


def traducir_nombre_receta(nombre, idioma):
    normal = _norm(nombre)
    if not normal or idioma not in IDIOMAS_RECETA:
        return ""
    if normal in EXPRESIONES and idioma in EXPRESIONES[normal]:
        return EXPRESIONES[normal][idioma].title()
    texto = normal
    for origen in sorted(GLOSARIO[idioma], key=len, reverse=True):
        destino = GLOSARIO[idioma][origen]
        texto = re.sub(rf"\b{re.escape(origen)}\b", destino, texto)
    texto = re.sub(r"\s+", " ", texto).strip(" -")
    if not texto or texto == normal:
        return str(nombre or "").strip()
    return texto.title()


def autocompletar_traducciones(nombre, actuales=None):
    actuales = actuales or {}
    resultado = {}
    for idioma in IDIOMAS_RECETA:
        campo = f"nombre_{idioma}"
        valor = str(actuales.get(campo) or "").strip()
        resultado[campo] = valor or traducir_nombre_receta(nombre, idioma)
    return resultado
