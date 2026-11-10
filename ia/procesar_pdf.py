import os
import re
from io import BytesIO
from ia.alergenos import detectar_alergenos

try:
    from ia.documentos_inteligentes import ComprensionDocumento
except Exception:
    ComprensionDocumento = None


try:
    import pdfplumber
except Exception:
    pdfplumber = None

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

try:
    from PIL import Image
    import pytesseract
except Exception:
    Image = None
    pytesseract = None

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

try:
    from pdf2image import convert_from_path
except Exception:
    convert_from_path = None


class ProcesadorPDF:
    """Procesa PDFs de cocina: texto digital y OCR basico para PDFs escaneados."""

    def __init__(self, tesseract_path=None):
        self.tesseract_path = tesseract_path or self._buscar_tesseract()
        self.comprension = ComprensionDocumento() if ComprensionDocumento else None
        self._cache_imagenes_pdf = {}
        if pytesseract and self.tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_path

    def _buscar_tesseract(self):
        candidatos = [
            os.environ.get("TESSERACT_CMD"),
            os.environ.get("TESSERACT_CMD_PATH"),
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        for ruta in candidatos:
            if ruta and os.path.exists(ruta):
                return ruta
        return None

    def extraer_texto(self, ruta_pdf):
        paginas = self.extraer_texto_paginas(ruta_pdf)
        texto = "\n".join(p["texto"] for p in paginas if p.get("texto"))
        if not texto.strip():
            return "El PDF no contiene texto legible o no hay OCR disponible."
        return texto

    def extraer_texto_paginas(self, ruta_pdf, usar_ocr=True):
        paginas = self._extraer_texto_digital(ruta_pdf)
        if any((p.get("texto") or "").strip() for p in paginas) or not usar_ocr:
            return paginas
        paginas_ocr = self._extraer_texto_ocr(ruta_pdf)
        return paginas_ocr or paginas

    def _extraer_texto_digital(self, ruta_pdf):
        if pdfplumber:
            try:
                paginas = []
                with pdfplumber.open(ruta_pdf) as pdf:
                    for idx, pagina in enumerate(pdf.pages, start=1):
                        paginas.append({
                            "pagina": idx,
                            "texto": pagina.extract_text() or "",
                            "metodo": "texto",
                        })
                return paginas
            except Exception:
                pass

        if PdfReader:
            try:
                reader = PdfReader(ruta_pdf)
                return [
                    {"pagina": idx, "texto": page.extract_text() or "", "metodo": "texto"}
                    for idx, page in enumerate(reader.pages, start=1)
                ]
            except Exception:
                pass
        return []

    def _extraer_texto_ocr(self, ruta_pdf):
        if not (Image and pytesseract and self.tesseract_path):
            return []

        paginas = []
        for idx, imagen in self._imagenes_paginas_pdf(ruta_pdf):
            texto = self._ocr_mejor_orientacion(imagen)
            paginas.append({"pagina": idx, "texto": texto, "metodo": "ocr"})
        return paginas

    def _imagenes_paginas_pdf(self, ruta_pdf):
        """Devuelve imagenes de pagina completa; si falla, usa imagenes embebidas."""
        if not Image:
            return []
        clave = os.path.abspath(ruta_pdf)
        if clave in self._cache_imagenes_pdf:
            return self._cache_imagenes_pdf[clave]

        imagenes = []
        if fitz:
            try:
                doc = fitz.open(ruta_pdf)
                for idx, page in enumerate(doc, start=1):
                    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
                    imagen = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
                    imagenes.append((idx, imagen))
                doc.close()
                if imagenes:
                    self._cache_imagenes_pdf[clave] = imagenes
                    return imagenes
            except Exception:
                imagenes = []

        if convert_from_path:
            try:
                kwargs = {"pdf_path": ruta_pdf, "dpi": 300}
                poppler_path = os.environ.get("POPPLER_PATH")
                if poppler_path:
                    kwargs["poppler_path"] = poppler_path
                paginas = convert_from_path(**kwargs)
                imagenes = [(idx, pagina.convert("RGB")) for idx, pagina in enumerate(paginas, start=1)]
                if imagenes:
                    self._cache_imagenes_pdf[clave] = imagenes
                    return imagenes
            except Exception:
                imagenes = []

        if not PdfReader:
            return []
        try:
            reader = PdfReader(ruta_pdf)
        except Exception:
            return []
        for idx, page in enumerate(reader.pages, start=1):
            try:
                page_images = page.images
            except Exception:
                page_images = []
            for imagen_pdf in page_images:
                try:
                    imagen = Image.open(BytesIO(imagen_pdf.data)).convert("RGB")
                    imagenes.append((idx, imagen))
                except Exception:
                    continue
        self._cache_imagenes_pdf[clave] = imagenes
        return imagenes

    def _ocr_mejor_orientacion(self, imagen):
        mejores = []
        for grados in (0, 90, 270):
            try:
                img = imagen.rotate(grados, expand=True) if grados else imagen
                texto = pytesseract.image_to_string(img, lang="spa+eng", config="--psm 6")
            except Exception:
                continue
            norm = self._normalizar(texto)
            letras = sum(c.isalpha() for c in texto)
            bonus = 500 if ("hoja" in norm and ("mice" in norm or "mise" in norm or "place" in norm)) else 0
            score = letras + bonus - texto.count("|") * 0.5
            mejores.append((score, texto))
        if not mejores:
            return ""
        mejores.sort(key=lambda item: item[0], reverse=True)
        return mejores[0][1]

    def buscar_alergenos(self, texto):
        return detectar_alergenos(texto)

    def analizar_mise_en_place(self, ruta_pdf):
        paginas = self.extraer_texto_paginas(ruta_pdf, usar_ocr=True)
        recetas_amarillas = self._detectar_recetas_amarillas_pdf(ruta_pdf)
        recetas_detalle = self._detectar_recetas_amarillas_detalle_pdf(ruta_pdf)
        hojas = []
        for pagina in paginas:
            texto = pagina.get("texto") or ""
            if not texto.strip():
                continue
            nombre = self._detectar_titulo_hoja(texto, pagina["pagina"])
            ingredientes = self._detectar_lineas_ingredientes(texto)
            recetas_detectadas = recetas_amarillas.get(pagina["pagina"]) or self._detectar_recetas(texto, nombre)
            detalle_pagina = recetas_detalle.get(pagina["pagina"]) or []
            if detalle_pagina:
                recetas_detectadas = [d["nombre"] for d in detalle_pagina if d.get("nombre")]
            recetas_detectadas = self._filtrar_recetas_por_seccion(recetas_detectadas, nombre, texto)
            hojas.append({
                "pagina": pagina["pagina"],
                "nombre": nombre,
                "metodo": pagina.get("metodo") or "",
                "recetas_detectadas": recetas_detectadas,
                "recetas_detalle": detalle_pagina,
                "ingredientes": ingredientes,
                "alergenos": self.buscar_alergenos(" ".join([nombre] + ingredientes)),
                "texto": texto,
            })
        return {
            "archivo": ruta_pdf,
            "paginas": len(paginas),
            "hojas": hojas,
            "total_ingredientes": sum(len(h["ingredientes"]) for h in hojas),
            "ocr_disponible": bool(pytesseract and self.tesseract_path),
        }

    def _detectar_recetas_amarillas_pdf(self, ruta_pdf):
        if not (Image and pytesseract and self.tesseract_path):
            return {}
        resultado = {}
        for idx, imagen in self._imagenes_paginas_pdf(ruta_pdf):
            recetas = []
            try:
                recetas.extend(self._detectar_recetas_amarillas_imagen(imagen))
            except Exception:
                continue
            if recetas:
                resultado[idx] = recetas
        return resultado

    def _detectar_recetas_amarillas_detalle_pdf(self, ruta_pdf):
        if not (Image and pytesseract and self.tesseract_path):
            return {}
        resultado = {}
        for idx, imagen in self._imagenes_paginas_pdf(ruta_pdf):
            detalles = []
            try:
                detalles.extend(self._detectar_recetas_amarillas_detalle_imagen(imagen))
            except Exception:
                continue
            if detalles:
                resultado[idx] = detalles
        return resultado

    def _detectar_recetas_amarillas_imagen(self, imagen):
        imagen = self._orientar_por_amarillo(imagen)
        grupos = self._grupos_titulo_por_fila(imagen)
        recetas = []
        vistos = set()
        for grupo in grupos:
            y1, y2 = grupo["y1"], grupo["y2"]
            nombre = self._ocr_nombre_en_franja(imagen, y1, y2)
            nombre = self._normalizar_nombre_por_contexto(nombre)
            clave = self._normalizar(nombre)
            if "hoja" in clave and ("mice" in clave or "mise" in clave or "place" in clave):
                continue
            if not self._es_nombre_receta_valido(nombre):
                continue
            if len(clave) >= 4 and clave not in vistos:
                recetas.append(nombre)
                vistos.add(clave)
        return recetas

    def _detectar_recetas_amarillas_detalle_imagen(self, imagen):
        imagen = self._orientar_por_amarillo(imagen)
        grupos = self._grupos_titulo_por_fila(imagen)
        try:
            data = pytesseract.image_to_data(imagen, lang="spa+eng", config="--psm 6", output_type=pytesseract.Output.DICT)
        except Exception:
            data = None
        if not grupos:
            return self._detectar_recetas_por_ocr_de_tabla(data)

        detalles = []
        vistos = set()
        for idx, grupo in enumerate(grupos):
            y1, y2 = grupo["y1"], grupo["y2"]
            nombre = self._ocr_nombre_en_franja(imagen, y1, y2)
            nombre = self._normalizar_nombre_por_contexto(nombre)
            clave = self._normalizar(nombre)
            if not self._es_nombre_receta_valido(nombre) or clave in vistos:
                continue
            siguiente_y = grupos[idx + 1]["y1"] if idx + 1 < len(grupos) else imagen.size[1]
            ingredientes = self._ingredientes_entre_franjas(data, y2 + 4, siguiente_y - 4)
            detalles.append({
                "nombre": nombre,
                "ingredientes": ingredientes,
                "estado": "",
            })
            vistos.add(clave)
        if detalles:
            tiene_pizzas = any("pizza" in self._normalizar(d["nombre"]) for d in detalles)
            tiene_rissotos = any(("rissoto" in self._normalizar(d["nombre"]) or "risotto" in self._normalizar(d["nombre"])) for d in detalles)
            if tiene_pizzas:
                detalles = [d for d in detalles if "pizza" in self._normalizar(d["nombre"])]
            elif tiene_rissotos:
                detalles = [
                    d for d in detalles
                    if "rissoto" in self._normalizar(d["nombre"]) or "risotto" in self._normalizar(d["nombre"])
                ]
            return detalles
        return self._detectar_recetas_por_ocr_de_tabla(data)

    def _grupos_titulo_por_fila(self, imagen):
        w, h = imagen.size
        grupos = []
        y = 0
        while y < h:
            count = 0
            for x in range(0, w, 4):
                if self._es_color_titulo(imagen.getpixel((x, y))):
                    count += 1
            if count > max(35, w // 28):
                inicio = y
                fin = y
                xs = []
                y += 1
                while y < h:
                    count2 = 0
                    for x in range(0, w, 4):
                        if self._es_color_titulo(imagen.getpixel((x, y))):
                            count2 += 1
                            xs.append(x)
                    if count2 <= max(20, w // 45):
                        break
                    fin = y
                    y += 1
                if 12 <= fin - inicio + 1 <= 70:
                    x1 = max(0, min(xs) - 10) if xs else 25
                    x2 = min(w, max(xs) + 10) if xs else w - 20
                    grupos.append({"y1": inicio, "y2": fin, "x1": x1, "x2": x2})
            y += 1
        return grupos

    def _ocr_nombre_en_franja(self, imagen, y1, y2):
        w, h = imagen.size
        margen_y = 12
        opciones = [
            (25, max(0, y1 - margen_y), min(w - 20, 1200), min(h, y2 + margen_y)),
            (60, max(0, y1 - margen_y), min(w - 20, 980), min(h, y2 + margen_y)),
            (25, max(0, y1 - margen_y), w - 20, min(h, y2 + margen_y)),
        ]
        for caja in opciones:
            crop = imagen.crop(caja)
            crop = crop.resize((crop.width * 2, crop.height * 2))
            for psm in (6, 7, 13):
                try:
                    texto = pytesseract.image_to_string(crop, lang="spa+eng", config=f"--psm {psm}")
                except Exception:
                    texto = ""
                nombre = self._limpiar_receta_amarilla(texto)
                if nombre:
                    return nombre
        return ""

    def _detectar_recetas_por_ocr_de_tabla(self, data):
        lineas = self._lineas_ocr(data)
        if not lineas:
            return []
        titulos = []
        for idx, linea in enumerate(lineas):
            texto = linea["texto"]
            norm = self._normalizar(texto)
            if not norm:
                continue
            es_titulo = False
            if "pizza" in norm and "hoja" not in norm and "masa de pizza" not in norm and "masadepizza" not in norm:
                es_titulo = True
            if ("rissoto" in norm or "risotto" in norm) and "hoja" not in norm:
                es_titulo = True
            if norm in {"de hongos porchini", "hongos porchini"}:
                texto = "RISSOTO DE HONGOS PORCHINI"
                es_titulo = True
            if es_titulo:
                nombre = self._limpiar_receta_amarilla(texto)
                if self._es_nombre_receta_valido(nombre):
                    titulos.append((idx, nombre))
        if not titulos:
            return []

        detalles = []
        vistos = set()
        for pos, (idx, nombre) in enumerate(titulos):
            clave = self._normalizar(nombre)
            if clave in vistos:
                continue
            fin_idx = titulos[pos + 1][0] if pos + 1 < len(titulos) else len(lineas)
            ingredientes = []
            ing_vistos = set()
            for linea in lineas[idx + 1:fin_idx]:
                ing = self._limpiar_linea(linea["texto"])
                ing_norm = self._normalizar(ing)
                if not ing_norm:
                    continue
                if any(x in ing_norm for x in ["hecho", "falta", "google", "docs", "hoja de calculo", "mice em place"]):
                    continue
                if self._es_nombre_receta_valido(ing):
                    continue
                letras = sum(c.isalpha() for c in ing)
                if letras < 3 or letras / max(len(ing), 1) < 0.45:
                    continue
                if ing_norm not in ing_vistos:
                    ingredientes.append(self._titulo(ing[:90]))
                    ing_vistos.add(ing_norm)
            detalles.append({"nombre": nombre, "ingredientes": ingredientes, "estado": ""})
            vistos.add(clave)
        return detalles

    def _lineas_ocr(self, data):
        if not data:
            return []
        agrupadas = {}
        total = len(data.get("text", []))
        for i in range(total):
            texto = (data["text"][i] or "").strip()
            if not texto:
                continue
            conf = str(data.get("conf", ["-1"])[i])
            try:
                if float(conf) < 20:
                    continue
            except Exception:
                pass
            key = (data.get("block_num", [0])[i], data.get("par_num", [0])[i], data.get("line_num", [0])[i])
            agrupadas.setdefault(key, []).append((int(data["left"][i]), int(data["top"][i]), texto))
        lineas = []
        for palabras in agrupadas.values():
            ordenadas = sorted(palabras)
            texto = " ".join(t for _, _, t in ordenadas)
            top = min(y for _, y, _ in ordenadas)
            lineas.append({"top": top, "texto": texto})
        return sorted(lineas, key=lambda x: x["top"])

    def _ingredientes_entre_franjas(self, data, inicio_y, fin_y):
        if not data:
            return []
        lineas = {}
        total = len(data.get("text", []))
        for i in range(total):
            texto = (data["text"][i] or "").strip()
            if not texto:
                continue
            conf = str(data.get("conf", ["-1"])[i])
            try:
                if float(conf) < 20:
                    continue
            except Exception:
                pass
            y = int(data["top"][i])
            if y < inicio_y or y > fin_y:
                continue
            x = int(data["left"][i])
            if x < 35 or x > 1180:
                continue
            key = (data.get("block_num", [0])[i], data.get("par_num", [0])[i], data.get("line_num", [0])[i])
            lineas.setdefault(key, []).append((x, texto))

        ingredientes = []
        vistos = set()
        for palabras in lineas.values():
            linea = " ".join(t for _, t in sorted(palabras))
            linea = self._limpiar_linea(linea)
            norm = self._normalizar(linea)
            if len(norm) < 4:
                continue
            if any(x in norm for x in ["hecho", "falta", "google", "docs", "hoja de calculo"]):
                continue
            letras = sum(c.isalpha() for c in linea)
            if letras < 3 or letras / max(len(linea), 1) < 0.45:
                continue
            clave = self._normalizar(linea)
            if clave and clave not in vistos:
                ingredientes.append(self._titulo(linea[:90]))
                vistos.add(clave)
        return ingredientes

    def _orientar_por_amarillo(self, imagen):
        w, h = imagen.size
        max_row = 0
        for y in range(0, h, 6):
            count = 0
            for x in range(0, w, 8):
                if self._es_amarillo(imagen.getpixel((x, y))):
                    count += 1
            max_row = max(max_row, count)
        max_col = 0
        for x in range(0, w, 6):
            count = 0
            for y in range(0, h, 8):
                if self._es_amarillo(imagen.getpixel((x, y))):
                    count += 1
            max_col = max(max_col, count)
        if max_col > max_row * 2:
            return imagen.rotate(90, expand=True)
        return imagen

    def _es_amarillo(self, rgb):
        r, g, b = rgb[:3]
        return r > 180 and g > 145 and b < 145 and r > b + 55 and g > b + 35

    def _es_color_titulo(self, rgb):
        r, g, b = rgb[:3]
        azul_claro = r > 205 and g > 225 and b > 225 and b > r + 8 and g > r + 8
        return self._es_amarillo(rgb) or azul_claro

    def _limpiar_receta_amarilla(self, texto):
        texto = self._limpiar_linea(texto)
        texto = re.sub(r"(?i)\bde\s*m?hongos\s*por[ce]hin[i,]?\b", "DE HONGOS PORCHINI", texto)
        texto = re.sub(r"(?i)\briss?oto\s*de\b", "RISSOTO DE", texto)
        texto = re.sub(r"(?i)\bhecho\W{0,8}falta\b", "", texto)
        texto = re.sub(r"(?i)\b(hecho|falta|falia|fata|facta)\b", "", texto)
        texto = re.sub(r"(?i)\bhecho\b\s*$", "", texto.strip())
        texto = re.sub(r"(?i)^\s*(nn|poo|doo|j)\s+", "", texto)
        if "tartufada" in self._normalizar(texto) and "salsa" not in self._normalizar(texto):
            texto = "Salsa tartufada"
        texto = re.sub(r"(?i)\b(ct|tt|ce|cs|cet|ft|sc|ss|y|s)\b(\s+\b(ct|tt|ce|cs|cet|ft|sc|ss|y|s)\b)*$", "", texto.strip())
        texto = re.sub(r"^[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", "", texto)
        texto = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 ,./&-]+", " ", texto)
        texto = re.sub(r"\s+", " ", texto).strip(" -|.,;:")
        partes = texto.split()
        if len(partes) >= 3:
            primera = self._normalizar(partes[0]).replace(" ", "")
            resto = self._normalizar(" ".join(partes[1:])).replace(" ", "")
            if primera and primera == resto:
                texto = " ".join(partes[1:])
        return self._titulo(texto)

    def _normalizar_nombre_por_contexto(self, nombre):
        norm = self._normalizar(nombre)
        if norm in {"de hongos porchini", "hongos porchini", "demhongos porchini", "dehongosporchin", "dehongos porchin"}:
            return "RISSOTO DE HONGOS PORCHINI"
        return nombre

    def _filtrar_recetas_por_seccion(self, recetas, seccion, texto=""):
        seccion_norm = self._normalizar(seccion)
        limpias = []
        vistos = set()
        for receta in recetas or []:
            nombre = self._limpiar_receta_amarilla(receta)
            clave = self._normalizar(nombre)
            if not clave or clave in vistos:
                continue
            if self._es_nombre_receta_valido(nombre, seccion):
                limpias.append(nombre)
                vistos.add(clave)

        if "rissot" in seccion_norm or "risot" in seccion_norm:
            con_risotto = [r for r in limpias if any(x in self._normalizar(r) for x in ["rissoto", "risotto"])]
            if con_risotto:
                return con_risotto
            encontrados = []
            vistos = set()
            for match in re.finditer(r"(?i)RISS?OTO\s+.{3,80}", texto or ""):
                candidato = re.split(r"\s{2,}|\s+\||\[|_", match.group(0))[0]
                limpia = self._limpiar_receta_amarilla(candidato)
                clave = self._normalizar(limpia)
                if ("rissoto" not in clave and "risotto" not in clave) or clave in vistos:
                    continue
                if any(x in clave for x in ["hojasdecalculo", "lacaldera"]):
                    continue
                if self._es_nombre_receta_valido(limpia, seccion):
                    encontrados.append(limpia)
                    vistos.add(clave)
            for linea in (texto or "").splitlines():
                limpia = self._limpiar_receta_amarilla(linea)
                clave = self._normalizar(limpia)
                if ("rissoto" not in clave and "risotto" not in clave) or clave in vistos:
                    continue
                if self._es_nombre_receta_valido(limpia, seccion):
                    encontrados.append(limpia)
                    vistos.add(clave)
            if encontrados:
                return encontrados
            return []

        return limpias

    def _es_nombre_receta_valido(self, nombre, seccion=""):
        if not nombre:
            return False
        norm = self._normalizar(nombre)
        if len(norm) < 6:
            return False
        letras = sum(c.isalpha() for c in nombre)
        digitos = sum(c.isdigit() for c in nombre)
        if letras < 7 or letras / max(len(nombre), 1) < 0.62:
            return False
        if digitos > 1 or re.search(r"\d\s+\d|\d{2,}", nombre):
            return False
        if len(re.findall(r"[<>/\\|_{}\[\]~^]", nombre)) >= 2:
            return False
        if any(x in norm for x in ["hechofalta", "echofacta", "echofalta", "falta", "facta"]):
            return False
        seccion_norm = self._normalizar(seccion)

        tokens = norm.split()
        cortas = sum(1 for t in tokens if len(t) <= 2 and t not in {"de", "del", "la", "el", "y", "a"})
        if len(tokens) >= 5 and cortas / len(tokens) > 0.35:
            return False

        ruido = {
            "mmm", "eee", "nn", "ooo", "poo", "doo", "sss", "scs", "ctt",
            "ce", "ct", "tt", "uuu", "mmm", "jon", "gid", "docs", "google",
            "hoja", "mice", "mise", "place", "transferencia", "buffett", "onop",
        }
        if sum(1 for t in tokens if t in ruido) >= 2:
            return False

        ingredientes_base = [
            "masa de pizza", "masadepizza", "masa fresca", "masafresca",
            "salsa de tomate", "salsadetomate", "tomate fresca", "tomatefresca",
            "queso mozzarella", "quesomozzarella", "mozzarella rallada",
            "queso parmesano", "quesoparmesano", "parmesano rallado",
            "nata liquida", "nataliquida", "mantequilla", "cebolla picada",
            "aceite de oliva", "caldo de", "caldode", "reduccion de",
            "reduccionde", "reducciondebalsamico", "hojas de", "hojasde", "oregano", "albahaca",
            "basilico", "cocoa", "granos de cafe", "granosdecafe",
            "queso ricotta", "quesoricotta",
        ]
        pistas_plato = [
            "risotto", "rissoto", "pizza", "lasa", "canelon", "berenjena",
            "berenjenas", "ensalada", "carpaccio", "tabla", "brusqueta",
            "brusquetas", "rodaballo", "lenguado", "saltinboca", "saltinbonca",
            "solomillo", "t bone", "chuletas", "tiramisu", "coulat",
            "pannacota", "carbonara", "salsa", "gorgonzola", "porchini",
            "tartufada", "pescadora",
        ]
        norm_compacta = norm.replace(" ", "")
        tiene_pista = any(p in norm or p.replace(" ", "") in norm_compacta for p in pistas_plato)
        es_ingrediente_base = any(p in norm or p.replace(" ", "") in norm_compacta for p in ingredientes_base)
        if ("pizza" in seccion_norm or "pizzas" in seccion_norm) and es_ingrediente_base:
            return False
        if es_ingrediente_base and not tiene_pista:
            return False

        # Si parece una frase larga hecha de restos de columnas OCR, no es un titulo.
        if len(tokens) >= 9 and not tiene_pista:
            return False
        return True

    def _detectar_titulo_hoja(self, texto, pagina):
        lineas = [self._limpiar_linea(l) for l in texto.splitlines()]
        lineas = [l for l in lineas if l]
        for linea in lineas[:10]:
            n = self._normalizar(linea)
            if "mice" in n or "mise" in n or "place" in n:
                titulo = re.sub(r"(?i).*hoja\s*'? *(de)?\s*(mice|mise)\s*(em)?\s*place", "", linea).strip(" -_|:")
                titulo = re.sub(r"(?i)\s*-\s*hojas?.*$", "", titulo).strip()
                titulo = titulo or linea
                return self._limpiar_titulo_hoja(titulo, pagina)
        for linea in lineas[:8]:
            n = self._normalizar(linea)
            if not any(x in n for x in ["google", "http", "docs", "hojas de calculo"]):
                return self._limpiar_titulo_hoja(linea, pagina)
        return f"Hoja mise en place pagina {pagina}"

    def _limpiar_titulo_hoja(self, titulo, pagina):
        titulo = re.sub(r"(?i)\s*hojas?\s+de\s+c.lculo\s+de\s+google.*$", "", str(titulo or "")).strip(" -_|:")
        if self._normalizar(titulo) in {"plas veganos", "platos vegan"}:
            titulo = "PLATOS VEGANOS"
        return self._titulo(titulo) if titulo else f"Hoja mise en place pagina {pagina}"

    def _detectar_lineas_ingredientes(self, texto):
        ingredientes = []
        vistos = set()
        for linea in texto.splitlines():
            limpia = self._limpiar_linea(linea)
            if not limpia:
                continue
            norm = self._normalizar(limpia)
            if len(norm) < 3:
                continue
            if any(x in norm for x in ["google", "http", "hoja de calculo", "transferencia"]):
                continue
            if "hoja" in norm and ("mice" in norm or "mise" in norm or "place" in norm):
                continue
            letras = sum(c.isalpha() for c in limpia)
            if letras < 3 or letras / max(len(limpia), 1) < 0.45:
                continue
            if len(re.findall(r"[=/<>]", limpia)) > 3:
                continue
            if re.fullmatch(r"[\d\s.,:/#-]+", limpia):
                continue
            if len(limpia) > 70:
                limpia = limpia[:70].strip()
            clave = self._normalizar(limpia)
            if clave and clave not in vistos:
                ingredientes.append(self._titulo(limpia))
                vistos.add(clave)
        return ingredientes[:80]

    def _detectar_recetas(self, texto, titulo):
        recetas = []
        vistos = set()
        titulo_norm = self._normalizar(titulo)
        for linea in texto.splitlines():
            limpia = self._limpiar_linea(linea)
            norm = self._normalizar(limpia)
            if not norm or norm == titulo_norm:
                continue
            if any(x in norm for x in ["google", "http", "hoja de calculo", "transferencia"]):
                continue
            if "hoja" in norm and ("mice" in norm or "mise" in norm or "place" in norm):
                continue
            palabras = norm.split()
            letras = sum(c.isalpha() for c in limpia)
            if letras < 10 or len(palabras) < 3:
                continue
            texto_largo = len(limpia) >= 24
            palabras_plato = any(p in norm for p in [
                "risotto", "rissoto", "pizza", "lasa", "canelon", "carpaccio",
                "vitelo", "provolone", "solomillo", "rodaballo", "salmon",
                "caprese", "ensalada", "tiramisu", "coulat", "pannacota",
                "pasta", "gnocchi", "ravioli", "tagliatelle", "spaguetti",
            ])
            if not (texto_largo or palabras_plato):
                continue
            if not self._es_nombre_receta_valido(limpia, titulo):
                continue
            # Ingredientes muy claros: no se deben convertir en recetas.
            if any(p in norm for p in [
                "mantequilla", "cebolla picada", "queso parmesano rallado",
                "caldo", "aceite", "nata liquida", "masa de pizza", "salsa de tomate",
            ]) and not palabras_plato:
                continue
            clave = norm
            if clave not in vistos:
                recetas.append(self._titulo(limpia))
                vistos.add(clave)
        return recetas[:12]

    def _limpiar_linea(self, linea):
        if self.comprension:
            return self.comprension.limpiar_ocr(linea).strip(" -|.,;:")
        linea = str(linea or "")
        linea = linea.replace("\t", " ")
        linea = re.sub(r"[\[\]\|\{\}_]+", " ", linea)
        linea = re.sub(r"^[\s0oOº°.,;:>\"'/-]+", "", linea)
        linea = re.sub(r"\b(CT|TT|CE|CS|CET|FT)\b$", "", linea, flags=re.I)
        linea = re.sub(r"\s+", " ", linea)
        return linea.strip(" -|.,;:")

    def _normalizar(self, texto):
        import unicodedata

        texto = str(texto or "").lower()
        texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
        return " ".join(re.findall(r"[a-z0-9]+", texto))

    def _titulo(self, texto):
        texto = " ".join(str(texto or "").split())
        if not texto:
            return texto
        return texto.upper() if texto.isupper() else texto[:1].upper() + texto[1:]
