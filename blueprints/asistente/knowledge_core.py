"""Nucleo de conocimiento sectorial para IA estrategica.

Clasifica fuentes internas y externas sin depender de Flask ni de la ERP.
"""

import re
from html import unescape
from urllib.error import URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


def _to_float(value, default=0.0):
    try:
        if value is None:
            return default
        if isinstance(value, str):
            value = value.strip().replace("€", "").replace(" ", "")
            value = "".join(ch for ch in value if ch.isdigit() or ch in ",.-")
            if not value:
                return default
            if "," in value and "." in value:
                decimal = "," if value.rfind(",") > value.rfind(".") else "."
                miles = "." if decimal == "," else ","
                value = value.replace(miles, "").replace(decimal, ".")
            elif "," in value:
                value = value.replace(".", "").replace(",", ".")
            elif value.count(".") > 1:
                partes = value.split(".")
                value = "".join(partes[:-1]) + "." + partes[-1]
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_euros(value, decimales=2):
    number = _to_float(value, 0.0)
    text = f"{number:,.{int(decimales)}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text}€"

TIPOS_FUENTE_CONOCIMIENTO = [
    ("dato_interno", "Dato interno"),
    ("comentario_cliente", "Comentario cliente"),
    ("comentario_equipo", "Comentario equipo"),
    ("linkedin", "LinkedIn / red profesional"),
    ("periodico", "Periodico"),
    ("revista", "Revista sectorial"),
    ("informe", "Informe / estudio"),
    ("proveedor", "Proveedor"),
    ("tour_operador", "Tour operador"),
    ("competencia", "Competencia"),
    ("agencia_viajes", "Agencia de viajes"),
    ("manual_operativo", "Manual operativo"),
    ("otro", "Otro"),
]

AMBITOS_HOSTELERIA = [
    ("hotel", "Hotel"),
    ("restaurante", "Restaurante"),
    ("cadena_restaurantes", "Cadena restaurantes"),
    ("bar_pub", "Bar / pub"),
    ("discoteca", "Discoteca"),
    ("food_truck", "Food truck"),
    ("takeaway", "Comida para llevar"),
    ("catering", "Catering"),
    ("agencia_viajes", "Agencia de viajes"),
    ("multi_hosteleria", "Hosteleria general"),
]

AREAS_CONOCIMIENTO = [
    ("direccion", "Direccion"),
    ("finanzas", "Finanzas"),
    ("revenue", "Revenue"),
    ("comercial", "Comercial"),
    ("marketing", "Marketing"),
    ("operaciones", "Operaciones"),
    ("cocina", "Cocina"),
    ("sala", "Sala"),
    ("rrhh", "RRHH"),
    ("mantenimiento", "Mantenimiento"),
    ("compras", "Compras"),
    ("experiencia_cliente", "Experiencia cliente"),
    ("mercado", "Mercado / tendencias"),
]

TEMAS_TENDENCIA = {
    "viajeros": ("viajero", "turista", "turismo", "demanda", "reserva", "ocupacion", "vacaciones", "destino", "ecoturista"),
    "precio_revenue": ("precio", "tarifa", "adr", "revpar", "revenue", "oferta", "descuento", "comision"),
    "gastronomia": ("gastronomia", "restaurante", "cocina", "menu", "carta", "chef", "food", "bebida", "vino"),
    "personal": ("empleo", "personal", "trabajador", "salario", "rotacion", "equipo", "talento", "formacion"),
    "sostenibilidad": ("sostenible", "sostenibilidad", "ecoturismo", "energia", "residuo", "agua", "carbono"),
    "tecnologia": ("tecnologia", "ia", "inteligencia artificial", "software", "digital", "automatizacion", "datos"),
    "agencias_ttoo": ("agencia", "tour operador", "turoperador", "paquete", "viajes", "intermediacion"),
    "experiencia": ("experiencia", "cliente", "fidelizacion", "familia", "satisfaccion", "review", "opinion"),
}

PALANCAS_COMPETITIVAS = {
    "precio": ("precio", "tarifa", "barato", "descuento", "oferta", "comision", "promocion"),
    "experiencia": ("experiencia", "cliente", "fidelizacion", "familia", "review", "opinion", "servicio"),
    "producto": ("producto", "habitacion", "menu", "carta", "gastronomia", "actividad", "paquete"),
    "canal": ("booking", "agencia", "tour operador", "turoperador", "ota", "directa", "intermediacion"),
    "marca": ("marca", "posicionamiento", "diferenciacion", "local", "autentico", "premium"),
    "operacion": ("personal", "equipo", "coste", "rotacion", "mantenimiento", "limpieza", "cola"),
}

MODELOS_NEGOCIO_CHAT = {
    "hotel": ("hotel", "habitaciones, ocupacion, ADR, RevPAR, reputacion, mantenimiento, personal y canales"),
    "aparthotel": ("aparthotel", "unidades, estancia media, ocupacion, ADR, limpieza, mantenimiento y servicios"),
    "restaurante": ("restaurante", "ticket medio, rotacion de mesas, coste de receta, sala, cocina, compras y reseñas"),
    "cadena_restaurantes": ("cadena de restaurantes", "unidad economica por local, compras, estandarizacion, personal y marca"),
    "kioscos": ("cadena de kioscos", "trafico, ticket, merma, stock, turnos, ubicacion y velocidad de servicio"),
    "pub": ("pub/bar", "aforo, ticket, horarios, personal, seguridad, musica, bebidas y recurrencia"),
    "discoteca": ("discoteca", "aforo, barras, entradas, seguridad, eventos, rotacion y reputacion"),
    "takeaway": ("comida para llevar", "pedidos, ticket, packaging, delivery, cocina, tiempos y comisiones"),
    "catering": ("catering/eventos", "coste por evento, personal eventual, logistica, compras y margen por cliente"),
    "agencia_viajes": ("agencia de viajes", "margen por producto, proveedor, comision, captacion, repeticion y riesgo"),
    "food_truck": ("food truck", "ubicacion, flujo, ticket, permisos, stock, velocidad y calendario de eventos"),
}

PALABRAS_MODELO_CHAT = {
    "hotel": ("hotel", "habitacion", "habitaciones", "ocupacion", "adr", "revpar", "recepcion"),
    "aparthotel": ("aparthotel", "apartamento", "apartamentos"),
    "restaurante": ("restaurante", "mesa", "mesas", "sala", "carta", "menu", "comedor"),
    "cadena_restaurantes": ("cadena de restaurantes", "varios restaurantes", "franquicia"),
    "kioscos": ("kiosco", "kioscos"),
    "pub": ("pub", "bar", "copas"),
    "discoteca": ("discoteca", "aforo", "entrada", "seguridad"),
    "takeaway": ("takeaway", "delivery", "domicilio", "llevar", "pedidos"),
    "catering": ("catering", "evento", "banquete"),
    "agencia_viajes": ("agencia de viajes", "viajes", "paquete turistico"),
    "food_truck": ("food truck", "camion", "feria"),
}


def _detectar_modelo_negocio(texto, contexto):
    for clave, palabras in PALABRAS_MODELO_CHAT.items():
        if _coincide(texto, *palabras):
            nombre, datos_clave = MODELOS_NEGOCIO_CHAT[clave]
            return {"clave": clave, "nombre": nombre, "datos_clave": datos_clave}
    escenarios = contexto.get("escenarios") or []
    for esc in escenarios:
        clave = str(esc.get("tipo") or "").lower()
        if clave in MODELOS_NEGOCIO_CHAT:
            nombre, datos_clave = MODELOS_NEGOCIO_CHAT[clave]
            return {"clave": clave, "nombre": nombre, "datos_clave": datos_clave}
    nombre, datos_clave = ("hosteleria general", "ingresos, costes, personal, cliente, operacion, mercado y caja")
    return {"clave": "multi_hosteleria", "nombre": nombre, "datos_clave": datos_clave}


