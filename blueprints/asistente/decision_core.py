"""Nucleo reutilizable para simulacion y decision estrategica.

Este modulo no depende de Flask ni de la base de datos. La ERP lo usa desde
sus rutas, pero tambien puede moverse a una API o app independiente.
"""

MODELOS_NEGOCIO_HOSTELERIA = [
    ("hotel", "Hotel"),
    ("aparthotel", "Aparthotel"),
    ("restaurante", "Restaurante"),
    ("cadena_restaurantes", "Cadena de restaurantes"),
    ("kioscos", "Cadena de kioscos"),
    ("pub", "Pub / bar nocturno"),
    ("discoteca", "Discoteca"),
    ("takeaway", "Comida para llevar"),
    ("catering", "Catering / eventos"),
]

FUENTES_DATOS_ESCENARIO = [
    ("simulado", "Simulado"),
    ("manual", "Manual"),
    ("mixto", "Mixto real + simulado"),
    ("real", "Real operativo"),
    ("externo", "Externo / mercado"),
]

FASES_NEGOCIO_ESCENARIO = [
    ("idea_inversion", "Idea / inversion sin datos reales"),
    ("preapertura", "Preapertura"),
    ("rodaje", "Rodaje inicial"),
    ("operativo", "Operativo con datos reales"),
    ("reposicionamiento", "Reposicionamiento"),
]

CATEGORIAS_ESTABLECIMIENTO = [
    ("sin_categoria", "Sin categoria"),
    ("hostal", "Hostal / pension"),
    ("3_estrellas", "3 estrellas"),
    ("4_estrellas", "4 estrellas"),
    ("4_plus", "4 estrellas plus"),
    ("5_estrellas", "5 estrellas"),
    ("5_gl", "5 estrellas gran lujo"),
    ("boutique", "Boutique / singular"),
    ("rural_premium", "Rural premium"),
]

ZONAS_MERCADO = [
    ("sin_zona", "Sin zona definida"),
    ("pirineo", "Pirineo / montaña"),
    ("costa", "Costa"),
    ("urbano", "Urbano"),
    ("rural", "Rural interior"),
    ("islas", "Islas"),
    ("aeropuerto", "Aeropuerto / transito"),
]

MODELOS_HOTEL = {"hotel", "aparthotel"}
MODELOS_SERVICIO = {"restaurante", "cadena_restaurantes", "kioscos", "pub", "discoteca", "takeaway", "catering"}

_CATEGORIA_LECTURA = {
    "hostal": "precio sensible, coste ajustado, reputacion y limpieza pesan mucho",
    "3_estrellas": "volumen, precio competitivo, control de costes y distribucion",
    "4_estrellas": "equilibrio entre ADR, experiencia, equipo y mantenimiento preventivo",
    "4_plus": "diferenciacion clara, venta directa, servicio estable y producto cuidado",
    "5_estrellas": "ADR alto, personal cualificado, reputacion, spa/experiencias y coste laboral alto",
    "5_gl": "lujo real, excelencia de servicio, inversion fuerte y tolerancia minima al fallo",
    "boutique": "singularidad, marca, experiencia local, menor volumen y mayor personalizacion",
    "rural_premium": "entorno, escapadas, gastronomia, bienestar y estacionalidad marcada",
}

_ZONA_LECTURA = {
    "pirineo": "estacionalidad alta, nieve/naturaleza, familias, escapadas, clima, accesos y dependencia de fines de semana",
    "costa": "temporada fuerte, turoperacion, ocupacion alta en verano y presion de costes",
    "urbano": "demanda corporativa/ocio, eventos, competencia diaria y revenue dinamico",
    "rural": "escapadas, baja densidad, experiencia local y necesidad de diferenciacion",
    "islas": "conectividad aerea, turoperacion, temporada, coste logistico y empleo",
    "aeropuerto": "transito, tarifa dinamica, contratos, tripulaciones y rapidez operativa",
}


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


def sim_float(form, nombre, default=0.0):
    return _to_float(form.get(nombre), default)


def _format_euros(value, decimales=2):
    number = _to_float(value, 0.0)
    text = f"{number:,.{int(decimales)}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text}€"


