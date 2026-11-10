import re
import unicodedata


class ComprensionDocumento:
    """Capa comun para limpiar OCR y convertir texto sucio en senales utiles."""

    VOCABULARIO_HOSTELERIA = [
        "masa", "pizza", "fresca", "salsa", "tomate", "pomodoro", "queso",
        "mozzarella", "rallada", "oregano", "basilico", "prochutto",
        "prosciutto", "burrata", "cherry", "confitados", "hongos", "porchini",
        "ricotta", "aceite", "oliva", "pesto", "verde", "crema", "trufa",
        "blanca", "parmesano", "laminas", "nueces", "tostadas", "miel",
        "negra", "rellada", "compota", "almendras", "peperonchini",
        "cebolletas", "cordero", "asado", "josper", "fetta", "pimientos",
        "hojas", "menta", "arroz", "carnaroli", "caldo", "vegetal",
        "ternera", "pescado", "trompetillas", "muerte", "polvo", "shitake",
        "remolacha", "asada", "mascarpone", "nata", "liquida",
        "mantequilla", "fria", "ossobuco", "estofado", "azafran",
        "guisantes", "hervidos", "zanahoria", "oro", "vieiras",
        "langostinos", "limpios", "plancha", "calabaza", "horno",
        "ensalada", "rucula", "mango", "espuma", "carpaccio", "bogabante",
        "tabla", "brusquetas", "rodaballo", "lenguado", "solomillo",
        "chuletas", "lechal", "lasaña", "verduras", "carrilladas",
        "berenjenas", "parmesana", "tiramisu", "coulat", "chocolate",
        "pannacotas", "gorgonzola", "carbonara", "tartufada", "pescadora",
    ]

    CORRECCIONES = {
        "masadepizzafresca": "MASA DE PIZZA FRESCA",
        "salsadetomatefresca": "SALSA DE TOMATE FRESCA",
        "salsadetomatepomodoro": "SALSA DE TOMATE POMODORO",
        "quesomozzarellarallada": "QUESO MOZZARELLA RALLADA",
        "quesomozzarellas": "QUESO MOZZARELLAS",
        "quesoparmesanorallado": "QUESO PARMESANO RALLADO",
        "quesomarcarpone": "QUESO MASCARPONE",
        "marcadearrozcarnaloni": "MARCA DE ARROZ CARNAROLI",
        "caldovegetal": "CALDO VEGETAL",
        "caldodeternera": "CALDO DE TERNERA",
        "caldodepescado": "CALDO DE PESCADO",
        "cubosdemantequillafria": "CUBOS DE MANTEQUILLA FRIA",
        "cubosderemolachaasada": "CUBOS DE REMOLACHA ASADA",
        "cubosdezanahoriahervidos": "CUBOS DE ZANAHORIA HERVIDOS",
        "trompetillasdelamuerte": "TROMPETILLAS DE LA MUERTE",
        "polvodeshitake": "POLVO DE SHITAKE",
        "nataliquida": "NATA LIQUIDA",
        "hojasdepandeoro": "HOJAS DE PAN DE ORO",
        "laminasdequesoparmesano": "LAMINAS DE QUESO PARMESANO",
        "langostinoslimpios": "LANGOSTINOS LIMPIOS",
        "vieirasplancha": "VIEIRAS PLANCHA",
        "tomatescherryconfitados": "TOMATES CHERRY CONFITADOS",
        "basilicofresco": "BASILICO FRESCO",
        "aceitedeoliva": "ACEITE DE OLIVA",
        "cremadetrufablanca": "CREMA DE TRUFA BLANCA",
        "parmesanorallado": "PARMESANO RALLADO",
        "parmesanolaminas": "PARMESANO LAMINAS",
        "trufanegrarellada": "TRUFA NEGRA RELLADA",
        "corderoasadoenjosper": "CORDERO ASADO EN JOSPER",
        "yoguralasfinashierbas": "YOGUR A LAS FINAS HIERBAS",
    }

    BASURA_OCR = {
        "ct", "tt", "ce", "cs", "cet", "ft", "sc", "sss", "scs", "poo",
        "doo", "jo", "ll", "ooo", "aaa", "eee", "gid",
    }

    def normalizar(self, texto):
        texto = str(texto or "").lower()
        texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
        return " ".join(re.findall(r"[a-z0-9]+", texto))

    def limpiar_ocr(self, texto, contexto=""):
        texto = str(texto or "")
        texto = texto.replace("\t", " ")
        texto = re.sub(r"[\[\]\|\{\}_]+", " ", texto)
        texto = re.sub(r"^[\s0oOÂºÂ°.,;:>\"'/-]+", "", texto)
        texto = re.sub(r"\b(HECHO|FALTA|FALIA|FATA|FACTA)\b", "", texto, flags=re.I)
        texto = re.sub(r"\s+", " ", texto).strip(" -|.,;:")
        texto = self.reconstruir_palabras(texto, contexto=contexto)
        texto = re.sub(r"\s+", " ", texto).strip(" -|.,;:")
        return texto

    def reconstruir_palabras(self, texto, contexto=""):
        partes = []
        for token in str(texto or "").split():
            limpio = self._normalizar_token(token)
            if not limpio:
                continue
            if limpio in self.BASURA_OCR or (len(limpio) == 1 and limpio not in {"a", "y"}):
                continue
            if limpio in self.CORRECCIONES:
                partes.extend(self.CORRECCIONES[limpio].split())
                continue
            if len(limpio) >= 12 and " " not in token:
                reconstruido = self._segmentar_token(limpio)
                if reconstruido:
                    partes.extend(reconstruido)
                    continue
            partes.append(token.strip(" -|.,;:"))
        texto = " ".join(p for p in partes if p)
        texto = re.sub(r"\b(SS|SCS|CT|TT|CE|CS|SC)\b$", "", texto, flags=re.I).strip()
        return texto

    def _normalizar_token(self, token):
        token = self.normalizar(token).replace(" ", "")
        return token

    def _segmentar_token(self, token):
        vocab = sorted(self.VOCABULARIO_HOSTELERIA, key=len, reverse=True)
        resultado = []
        resto = token
        while resto:
            match = None
            for palabra in vocab:
                p = self.normalizar(palabra).replace(" ", "")
                if resto.startswith(p):
                    match = palabra.upper()
                    resto = resto[len(p):]
                    break
            if not match:
                if len(resto) <= 3:
                    return None
                return None
            resultado.append(match)
        return resultado if len(resultado) > 1 else None