def clasificar_aprendizaje_chat(texto, tipo_declarado="auto"):
    texto = (texto or "").strip()
    base = texto.lower()
    tipo_declarado = (tipo_declarado or "auto").strip().lower()
    tipos_validos = {"dato_confirmado", "hipotesis", "criterio_direccion", "senal_externa", "auto"}
    if tipo_declarado not in tipos_validos:
        tipo_declarado = "auto"

    if tipo_declarado != "auto":
        tipo = tipo_declarado
    elif _coincide(base, "creo", "podria", "supongo", "hipotesis", "igual", "quizas", "parece"):
        tipo = "hipotesis"
    elif _coincide(base, "linkedin", "periodico", "revista", "articulo", "mercado", "competencia", "tendencia"):
        tipo = "senal_externa"
    elif _coincide(base, "quiero", "prefiero", "mi criterio", "nuestra idea", "estrategia", "prioridad"):
        tipo = "criterio_direccion"
    else:
        tipo = "dato_confirmado"

    confianza = {
        "dato_confirmado": 0.78,
        "criterio_direccion": 0.68,
        "senal_externa": 0.52,
        "hipotesis": 0.38,
    }.get(tipo, 0.45)
    modulo = "Direccion"
    if _coincide(base, "personal", "equipo", "rrhh", "fichaje", "rotacion"):
        modulo = "RRHH"
    elif _coincide(base, "cocina", "menu", "receta", "buffet", "carta"):
        modulo = "Cocina"
    elif _coincide(base, "precio", "adr", "ocupacion", "tour", "operador", "revenue"):
        modulo = "Revenue"
    elif _coincide(base, "mantenimiento", "averia", "capex", "inversion"):
        modulo = "Inversion"
    return {
        "tipo": tipo,
        "modulo": modulo,
        "clave": texto[:90] or "aprendizaje chat",
        "valor": tipo,
        "detalle": texto,
        "confianza": confianza,
    }


def _tokens_contraste(texto):
    stop = {
        "para", "como", "este", "esta", "esto", "desde", "donde", "cuando", "porque",
        "sobre", "entre", "tener", "hacer", "mejor", "datos", "hotel", "negocio",
        "decision", "decidir", "quiero", "puede", "pueden", "debe", "deben",
    }
    tokens = re.findall(r"[a-z0-9áéíóúñü]{4,}", (texto or "").lower())
    return {t for t in tokens if t not in stop}


def contrastar_interno_externo(pregunta, fuentes, memoria, escenarios):
    tokens = _tokens_contraste(pregunta)

    def score_texto(texto):
        if not tokens:
            return 0
        return len(tokens & _tokens_contraste(texto))

    fuentes_rel = []
    for fuente in fuentes or []:
        texto = " ".join(str(fuente.get(k) or "") for k in ("titulo", "resumen", "lectura", "uso_recomendado", "riesgos", "area", "ambito"))
        score = score_texto(texto)
        if score:
            item = dict(fuente)
            item["_score_contraste"] = score
            fuentes_rel.append(item)
    fuentes_rel.sort(key=lambda x: (x.get("_score_contraste", 0), x.get("confianza_pct") or 0), reverse=True)

    memoria_rel = []
    for memo in memoria or []:
        texto = " ".join(str(memo.get(k) or "") for k in ("tipo", "modulo", "clave", "valor", "detalle", "origen"))
        score = score_texto(texto)
        if score:
            item = dict(memo)
            item["_score_contraste"] = score
            memoria_rel.append(item)
    memoria_rel.sort(key=lambda x: (x.get("_score_contraste", 0), x.get("confianza") or 0), reverse=True)

    escenarios_rel = []
    for esc in escenarios or []:
        res = esc.get("resultado") or {}
        texto = " ".join(str(x or "") for x in (
            esc.get("nombre"), esc.get("tipo"), esc.get("decision_tipo"), esc.get("fase_negocio"),
            esc.get("zona_mercado"), esc.get("categoria_establecimiento"), esc.get("notas"),
            res.get("veredicto"), res.get("lectura_zona"), res.get("lectura_categoria"),
        ))
        score = score_texto(texto)
        if score:
            item = dict(esc)
            item["_score_contraste"] = score
            escenarios_rel.append(item)
    escenarios_rel.sort(key=lambda x: x.get("_score_contraste", 0), reverse=True)

    contradicciones = []
    for memo in memoria_rel[:4]:
        tipo = str(memo.get("tipo") or "").lower()
        if tipo == "hipotesis" and not fuentes_rel:
            contradicciones.append(f"'{memo.get('clave')}' es hipotesis interna y no tiene fuente externa relacionada.")
        if tipo == "senal_externa" and not escenarios_rel:
            contradicciones.append(f"'{memo.get('clave')}' es senal externa pero aun no tiene escenario que mida impacto.")

    if fuentes_rel and (memoria_rel or escenarios_rel):
        nivel = "contrastado_parcial"
        lectura = "Hay relacion entre informacion interna y fuentes externas, pero aun conviene medir impacto con escenarios o datos reales."
    elif fuentes_rel:
        nivel = "solo_exterior"
        lectura = "Hay senales externas, pero falta cruzarlas con memoria interna, escenarios o datos reales del negocio."
    elif memoria_rel or escenarios_rel:
        nivel = "solo_interno"
        lectura = "Hay informacion interna, pero falta contraste exterior para no crear una burbuja de decision."
    else:
        nivel = "sin_evidencia"
        lectura = "No encuentro evidencia interna ni externa suficiente relacionada con la consulta."

    return {
        "nivel": nivel,
        "lectura": lectura,
        "fuentes": fuentes_rel[:5],
        "memoria": memoria_rel[:5],
        "escenarios": escenarios_rel[:5],
        "contradicciones": contradicciones[:4],
        "tokens": sorted(tokens)[:12],
    }


def generar_plan_contraste(pregunta, foco, modelo, contraste):
    tokens = contraste.get("tokens") or list(_tokens_contraste(pregunta))[:8]
    modelo_nombre = modelo.get("nombre") or "hosteleria"
    base = " ".join(tokens[:5]) or modelo_nombre
    busquedas = [
        f"{modelo_nombre} {base} tendencias mercado hosteleria",
        f"{modelo_nombre} {base} ADR ocupacion demanda turismo",
        f"{modelo_nombre} {base} costes personal rentabilidad",
    ]
    if foco == "finanzas":
        busquedas = [
            f"inversion hotel {base} capex payback ADR ocupacion",
            f"mercado hotelero {base} compraventa rentabilidad",
            f"turismo {base} demanda viajeros gasto medio prevision",
        ]
    elif foco == "revenue":
        busquedas = [
            f"{modelo_nombre} {base} precio ADR RevPAR ocupacion",
            f"{modelo_nombre} {base} OTA tour operador comision venta directa",
            f"turismo {base} reservas pickup demanda temporada",
        ]
    elif foco == "rrhh":
        busquedas = [
            f"hosteleria {base} rotacion personal salarios absentismo",
            f"{modelo_nombre} {base} productividad plantilla coste laboral",
            f"turismo {base} empleo hosteleria temporada",
        ]
    elif foco == "cocina":
        busquedas = [
            f"tendencias gastronomia {base} hotel restaurante",
            f"coste alimentario {base} menu rentabilidad hosteleria",
            f"preferencias cliente {base} comida turismo",
        ]

    datos_internos = [
        "ingresos reales o estimados por dia/mes",
        "coste variable unitario",
        "coste laboral imputable",
        "ocupacion/uso y estacionalidad",
        "satisfaccion, quejas y repeticion",
    ]
    if foco == "finanzas":
        datos_internos.extend(["precio de compra o inversion", "capex pendiente", "deuda/coste financiero", "payback objetivo"])
    elif foco == "revenue":
        datos_internos.extend(["precio neto por canal", "comision", "cancelaciones", "pickup"])
    elif foco == "rrhh":
        datos_internos.extend(["fichajes", "ausencias", "horas extra", "rotacion por departamento"])

    escenario = "Crear o actualizar un escenario con fuente marcada como externo/mixto, calidad de datos y punto de equilibrio."
    if contraste.get("nivel") == "solo_interno":
        prioridad = "Alta: falta contraste exterior antes de decidir."
    elif contraste.get("nivel") == "solo_exterior":
        prioridad = "Alta: falta cruzarlo con datos internos o simulador."
    elif contraste.get("nivel") == "sin_evidencia":
        prioridad = "Muy alta: primero hay que conseguir evidencia minima."
    else:
        prioridad = "Media: ya hay contraste parcial; falta medir impacto y seguimiento."

    return {
        "prioridad": prioridad,
        "busquedas_externas": busquedas[:4],
        "datos_internos": datos_internos[:9],
        "escenario_minimo": escenario,
    }