def sim_int(form, nombre, default=0):
    try:
        return int(float(str(form.get(nombre, default)).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def datos_escenario_desde_form(form):
    fuente = (form.get("fuente_datos") or "simulado").strip().lower()
    if fuente not in {valor for valor, _ in FUENTES_DATOS_ESCENARIO}:
        fuente = "simulado"
    return {
        "nombre": (form.get("nombre") or "Escenario sin nombre").strip(),
        "tipo": form.get("tipo") or "hotel",
        "decision_tipo": form.get("decision_tipo") or "oferta",
        "fase_negocio": form.get("fase_negocio") or "idea_inversion",
        "zona_mercado": form.get("zona_mercado") or "sin_zona",
        "categoria_establecimiento": form.get("categoria_establecimiento") or "sin_categoria",
        "calidad_datos_pct": sim_float(form, "calidad_datos_pct", 35),
        "habitaciones": sim_int(form, "habitaciones", 100),
        "ocupacion_pct": sim_float(form, "ocupacion_pct", 70),
        "adr": sim_float(form, "adr", 95),
        "dias": sim_int(form, "dias", 30),
        "pax_medio": sim_float(form, "pax_medio", 2),
        "coste_variable_pax_dia": sim_float(form, "coste_variable_pax_dia", 18),
        "coste_fijo_mes": sim_float(form, "coste_fijo_mes", 35000),
        "coste_personal_mes": sim_float(form, "coste_personal_mes", 55000),
        "alcance_costes_pct": sim_float(form, "alcance_costes_pct", 100),
        "comision_pct": sim_float(form, "comision_pct", 12),
        "inversion": sim_float(form, "inversion", 0),
        "satisfaccion_objetivo": sim_float(form, "satisfaccion_objetivo", 85),
        "rotacion_riesgo_pct": sim_float(form, "rotacion_riesgo_pct", 15),
        "empleados_no_presentan": sim_int(form, "empleados_no_presentan", 0),
        "retrasos_pct": sim_float(form, "retrasos_pct", 0),
        "cambios_equipo": sim_int(form, "cambios_equipo", 0),
        "aceptacion_sistema_pct": sim_float(form, "aceptacion_sistema_pct", 90),
        "cobertura_ops_pct": sim_float(form, "cobertura_ops_pct", 70),
        "fuente_datos": fuente,
        "coste_personal_origen": form.get("coste_personal_origen") or "manual",
        "notas": (form.get("notas") or "").strip(),
    }


def procedencia_escenario_decision(datos):
    fuente = (datos.get("fuente_datos") or "simulado").strip().lower()
    fuentes_validas = {valor for valor, _ in FUENTES_DATOS_ESCENARIO}
    if fuente not in fuentes_validas:
        fuente = "simulado"

    origen_personal = (datos.get("coste_personal_origen") or "").strip().lower()
    if origen_personal in {"nomina_real", "nominas"}:
        coste_personal_tipo = "real"
        coste_personal_texto = "coste laboral desde nominas"
    elif origen_personal in {"prenomina_ops", "nomina_y_prenomina_ops", "presupuesto_centro"}:
        coste_personal_tipo = "estimado"
        coste_personal_texto = "coste laboral arrastrado de OPS/RRHH o presupuesto del centro"
    elif _to_float(datos.get("coste_personal_mes"), 0) > 0:
        coste_personal_tipo = "manual"
        coste_personal_texto = "coste laboral introducido manualmente"
    else:
        coste_personal_tipo = "simulado"
        coste_personal_texto = "coste laboral de referencia"

    pesos_base = {
        "real": {"real": 70, "estimado": 15, "manual": 10, "simulado": 5, "externo": 0},
        "mixto": {"real": 30, "estimado": 25, "manual": 25, "simulado": 15, "externo": 5},
        "manual": {"real": 0, "estimado": 15, "manual": 65, "simulado": 20, "externo": 0},
        "externo": {"real": 0, "estimado": 15, "manual": 15, "simulado": 20, "externo": 50},
        "simulado": {"real": 0, "estimado": 10, "manual": 15, "simulado": 75, "externo": 0},
    }
    pesos = dict(pesos_base.get(fuente, pesos_base["simulado"]))
    if coste_personal_tipo == "real":
        pesos["real"] += 10
        pesos["simulado"] = max(0, pesos["simulado"] - 10)
    elif coste_personal_tipo == "estimado":
        pesos["estimado"] += 10
        pesos["simulado"] = max(0, pesos["simulado"] - 10)

    total = sum(pesos.values()) or 1
    composicion = {k: round(v * 100 / total) for k, v in pesos.items()}
    confianza_base = {
        "real": 82,
        "mixto": 58,
        "manual": 48,
        "externo": 45,
        "simulado": 34,
    }.get(fuente, 34)
    if coste_personal_tipo == "real":
        confianza_base += 8
    elif coste_personal_tipo == "estimado":
        confianza_base += 4
    confianza = max(15, min(92, confianza_base))

    detalle = [
        {"campo": "fuente_principal", "tipo": fuente, "lectura": "modo declarado del escenario"},
        {"campo": "coste_laboral", "tipo": coste_personal_tipo, "lectura": coste_personal_texto},
        {"campo": "ocupacion_ingreso_comision", "tipo": fuente if fuente in {"real", "externo"} else "manual", "lectura": "parametros de demanda, precio y canal"},
        {"campo": "ops_rrhh", "tipo": "simulado" if fuente != "real" else "mixto", "lectura": "faltas, retrasos, cambios y cobertura cooperativa"},
    ]
    resumen = ", ".join(f"{valor}% {clave}" for clave, valor in composicion.items() if valor)
    return {
        "fuente": fuente,
        "confianza_pct": confianza,
        "composicion": composicion,
        "detalle": detalle,
        "resumen": resumen,
        "aviso": "Escenario de decision: usar como hipotesis hasta comparar contra resultado real.",
    }


def calcular_escenario_decision(datos):
    tipo = (datos.get("tipo") or "hotel").strip().lower()
    fase_negocio = (datos.get("fase_negocio") or "idea_inversion").strip().lower()
    zona_mercado = (datos.get("zona_mercado") or "sin_zona").strip().lower()
    categoria_establecimiento = (datos.get("categoria_establecimiento") or "sin_categoria").strip().lower()
    calidad_datos_pct = max(min(_to_float(datos.get("calidad_datos_pct"), 35), 100), 0)
    habitaciones = max(int(datos.get("habitaciones") or 0), 0)
    ocupacion_pct = max(min(_to_float(datos.get("ocupacion_pct"), 0), 100), 0)
    adr = max(_to_float(datos.get("adr"), 0), 0)
    dias = max(int(datos.get("dias") or 30), 1)
    pax_medio = max(_to_float(datos.get("pax_medio"), 2), 0.1)
    coste_variable = max(_to_float(datos.get("coste_variable_pax_dia"), 0), 0)
    coste_fijo_mes = max(_to_float(datos.get("coste_fijo_mes"), 0), 0)
    coste_personal_mes = max(_to_float(datos.get("coste_personal_mes"), 0), 0)
    alcance_costes_pct = max(min(_to_float(datos.get("alcance_costes_pct"), 100), 100), 0)
    comision_pct = max(min(_to_float(datos.get("comision_pct"), 0), 100), 0)
    inversion = max(_to_float(datos.get("inversion"), 0), 0)
    satisfaccion_objetivo = max(min(_to_float(datos.get("satisfaccion_objetivo"), 80), 100), 0)
    rotacion_riesgo_pct = max(min(_to_float(datos.get("rotacion_riesgo_pct"), 15), 100), 0)
    empleados_no_presentan = max(int(datos.get("empleados_no_presentan") or 0), 0)
    retrasos_pct = max(min(_to_float(datos.get("retrasos_pct"), 0), 100), 0)
    cambios_equipo = max(int(datos.get("cambios_equipo") or 0), 0)
    aceptacion_sistema_pct = max(min(_to_float(datos.get("aceptacion_sistema_pct"), 90), 100), 0)
    cobertura_ops_pct = max(min(_to_float(datos.get("cobertura_ops_pct"), 70), 100), 0)
    procedencia = procedencia_escenario_decision(datos)
    if fase_negocio in {"idea_inversion", "preapertura"}:
        procedencia["confianza_pct"] = min(procedencia["confianza_pct"], max(18, int(calidad_datos_pct)))
        procedencia["aviso"] = "Escenario de inversion/preapertura: son hipotesis hasta contrastar mercado, competencia y datos reales."

    unidades_activas_dia = habitaciones * (ocupacion_pct / 100)
    if tipo in MODELOS_SERVICIO:
        modo_calculo = "servicio"
        volumen = unidades_activas_dia * pax_medio * dias
        pax = volumen
        ingresos_brutos = volumen * adr
        explicacion_formula = (
            "Servicio: unidades base x uso % x multiplicador x dias x ticket medio. "
            "Ejemplo: mesas/pedidos/plazas activas convertidas en clientes o servicios."
        )
    else:
        modo_calculo = "alojamiento"
        volumen = unidades_activas_dia * dias
        pax = volumen * pax_medio
        ingresos_brutos = volumen * adr
        explicacion_formula = (
            "Alojamiento: habitaciones/unidades ocupadas x dias x ADR. "
            "El multiplicador convierte noches en pax para calcular coste variable."
        )
    estancias = volumen
    factor_mes = dias / 30
    factor_alcance = alcance_costes_pct / 100
    coste_laboral_dia = coste_personal_mes / 30 if coste_personal_mes else 0
    sobrecoste_no_presentados = empleados_no_presentan * coste_laboral_dia * 0.18 * (1 - cobertura_ops_pct / 100)
    sobrecoste_retrasos = coste_personal_mes * (retrasos_pct / 100) * 0.20 * factor_mes
    sobrecoste_cambios = cambios_equipo * coste_laboral_dia * 0.35
    coste_ops_extra = sobrecoste_no_presentados + sobrecoste_retrasos + sobrecoste_cambios
    servicio_penalizacion = (
        empleados_no_presentan * 4
        + retrasos_pct * 0.18
        + cambios_equipo * 1.5
        + max(0, 75 - aceptacion_sistema_pct) * 0.25
    )
    servicio_penalizacion *= max(0.35, 1 - cobertura_ops_pct / 140)
    satisfaccion_estimada = max(0, min(100, satisfaccion_objetivo - servicio_penalizacion))
    comisiones = ingresos_brutos * (comision_pct / 100)
    ingresos_netos = ingresos_brutos - comisiones
    costes_variables = pax * coste_variable
    costes_fijos_base = (coste_fijo_mes + coste_personal_mes) * factor_mes
    costes_fijos_imputados = costes_fijos_base * factor_alcance
    costes_fijos = costes_fijos_imputados + coste_ops_extra
    coste_total = costes_variables + costes_fijos
    margen = ingresos_netos - coste_total
    margen_pct = (margen / ingresos_netos * 100) if ingresos_netos else 0
    factor_comision = 1 - (comision_pct / 100)
    ingreso_neto_unitario = (adr * factor_comision) if factor_comision > 0 else 0
    coste_variable_unitario = coste_variable if modo_calculo == "servicio" else coste_variable * pax_medio
    contribucion_unitaria = ingreso_neto_unitario - coste_variable_unitario
    punto_equilibrio_volumen = (costes_fijos / contribucion_unitaria) if contribucion_unitaria > 0 else None
    capacidad_periodo = habitaciones * dias * (pax_medio if modo_calculo == "servicio" else 1)
    punto_equilibrio_ocupacion_pct = (
        punto_equilibrio_volumen / capacidad_periodo * 100
        if punto_equilibrio_volumen is not None and capacidad_periodo
        else None
    )
    precio_equilibrio = (
        (costes_variables + costes_fijos) / (volumen * factor_comision)
        if volumen > 0 and factor_comision > 0
        else None
    )
    precio_objetivo_18 = (
        (costes_variables + costes_fijos) / (0.82 * volumen * factor_comision)
        if volumen > 0 and factor_comision > 0
        else None
    )
    margen_seguridad_pct = (
        (volumen - punto_equilibrio_volumen) / volumen * 100
        if punto_equilibrio_volumen is not None and volumen
        else None
    )
    impacto_adr_5 = volumen * (adr * 0.05) * factor_comision
    impacto_ocupacion_5 = ingresos_netos * (5 / ocupacion_pct) - costes_variables * (5 / ocupacion_pct) if ocupacion_pct else 0
    impacto_variable_5 = costes_variables * 0.05
    impacto_fijos_5 = costes_fijos_imputados * 0.05
    palancas = [
        {"nombre": "Subir ingreso medio 5%", "impacto": impacto_adr_5},
        {"nombre": "Subir uso/ocupacion 5 puntos relativos", "impacto": impacto_ocupacion_5},
        {"nombre": "Bajar coste variable 5%", "impacto": impacto_variable_5},
        {"nombre": "Bajar coste fijo/laboral imputado 5%", "impacto": impacto_fijos_5},
    ]
    palancas.sort(key=lambda item: item["impacto"], reverse=True)
    retorno_anual = margen * (365 / dias)
    payback_anios = (inversion / retorno_anual) if inversion and retorno_anual > 0 else None

    riesgo = 0
    motivos = []
    if margen_pct < 8:
        riesgo += 35
        motivos.append("margen bajo")
    if comision_pct > 18:
        riesgo += 18
        motivos.append("comision alta")
    if ocupacion_pct > 90 and satisfaccion_objetivo < 80:
        riesgo += 16
        motivos.append("llena mucho pero puede tensionar experiencia")
    if rotacion_riesgo_pct > 25:
        riesgo += 18
        motivos.append("riesgo de rotacion del equipo")
    if empleados_no_presentan > 0:
        riesgo += min(25, empleados_no_presentan * 5)
        motivos.append("empleados no se presentan")
    if retrasos_pct > 8:
        riesgo += min(18, int(retrasos_pct))
        motivos.append("retrasos de fichaje")
    if cambios_equipo > 3:
        riesgo += min(16, cambios_equipo * 2)
        motivos.append("cambios de equipo")
    if aceptacion_sistema_pct < 70:
        riesgo += 18
        motivos.append("baja aceptacion del sistema OPS")
    if cobertura_ops_pct < 50 and (empleados_no_presentan or cambios_equipo):
        riesgo += 15
        motivos.append("la cooperativa OPS no cubre suficiente")
    if satisfaccion_estimada < 75:
        riesgo += 12
        motivos.append("la satisfaccion estimada cae por debajo del objetivo")
    if inversion and payback_anios and payback_anios > 5:
        riesgo += 18
        motivos.append("retorno lento")
    riesgo = min(riesgo, 100)

    if margen <= 0:
        veredicto = "No aconsejable"
        accion = "No ejecutar sin subir precio, bajar comision/coste o reducir alcance."
    elif margen_seguridad_pct is not None and margen_seguridad_pct < 10:
        veredicto = "Viable fragil"
        accion = "Sale positivo, pero con poco colchon. Probar con limite de gasto y revision diaria."
    elif riesgo >= 60:
        veredicto = "Revisar antes"
        accion = "Hacer prueba piloto y bloquear condiciones de salida antes de comprometer caja."
    elif margen_pct < 18:
        veredicto = "Viable con control"
        accion = "Puede probarse con cupo limitado, seguimiento de satisfaccion y coste diario."
    else:
        veredicto = "Viable"
        accion = "Puede avanzar a plan operativo, con responsables y medicion semanal."

    lectura_zona = _ZONA_LECTURA.get(zona_mercado, "zona no definida: falta demanda, competencia, accesos y estacionalidad")
    lectura_categoria = _CATEGORIA_LECTURA.get(categoria_establecimiento, "categoria no definida: falta posicionamiento y nivel de servicio")
    datos_a_contrastar = [
        "ADR real de competidores comparables por temporada",
        "ocupacion por mes y dias de la semana",
        "coste laboral por departamento y disponibilidad de personal",
        "capex inicial, mantenimiento diferido y energia",
        "canales de venta, comisiones y dependencia de intermediarios",
        "reseñas, propuesta de valor y repeticion de cliente",
    ]
    if zona_mercado == "pirineo":
        datos_a_contrastar.extend([
            "impacto de nieve, meteorologia y accesos",
            "demanda de verano de montaña frente a temporada de esqui",
            "peso de familias, escapadas wellness, grupos y turismo activo",
        ])
    decision_tiempo_real = [
        "comparar ingresos reales contra punto de equilibrio diario",
        "vigilar pickup/reservas, cancelaciones y precio neto por canal",
        "cruzar satisfaccion y quejas con ocupacion y coste de personal",
        "sustituir supuestos por datos reales cada semana en rodaje",
    ]

    return {
        "fase_negocio": fase_negocio,
        "zona_mercado": zona_mercado,
        "categoria_establecimiento": categoria_establecimiento,
        "calidad_datos_pct": round(calidad_datos_pct, 1),
        "lectura_zona": lectura_zona,
        "lectura_categoria": lectura_categoria,
        "datos_a_contrastar": datos_a_contrastar,
        "decision_tiempo_real": decision_tiempo_real,
        "modo_calculo": modo_calculo,
        "explicacion_formula": explicacion_formula,
        "alcance_costes_pct": round(alcance_costes_pct, 1),
        "unidades_activas_dia": round(unidades_activas_dia, 1),
        "habitaciones_ocupadas_dia": round(unidades_activas_dia, 1),
        "estancias": round(estancias, 1),
        "volumen_servicio": round(volumen, 1),
        "pax_estimados": round(pax, 1),
        "ingresos_brutos": round(ingresos_brutos, 2),
        "comisiones": round(comisiones, 2),
        "ingresos_netos": round(ingresos_netos, 2),
        "costes_variables": round(costes_variables, 2),
        "costes_fijos_base": round(costes_fijos_base, 2),
        "costes_fijos_imputados": round(costes_fijos_imputados, 2),
        "costes_fijos": round(costes_fijos, 2),
        "coste_ops_extra": round(coste_ops_extra, 2),
        "coste_total": round(coste_total, 2),
        "margen": round(margen, 2),
        "margen_pct": round(margen_pct, 1),
        "ingreso_neto_unitario": round(ingreso_neto_unitario, 2),
        "coste_variable_unitario": round(coste_variable_unitario, 2),
        "contribucion_unitaria": round(contribucion_unitaria, 2),
        "punto_equilibrio_volumen": round(punto_equilibrio_volumen, 1) if punto_equilibrio_volumen is not None else None,
        "punto_equilibrio_ocupacion_pct": round(punto_equilibrio_ocupacion_pct, 1) if punto_equilibrio_ocupacion_pct is not None else None,
        "precio_equilibrio": round(precio_equilibrio, 2) if precio_equilibrio is not None else None,
        "precio_objetivo_18": round(precio_objetivo_18, 2) if precio_objetivo_18 is not None else None,
        "margen_seguridad_pct": round(margen_seguridad_pct, 1) if margen_seguridad_pct is not None else None,
        "palancas": [{"nombre": p["nombre"], "impacto": round(p["impacto"], 2)} for p in palancas],
        "satisfaccion_estimada": round(satisfaccion_estimada, 1),
        "servicio_penalizacion": round(servicio_penalizacion, 1),
        "empleados_no_presentan": empleados_no_presentan,
        "retrasos_pct": round(retrasos_pct, 1),
        "cambios_equipo": cambios_equipo,
        "aceptacion_sistema_pct": round(aceptacion_sistema_pct, 1),
        "cobertura_ops_pct": round(cobertura_ops_pct, 1),
        "payback_anios": round(payback_anios, 1) if payback_anios else None,
        "riesgo_pct": riesgo,
        "motivos": motivos,
        "veredicto": veredicto,
        "accion": accion,
        "fuente_datos": procedencia["fuente"],
        "confianza_pct": procedencia["confianza_pct"],
        "procedencia": procedencia,
    }


def analizar_cartera_escenarios(escenarios):
    """Resume escenarios guardados en lectura de decision por modelo.

    Recibe una lista de dicts con los datos del escenario y su resultado_json ya
    cargado como ``resultado``. No depende de Flask ni de base de datos.
    """
    grupos = {}
    for esc in escenarios or []:
        resultado = esc.get("resultado") or {}
        tipo = esc.get("tipo") or resultado.get("tipo") or "sin_modelo"
        item = {
            "id": esc.get("id"),
            "nombre": esc.get("nombre") or "Escenario sin nombre",
            "tipo": tipo,
            "fase_negocio": esc.get("fase_negocio") or resultado.get("fase_negocio") or "idea_inversion",
            "zona_mercado": esc.get("zona_mercado") or resultado.get("zona_mercado") or "sin_zona",
            "categoria_establecimiento": esc.get("categoria_establecimiento") or resultado.get("categoria_establecimiento") or "sin_categoria",
            "margen": _to_float(resultado.get("margen"), 0),
            "margen_pct": _to_float(resultado.get("margen_pct"), 0),
            "riesgo_pct": _to_float(resultado.get("riesgo_pct"), 100),
            "satisfaccion": _to_float(resultado.get("satisfaccion_estimada"), 0),
            "equilibrio_uso": resultado.get("punto_equilibrio_ocupacion_pct"),
            "payback": resultado.get("payback_anios"),
            "veredicto": resultado.get("veredicto") or "-",
            "confianza_pct": _to_float(resultado.get("confianza_pct") or esc.get("confianza_pct"), 0),
            "calidad_datos_pct": _to_float(resultado.get("calidad_datos_pct") or esc.get("calidad_datos_pct"), 0),
        }
        item["score"] = (
            item["margen_pct"] * 1.8
            + max(0, item["satisfaccion"] - 70) * 0.8
            + item["confianza_pct"] * 0.35
            - item["riesgo_pct"] * 1.1
        )
        if item["payback"]:
            item["score"] -= min(30, _to_float(item["payback"], 0) * 1.2)
        if item["fase_negocio"] in {"idea_inversion", "preapertura"} and item["calidad_datos_pct"] < 40:
            item["score"] -= 8
        grupos.setdefault(tipo, []).append(item)

    por_modelo = []
    todos = []
    for tipo, items in grupos.items():
        ordenados = sorted(items, key=lambda x: x["score"], reverse=True)
        todos.extend(ordenados)
        positivos = [x for x in items if x["margen"] > 0]
        fragiles = [
            x for x in items
            if x["margen"] > 0 and x["equilibrio_uso"] is not None and _to_float(x["equilibrio_uso"], 0) >= 70
        ]
        por_modelo.append({
            "tipo": tipo,
            "total": len(items),
            "positivos": len(positivos),
            "fragiles": len(fragiles),
            "mejor": ordenados[0] if ordenados else None,
            "peor": ordenados[-1] if ordenados else None,
            "margen_total": round(sum(x["margen"] for x in items), 2),
            "riesgo_medio": round(sum(x["riesgo_pct"] for x in items) / len(items), 1) if items else 0,
        })

    por_modelo.sort(key=lambda x: ((x["mejor"] or {}).get("score", -999)), reverse=True)
    todos_ordenados = sorted(todos, key=lambda x: x["score"], reverse=True)
    recomendaciones = []
    if todos_ordenados:
        mejor = todos_ordenados[0]
        recomendaciones.append(
            f"Mejor candidato actual: {mejor['nombre']} ({mejor['tipo']}) con margen {_format_euros(mejor['margen'])} y riesgo {round(mejor['riesgo_pct'], 1)}%."
        )
    baja_calidad = [x for x in todos if x["fase_negocio"] in {"idea_inversion", "preapertura"} and x["calidad_datos_pct"] < 40]
    if baja_calidad:
        recomendaciones.append(
            f"Hay {len(baja_calidad)} escenarios de inversion/preapertura con datos debiles: antes de invertir necesitan contraste de mercado."
        )
    alto_riesgo = [x for x in todos if x["riesgo_pct"] >= 60]
    if alto_riesgo:
        recomendaciones.append(
            f"Hay {len(alto_riesgo)} escenarios con riesgo alto: no deberian pasar a decision sin plan de salida."
        )
    if not recomendaciones:
        recomendaciones.append("No hay suficientes escenarios para recomendar: crea hipotesis por modelo y compara margen, riesgo, satisfaccion y datos reales.")

    return {
        "total": len(todos),
        "por_modelo": por_modelo,
        "top": todos_ordenados[:5],
        "recomendaciones": recomendaciones,
    }


def escenarios_demo_decision(coste_laboral_mensual):
    base = max(_to_float(coste_laboral_mensual, 0), 55000)
    return [
        {
            "nombre": "Grande lleno pero tensionado",
            "tipo": "hotel",
            "decision_tipo": "tour_operador",
            "habitaciones": 260,
            "ocupacion_pct": 94,
            "adr": 72,
            "dias": 30,
            "pax_medio": 2.1,
            "coste_variable_pax_dia": 24,
            "coste_fijo_mes": 118000,
            "coste_personal_mes": base * 1.65,
            "alcance_costes_pct": 100,
            "coste_personal_origen": "prenomina_ops",
            "fuente_datos": "simulado",
            "comision_pct": 22,
            "inversion": 0,
            "satisfaccion_objetivo": 78,
            "rotacion_riesgo_pct": 34,
            "empleados_no_presentan": 6,
            "retrasos_pct": 14,
            "cambios_equipo": 9,
            "aceptacion_sistema_pct": 58,
            "cobertura_ops_pct": 45,
            "notas": "Mucho volumen, precio neto bajo y equipo tensionado. Sirve para ver el riesgo de llenar perdiendo control.",
        },
        {
            "nombre": "Pequeno eficiente y familiar",
            "tipo": "hotel",
            "decision_tipo": "oferta",
            "habitaciones": 58,
            "ocupacion_pct": 76,
            "adr": 112,
            "dias": 30,
            "pax_medio": 1.9,
            "coste_variable_pax_dia": 17,
            "coste_fijo_mes": 23000,
            "coste_personal_mes": base * 0.42,
            "alcance_costes_pct": 100,
            "coste_personal_origen": "prenomina_ops",
            "fuente_datos": "simulado",
            "comision_pct": 9,
            "inversion": 0,
            "satisfaccion_objetivo": 91,
            "rotacion_riesgo_pct": 8,
            "empleados_no_presentan": 0,
            "retrasos_pct": 2,
            "cambios_equipo": 1,
            "aceptacion_sistema_pct": 93,
            "cobertura_ops_pct": 80,
            "notas": "Menos volumen pero mejor experiencia, equipo estable y comision baja.",
        },
        {
            "nombre": "Reforma con retorno lento",
            "tipo": "hotel",
            "decision_tipo": "inversion",
            "habitaciones": 140,
            "ocupacion_pct": 72,
            "adr": 104,
            "dias": 30,
            "pax_medio": 2,
            "coste_variable_pax_dia": 19,
            "coste_fijo_mes": 62000,
            "coste_personal_mes": base,
            "alcance_costes_pct": 100,
            "coste_personal_origen": "prenomina_ops",
            "fuente_datos": "mixto",
            "comision_pct": 12,
            "inversion": 850000,
            "satisfaccion_objetivo": 88,
            "rotacion_riesgo_pct": 16,
            "empleados_no_presentan": 1,
            "retrasos_pct": 5,
            "cambios_equipo": 2,
            "aceptacion_sistema_pct": 84,
            "cobertura_ops_pct": 72,
            "notas": "Escenario para valorar si la mejora de producto paga la inversion a medio plazo.",
        },
        {
            "nombre": "OPS cooperativa estabiliza temporada",
            "tipo": "hotel",
            "decision_tipo": "rrhh",
            "habitaciones": 180,
            "ocupacion_pct": 86,
            "adr": 98,
            "dias": 30,
            "pax_medio": 2,
            "coste_variable_pax_dia": 20,
            "coste_fijo_mes": 76000,
            "coste_personal_mes": base * 1.18,
            "alcance_costes_pct": 100,
            "coste_personal_origen": "prenomina_ops",
            "fuente_datos": "mixto",
            "comision_pct": 13,
            "inversion": 45000,
            "satisfaccion_objetivo": 89,
            "rotacion_riesgo_pct": 13,
            "empleados_no_presentan": 2,
            "retrasos_pct": 4,
            "cambios_equipo": 2,
            "aceptacion_sistema_pct": 88,
            "cobertura_ops_pct": 92,
            "notas": "Prueba de cooperativa OPS cubriendo incidencias y reduciendo rotacion.",
        },
        {
            "nombre": "Restaurante carta local rentable",
            "tipo": "restaurante",
            "decision_tipo": "menu",
            "habitaciones": 120,
            "ocupacion_pct": 70,
            "adr": 28,
            "dias": 30,
            "pax_medio": 1,
            "coste_variable_pax_dia": 9,
            "coste_fijo_mes": 14000,
            "coste_personal_mes": 26000,
            "alcance_costes_pct": 100,
            "coste_personal_origen": "manual",
            "fuente_datos": "simulado",
            "comision_pct": 0,
            "inversion": 6500,
            "satisfaccion_objetivo": 90,
            "rotacion_riesgo_pct": 10,
            "empleados_no_presentan": 0,
            "retrasos_pct": 2,
            "cambios_equipo": 1,
            "aceptacion_sistema_pct": 92,
            "cobertura_ops_pct": 82,
            "notas": "Ejemplo de calculo por clientes/servicios, no por habitaciones.",
        },
    ]


def escenarios_inversion_pirineo(coste_laboral_mensual=0):
    base = max(_to_float(coste_laboral_mensual, 0), 52000)
    comunes = {
        "tipo": "hotel",
        "decision_tipo": "inversion",
        "fase_negocio": "idea_inversion",
        "zona_mercado": "pirineo",
        "fuente_datos": "externo",
        "coste_personal_origen": "manual",
        "dias": 30,
        "pax_medio": 1.9,
        "comision_pct": 14,
        "empleados_no_presentan": 0,
        "retrasos_pct": 2,
        "cambios_equipo": 1,
        "aceptacion_sistema_pct": 88,
        "cobertura_ops_pct": 75,
        "calidad_datos_pct": 28,
    }
    return [
        {
            **comunes,
            "nombre": "Pirineo 3 estrellas - compra prudente",
            "categoria_establecimiento": "3_estrellas",
            "habitaciones": 72,
            "ocupacion_pct": 58,
            "adr": 108,
            "coste_variable_pax_dia": 18,
            "coste_fijo_mes": 34000,
            "coste_personal_mes": base * 0.72,
            "alcance_costes_pct": 100,
            "inversion": 2200000,
            "satisfaccion_objetivo": 84,
            "rotacion_riesgo_pct": 18,
            "notas": "Hipotesis sin datos reales: activo de montaña con precio contenido y control fuerte de costes.",
        },
        {
            **comunes,
            "nombre": "Pirineo 4 estrellas - reposicionamiento",
            "categoria_establecimiento": "4_estrellas",
            "habitaciones": 86,
            "ocupacion_pct": 63,
            "adr": 165,
            "coste_variable_pax_dia": 27,
            "coste_fijo_mes": 61000,
            "coste_personal_mes": base * 1.05,
            "alcance_costes_pct": 100,
            "inversion": 4200000,
            "satisfaccion_objetivo": 88,
            "rotacion_riesgo_pct": 16,
            "notas": "Hipotesis para elevar ADR con producto familiar, wellness ligero y buena venta directa.",
        },
        {
            **comunes,
            "nombre": "Pirineo 4 plus - experiencia diferencial",
            "categoria_establecimiento": "4_plus",
            "habitaciones": 74,
            "ocupacion_pct": 66,
            "adr": 218,
            "coste_variable_pax_dia": 36,
            "coste_fijo_mes": 78000,
            "coste_personal_mes": base * 1.25,
            "alcance_costes_pct": 100,
            "inversion": 6500000,
            "satisfaccion_objetivo": 91,
            "rotacion_riesgo_pct": 14,
            "notas": "Hipotesis de posicionamiento alto sin llegar a lujo extremo: gastronomia, actividades y descanso.",
        },
        {
            **comunes,
            "nombre": "Pirineo 5 GL - lujo de montaña",
            "categoria_establecimiento": "5_gl",
            "habitaciones": 48,
            "ocupacion_pct": 52,
            "adr": 395,
            "coste_variable_pax_dia": 62,
            "coste_fijo_mes": 132000,
            "coste_personal_mes": base * 2.15,
            "alcance_costes_pct": 100,
            "inversion": 14500000,
            "satisfaccion_objetivo": 96,
            "rotacion_riesgo_pct": 20,
            "notas": "Hipotesis de lujo real: puede tener ADR alto, pero exige capex, equipo excelente y cero fallos.",
        },
    ]