def analizar_datos_para_chat(metricas, escenarios, contraste):
    metricas = metricas or {}
    escenarios = escenarios or []
    contraste = contraste or {}
    margen = _to_float(metricas.get("margen_aprox"), 0)
    ocupacion = _to_float(metricas.get("ocupacion_aprox_pct"), 0)
    quejas = int(_to_float(metricas.get("quejas_abiertas"), 0))
    ingresos = _to_float(metricas.get("ingresos_totales_aprox"), 0)
    costes = _to_float(metricas.get("costes_controlados_aprox"), 0)
    coste_pct = (costes / ingresos * 100) if ingresos else 0

    viables = []
    negativos = []
    fragiles = []
    for esc in escenarios:
        res = esc.get("resultado") or {}
        margen_esc = _to_float(res.get("margen"), 0)
        riesgo = _to_float(res.get("riesgo_pct"), 100)
        equilibrio = res.get("punto_equilibrio_ocupacion_pct")
        item = {"nombre": esc.get("nombre") or "escenario", "margen": margen_esc, "riesgo": riesgo, "equilibrio": equilibrio, "veredicto": res.get("veredicto") or "-"}
        if margen_esc > 0 and riesgo < 60:
            viables.append(item)
        if margen_esc <= 0:
            negativos.append(item)
        if margen_esc > 0 and equilibrio is not None and _to_float(equilibrio, 0) >= 70:
            fragiles.append(item)

    puntos = []
    alertas = []
    if ingresos:
        puntos.append(f"ingresos aprox. {_format_euros(ingresos)}")
    puntos.append(f"margen aprox. {_format_euros(margen)}")
    if ocupacion:
        puntos.append(f"ocupacion/uso {round(ocupacion, 1)}%")
    if coste_pct:
        puntos.append(f"coste controlado {round(coste_pct, 1)}% de ingresos")
    puntos.append(f"{quejas} quejas abiertas")
    puntos.append(f"{len(viables)} escenarios viables, {len(negativos)} negativos, {len(fragiles)} fragiles")

    if margen <= 0:
        alertas.append("margen interno negativo o sin margen claro")
    if coste_pct > 75:
        alertas.append("coste demasiado alto frente a ingresos")
    if ocupacion and ocupacion < 50:
        alertas.append("demanda/uso bajo")
    if ocupacion > 90:
        alertas.append("ocupacion alta: riesgo de tension operativa")
    if quejas >= 5:
        alertas.append("quejas altas: riesgo para fidelizacion")
    if contraste.get("nivel") in {"solo_interno", "sin_evidencia"}:
        alertas.append("falta contraste exterior")
    if contraste.get("nivel") == "solo_exterior":
        alertas.append("falta medirlo con datos internos")

    score = 50
    if margen > 0:
        score += 15
    else:
        score -= 20
    if coste_pct and coste_pct > 75:
        score -= 15
    if quejas >= 5:
        score -= 10
    if viables:
        score += 10
    if contraste.get("nivel") == "contrastado_parcial":
        score += 15
    elif contraste.get("nivel") in {"solo_interno", "solo_exterior"}:
        score -= 8
    elif contraste.get("nivel") == "sin_evidencia":
        score -= 18
    score = max(0, min(100, score))

    if score >= 70:
        semaforo = "verde"
        lectura = "Hay base para avanzar con una prueba controlada."
    elif score >= 45:
        semaforo = "ambar"
        lectura = "Hay señales utiles, pero falta contraste antes de decidir fuerte."
    else:
        semaforo = "rojo"
        lectura = "No tomaria una decision fuerte todavia; faltan datos o el riesgo es alto."

    if viables and contraste.get("nivel") == "contrastado_parcial":
        accion = f"Tomar el mejor escenario viable como piloto: {viables[0]['nombre']}."
    elif viables:
        accion = f"Usar {viables[0]['nombre']} como hipotesis, pero buscar contraste exterior antes de ejecutar."
    elif negativos:
        accion = "No ejecutar todavia; ajustar precio, costes o alcance y volver a simular."
    else:
        accion = "Crear un escenario minimo con ingresos, costes, satisfaccion y fuente de datos marcada."

    return {
        "semaforo": semaforo,
        "score": round(score, 1),
        "lectura": lectura,
        "datos_clave": puntos,
        "alertas": alertas,
        "accion_recomendada": accion,
        "viables": viables[:3],
        "negativos": negativos[:3],
        "fragiles": fragiles[:3],
    }


def _limpiar_html(texto):
    texto = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", texto or "")
    texto = re.sub(r"(?is)<br\s*/?>", "\n", texto)
    texto = re.sub(r"(?is)</p>", "\n", texto)
    texto = re.sub(r"(?is)<[^>]+>", " ", texto)
    texto = unescape(texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def _buscar_meta(html, nombre):
    patrones = [
        rf'<meta[^>]+name=["\']{re.escape(nombre)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+property=["\']{re.escape(nombre)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(nombre)}["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(nombre)}["\']',
    ]
    for patron in patrones:
        match = re.search(patron, html or "", flags=re.I | re.S)
        if match:
            return _limpiar_html(match.group(1))
    return ""


def _inferir_tipo_desde_url(url):
    host = (urlparse(url).netloc or "").lower()
    if "linkedin." in host:
        return "linkedin"
    if any(x in host for x in ("hosteltur", "tecnohotel", "revistahosteleria", "restauracionnews")):
        return "revista"
    if any(x in host for x in ("elpais", "elmundo", "abc.", "lavanguardia", "eldiario", "expansion")):
        return "periodico"
    if any(x in host for x in ("booking", "tripadvisor", "google.")):
        return "competencia"
    return "otro"


def extraer_fuente_desde_url(url, timeout=8):
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"ok": False, "error": "URL no valida. Usa un enlace http o https."}
    req = Request(
        url,
        headers={
            "User-Agent": "ElRacoERP/1.0 (+fuentes conocimiento)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            raw = response.read(600000)
    except (OSError, URLError, ValueError) as exc:
        return {"ok": False, "error": f"No se pudo leer el enlace: {exc}"}

    if "html" not in content_type and b"<html" not in raw[:3000].lower():
        return {"ok": False, "error": "El enlace no parece una pagina HTML legible."}
    html = raw.decode("utf-8", errors="ignore")
    titulo = _buscar_meta(html, "og:title")
    if not titulo:
        match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        titulo = _limpiar_html(match.group(1)) if match else ""
    descripcion = _buscar_meta(html, "description") or _buscar_meta(html, "og:description")
    cuerpo = _limpiar_html(html)
    if titulo and cuerpo.startswith(titulo):
        cuerpo = cuerpo[len(titulo):].strip()
    resumen_base = descripcion or cuerpo[:900]
    lectura = (
        "Fuente capturada automaticamente. Revisar si la tendencia afecta a precio, "
        "producto, personal, compras, posicionamiento o experiencia antes de usarla."
    )
    riesgos = (
        "Contrastar con datos internos y fecha de publicacion. Puede estar sesgada, "
        "incompleta, promocional o bloqueada por el sitio original."
    )
    return {
        "ok": True,
        "tipo": _inferir_tipo_desde_url(url),
        "ambito": "multi_hosteleria",
        "area": "mercado",
        "titulo": titulo or parsed.netloc,
        "url": url,
        "autor": parsed.netloc,
        "resumen": resumen_base[:1200],
        "lectura": lectura,
        "uso_recomendado": "Guardar como hipotesis sectorial y preguntar al chat estrategico que escenarios conviene simular.",
        "riesgos": riesgos,
        "texto_extraido": cuerpo[:2500],
    }


def _extraer_tag_xml(item, tag):
    match = re.search(rf"(?is)<{tag}[^>]*>(.*?)</{tag}>", item or "")
    if not match:
        return ""
    valor = match.group(1)
    valor = re.sub(r"(?is)<!\[CDATA\[(.*?)\]\]>", r"\1", valor)
    return _limpiar_html(valor)


def _extraer_link_feed(item, base_url):
    link = _extraer_tag_xml(item, "link")
    if link:
        return urljoin(base_url, link)
    match = re.search(r'(?is)<link[^>]+href=["\']([^"\']+)["\']', item or "")
    if match:
        return urljoin(base_url, unescape(match.group(1)))
    return base_url


def extraer_fuentes_desde_feed(url, limite=8, timeout=10):
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"ok": False, "error": "Feed no valido. Usa una URL http o https."}
    req = Request(
        url,
        headers={
            "User-Agent": "ElRacoERP/1.0 (+radar hosteleria)",
            "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*;q=0.8",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read(900000)
    except (OSError, URLError, ValueError) as exc:
        return {"ok": False, "error": f"No se pudo leer el feed: {exc}"}

    xml = raw.decode("utf-8", errors="ignore")
    bloques = re.findall(r"(?is)<item\b.*?</item>", xml)
    if not bloques:
        bloques = re.findall(r"(?is)<entry\b.*?</entry>", xml)
    if not bloques:
        pagina = extraer_fuente_desde_url(url, timeout=timeout)
        if pagina.get("ok"):
            return {"ok": True, "items": [pagina], "modo": "html"}
        return {"ok": False, "error": "No se han encontrado noticias en formato RSS/Atom."}

    items = []
    for bloque in bloques[: max(1, min(int(limite or 8), 20))]:
        titulo = _extraer_tag_xml(bloque, "title") or parsed.netloc
        link = _extraer_link_feed(bloque, url)
        resumen = (
            _extraer_tag_xml(bloque, "description")
            or _extraer_tag_xml(bloque, "summary")
            or _extraer_tag_xml(bloque, "content")
            or titulo
        )
        publicado = _extraer_tag_xml(bloque, "pubDate") or _extraer_tag_xml(bloque, "updated") or _extraer_tag_xml(bloque, "published")
        items.append({
            "ok": True,
            "tipo": _inferir_tipo_desde_url(link or url),
            "ambito": "multi_hosteleria",
            "area": "mercado",
            "titulo": titulo[:240],
            "url": link,
            "autor": parsed.netloc,
            "resumen": resumen[:1200],
            "lectura": "Fuente importada desde feed externo. Usar como senal de mercado pendiente de contraste.",
            "uso_recomendado": "Agrupar con otras fuentes y consultar radar/chat antes de simular decisiones.",
            "riesgos": "Puede ser titular parcial, noticia repetida, promocional o no aplicable al establecimiento.",
            "publicado": publicado,
        })
    return {"ok": True, "items": items, "modo": "feed"}


def normalizar_fuente_conocimiento(datos):
    tipo = (datos.get("tipo") or "otro").strip().lower()
    ambito = (datos.get("ambito") or "multi_hosteleria").strip().lower()
    area = (datos.get("area") or "mercado").strip().lower()
    tipos_validos = {valor for valor, _ in TIPOS_FUENTE_CONOCIMIENTO}
    ambitos_validos = {valor for valor, _ in AMBITOS_HOSTELERIA}
    areas_validas = {valor for valor, _ in AREAS_CONOCIMIENTO}
    if tipo not in tipos_validos:
        tipo = "otro"
    if ambito not in ambitos_validos:
        ambito = "multi_hosteleria"
    if area not in areas_validas:
        area = "mercado"
    return {
        "tipo": tipo,
        "ambito": ambito,
        "area": area,
        "titulo": (datos.get("titulo") or "Fuente sin titulo").strip(),
        "url": (datos.get("url") or "").strip(),
        "autor": (datos.get("autor") or "").strip(),
        "resumen": (datos.get("resumen") or "").strip(),
        "lectura": (datos.get("lectura") or "").strip(),
        "uso_recomendado": (datos.get("uso_recomendado") or "").strip(),
        "riesgos": (datos.get("riesgos") or "").strip(),
    }


def evaluar_fuente_conocimiento(datos):
    item = normalizar_fuente_conocimiento(datos)
    tipo = item["tipo"]
    confianza = {
        "dato_interno": 82,
        "informe": 72,
        "manual_operativo": 70,
        "proveedor": 55,
        "tour_operador": 55,
        "revista": 52,
        "periodico": 48,
        "agencia_viajes": 48,
        "linkedin": 38,
        "competencia": 36,
        "comentario_cliente": 45,
        "comentario_equipo": 50,
        "otro": 30,
    }.get(tipo, 30)
    if item["url"]:
        confianza += 5
    if item["resumen"] and item["lectura"]:
        confianza += 8
    if item["riesgos"]:
        confianza += 4
    confianza = max(15, min(90, confianza))

    if tipo in {"linkedin", "periodico", "revista", "competencia"}:
        estado = "pendiente_contrastar"
        lectura = "Fuente util para detectar tendencia, pero necesita contraste antes de convertirse en decision."
    elif tipo in {"dato_interno", "manual_operativo"}:
        estado = "utilizable"
        lectura = "Fuente interna o criterio propio; puede alimentar recomendaciones si esta vigente."
    else:
        estado = "pendiente_revision"
        lectura = "Fuente guardada para revision humana y cruce con datos internos."

    return {
        **item,
        "confianza_pct": confianza,
        "estado": estado,
        "lectura_ia": lectura,
        "etiqueta": f"{item['ambito']} / {item['area']} / {tipo}",
    }


def _coincide(texto, *partes):
    texto = (texto or "").lower()
    return any(parte in texto for parte in partes)


def detectar_tendencias_sectoriales(fuentes, minimo=1):
    tendencias = []
    for tema, palabras in TEMAS_TENDENCIA.items():
        matches = []
        for fuente in fuentes or []:
            texto = " ".join([
                str(fuente.get("titulo") or ""),
                str(fuente.get("resumen") or ""),
                str(fuente.get("lectura") or ""),
                str(fuente.get("uso_recomendado") or ""),
            ]).lower()
            hits = [p for p in palabras if p in texto]
            if hits:
                item = dict(fuente)
                item["hits"] = hits[:5]
                matches.append(item)
        if len(matches) >= minimo:
            confianza_media = sum(float(m.get("confianza_pct") or 0) for m in matches) / max(len(matches), 1)
            prioridad = "alta" if len(matches) >= 4 else "media" if len(matches) >= 2 else "info"
            tendencias.append({
                "tema": tema,
                "titulo": tema.replace("_", " ").title(),
                "prioridad": prioridad,
                "senales": len(matches),
                "confianza_pct": round(min(90, confianza_media + len(matches) * 3), 1),
                "fuentes": matches[:6],
                "lectura": _lectura_tendencia(tema, len(matches)),
                "accion": _accion_tendencia(tema),
            })
    orden = {"alta": 0, "media": 1, "info": 2}
    return sorted(tendencias, key=lambda t: (orden.get(t["prioridad"], 9), -t["senales"], -t["confianza_pct"]))


def analizar_posicion_competitiva(contexto):
    fuentes = contexto.get("fuentes") or []
    metricas = contexto.get("metricas") or {}
    escenarios = contexto.get("escenarios") or []
    margen = metricas.get("margen_aprox") or 0
    ocupacion = metricas.get("ocupacion_aprox_pct") or 0
    quejas = metricas.get("quejas_abiertas") or 0

    fuentes_comp = []
    for f in fuentes:
        texto = " ".join([
            str(f.get("tipo") or ""),
            str(f.get("titulo") or ""),
            str(f.get("resumen") or ""),
            str(f.get("lectura") or ""),
            str(f.get("autor") or ""),
            str(f.get("url") or ""),
        ]).lower()
        if f.get("tipo") in {"competencia", "agencia_viajes", "tour_operador"} or any(k in texto for k in ("booking", "tripadvisor", "competencia", "agencia", "tour operador", "turoperador", "ota")):
            item = dict(f)
            item["texto_competitivo"] = texto
            fuentes_comp.append(item)

    palancas = []
    for nombre, palabras in PALANCAS_COMPETITIVAS.items():
        matches = []
        for f in fuentes_comp:
            hits = [p for p in palabras if p in f["texto_competitivo"]]
            if hits:
                item = dict(f)
                item["hits"] = hits[:5]
                matches.append(item)
        if matches:
            riesgo = "alto" if nombre == "precio" and margen <= 0 else "medio"
            if nombre == "experiencia" and quejas:
                riesgo = "alto"
            palancas.append({
                "nombre": nombre,
                "titulo": nombre.replace("_", " ").title(),
                "senales": len(matches),
                "riesgo": riesgo,
                "fuentes": matches[:5],
                "lectura": _lectura_competitiva(nombre, margen, ocupacion, quejas),
                "accion": _accion_competitiva(nombre),
            })

    ventajas = []
    riesgos = []
    if margen <= 0:
        riesgos.append("No entrar en guerra de precio: el margen aproximado no lo soporta.")
    if ocupacion < 55:
        riesgos.append("Demanda floja: diferenciar producto/canal antes de descontar sin control.")
    if ocupacion > 88:
        ventajas.append("Ocupacion alta: hay margen para proteger precio y filtrar demanda de baja rentabilidad.")
    if quejas:
        riesgos.append("Las quejas abiertas debilitan posicionamiento aunque el precio sea competitivo.")
    escenarios_viables = [
        e for e in escenarios
        if (e.get("resultado") or {}).get("veredicto") in {"Viable", "Viable con control"}
    ]
    if escenarios_viables:
        ventajas.append("Hay escenarios viables para probar ventaja competitiva sin apostar a ciegas.")
    if not fuentes_comp:
        riesgos.append("Faltan fuentes de competencia/intermediarios: el mapa competitivo aun esta poco alimentado.")

    recomendacion = "Diferenciar antes de descontar. Usar precio solo con cupo, limite y medicion."
    if palancas and palancas[0]["nombre"] == "experiencia":
        recomendacion = "Competir por experiencia y fidelizacion, no solo por canal o precio."
    elif any(p["nombre"] == "canal" for p in palancas):
        recomendacion = "Comparar venta directa, OTA/agencia y tour operador con margen neto real."

    return {
        "fuentes_competitivas": fuentes_comp[:12],
        "palancas": sorted(palancas, key=lambda p: (-p["senales"], p["riesgo"] != "alto")),
        "ventajas": ventajas,
        "riesgos": riesgos,
        "recomendacion": recomendacion,
        "metricas": {
            "margen": margen,
            "ocupacion_pct": ocupacion,
            "quejas": quejas,
            "fuentes_competitivas": len(fuentes_comp),
        },
    }


def _lectura_competitiva(nombre, margen, ocupacion, quejas):
    if nombre == "precio":
        return "La competencia por precio puede traer volumen, pero tambien baja margen y sube tension operativa."
    if nombre == "experiencia":
        return "La experiencia es una palanca defensiva: fideliza y reduce dependencia de descuentos si se ejecuta bien."
    if nombre == "producto":
        return "Producto y paquetes permiten diferenciarse sin regalar precio, pero deben tener coste y capacidad medidos."
    if nombre == "canal":
        return "El canal puede traer demanda, pero la comision y condiciones pueden convertir ocupacion en poca caja."
    if nombre == "marca":
        return "La marca ayuda a sostener precio cuando hay autenticidad, repeticion y una promesa clara."
    if nombre == "operacion":
        return "La operacion sostiene la promesa competitiva: si falla personal/mantenimiento, la ventaja se evapora."
    return "Senal competitiva pendiente de contraste."


def _accion_competitiva(nombre):
    return {
        "precio": "Simular precio neto y cupos antes de igualar descuentos de terceros.",
        "experiencia": "Cruzar reseñas/quejas con acciones de mejora y medir repeticion o gasto medio.",
        "producto": "Crear piloto de paquete/producto con coste, margen y satisfaccion esperada.",
        "canal": "Comparar venta directa, OTA/agencia y tour operador por margen neto y riesgo.",
        "marca": "Definir una propuesta concreta que el cliente pueda reconocer y recomendar.",
        "operacion": "Corregir cuello operativo antes de prometer mas demanda o mejor servicio.",
    }.get(nombre, "Contrastar con datos internos y simular antes de decidir.")


def _lectura_tendencia(tema, senales):
    base = {
        "viajeros": "Hay senales sobre demanda, perfiles de viajero o destinos. Puede afectar producto, calendario, precios y personal.",
        "precio_revenue": "Hay senales sobre precio/canal. Puede afectar margen, comision, ocupacion y dependencia de intermediarios.",
        "gastronomia": "Hay senales gastronomicas. Conviene compararlas con recetas, coste, alergenos y capacidad de produccion.",
        "personal": "Hay senales sobre empleo/equipo. El impacto real puede estar en rotacion, absentismo, clima y calidad de servicio.",
        "sostenibilidad": "Hay senales de sostenibilidad. Puede ser propuesta de valor, ahorro operativo o requisito de mercado.",
        "tecnologia": "Hay senales tecnologicas. Deben traducirse a ahorro, mejor decision, menos carga o mejor experiencia.",
        "agencias_ttoo": "Hay senales de agencias o tour operadores. Puede abrir demanda, pero tambien comision y dependencia.",
        "experiencia": "Hay senales sobre experiencia cliente. Pueden ayudar a fidelizar si no rompen margen ni operacion.",
    }.get(tema, "Hay senales externas que merecen contraste.")
    return f"{base} Senales detectadas: {senales}."


def _accion_tendencia(tema):
    return {
        "viajeros": "Crear escenario de temporada con perfil de cliente, ocupacion esperada y propuesta de valor.",
        "precio_revenue": "Simular precio neto, comision, cupos y efecto en satisfaccion antes de mover tarifas.",
        "gastronomia": "Comparar con recetas existentes y lanzar prueba limitada en menu/carta.",
        "personal": "Cruzar con fichajes, ausencias, rotacion y costes OPS antes de cambiar plantilla.",
        "sostenibilidad": "Separar ahorro operativo, marketing y cumplimiento; calcular payback.",
        "tecnologia": "Probar una mejora pequena con metrica clara de tiempo, error o margen.",
        "agencias_ttoo": "Comparar venta directa, agencia y tour operador con limite de cupo y salida.",
        "experiencia": "Cruzar con quejas, reseñas, repeticion y gasto medio; probar en segmento concreto.",
    }.get(tema, "Contrastar con datos internos y crear un escenario antes de decidir.")


def responder_chat_estrategico(pregunta, contexto):
    """Genera una primera respuesta estrategica trazable.

    No llama a ningun proveedor externo. Prepara una respuesta prudente usando
    fuentes, memoria y escenarios que la ERP le entregue.
    """
    pregunta = (pregunta or "").strip()
    fuentes = contexto.get("fuentes") or []
    memoria = contexto.get("memoria") or []
    escenarios = contexto.get("escenarios") or []
    metricas = contexto.get("metricas") or {}
    aprendizaje_guardado = contexto.get("aprendizaje_guardado") or None
    texto = pregunta.lower()
    modelo = _detectar_modelo_negocio(texto, contexto)
    contraste = contrastar_interno_externo(pregunta, fuentes, memoria, escenarios)
    analisis_datos = analizar_datos_para_chat(metricas, escenarios, contraste)

    foco = "direccion"
    if _coincide(texto, "personal", "rrhh", "equipo", "rotacion", "fichaje", "ops"):
        foco = "rrhh"
    elif _coincide(texto, "menu", "carta", "receta", "cocina", "comida", "buffet"):
        foco = "cocina"
    elif _coincide(texto, "precio", "oferta", "tour", "operador", "revenue", "ocupacion"):
        foco = "revenue"
    elif _coincide(texto, "inversor", "invertir", "inversion", "comprar", "capex", "payback", "rentabilidad"):
        foco = "finanzas"
    elif _coincide(texto, "mantenimiento", "averia", "infraestructura", "reforma"):
        foco = "mantenimiento"
    elif _coincide(texto, "cliente", "satisfaccion", "queja", "experiencia", "familia"):
        foco = "experiencia_cliente"
    elif _coincide(texto, "mercado", "tendencia", "viajeros", "linkedin", "revista", "periodico"):
        foco = "mercado"
    plan_contraste = generar_plan_contraste(pregunta, foco, modelo, contraste)

    fuentes_foco = [
        f for f in fuentes
        if foco in {str(f.get("area", "")).lower(), "direccion"} or str(f.get("ambito", "")).lower() == "multi_hosteleria"
    ][:5]
    memoria_foco = [
        m for m in memoria
        if foco in str(m.get("modulo", "")).lower() or foco in str(m.get("tipo", "")).lower()
    ][:4]
    escenarios_ordenados = sorted(
        escenarios,
        key=lambda e: (
            (e.get("resultado") or {}).get("riesgo_pct", 100),
            -((e.get("resultado") or {}).get("margen", 0)),
        ),
    )[:3]

    lineas = []
    lineas.append("Decision")
    lineas.append(analisis_datos["lectura"])
    lineas.append("Accion recomendada")
    lineas.append(analisis_datos["accion_recomendada"])
    lineas.append("Datos que estoy usando")
    lineas.append("; ".join(analisis_datos["datos_clave"]))
    if analisis_datos["alertas"]:
        lineas.append("Alertas")
        lineas.append("; ".join(analisis_datos["alertas"]))
    lineas.append("Modelo")
    lineas.append(f"{modelo['nombre']}: {modelo['datos_clave']}.")

    if metricas:
        margen = metricas.get("margen_aprox")
        ocupacion = metricas.get("ocupacion_aprox_pct")
        quejas = metricas.get("quejas_abiertas")
        lineas.append("Detalle interno")
        lineas.append(
            f"Margen aprox. {_format_euros(margen) if margen is not None else '-'}; "
            f"ocupacion/uso {ocupacion if ocupacion is not None else '-'}%; "
            f"quejas abiertas {quejas if quejas is not None else '-'}."
        )

    evidencias = []
    if fuentes_foco:
        titulos = ", ".join((f.get("titulo") or "fuente")[:70] for f in fuentes_foco[:3])
        evidencias.append(f"{len(fuentes_foco)} fuentes sectoriales")

    if memoria_foco:
        claves = ", ".join((m.get("clave") or "memoria")[:60] for m in memoria_foco[:3])
        evidencias.append(f"{len(memoria_foco)} memorias internas")
    if escenarios_ordenados:
        evidencias.append(f"{len(escenarios_ordenados)} escenarios comparables")
    if aprendizaje_guardado:
        evidencias.append(f"aprendizaje nuevo guardado como {aprendizaje_guardado.get('tipo')}")

    lineas.append("Contraste")
    lineas.append(contraste["lectura"])
    if contraste["fuentes"]:
        titulos_ext = ", ".join((f.get("titulo") or "fuente externa")[:65] for f in contraste["fuentes"][:3])
        lineas.append(f"Exterior relacionado: {titulos_ext}.")
    else:
        lineas.append("Exterior relacionado: no tengo fuente externa guardada para esta consulta.")
    if contraste["memoria"]:
        claves_int = ", ".join((m.get("clave") or "memoria")[:65] for m in contraste["memoria"][:3])
        lineas.append(f"Interno relacionado: {claves_int}.")
    if contraste["contradicciones"]:
        lineas.append("Alertas de contraste: " + " ".join(contraste["contradicciones"]))

    lineas.append("Siguiente dato que falta")
    if contraste["nivel"] in {"solo_interno", "sin_evidencia"}:
        lineas.append("Buscar una fuente externa comparable y guardarla antes de subir confianza.")
    elif contraste["nivel"] == "solo_exterior":
        lineas.append("Crear o actualizar un escenario con datos internos para medir impacto real.")
    else:
        lineas.append("Convertir el cruce en piloto con responsable, limite de perdida y fecha de revision.")

    acciones = []
    if foco == "rrhh":
        acciones = [
            "Cruzar fichajes, ausencias, retrasos y satisfaccion antes de tocar horarios.",
            "Simular coste laboral con y sin cobertura OPS.",
            "Separar problema de salario, organizacion, liderazgo y herramienta.",
        ]
    elif foco == "cocina":
        acciones = [
            "Comparar tendencia con recetas existentes, coste, alergenos y produccion real.",
            "Proponer prueba pequena antes de cambiar menu completo.",
            "Medir sobrantes, aceptacion del cliente y carga de cocina.",
        ]
    elif foco == "revenue":
        acciones = [
            "Simular precio neto despues de comision antes de aceptar cupos.",
            "Comparar venta directa, tour operador y paquete de valor.",
            "No bajar precio sin comprobar impacto en personal, cocina y experiencia.",
        ]
    elif foco == "mantenimiento":
        acciones = [
            "Separar inversion urgente, preventiva y mejora comercial.",
            "Calcular retorno a 1, 3 y 5 anos.",
            "Cruzar quejas, averias, energia y capacidad de vender mejor producto.",
        ]
    elif foco == "finanzas":
        acciones = [
            "Separar supuestos de mercado, datos reales y sensibilidad antes de comprar o invertir.",
            "Comparar varias categorias del activo: precio, capex, ADR, ocupacion, equipo y payback.",
            "Definir condiciones de salida si el rodaje real no confirma la hipotesis.",
        ]
    elif foco == "experiencia_cliente":
        acciones = [
            "Cruzar quejas con departamentos, turnos, ocupacion y producto vendido.",
            "Priorizar mejoras que aumenten satisfaccion sin romper margen.",
            "Medir si la solucion fideliza cliente y equipo.",
        ]
    else:
        acciones = [
            "Registrar la hipotesis como fuente o memoria antes de convertirla en decision.",
            "Crear un escenario en el simulador con fuente de datos marcada.",
            "Comparar resultado esperado contra datos reales cuando exista historico.",
        ]

    lineas.append("Trabajo operativo")
    lineas.append(acciones[0])
    if escenarios_ordenados:
        resumen = []
        for esc in escenarios_ordenados:
            res = esc.get("resultado") or {}
            resumen.append(
                f"{esc.get('nombre', 'escenario')} ({res.get('veredicto', '-')}, riesgo {res.get('riesgo_pct', '-')}%, margen {res.get('margen', '-')}, equilibrio uso {res.get('punto_equilibrio_ocupacion_pct', '-') }%)"
            )
        lineas.append("Escenarios comparables")
        lineas.append("; ".join(resumen) + ".")

    lineas.append("Limite")
    lineas.append("Si falta precio, coste, demanda, fuente externa o resultado real, no cierro la decision.")

    confianza = min(85, 35 + len(fuentes_foco) * 8 + len(memoria_foco) * 6 + len(escenarios_ordenados) * 5)
    if contraste["nivel"] == "solo_interno":
        confianza = min(confianza, 55)
    elif contraste["nivel"] == "solo_exterior":
        confianza = min(confianza, 50)
    elif contraste["nivel"] == "sin_evidencia":
        confianza = min(confianza, 35)
    elif contraste["nivel"] == "contrastado_parcial":
        confianza = min(88, confianza + 8)

    return {
        "foco": foco,
        "modelo_negocio": modelo,
        "aprendizaje_guardado": aprendizaje_guardado,
        "contraste": contraste,
        "plan_contraste": plan_contraste,
        "analisis_datos": analisis_datos,
        "respuesta": "\n\n".join(lineas),
        "acciones": acciones,
        "fuentes_usadas": fuentes_foco,
        "memoria_usada": memoria_foco,
        "escenarios_usados": escenarios_ordenados,
        "confianza_pct": confianza,
    }


def generar_radar_estrategico(contexto):
    """Convierte fuentes, memoria, escenarios y metricas en un radar accionable."""
    fuentes = contexto.get("fuentes") or []
    memoria = contexto.get("memoria") or []
    escenarios = contexto.get("escenarios") or []
    metricas = contexto.get("metricas") or {}

    areas = {area: [] for area, _ in AREAS_CONOCIMIENTO}
    for fuente in fuentes:
        area = str(fuente.get("area") or "mercado").lower()
        if area not in areas:
            area = "mercado"
        areas[area].append(fuente)

    margen = metricas.get("margen_aprox") or 0
    ocupacion = metricas.get("ocupacion_aprox_pct") or 0
    quejas = metricas.get("quejas_abiertas") or 0
    coste_controlado = metricas.get("costes_controlados_aprox") or 0
    ingresos = metricas.get("ingresos_totales_aprox") or 0
    coste_pct = (coste_controlado / ingresos * 100) if ingresos else 0

    oportunidades = []

    def _cruce_datos(area, fuente_items, datos, titulo):
        fuente_items = fuente_items or []
        datos = datos or {}
        interno = []
        if "margen" in datos:
            interno.append(f"margen {datos.get('margen')}")
        if "coste_pct" in datos:
            interno.append(f"coste/ingresos {datos.get('coste_pct')}%")
        if "ocupacion_pct" in datos:
            interno.append(f"ocupacion/uso {datos.get('ocupacion_pct')}%")
        if "quejas_abiertas" in datos:
            interno.append(f"quejas abiertas {datos.get('quejas_abiertas')}")
        if "escenario" in datos:
            interno.append(f"escenario {datos.get('escenario')}")
        if not interno:
            interno.append("faltan datos internos especificos para medir impacto")

        exterior = []
        if fuente_items:
            exterior = [(f.get("titulo") or f.get("url") or "fuente externa")[:90] for f in fuente_items[:3]]
        else:
            exterior.append("sin fuente externa relacionada todavia")

        area_norm = (area or "").lower()
        if "revenue" in area_norm:
            escenario = "Simular canal/oferta con precio neto, comision, cupo, ocupacion, coste variable y satisfaccion."
            idea = "Crear una oferta o canal con cupo limitado solo si mejora margen neto y no aumenta quejas."
        elif "finanzas" in area_norm:
            escenario = "Simular inversion con capex, ADR, ocupacion por temporada, coste laboral, deuda y payback."
            idea = "Proteger caja: invertir solo si el payback y el margen sobreviven a un escenario conservador."
        elif "rrhh" in area_norm or "ops" in area_norm:
            escenario = "Simular turnos con ausencias, retrasos, coste laboral, cobertura OPS y satisfaccion."
            idea = "Reducir rotacion y fallos de servicio antes de buscar mas volumen."
        elif "cocina" in area_norm or "compras" in area_norm:
            escenario = "Simular receta/menu con coste por racion, merma, ventas esperadas, alergenos y carga de produccion."
            idea = "Probar producto nuevo solo si mejora margen y simplifica o no rompe cocina."
        elif "experiencia" in area_norm:
            escenario = "Simular mejora de experiencia con coste, impacto en quejas, repeticion, gasto medio y satisfaccion."
            idea = "Atacar quejas que puedan convertirse en fidelizacion, no solo apagar incidencias."
        else:
            escenario = "Crear escenario minimo con ingresos, costes imputables, riesgo operativo y satisfaccion."
            idea = f"Convertir '{titulo}' en una prueba pequeña, medible y reversible."

        if fuente_items and datos:
            nivel = "cruce_parcial"
        elif fuente_items:
            nivel = "solo_exterior"
        elif datos:
            nivel = "solo_interno"
        else:
            nivel = "sin_base"
        return {
            "nivel": nivel,
            "idea": idea,
            "dato_interno": "; ".join(interno),
            "senal_externa": "; ".join(exterior),
            "escenario_sugerido": escenario,
            "riesgo_inventar": "bajo" if nivel == "cruce_parcial" else ("medio" if nivel in {"solo_interno", "solo_exterior"} else "alto"),
        }

    def _plan_piloto(area, titulo, prioridad):
        area_norm = (area or "").lower()
        if "revenue" in area_norm:
            metrica = "margen neto por reserva, comision media, ocupacion y satisfaccion"
            limite = "parar si el margen cae por debajo del minimo o suben quejas/carga operativa"
            prueba = "cupo pequeno por canal o paquete durante 7-14 dias"
        elif "rrhh" in area_norm or "ops" in area_norm:
            metrica = "ausencias, retrasos, cobertura de turnos, rotacion y satisfaccion del equipo"
            limite = "parar si sube la tension del servicio o el coste laboral no mejora"
            prueba = "piloto en un departamento o turno antes de extenderlo"
        elif "cocina" in area_norm or "compras" in area_norm:
            metrica = "coste por racion, sobrantes, ventas/aceptacion, alergenos y carga de produccion"
            limite = "parar si aumenta merma, complica APPCC o baja satisfaccion"
            prueba = "test de carta/menu en una semana o punto de venta"
        elif "experiencia" in area_norm:
            metrica = "quejas, menciones positivas, repeticion, gasto medio y tiempo de resolucion"
            limite = "parar si la mejora no reduce quejas o exige coste sin retorno"
            prueba = "piloto con segmento concreto de cliente o zona del establecimiento"
        elif "finanzas" in area_norm:
            metrica = "caja, margen, payback, coste fijo y coste variable"
            limite = "parar si no hay retorno claro o se tensiona tesoreria"
            prueba = "simulacion antes de gasto real y piloto con presupuesto cerrado"
        else:
            metrica = "margen, satisfaccion, coste operativo y aprendizaje obtenido"
            limite = "parar si no hay evidencia suficiente o se mezclan datos simulados con reales"
            prueba = "hipotesis pequena, medible y reversible"
        return {
            "hipotesis": f"Si probamos '{titulo}', deberia mejorar {area} sin deteriorar margen ni experiencia.",
            "prueba": prueba,
            "metrica_exito": metrica,
            "limite_parada": limite,
            "duracion": "7-14 dias" if prioridad == "alta" else "14-30 dias",
            "responsable": "Direccion / jefe de area",
        }

    def add(area, prioridad, titulo, lectura, accion, fuente_items=None, datos=None):
        fuente_items = fuente_items or []
        datos = datos or {}
        cruce = _cruce_datos(area, fuente_items, datos, titulo)
        confianza = min(90, 35 + len(fuente_items) * 8 + len(memoria) * 2 + (10 if escenarios else 0))
        if cruce["nivel"] in {"solo_interno", "solo_exterior"}:
            confianza = min(confianza, 58)
        elif cruce["nivel"] == "sin_base":
            confianza = min(confianza, 35)
        oportunidades.append({
            "area": area,
            "prioridad": prioridad,
            "titulo": titulo,
            "lectura": lectura,
            "accion": accion,
            "confianza_pct": confianza,
            "fuentes": fuente_items[:4],
            "datos": datos,
            "cruce_datos": cruce,
            "plan_piloto": _plan_piloto(area, titulo, prioridad),
        })

    mercado_fuentes = areas.get("mercado", []) + areas.get("marketing", []) + areas.get("comercial", [])
    if mercado_fuentes:
        add(
            "Mercado",
            "alta" if len(mercado_fuentes) >= 3 else "media",
            "Tendencias externas listas para convertirse en hipotesis",
            "Hay fuentes recientes de mercado/marketing/comercial. No son verdad operativa, pero pueden abrir producto, oferta o posicionamiento.",
            "Elegir una tendencia, crear escenario con fuente marcada como externa y comparar contra margen, personal y experiencia.",
            mercado_fuentes,
            {"fuentes": len(mercado_fuentes)},
        )

    revenue_fuentes = areas.get("revenue", []) + [f for f in fuentes if str(f.get("tipo")) in {"tour_operador", "agencia_viajes"}]
    if revenue_fuentes or ocupacion < 55 or ocupacion > 88:
        prioridad = "alta" if ocupacion < 45 or ocupacion > 92 else "media"
        add(
            "Revenue",
            prioridad,
            "Precio, canal y ocupacion necesitan simulacion antes de decidir",
            f"Ocupacion aproximada {ocupacion}%. El riesgo es llenar barato, depender de comision o quedarse corto de demanda.",
            "Comparar venta directa, tour operador y paquete de valor en el simulador antes de firmar cupos o lanzar descuento.",
            revenue_fuentes,
            {"ocupacion_pct": ocupacion},
        )

    if margen <= 0 or coste_pct > 75:
        add(
            "Finanzas",
            "alta",
            "La rentabilidad necesita proteccion antes de crecer",
            f"Margen aproximado {margen}; coste controlado sobre ingresos {round(coste_pct, 1)}%. Crecer sin control puede empeorar caja.",
            "Bloquear decisiones con payback, coste laboral, compras y comisiones antes de ejecutar ofertas o inversiones.",
            areas.get("finanzas", []),
            {"margen": margen, "coste_pct": round(coste_pct, 1)},
        )

    rrhh_fuentes = areas.get("rrhh", []) + areas.get("operaciones", [])
    if rrhh_fuentes:
        add(
            "RRHH / OPS",
            "media",
            "Fidelizar equipo puede ser una ventaja competitiva",
            "Hay senales relacionadas con personal u operacion. La IA debe mirar rotacion, fichajes, ausencias y carga real, no solo coste.",
            "Crear escenario con incidencias OPS: no presentados, retrasos, cambios de equipo y cobertura cooperativa.",
            rrhh_fuentes,
            {"fuentes_rrhh_ops": len(rrhh_fuentes)},
        )

    cocina_fuentes = areas.get("cocina", []) + areas.get("compras", [])
    if cocina_fuentes:
        add(
            "Cocina / Compras",
            "media",
            "Producto y carta pueden mejorar sin romper margen",
            "Hay fuentes de cocina/compras. La oportunidad buena es adaptar tendencias a recetas existentes, coste y alergenos.",
            "Comparar fuente contra recetas validadas, coste de proveedor, sobrantes y carga de produccion antes de cambiar menu.",
            cocina_fuentes,
            {"fuentes_cocina": len(cocina_fuentes)},
        )

    if quejas > 0:
        add(
            "Experiencia cliente",
            "alta" if quejas >= 5 else "media",
            "Las quejas son senales de dinero futuro",
            f"Hay {quejas} quejas abiertas. No son solo incidencias: pueden explicar perdida de fidelidad o coste oculto.",
            "Cruzar quejas con departamento, turno, ocupacion, producto vendido y decisiones recientes.",
            areas.get("experiencia_cliente", []),
            {"quejas_abiertas": quejas},
        )

    escenarios_viables = [
        e for e in escenarios
        if (e.get("resultado") or {}).get("veredicto") in {"Viable", "Viable con control"}
    ]
    if escenarios_viables:
        mejor = sorted(
            escenarios_viables,
            key=lambda e: -((e.get("resultado") or {}).get("margen", 0)),
        )[0]
        add(
            "Escenarios",
            "media",
            "Ya hay escenarios que merecen prueba piloto",
            f"El escenario '{mejor.get('nombre')}' aparece como {(mejor.get('resultado') or {}).get('veredicto')}.",
            "Convertirlo en piloto con responsable, fecha, metrica de exito y limite de perdida.",
            [],
            {
                "escenario": mejor.get("nombre"),
                "margen": (mejor.get("resultado") or {}).get("margen"),
                "riesgo_pct": (mejor.get("resultado") or {}).get("riesgo_pct"),
            },
        )

    if not oportunidades:
        add(
            "Direccion",
            "info",
            "Faltan senales para un radar potente",
            "Todavia hay pocas fuentes, escenarios o datos internos para detectar oportunidades fuertes.",
            "Meter fuentes por enlace, crear escenarios demo y guardar memoria operativa de direccion.",
            [],
            {"fuentes": len(fuentes), "escenarios": len(escenarios), "memoria": len(memoria)},
        )

    orden = {"alta": 0, "media": 1, "info": 2, "baja": 3}
    oportunidades = sorted(oportunidades, key=lambda item: (orden.get(item["prioridad"], 9), -item["confianza_pct"]))
    return {
        "oportunidades": oportunidades,
        "resumen": {
            "fuentes": len(fuentes),
            "memoria": len(memoria),
            "escenarios": len(escenarios),
            "margen": margen,
            "ocupacion_pct": ocupacion,
            "quejas": quejas,
        },
    }


def generar_brief_ejecutivo(contexto):
    metricas = contexto.get("metricas") or {}
    radar = contexto.get("radar") or {}
    tendencias = contexto.get("tendencias") or []
    escenarios = contexto.get("escenarios") or []
    fuentes = contexto.get("fuentes") or []

    oportunidades = radar.get("oportunidades") or []
    salud = contexto.get("salud")
    margen = metricas.get("margen_aprox") or 0
    ocupacion = metricas.get("ocupacion_aprox_pct") or 0
    quejas = metricas.get("quejas_abiertas") or 0

    prioridades = [op for op in oportunidades if op.get("prioridad") in {"alta", "critica"}]
    top_oportunidad = prioridades[0] if prioridades else (oportunidades[0] if oportunidades else None)
    top_tendencia = tendencias[0] if tendencias else None
    escenarios_viables = [
        e for e in escenarios
        if (e.get("resultado") or {}).get("veredicto") in {"Viable", "Viable con control"}
    ]
    mejor_escenario = sorted(
        escenarios_viables,
        key=lambda e: -((e.get("resultado") or {}).get("margen", 0)),
    )[0] if escenarios_viables else None

    alertas = []
    if margen <= 0:
        alertas.append("Margen aproximado no positivo: proteger caja antes de crecer.")
    if ocupacion > 90:
        alertas.append("Ocupacion alta: vigilar tension de servicio, personal y experiencia.")
    if ocupacion < 45:
        alertas.append("Ocupacion baja: revisar demanda, canales y propuesta de valor.")
    if quejas:
        alertas.append(f"Hay {quejas} quejas abiertas: convertirlas en aprendizaje y accion medible.")
    if not fuentes:
        alertas.append("Faltan fuentes externas: el radar aun mira poco mercado.")
    if not escenarios:
        alertas.append("Faltan escenarios: las decisiones todavia no tienen laboratorio economico.")

    decisiones_hoy = []
    if top_oportunidad:
        decisiones_hoy.append({
            "titulo": top_oportunidad["titulo"],
            "accion": top_oportunidad["accion"],
            "origen": "Radar estrategico",
            "prioridad": top_oportunidad["prioridad"],
        })
    if top_tendencia:
        decisiones_hoy.append({
            "titulo": f"Contrastar tendencia: {top_tendencia['titulo']}",
            "accion": top_tendencia["accion"],
            "origen": "Tendencias",
            "prioridad": top_tendencia["prioridad"],
        })
    if mejor_escenario:
        res = mejor_escenario.get("resultado") or {}
        decisiones_hoy.append({
            "titulo": f"Valorar piloto: {mejor_escenario.get('nombre')}",
            "accion": f"{res.get('veredicto')} con margen {res.get('margen')} y riesgo {res.get('riesgo_pct')}%.",
            "origen": "Simulador",
            "prioridad": "media",
        })

    lectura = "Estable"
    if prioridades or margen <= 0 or quejas >= 5:
        lectura = "Atencion alta"
    elif oportunidades or tendencias:
        lectura = "Movimiento recomendado"

    return {
        "lectura": lectura,
        "salud": salud,
        "metricas": {
            "margen": margen,
            "ocupacion_pct": ocupacion,
            "quejas": quejas,
            "fuentes": len(fuentes),
            "tendencias": len(tendencias),
            "escenarios": len(escenarios),
        },
        "alertas": alertas[:6],
        "decisiones_hoy": decisiones_hoy[:5],
        "top_oportunidad": top_oportunidad,
        "top_tendencia": top_tendencia,
        "mejor_escenario": mejor_escenario,
    }
