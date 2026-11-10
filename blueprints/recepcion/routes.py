import csv
import hashlib
import json
import secrets
import unicodedata
from datetime import date, datetime, timedelta
from functools import wraps
from html import escape
from io import StringIO
from urllib.error import URLError
from urllib.request import Request, urlopen

from flask import render_template, request, redirect, url_for, session, flash, abort, Response, current_app

from database import get_db
from . import recepcion_bp
from blueprints.auth.routes import login_required
from helpers.centros_helper import centro_activo_id
from helpers.email_helper import enviar_email


REGIMENES = {
    "solo_alojamiento": "Solo alojamiento",
    "alojamiento_desayuno": "Alojamiento y desayuno",
    "media_pension": "Media pension",
    "pension_completa": "Pension completa",
    "todo_incluido": "Todo incluido",
}

ESTADOS_RESERVA = {
    "pendiente": "Pendiente",
    "confirmada": "Confirmada",
    "checkin": "Check-in",
    "alojado": "Alojado",
    "checkout": "Check-out",
    "no_show": "No-show",
    "cancelada": "Cancelada",
}

DEPARTAMENTOS_AVISO = {
    "cocina": "Cocina",
    "pisos": "Pisos",
    "recepcion": "Recepcion",
    "mantenimiento": "Mantenimiento",
    "direccion": "Direccion",
}

SERVICIOS_COMIDA = {
    "desayuno": "Desayuno",
    "comida": "Comida",
    "cena": "Cena",
    "bebidas_snacks": "Bebidas / snacks",
}

IDIOMAS_DOCUMENTO = {
    "es": "Espanol",
    "en": "English",
    "de": "Deutsch",
    "it": "Italiano",
}

EDAD_DOCUMENTO_OBLIGATORIO_DEFAULT = 14

PREFERENCIAS_CAMA = {
    "": "Sin preferencia",
    "matrimonio": "Matrimonio",
    "dos_juntas": "2 camas juntas",
    "dos_separadas": "2 camas separadas",
    "individual": "Individual",
    "sofa_cama": "Sofa cama",
    "mixto": "Mixto",
}

TEXTOS_FACTURA = {
    "es": {
        "documento": "Documento",
        "numero": "Numero",
        "tipo": "Tipo",
        "fecha": "Fecha",
        "estado": "Estado",
        "reserva": "Reserva",
        "habitacion": "Habitacion",
        "cliente": "Cliente",
        "nombre": "Nombre",
        "nif": "NIF/CIF",
        "email": "Email",
        "direccion": "Direccion",
        "concepto": "Concepto",
        "cantidad": "Cantidad",
        "precio": "Precio",
        "base": "Base",
        "impuesto": "Impuesto",
        "total": "Total",
        "pagado": "Pagado",
        "saldo": "Saldo",
        "pagos": "Pagos de reserva",
        "sin_pagos": "Sin pagos registrados.",
        "sin_lineas": "Sin lineas.",
        "estancia": "Estancia {entrada} a {salida} ({noches} noches)",
        "cargo": "Cargo a habitacion",
    },
    "en": {
        "documento": "Document",
        "numero": "Number",
        "tipo": "Type",
        "fecha": "Date",
        "estado": "Status",
        "reserva": "Booking",
        "habitacion": "Room",
        "cliente": "Guest / customer",
        "nombre": "Name",
        "nif": "Tax ID",
        "email": "Email",
        "direccion": "Address",
        "concepto": "Description",
        "cantidad": "Quantity",
        "precio": "Price",
        "base": "Taxable amount",
        "impuesto": "Tax",
        "total": "Total",
        "pagado": "Paid",
        "saldo": "Balance",
        "pagos": "Booking payments",
        "sin_pagos": "No payments recorded.",
        "sin_lineas": "No lines.",
        "estancia": "Stay from {entrada} to {salida} ({noches} nights)",
        "cargo": "Room charge",
    },
    "de": {
        "documento": "Dokument",
        "numero": "Nummer",
        "tipo": "Art",
        "fecha": "Datum",
        "estado": "Status",
        "reserva": "Buchung",
        "habitacion": "Zimmer",
        "cliente": "Gast / Kunde",
        "nombre": "Name",
        "nif": "Steuer-ID",
        "email": "E-Mail",
        "direccion": "Adresse",
        "concepto": "Beschreibung",
        "cantidad": "Menge",
        "precio": "Preis",
        "base": "Bemessungsgrundlage",
        "impuesto": "Steuer",
        "total": "Gesamt",
        "pagado": "Bezahlt",
        "saldo": "Saldo",
        "pagos": "Zahlungen der Buchung",
        "sin_pagos": "Keine Zahlungen erfasst.",
        "sin_lineas": "Keine Positionen.",
        "estancia": "Aufenthalt vom {entrada} bis {salida} ({noches} Nachte)",
        "cargo": "Zimmerbelastung",
    },
    "it": {
        "documento": "Documento",
        "numero": "Numero",
        "tipo": "Tipo",
        "fecha": "Data",
        "estado": "Stato",
        "reserva": "Prenotazione",
        "habitacion": "Camera",
        "cliente": "Ospite / cliente",
        "nombre": "Nome",
        "nif": "Codice fiscale",
        "email": "Email",
        "direccion": "Indirizzo",
        "concepto": "Descrizione",
        "cantidad": "Quantita",
        "precio": "Prezzo",
        "base": "Imponibile",
        "impuesto": "Imposta",
        "total": "Totale",
        "pagado": "Pagato",
        "saldo": "Saldo",
        "pagos": "Pagamenti prenotazione",
        "sin_pagos": "Nessun pagamento registrato.",
        "sin_lineas": "Nessuna riga.",
        "estancia": "Soggiorno dal {entrada} al {salida} ({noches} notti)",
        "cargo": "Addebito camera",
    },
}

TEXTOS_RECOGIDA_DATOS = {
    "es": {
        "titulo": "Hoja de recogida de datos de viajeros",
        "intro": "Rellene los datos de cada persona alojada. El establecimiento comprobara el documento original y no conservara copia ni escaneado.",
        "reserva": "Reserva",
        "titular": "Titular",
        "habitacion": "Habitacion",
        "fechas": "Fechas",
        "viajero": "Viajero",
        "nombre": "Nombre",
        "apellidos": "Apellidos",
        "nacimiento": "Fecha de nacimiento",
        "nacionalidad": "Nacionalidad",
        "sexo": "Sexo",
        "tipo_doc": "Tipo documento",
        "num_doc": "Numero documento",
        "soporte_doc": "Soporte documento",
        "direccion": "Direccion",
        "cp": "Codigo postal",
        "localidad": "Localidad",
        "pais": "Pais",
        "telefono": "Telefono",
        "email": "Email",
        "parentesco": "Parentesco",
        "alergias": "Alergias / intolerancias / avisos alimentarios",
        "salud": "Otros avisos importantes para la estancia",
        "firma": "Firma",
        "fecha": "Fecha",
        "legal": "Declaro que los datos facilitados son correctos y autorizo su tratamiento para gestionar la estancia, la facturacion y las obligaciones legales de registro de viajeros.",
    },
    "en": {
        "titulo": "Guest data collection form",
        "intro": "Please complete the details for each guest. The establishment will check the original ID document and will not keep any copy or scan.",
        "reserva": "Booking",
        "titular": "Main guest",
        "habitacion": "Room",
        "fechas": "Dates",
        "viajero": "Guest",
        "nombre": "First name",
        "apellidos": "Last name",
        "nacimiento": "Date of birth",
        "nacionalidad": "Nationality",
        "sexo": "Sex",
        "tipo_doc": "Document type",
        "num_doc": "Document number",
        "soporte_doc": "Document support number",
        "direccion": "Address",
        "cp": "Postal code",
        "localidad": "City",
        "pais": "Country",
        "telefono": "Phone",
        "email": "Email",
        "parentesco": "Relationship",
        "alergias": "Allergies / intolerances / food notices",
        "salud": "Other important notices for the stay",
        "firma": "Signature",
        "fecha": "Date",
        "legal": "I declare that the information provided is correct and authorise its processing to manage the stay, invoicing and legal guest registration duties.",
    },
    "de": {
        "titulo": "Formular zur Erfassung von Gastdaten",
        "intro": "Bitte tragen Sie die Daten jeder beherbergten Person ein. Der Betrieb pruft das Originaldokument und bewahrt keine Kopie und keinen Scan auf.",
        "reserva": "Buchung",
        "titular": "Hauptgast",
        "habitacion": "Zimmer",
        "fechas": "Daten",
        "viajero": "Gast",
        "nombre": "Vorname",
        "apellidos": "Nachname",
        "nacimiento": "Geburtsdatum",
        "nacionalidad": "Staatsangehorigkeit",
        "sexo": "Geschlecht",
        "tipo_doc": "Dokumentart",
        "num_doc": "Dokumentnummer",
        "soporte_doc": "Dokument-Supportnummer",
        "direccion": "Adresse",
        "cp": "Postleitzahl",
        "localidad": "Ort",
        "pais": "Land",
        "telefono": "Telefon",
        "email": "E-Mail",
        "parentesco": "Beziehung",
        "alergias": "Allergien / Unvertraglichkeiten / Hinweise zu Lebensmitteln",
        "salud": "Weitere wichtige Hinweise fur den Aufenthalt",
        "firma": "Unterschrift",
        "fecha": "Datum",
        "legal": "Ich erklare, dass die angegebenen Daten richtig sind, und stimme ihrer Verarbeitung zur Verwaltung des Aufenthalts, der Rechnungsstellung und der gesetzlichen Gasteregistrierung zu.",
    },
    "it": {
        "titulo": "Modulo raccolta dati ospiti",
        "intro": "Compilare i dati di ogni persona alloggiata. La struttura verifichera il documento originale e non conservera copie ne scansioni.",
        "reserva": "Prenotazione",
        "titular": "Ospite principale",
        "habitacion": "Camera",
        "fechas": "Date",
        "viajero": "Ospite",
        "nombre": "Nome",
        "apellidos": "Cognome",
        "nacimiento": "Data di nascita",
        "nacionalidad": "Nazionalita",
        "sexo": "Sesso",
        "tipo_doc": "Tipo documento",
        "num_doc": "Numero documento",
        "soporte_doc": "Numero supporto documento",
        "direccion": "Indirizzo",
        "cp": "Codice postale",
        "localidad": "Citta",
        "pais": "Paese",
        "telefono": "Telefono",
        "email": "Email",
        "parentesco": "Rapporto",
        "alergias": "Allergie / intolleranze / avvisi alimentari",
        "salud": "Altri avvisi importanti per il soggiorno",
        "firma": "Firma",
        "fecha": "Data",
        "legal": "Dichiaro che i dati forniti sono corretti e autorizzo il loro trattamento per gestire il soggiorno, la fatturazione e gli obblighi legali di registrazione degli ospiti.",
    },
}

TEXTOS_DOCUMENTACION_CLIENTE = {
    "es": {
        "titulo": "Documentacion de check-in",
        "subtitulo": "Informacion de estancia entregada al cliente",
        "reserva": "Reserva",
        "titular": "Titular",
        "habitacion": "Habitacion",
        "fechas": "Fechas",
        "regimen": "Regimen contratado",
        "personas": "Personas",
        "incluido": "Servicios incluidos",
        "avisos": "Avisos registrados",
        "proteccion": "Proteccion de datos",
        "facturacion": "Facturacion",
        "entrega": "Documentacion entregada",
        "checklist": "Documentos / informacion entregada",
        "parte": "Parte de entrada y proteccion de datos",
        "estancia": "Informacion de estancia y regimen contratado",
        "fiscal": "Factura, proforma o informacion fiscal disponible",
        "alergias": "Avisos de alergias/intolerancias comunicados al departamento correspondiente",
        "sin_incluidos": "No hay servicios incluidos configurados para este regimen.",
        "sin_avisos": "No constan avisos especiales.",
        "texto_factura": "La factura se emitira con la fiscalidad configurada para el establecimiento y el servicio contratado.",
    },
    "en": {
        "titulo": "Check-in documentation",
        "subtitulo": "Stay information handed to the guest",
        "reserva": "Booking",
        "titular": "Main guest",
        "habitacion": "Room",
        "fechas": "Dates",
        "regimen": "Booked board basis",
        "personas": "Guests",
        "incluido": "Included services",
        "avisos": "Registered notices",
        "proteccion": "Data protection",
        "facturacion": "Invoicing",
        "entrega": "Documentation handed over",
        "checklist": "Documents / information provided",
        "parte": "Guest registration and data protection form",
        "estancia": "Stay information and booked board basis",
        "fiscal": "Invoice, proforma or tax information available",
        "alergias": "Allergy/intolerance notices sent to the relevant department",
        "sin_incluidos": "No included services are configured for this board basis.",
        "sin_avisos": "No special notices recorded.",
        "texto_factura": "The invoice will be issued with the tax configuration applicable to the establishment and the booked service.",
    },
    "de": {
        "titulo": "Check-in-Unterlagen",
        "subtitulo": "Informationen zum Aufenthalt fur den Gast",
        "reserva": "Buchung",
        "titular": "Hauptgast",
        "habitacion": "Zimmer",
        "fechas": "Daten",
        "regimen": "Gebuchte Verpflegungsart",
        "personas": "Gaste",
        "incluido": "Inbegriffene Leistungen",
        "avisos": "Registrierte Hinweise",
        "proteccion": "Datenschutz",
        "facturacion": "Rechnung",
        "entrega": "Unterlagen ubergeben",
        "checklist": "Ubergebene Dokumente / Informationen",
        "parte": "Meldeschein und Datenschutzinformation",
        "estancia": "Informationen zum Aufenthalt und zur gebuchten Verpflegung",
        "fiscal": "Rechnung, Proforma oder Steuerinformation verfugbar",
        "alergias": "Allergie-/Unvertraglichkeitshinweise an die zustandige Abteilung gesendet",
        "sin_incluidos": "Fur diese Verpflegungsart sind keine inbegriffenen Leistungen konfiguriert.",
        "sin_avisos": "Keine besonderen Hinweise erfasst.",
        "texto_factura": "Die Rechnung wird mit der fur den Betrieb und die gebuchte Leistung geltenden Steuerkonfiguration ausgestellt.",
    },
    "it": {
        "titulo": "Documentazione check-in",
        "subtitulo": "Informazioni sul soggiorno consegnate al cliente",
        "reserva": "Prenotazione",
        "titular": "Ospite principale",
        "habitacion": "Camera",
        "fechas": "Date",
        "regimen": "Trattamento prenotato",
        "personas": "Ospiti",
        "incluido": "Servizi inclusi",
        "avisos": "Avvisi registrati",
        "proteccion": "Protezione dei dati",
        "facturacion": "Fatturazione",
        "entrega": "Documentazione consegnata",
        "checklist": "Documenti / informazioni consegnati",
        "parte": "Scheda di registrazione e informativa privacy",
        "estancia": "Informazioni sul soggiorno e trattamento prenotato",
        "fiscal": "Fattura, proforma o informazioni fiscali disponibili",
        "alergias": "Avvisi di allergie/intolleranze inviati al reparto competente",
        "sin_incluidos": "Non ci sono servizi inclusi configurati per questo trattamento.",
        "sin_avisos": "Nessun avviso speciale registrato.",
        "texto_factura": "La fattura sara emessa con la configurazione fiscale applicabile alla struttura e al servizio prenotato.",
    },
}


def _empresa_id():
    try:
        return session.get("user_empresa_id", 1) or 1
    except RuntimeError:
        return 1


def _empresa_datos(conn, empresa_id):
    if not _table_exists(conn, "empresas"):
        return None
    row = conn.execute("SELECT * FROM empresas WHERE id = ? LIMIT 1", (empresa_id,)).fetchone()
    if row:
        return row
    return conn.execute("SELECT * FROM empresas ORDER BY id LIMIT 1").fetchone()


def _empresa_logo_url(empresa):
    if not empresa or "logo" not in empresa.keys() or not empresa["logo"]:
        return ""
    logo = str(empresa["logo"]).replace("\\", "/").strip()
    if not logo:
        return ""
    if logo.startswith(("http://", "https://", "data:", "/static/", "/uploads/")):
        return logo
    if logo.startswith("static/"):
        return "/" + logo
    if logo.startswith(("uploads/", "img/", "images/")):
        return url_for("static", filename=logo)
    return url_for("static", filename=f"uploads/empresas/{logo}")


def _normalizar_rol(valor):
    return (valor or "").strip().lower()


def _puede_recepcion():
    rol = _normalizar_rol(session.get("user_rol"))
    return rol not in {"empleado", "kiosco", "tablet", "fichaje"}


def _require_recepcion(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _puede_recepcion():
            flash("No tienes permisos para acceder a recepcion.", "warning")
            return redirect(url_for("home"))
        return func(*args, **kwargs)

    return wrapper


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(conn, table_name, column_name):
    try:
        cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return False
    return any(c["name"] == column_name or c[1] == column_name for c in cols)


def _add_column_if_missing(conn, table_name, column_sql):
    column_name = column_sql.strip().split()[0]
    if _table_exists(conn, table_name) and not _column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def _to_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        return default


def _to_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _payment_service(conn, empresa_id):
    try:
        from integraciones.pagos_marca_blanca.service import PaymentService

        return PaymentService(
            conn,
            empresa_id=empresa_id,
            centro_id=centro_activo_id(),
            usuario=session.get("user_email") or session.get("user_nombre") or "recepcion",
        )
    except Exception:
        return None


def _pasarelas_pago_activas(conn, empresa_id):
    service = _payment_service(conn, empresa_id)
    if not service:
        return []
    try:
        return [cfg for cfg in service.list_configs() if cfg.get("activo")]
    except Exception:
        return []


def _operaciones_pago_reserva(conn, reserva_id, empresa_id):
    if not _table_exists(conn, "pagos_operaciones"):
        return []
    return conn.execute(
        """
        SELECT op.*, cfg.nombre AS config_nombre
        FROM pagos_operaciones op
        LEFT JOIN pagos_gateway_config cfg ON cfg.id=op.config_id
        WHERE op.empresa_id=? AND op.origen_modulo='recepcion'
          AND op.origen_tipo IN ('reserva','deposito','checkout')
          AND op.origen_id=?
        ORDER BY op.id DESC
        """,
        (empresa_id, reserva_id),
    ).fetchall()


def _parse_date(value, default=None):
    if not value:
        return default
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return default


def _seed_regimen_inclusiones(conn, empresa_id):
    defaults = [
        ("alojamiento_desayuno", "desayuno", "alimento", "Desayuno contratado", "1 servicio por persona y noche", "Segun horario del establecimiento"),
        ("media_pension", "desayuno", "alimento", "Desayuno contratado", "1 servicio por persona y noche", "Segun horario del establecimiento"),
        ("media_pension", "cena", "alimento", "Cena contratada", "1 servicio por persona y noche", "Bebidas segun politica del establecimiento"),
        ("pension_completa", "desayuno", "alimento", "Desayuno contratado", "1 servicio por persona y noche", "Segun horario del establecimiento"),
        ("pension_completa", "comida", "alimento", "Comida contratada", "1 servicio por persona y dia", "Bebidas segun politica del establecimiento"),
        ("pension_completa", "cena", "alimento", "Cena contratada", "1 servicio por persona y noche", "Bebidas segun politica del establecimiento"),
        ("todo_incluido", "desayuno", "alimento", "Desayuno todo incluido", "1 servicio por persona y noche", "Segun horario del establecimiento"),
        ("todo_incluido", "comida", "alimento", "Comida todo incluido", "1 servicio por persona y dia", "Segun politica del establecimiento"),
        ("todo_incluido", "cena", "alimento", "Cena todo incluido", "1 servicio por persona y noche", "Segun politica del establecimiento"),
        ("todo_incluido", "bebidas_snacks", "bebida", "Bebidas y snacks incluidos", "Segun pulsera o bono", "Aplican marcas y horarios configurados por el establecimiento"),
    ]
    for regimen, servicio, tipo, concepto, limite, condiciones in defaults:
        exists = conn.execute(
            """
            SELECT id
            FROM recepcion_regimen_inclusiones
            WHERE empresa_id = ? AND regimen = ? AND servicio = ? AND tipo = ? AND concepto = ?
            LIMIT 1
            """,
            (empresa_id, regimen, servicio, tipo, concepto),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO recepcion_regimen_inclusiones
            (regimen, servicio, tipo, concepto, incluido, limite, condiciones, activo, empresa_id)
            VALUES (?, ?, ?, ?, 1, ?, ?, 1, ?)
            """,
            (regimen, servicio, tipo, concepto, limite, condiciones, empresa_id),
        )


def _seed_canales_reserva(conn, empresa_id):
    defaults = [
        ("manual", "Manual", "directo", 0),
        ("telefono", "Telefono", "directo", 0),
        ("web", "Web propia", "directo", 0),
        ("booking", "Booking", "ota", 15),
        ("expedia", "Expedia", "ota", 15),
        ("airbnb", "Airbnb", "ota", 14),
        ("agencia", "Agencia", "agencia", 10),
    ]
    for codigo, nombre, tipo, comision in defaults:
        exists = conn.execute(
            """
            SELECT id
            FROM recepcion_canales_reserva
            WHERE empresa_id = ? AND codigo = ?
            LIMIT 1
            """,
            (empresa_id, codigo),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO recepcion_canales_reserva
            (codigo, nombre, tipo, comision_pct, activo, empresa_id)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (codigo, nombre, tipo, comision, empresa_id),
        )


def _seed_tipos_habitacion(conn, empresa_id):
    defaults = [
        ("SGL", "Sencilla", 1, 1),
        ("DBL", "Doble", 2, 2),
        ("TPL", "Triple", 3, 3),
        ("CUA", "Cuadruple", 4, 4),
        ("SUI", "Suite", 2, 4),
    ]
    for codigo, nombre, capacidad_base, capacidad_max in defaults:
        exists = conn.execute(
            """
            SELECT id
            FROM recepcion_tipos_habitacion
            WHERE empresa_id = ? AND codigo = ?
            LIMIT 1
            """,
            (empresa_id, codigo),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO recepcion_tipos_habitacion
            (codigo, nombre, capacidad_base, capacidad_max, activo, empresa_id)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (codigo, nombre, capacidad_base, capacidad_max, empresa_id),
        )


def _seed_textos_checkin(conn, empresa_id):
    defaults = {
        "es": (
            "Parte de entrada y proteccion de datos",
            """El establecimiento recoge los datos identificativos necesarios para gestionar la estancia, emitir la factura y cumplir las obligaciones legales de registro documental e informacion de viajeros.

La persona alojada debe exhibir o facilitar su documento de identidad para verificar que los datos registrados son correctos. El establecimiento no conserva copia ni escaneado del DNI, pasaporte u otro documento de identidad; solo registra los datos necesarios.

Los datos se trataran conforme al RGPD y a la normativa espanola de proteccion de datos, con las medidas de seguridad correspondientes. Podra ejercer los derechos de acceso, rectificacion, supresion, oposicion, limitacion y portabilidad ante el responsable del establecimiento.

Con la firma de este parte, la persona firmante declara que los datos facilitados son correctos y que ha sido informada del tratamiento de datos asociado al check-in.""",
        ),
        "en": (
            "Guest registration and data protection notice",
            """The establishment collects the identification data required to manage the stay, issue the invoice and comply with the legal guest registration and reporting duties.

The guest must show or provide an identity document so that the registered details can be checked. The establishment does not keep a copy or scan of the ID card, passport or any other identity document; only the necessary data is recorded.

The data will be processed in accordance with the GDPR and Spanish data protection rules, using appropriate security measures. You may exercise your rights of access, rectification, erasure, objection, restriction and portability before the establishment controller.

By signing this form, the signer confirms that the data provided is accurate and that they have been informed about the data processing related to check-in.""",
        ),
        "de": (
            "Meldeschein und Datenschutzinformation",
            """Der Betrieb erhebt die Identifikationsdaten, die zur Verwaltung des Aufenthalts, zur Ausstellung der Rechnung und zur Erfullung der gesetzlichen Melde- und Informationspflichten erforderlich sind.

Der Gast muss ein Ausweisdokument vorzeigen oder bereitstellen, damit die registrierten Daten gepruft werden konnen. Der Betrieb speichert keine Kopie und keinen Scan des Personalausweises, Reisepasses oder eines anderen Ausweisdokuments; es werden nur die notwendigen Daten erfasst.

Die Daten werden gemass DSGVO und den spanischen Datenschutzvorschriften mit geeigneten Sicherheitsmassnahmen verarbeitet. Sie konnen Ihre Rechte auf Auskunft, Berichtigung, Loschung, Widerspruch, Einschrankung und Datenubertragbarkeit gegenuber dem Verantwortlichen des Betriebs ausuben.

Mit der Unterschrift bestatigt die unterzeichnende Person, dass die angegebenen Daten richtig sind und dass sie uber die Datenverarbeitung im Zusammenhang mit dem Check-in informiert wurde.""",
        ),
        "it": (
            "Scheda di registrazione e informativa privacy",
            """La struttura raccoglie i dati identificativi necessari per gestire il soggiorno, emettere la fattura e adempiere agli obblighi legali di registrazione e comunicazione degli ospiti.

L'ospite deve mostrare o fornire un documento di identita affinche i dati registrati possano essere verificati. La struttura non conserva copie ne scansioni della carta d'identita, del passaporto o di altro documento di identita; registra solo i dati necessari.

I dati saranno trattati in conformita al GDPR e alla normativa spagnola sulla protezione dei dati, con adeguate misure di sicurezza. Lei potra esercitare i diritti di accesso, rettifica, cancellazione, opposizione, limitazione e portabilita presso il titolare della struttura.

Con la firma del presente modulo, il firmatario dichiara che i dati forniti sono corretti e di essere stato informato sul trattamento dei dati relativo al check-in.""",
        ),
    }
    for idioma, (titulo, contenido) in defaults.items():
        exists = conn.execute(
            """
            SELECT id
            FROM recepcion_textos_checkin
            WHERE empresa_id = ? AND idioma = ? AND version = '2026-05'
            LIMIT 1
            """,
            (empresa_id, idioma),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO recepcion_textos_checkin
            (idioma, titulo, contenido, version, activo, empresa_id)
            VALUES (?, ?, ?, '2026-05', 1, ?)
            """,
            (idioma, titulo, contenido, empresa_id),
        )


def _seed_impuestos(conn, empresa_id):
    defaults = [
        ("IVA_10", "IVA reducido 10%", "peninsula_baleares", 10, "Hosteleria, alojamiento y restauracion cuando proceda."),
        ("IVA_21", "IVA general 21%", "peninsula_baleares", 21, "Tipo general para servicios o productos no reducidos."),
        ("IGIC_7", "IGIC general 7%", "canarias", 7, "Tipo general de IGIC cuando proceda en Canarias."),
        ("IGIC_0", "IGIC tipo cero / no sujeto 0%", "canarias", 0, "Usar solo cuando proceda legalmente."),
        ("EXENTO", "Exento / no sujeto 0%", "general", 0, "Usar solo con causa fiscal validada."),
    ]
    for codigo, nombre, jurisdiccion, porcentaje, descripcion in defaults:
        exists = conn.execute(
            """
            SELECT id
            FROM recepcion_impuestos
            WHERE empresa_id = ? AND codigo = ?
            LIMIT 1
            """,
            (empresa_id, codigo),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO recepcion_impuestos
            (codigo, nombre, jurisdiccion, porcentaje, descripcion, activo, empresa_id)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (codigo, nombre, jurisdiccion, porcentaje, descripcion, empresa_id),
        )


def _seed_config_fiscal(conn, empresa_id):
    exists = conn.execute(
        "SELECT id FROM recepcion_config_fiscal WHERE empresa_id = ? LIMIT 1",
        (empresa_id,),
    ).fetchone()
    if exists:
        return
    impuesto_estancia = conn.execute(
        """
        SELECT id
        FROM recepcion_impuestos
        WHERE empresa_id = ? AND codigo IN ('IGIC_7', 'IVA_10')
        ORDER BY CASE codigo WHEN 'IGIC_7' THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (empresa_id,),
    ).fetchone()
    impuesto_extras = conn.execute(
        """
        SELECT id
        FROM recepcion_impuestos
        WHERE empresa_id = ? AND codigo IN ('IGIC_7', 'IVA_10')
        ORDER BY CASE codigo WHEN 'IGIC_7' THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (empresa_id,),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO recepcion_config_fiscal
        (empresa_id, territorio, impuesto_estancia_id, impuesto_extras_id, serie_factura,
         serie_proforma, serie_rectificativa, modo_verifactu, modo_factura_electronica,
         modo_registro_viajeros, edad_min_documento, texto_pie_factura, updated_at)
        VALUES (?, 'canarias', ?, ?, 'REC', 'PRO', 'REC-R', 'preparado_no_enviado',
                'pendiente_configuracion', 'ses_hospedajes_manual', ?,
                'Documento emitido por el establecimiento. Conserve este justificante para cualquier consulta.',
                CURRENT_TIMESTAMP)
        """,
        (
            empresa_id,
            impuesto_estancia["id"] if impuesto_estancia else None,
            impuesto_extras["id"] if impuesto_extras else None,
            EDAD_DOCUMENTO_OBLIGATORIO_DEFAULT,
        ),
    )


def _ensure_recepcion_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_tipos_habitacion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT NOT NULL,
            nombre TEXT NOT NULL,
            capacidad_base INTEGER DEFAULT 2,
            capacidad_max INTEGER DEFAULT 2,
            descripcion TEXT,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "codigo TEXT", "nombre TEXT", "capacidad_base INTEGER DEFAULT 2", "capacidad_max INTEGER DEFAULT 2",
        "descripcion TEXT", "activo INTEGER DEFAULT 1", "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP", "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "recepcion_tipos_habitacion", col)
    _seed_tipos_habitacion(conn, _empresa_id())

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS habitaciones_recepcion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero TEXT NOT NULL,
            nombre TEXT,
            tipo TEXT,
            tipo_habitacion_id INTEGER,
            bloque TEXT,
            bloque_id INTEGER,
            planta TEXT,
            planta_id INTEGER,
            capacidad INTEGER DEFAULT 2,
            estado TEXT DEFAULT 'disponible',
            centro_id INTEGER,
            observaciones TEXT,
            ical_token TEXT,
            empresa_id INTEGER DEFAULT 1,
            activo INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "numero TEXT", "nombre TEXT", "tipo TEXT", "planta TEXT", "capacidad INTEGER DEFAULT 2",
        "tipo_habitacion_id INTEGER", "bloque TEXT", "bloque_id INTEGER", "planta_id INTEGER", "estado TEXT DEFAULT 'disponible'", "centro_id INTEGER", "observaciones TEXT",
        "ical_token TEXT",
        "empresa_id INTEGER DEFAULT 1", "activo INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "habitaciones_recepcion", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS habitaciones_equipamiento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habitacion_id INTEGER NOT NULL,
            tipo_cama TEXT,
            camas_matrimonio INTEGER DEFAULT 0,
            camas_individuales INTEGER DEFAULT 0,
            camas_juntas INTEGER DEFAULT 0,
            sofa_cama INTEGER DEFAULT 0,
            supletoria_posible INTEGER DEFAULT 0,
            supletorias_max INTEGER DEFAULT 0,
            cuna_posible INTEGER DEFAULT 0,
            tv INTEGER DEFAULT 1,
            minibar INTEGER DEFAULT 0,
            aire_tipo TEXT,
            armario INTEGER DEFAULT 1,
            mesas INTEGER DEFAULT 0,
            sillas INTEGER DEFAULT 0,
            mesillas INTEGER DEFAULT 0,
            tumbonas INTEGER DEFAULT 0,
            tendedero INTEGER DEFAULT 0,
            caja_fuerte INTEGER DEFAULT 0,
            terraza INTEGER DEFAULT 0,
            inventario_extra TEXT,
            observaciones TEXT,
            revisado_por TEXT,
            revisado_at TEXT,
            empresa_id INTEGER DEFAULT 1,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(habitacion_id, empresa_id)
        )
        """
    )
    for col in [
        "habitacion_id INTEGER",
        "tipo_cama TEXT",
        "camas_matrimonio INTEGER DEFAULT 0",
        "camas_individuales INTEGER DEFAULT 0",
        "camas_juntas INTEGER DEFAULT 0",
        "sofa_cama INTEGER DEFAULT 0",
        "supletoria_posible INTEGER DEFAULT 0",
        "supletorias_max INTEGER DEFAULT 0",
        "cuna_posible INTEGER DEFAULT 0",
        "tv INTEGER DEFAULT 1",
        "minibar INTEGER DEFAULT 0",
        "aire_tipo TEXT",
        "armario INTEGER DEFAULT 1",
        "mesas INTEGER DEFAULT 0",
        "sillas INTEGER DEFAULT 0",
        "mesillas INTEGER DEFAULT 0",
        "tumbonas INTEGER DEFAULT 0",
        "tendedero INTEGER DEFAULT 0",
        "caja_fuerte INTEGER DEFAULT 0",
        "terraza INTEGER DEFAULT 0",
        "inventario_extra TEXT",
        "observaciones TEXT",
        "revisado_por TEXT",
        "revisado_at TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "habitaciones_equipamiento", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reservas_recepcion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT,
            habitacion_id INTEGER,
            fecha_entrada TEXT NOT NULL,
            fecha_salida TEXT NOT NULL,
            hora_llegada TEXT,
            hora_salida TEXT,
            regimen TEXT DEFAULT 'solo_alojamiento',
            adultos INTEGER DEFAULT 1,
            menores INTEGER DEFAULT 0,
            preferencia_cama TEXT,
            supletoria_solicitada INTEGER DEFAULT 0,
            cuna_solicitada INTEGER DEFAULT 0,
            observaciones_habitacion TEXT,
            estado TEXT DEFAULT 'confirmada',
            origen TEXT DEFAULT 'manual',
            titular_nombre TEXT,
            titular_email TEXT,
            titular_telefono TEXT,
            precio_total REAL DEFAULT 0,
            deposito REAL DEFAULT 0,
            saldo REAL DEFAULT 0,
            extras_total REAL DEFAULT 0,
            observaciones TEXT,
            observaciones_operativas TEXT,
            alergias_resumen TEXT,
            empresa_id INTEGER DEFAULT 1,
            creado_por INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            checkin_at TEXT,
            checkout_at TEXT,
            no_show_at TEXT,
            cancelada_at TEXT,
            cuenta_cerrada_at TEXT,
            canal_id INTEGER,
            localizador_externo TEXT,
            comision_canal REAL DEFAULT 0,
            estado_externo TEXT,
            ultima_sincronizacion TEXT,
            documentacion_entregada_at TEXT,
            documentacion_entregada_idioma TEXT,
            politica_credito TEXT DEFAULT 'abierto',
            limite_credito REAL DEFAULT 0,
            fianza_virtual REAL DEFAULT 0,
            fianza_referencia TEXT,
            fianza_estado TEXT DEFAULT 'sin_fianza',
            fianza_liberada_at TEXT,
            fianza_cobrada_at TEXT,
            fianza_liquidada_importe REAL DEFAULT 0,
            checkin_factura_id INTEGER,
            updated_at TEXT
        )
        """
    )
    for col in [
        "codigo TEXT", "habitacion_id INTEGER", "tipo_habitacion_id INTEGER", "centro_id INTEGER", "fecha_entrada TEXT", "fecha_salida TEXT",
        "hora_llegada TEXT", "hora_salida TEXT", "regimen TEXT DEFAULT 'solo_alojamiento'",
        "adultos INTEGER DEFAULT 1", "menores INTEGER DEFAULT 0", "estado TEXT DEFAULT 'confirmada'",
        "preferencia_cama TEXT", "supletoria_solicitada INTEGER DEFAULT 0",
        "cuna_solicitada INTEGER DEFAULT 0", "observaciones_habitacion TEXT",
        "origen TEXT DEFAULT 'manual'", "titular_nombre TEXT", "titular_email TEXT", "titular_telefono TEXT",
        "precio_total REAL DEFAULT 0", "deposito REAL DEFAULT 0", "saldo REAL DEFAULT 0",
        "extras_total REAL DEFAULT 0",
        "observaciones TEXT", "observaciones_operativas TEXT", "alergias_resumen TEXT",
        "empresa_id INTEGER DEFAULT 1", "creado_por INTEGER", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "checkin_at TEXT", "checkout_at TEXT", "no_show_at TEXT", "cancelada_at TEXT",
        "cuenta_cerrada_at TEXT", "canal_id INTEGER", "localizador_externo TEXT",
        "comision_canal REAL DEFAULT 0", "estado_externo TEXT", "ultima_sincronizacion TEXT",
        "documentacion_entregada_at TEXT", "documentacion_entregada_idioma TEXT",
        "politica_credito TEXT DEFAULT 'abierto'", "limite_credito REAL DEFAULT 0",
        "fianza_virtual REAL DEFAULT 0", "fianza_referencia TEXT", "fianza_estado TEXT DEFAULT 'sin_fianza'",
        "fianza_liberada_at TEXT", "fianza_cobrada_at TEXT", "fianza_liquidada_importe REAL DEFAULT 0",
        "checkin_factura_id INTEGER",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "reservas_recepcion", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_canales_reserva (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT NOT NULL,
            nombre TEXT NOT NULL,
            tipo TEXT DEFAULT 'directo',
            comision_pct REAL DEFAULT 0,
            activo INTEGER DEFAULT 1,
            config_json TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "codigo TEXT", "nombre TEXT", "tipo TEXT DEFAULT 'directo'", "comision_pct REAL DEFAULT 0",
        "activo INTEGER DEFAULT 1", "config_json TEXT", "empresa_id INTEGER DEFAULT 1",
        "ultima_sincronizacion TEXT", "estado_sincronizacion TEXT",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP", "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "recepcion_canales_reserva", col)
    _seed_canales_reserva(conn, _empresa_id())

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_cupos_canal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canal_id INTEGER NOT NULL,
            tipo_habitacion_id INTEGER,
            fecha_inicio TEXT NOT NULL,
            fecha_fin TEXT NOT NULL,
            porcentaje REAL DEFAULT 0,
            habitaciones INTEGER DEFAULT 0,
            prioridad INTEGER DEFAULT 100,
            release_dias INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "canal_id INTEGER", "tipo_habitacion_id INTEGER", "fecha_inicio TEXT", "fecha_fin TEXT",
        "porcentaje REAL DEFAULT 0", "habitaciones INTEGER DEFAULT 0", "prioridad INTEGER DEFAULT 100",
        "release_dias INTEGER DEFAULT 0", "activo INTEGER DEFAULT 1", "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP", "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "recepcion_cupos_canal", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_sincronizaciones_canal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canal_id INTEGER NOT NULL,
            tipo TEXT DEFAULT 'csv',
            estado TEXT DEFAULT 'pendiente',
            archivo TEXT,
            total_filas INTEGER DEFAULT 0,
            creadas INTEGER DEFAULT 0,
            actualizadas INTEGER DEFAULT 0,
            omitidas INTEGER DEFAULT 0,
            errores INTEGER DEFAULT 0,
            resumen TEXT,
            empresa_id INTEGER DEFAULT 1,
            usuario_id INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "canal_id INTEGER", "tipo TEXT DEFAULT 'csv'", "estado TEXT DEFAULT 'pendiente'", "archivo TEXT",
        "total_filas INTEGER DEFAULT 0", "creadas INTEGER DEFAULT 0", "actualizadas INTEGER DEFAULT 0",
        "omitidas INTEGER DEFAULT 0", "errores INTEGER DEFAULT 0", "resumen TEXT",
        "empresa_id INTEGER DEFAULT 1", "usuario_id INTEGER", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "recepcion_sincronizaciones_canal", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS viajeros_recepcion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reserva_id INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            apellidos TEXT,
            fecha_nacimiento TEXT,
            nacionalidad TEXT,
            sexo TEXT,
            tipo_documento TEXT,
            numero_documento TEXT,
            soporte_documento TEXT,
            parentesco TEXT,
            telefono TEXT,
            email TEXT,
            direccion TEXT,
            codigo_postal TEXT,
            localidad TEXT,
            pais TEXT DEFAULT 'Espana',
            es_titular INTEGER DEFAULT 0,
            alergias_intolerancias TEXT,
            observaciones_salud TEXT,
            avisos_departamentos TEXT,
            registro_viajeros_estado TEXT DEFAULT 'pendiente',
            registro_viajeros_fecha TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "reserva_id INTEGER", "nombre TEXT", "apellidos TEXT", "fecha_nacimiento TEXT", "nacionalidad TEXT",
        "sexo TEXT", "tipo_documento TEXT", "numero_documento TEXT", "soporte_documento TEXT", "parentesco TEXT",
        "telefono TEXT", "email TEXT", "direccion TEXT", "codigo_postal TEXT", "localidad TEXT",
        "pais TEXT DEFAULT 'Espana'", "es_titular INTEGER DEFAULT 0", "alergias_intolerancias TEXT",
        "observaciones_salud TEXT", "avisos_departamentos TEXT",
        "registro_viajeros_estado TEXT DEFAULT 'pendiente'", "registro_viajeros_fecha TEXT",
        "empresa_id INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP", "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "viajeros_recepcion", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_textos_checkin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idioma TEXT NOT NULL,
            titulo TEXT NOT NULL,
            contenido TEXT NOT NULL,
            version TEXT DEFAULT '1',
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "idioma TEXT", "titulo TEXT", "contenido TEXT", "version TEXT DEFAULT '1'",
        "activo INTEGER DEFAULT 1", "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP", "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "recepcion_textos_checkin", col)
    _seed_textos_checkin(conn, _empresa_id())

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_documentos_checkin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reserva_id INTEGER NOT NULL,
            idioma TEXT DEFAULT 'es',
            version TEXT DEFAULT '1',
            titulo TEXT,
            estado TEXT DEFAULT 'generado',
            firmante_nombre TEXT,
            firmado_at TEXT,
            firma_cliente_data TEXT,
            firma_cliente_mime TEXT,
            firma_metodo TEXT,
            usuario_id INTEGER,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "reserva_id INTEGER", "idioma TEXT DEFAULT 'es'", "version TEXT DEFAULT '1'", "titulo TEXT",
        "estado TEXT DEFAULT 'generado'", "firmante_nombre TEXT", "firmado_at TEXT",
        "firma_cliente_data TEXT", "firma_cliente_mime TEXT", "firma_metodo TEXT", "usuario_id INTEGER",
        "observaciones TEXT", "empresa_id INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "recepcion_documentos_checkin", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_avisos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reserva_id INTEGER NOT NULL,
            viajero_id INTEGER,
            fecha_servicio TEXT,
            servicio TEXT DEFAULT 'todos',
            tipo TEXT DEFAULT 'alergia',
            descripcion TEXT NOT NULL,
            departamentos TEXT DEFAULT 'cocina',
            prioridad TEXT DEFAULT 'media',
            estado TEXT DEFAULT 'pendiente',
            origen TEXT DEFAULT 'manual',
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "reserva_id INTEGER", "viajero_id INTEGER", "fecha_servicio TEXT", "servicio TEXT DEFAULT 'todos'",
        "tipo TEXT DEFAULT 'alergia'", "descripcion TEXT", "departamentos TEXT DEFAULT 'cocina'",
        "prioridad TEXT DEFAULT 'media'", "estado TEXT DEFAULT 'pendiente'", "origen TEXT DEFAULT 'manual'",
        "empresa_id INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "recepcion_avisos", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pagos_reserva_recepcion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reserva_id INTEGER NOT NULL,
            fecha TEXT DEFAULT CURRENT_DATE,
            concepto TEXT,
            importe REAL DEFAULT 0,
            forma_pago TEXT,
            referencia TEXT,
            estado TEXT DEFAULT 'confirmado',
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            usuario_id INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "reserva_id INTEGER", "fecha TEXT DEFAULT CURRENT_DATE", "concepto TEXT", "importe REAL DEFAULT 0",
        "forma_pago TEXT", "referencia TEXT", "estado TEXT DEFAULT 'confirmado'", "observaciones TEXT",
        "empresa_id INTEGER DEFAULT 1", "usuario_id INTEGER", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "pagos_reserva_recepcion", col)

    conn.execute('''
        CREATE TABLE IF NOT EXISTS recepcion_claves_cobro (
            empresa_id INTEGER NOT NULL, reserva_id INTEGER NOT NULL,
            clave TEXT NOT NULL,
            PRIMARY KEY (empresa_id, reserva_id, clave)
        )
    ''')

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_cierres_caja (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            turno TEXT DEFAULT 'dia',
            efectivo_sistema REAL DEFAULT 0,
            tarjeta_sistema REAL DEFAULT 0,
            transferencia_sistema REAL DEFAULT 0,
            bizum_sistema REAL DEFAULT 0,
            otros_sistema REAL DEFAULT 0,
            total_sistema REAL DEFAULT 0,
            efectivo_real REAL DEFAULT 0,
            tarjeta_real REAL DEFAULT 0,
            transferencia_real REAL DEFAULT 0,
            bizum_real REAL DEFAULT 0,
            otros_real REAL DEFAULT 0,
            total_real REAL DEFAULT 0,
            diferencia REAL DEFAULT 0,
            estado TEXT DEFAULT 'cerrado',
            observaciones TEXT,
            usuario_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "fecha TEXT", "turno TEXT DEFAULT 'dia'", "efectivo_sistema REAL DEFAULT 0",
        "tarjeta_sistema REAL DEFAULT 0", "transferencia_sistema REAL DEFAULT 0",
        "bizum_sistema REAL DEFAULT 0", "otros_sistema REAL DEFAULT 0", "total_sistema REAL DEFAULT 0",
        "efectivo_real REAL DEFAULT 0", "tarjeta_real REAL DEFAULT 0", "transferencia_real REAL DEFAULT 0",
        "bizum_real REAL DEFAULT 0", "otros_real REAL DEFAULT 0", "total_real REAL DEFAULT 0",
        "diferencia REAL DEFAULT 0", "estado TEXT DEFAULT 'cerrado'", "observaciones TEXT",
        "usuario_id INTEGER", "empresa_id INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "recepcion_cierres_caja", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bloqueos_habitacion_recepcion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habitacion_id INTEGER NOT NULL,
            fecha_inicio TEXT NOT NULL,
            fecha_fin TEXT NOT NULL,
            motivo TEXT DEFAULT 'bloqueo',
            descripcion TEXT,
            estado TEXT DEFAULT 'activo',
            empresa_id INTEGER DEFAULT 1,
            creado_por INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "habitacion_id INTEGER", "fecha_inicio TEXT", "fecha_fin TEXT", "motivo TEXT DEFAULT 'bloqueo'",
        "descripcion TEXT", "estado TEXT DEFAULT 'activo'", "empresa_id INTEGER DEFAULT 1",
        "creado_por INTEGER", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "bloqueos_habitacion_recepcion", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS puntos_venta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            tipo TEXT,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            almacen_id INTEGER,
            imprimir_comanda INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1
        )
        """
    )
    for col in [
        "nombre TEXT", "tipo TEXT", "departamento_id INTEGER", "subdepartamento_id INTEGER",
        "almacen_id INTEGER", "imprimir_comanda INTEGER DEFAULT 0",
        "activo INTEGER DEFAULT 1", "empresa_id INTEGER DEFAULT 1",
    ]:
        _add_column_if_missing(conn, "puntos_venta", col)
    conn.execute(
        """
        INSERT INTO puntos_venta (nombre, tipo, activo, empresa_id)
        SELECT 'Restaurante principal', 'restaurante', 1, ?
        WHERE NOT EXISTS (
            SELECT 1 FROM puntos_venta WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        )
        """,
        (_empresa_id(), _empresa_id()),
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_servicios_establecimiento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT NOT NULL,
            nombre TEXT NOT NULL,
            departamento TEXT DEFAULT 'cocina',
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            UNIQUE(codigo, empresa_id)
        )
        """
    )
    for codigo, nombre, departamento in [
        ("desayuno", "Desayuno", "cocina"),
        ("comida", "Comida", "cocina"),
        ("cena", "Cena", "cocina"),
        ("bebidas_snacks", "Bebidas / snacks", "bar"),
    ]:
        conn.execute(
            """
            INSERT OR IGNORE INTO recepcion_servicios_establecimiento
            (codigo, nombre, departamento, empresa_id)
            VALUES (?, ?, ?, ?)
            """,
            (codigo, nombre, departamento, _empresa_id()),
        )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_reservas_restaurante (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reserva_id INTEGER,
            punto_venta_id INTEGER,
            fecha TEXT,
            hora TEXT,
            turno TEXT,
            personas INTEGER DEFAULT 1,
            nombre_contacto TEXT,
            telefono_contacto TEXT,
            mesa TEXT,
            estado TEXT DEFAULT 'pendiente',
            alergias_resumen TEXT,
            origen TEXT DEFAULT 'recepcion',
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "reserva_id INTEGER", "punto_venta_id INTEGER", "fecha TEXT", "hora TEXT", "turno TEXT",
        "personas INTEGER DEFAULT 1", "nombre_contacto TEXT", "telefono_contacto TEXT", "mesa TEXT",
        "estado TEXT DEFAULT 'pendiente'", "alergias_resumen TEXT", "origen TEXT DEFAULT 'recepcion'",
        "observaciones TEXT", "empresa_id INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "recepcion_reservas_restaurante", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_cargos_reserva (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reserva_id INTEGER NOT NULL,
            punto_venta_id INTEGER,
            restaurante_reserva_id INTEGER,
            fecha TEXT DEFAULT CURRENT_DATE,
            servicio TEXT,
            concepto TEXT NOT NULL,
            cantidad REAL DEFAULT 1,
            precio_unitario REAL DEFAULT 0,
            total REAL DEFAULT 0,
            incluido INTEGER DEFAULT 0,
            cargo_habitacion INTEGER DEFAULT 1,
            factura_id INTEGER,
            estado TEXT DEFAULT 'pendiente',
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            usuario_id INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "reserva_id INTEGER", "punto_venta_id INTEGER", "restaurante_reserva_id INTEGER",
        "fecha TEXT DEFAULT CURRENT_DATE", "servicio TEXT", "concepto TEXT", "cantidad REAL DEFAULT 1",
        "precio_unitario REAL DEFAULT 0", "total REAL DEFAULT 0", "incluido INTEGER DEFAULT 0",
        "cargo_habitacion INTEGER DEFAULT 1", "factura_id INTEGER", "estado TEXT DEFAULT 'pendiente'", "observaciones TEXT",
        "empresa_id INTEGER DEFAULT 1", "usuario_id INTEGER", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "recepcion_cargos_reserva", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_impuestos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT NOT NULL,
            nombre TEXT NOT NULL,
            jurisdiccion TEXT DEFAULT 'general',
            porcentaje REAL DEFAULT 0,
            descripcion TEXT,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "codigo TEXT", "nombre TEXT", "jurisdiccion TEXT DEFAULT 'general'",
        "porcentaje REAL DEFAULT 0", "descripcion TEXT", "activo INTEGER DEFAULT 1",
        "empresa_id INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP", "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "recepcion_impuestos", col)
    _seed_impuestos(conn, _empresa_id())

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_config_fiscal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            territorio TEXT DEFAULT 'canarias',
            impuesto_estancia_id INTEGER,
            impuesto_extras_id INTEGER,
            serie_factura TEXT DEFAULT 'REC',
            serie_proforma TEXT DEFAULT 'PRO',
            serie_rectificativa TEXT DEFAULT 'REC-R',
            modo_verifactu TEXT DEFAULT 'preparado_no_enviado',
            modo_factura_electronica TEXT DEFAULT 'pendiente_configuracion',
            modo_registro_viajeros TEXT DEFAULT 'ses_hospedajes_manual',
            registro_viajeros_alerta_hora TEXT DEFAULT '22:00',
            registro_viajeros_alerta_dias INTEGER DEFAULT 1,
            edad_min_documento INTEGER DEFAULT 14,
            responsable_tratamiento TEXT,
            delegado_proteccion_datos TEXT,
            texto_pie_factura TEXT,
            observaciones TEXT,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "empresa_id INTEGER DEFAULT 1", "territorio TEXT DEFAULT 'canarias'",
        "impuesto_estancia_id INTEGER", "impuesto_extras_id INTEGER",
        "serie_factura TEXT DEFAULT 'REC'", "serie_proforma TEXT DEFAULT 'PRO'",
        "serie_rectificativa TEXT DEFAULT 'REC-R'",
        "modo_verifactu TEXT DEFAULT 'preparado_no_enviado'",
        "modo_factura_electronica TEXT DEFAULT 'pendiente_configuracion'",
        "modo_registro_viajeros TEXT DEFAULT 'ses_hospedajes_manual'",
        "registro_viajeros_alerta_hora TEXT DEFAULT '22:00'",
        "registro_viajeros_alerta_dias INTEGER DEFAULT 1",
        "edad_min_documento INTEGER DEFAULT 14",
        "responsable_tratamiento TEXT", "delegado_proteccion_datos TEXT",
        "texto_pie_factura TEXT", "observaciones TEXT",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP", "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "recepcion_config_fiscal", col)
    _seed_config_fiscal(conn, _empresa_id())

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_registro_viajeros_alertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_objetivo TEXT NOT NULL,
            hora_programada TEXT DEFAULT '22:00',
            estado TEXT DEFAULT 'preparada',
            total_viajeros INTEGER DEFAULT 0,
            preparados INTEGER DEFAULT 0,
            incompletos INTEGER DEFAULT 0,
            reservas_sin_viajeros INTEGER DEFAULT 0,
            resumen TEXT,
            empresa_id INTEGER DEFAULT 1,
            usuario_id INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "fecha_objetivo TEXT", "hora_programada TEXT DEFAULT '22:00'", "estado TEXT DEFAULT 'preparada'",
        "total_viajeros INTEGER DEFAULT 0", "preparados INTEGER DEFAULT 0", "incompletos INTEGER DEFAULT 0",
        "reservas_sin_viajeros INTEGER DEFAULT 0", "resumen TEXT",
        "empresa_id INTEGER DEFAULT 1", "usuario_id INTEGER", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "recepcion_registro_viajeros_alertas", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_registro_viajeros_envios_demo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_objetivo TEXT NOT NULL,
            destinatario TEXT DEFAULT 'demo@nexoraia.studio',
            estado TEXT DEFAULT 'pendiente',
            total_viajeros INTEGER DEFAULT 0,
            enviados INTEGER DEFAULT 0,
            incompletos INTEGER DEFAULT 0,
            reservas_sin_viajeros INTEGER DEFAULT 0,
            resumen TEXT,
            error TEXT,
            empresa_id INTEGER DEFAULT 1,
            usuario_id INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "fecha_objetivo TEXT", "destinatario TEXT DEFAULT 'demo@nexoraia.studio'",
        "estado TEXT DEFAULT 'pendiente'", "total_viajeros INTEGER DEFAULT 0",
        "enviados INTEGER DEFAULT 0", "incompletos INTEGER DEFAULT 0",
        "reservas_sin_viajeros INTEGER DEFAULT 0", "resumen TEXT", "error TEXT",
        "empresa_id INTEGER DEFAULT 1", "usuario_id INTEGER", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "recepcion_registro_viajeros_envios_demo", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_facturas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reserva_id INTEGER NOT NULL,
            tipo TEXT DEFAULT 'factura',
            serie TEXT DEFAULT 'REC',
            ejercicio INTEGER,
            numero INTEGER,
            numero_completo TEXT,
            fecha TEXT DEFAULT CURRENT_DATE,
            cliente_nombre TEXT,
            cliente_nif TEXT,
            cliente_direccion TEXT,
            cliente_email TEXT,
            idioma TEXT DEFAULT 'es',
            impuesto_jurisdiccion TEXT,
            factura_rectificada_id INTEGER,
            motivo_rectificacion TEXT,
            base REAL DEFAULT 0,
            impuestos REAL DEFAULT 0,
            total REAL DEFAULT 0,
            pagado REAL DEFAULT 0,
            saldo REAL DEFAULT 0,
            estado TEXT DEFAULT 'emitida',
            observaciones TEXT,
            hash_factura TEXT,
            hash_anterior TEXT,
            qr_texto TEXT,
            verifactu_estado TEXT DEFAULT 'pendiente',
            verifactu_payload TEXT,
            empresa_id INTEGER DEFAULT 1,
            usuario_id INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "reserva_id INTEGER", "tipo TEXT DEFAULT 'factura'", "serie TEXT DEFAULT 'REC'", "ejercicio INTEGER",
        "numero INTEGER", "numero_completo TEXT", "fecha TEXT DEFAULT CURRENT_DATE", "cliente_nombre TEXT",
        "cliente_nif TEXT", "cliente_direccion TEXT", "cliente_email TEXT", "idioma TEXT DEFAULT 'es'",
        "impuesto_jurisdiccion TEXT", "factura_rectificada_id INTEGER", "motivo_rectificacion TEXT",
        "base REAL DEFAULT 0", "impuestos REAL DEFAULT 0",
        "total REAL DEFAULT 0", "pagado REAL DEFAULT 0", "saldo REAL DEFAULT 0",
        "estado TEXT DEFAULT 'emitida'", "observaciones TEXT", "empresa_id INTEGER DEFAULT 1",
        "hash_factura TEXT", "hash_anterior TEXT", "qr_texto TEXT",
        "verifactu_estado TEXT DEFAULT 'pendiente'", "verifactu_payload TEXT",
        "usuario_id INTEGER", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP", "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "recepcion_facturas", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_factura_lineas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factura_id INTEGER NOT NULL,
            origen TEXT,
            origen_id INTEGER,
            concepto TEXT NOT NULL,
            cantidad REAL DEFAULT 1,
            precio_unitario REAL DEFAULT 0,
            base REAL DEFAULT 0,
            impuesto_pct REAL DEFAULT 0,
            impuesto_id INTEGER,
            impuesto_codigo TEXT,
            impuesto_nombre TEXT,
            impuesto REAL DEFAULT 0,
            total REAL DEFAULT 0,
            orden INTEGER DEFAULT 0,
            empresa_id INTEGER DEFAULT 1
        )
        """
    )
    for col in [
        "factura_id INTEGER", "origen TEXT", "origen_id INTEGER", "concepto TEXT",
        "cantidad REAL DEFAULT 1", "precio_unitario REAL DEFAULT 0", "base REAL DEFAULT 0",
        "impuesto_pct REAL DEFAULT 0", "impuesto_id INTEGER", "impuesto_codigo TEXT", "impuesto_nombre TEXT",
        "impuesto REAL DEFAULT 0", "total REAL DEFAULT 0",
        "orden INTEGER DEFAULT 0", "empresa_id INTEGER DEFAULT 1",
    ]:
        _add_column_if_missing(conn, "recepcion_factura_lineas", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_tarifas_tipo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo_habitacion_id INTEGER NOT NULL,
            regimen TEXT DEFAULT 'solo_alojamiento',
            fecha_inicio TEXT NOT NULL,
            fecha_fin TEXT NOT NULL,
            precio_noche REAL DEFAULT 0,
            prioridad INTEGER DEFAULT 10,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "tipo_habitacion_id INTEGER", "regimen TEXT DEFAULT 'solo_alojamiento'",
        "fecha_inicio TEXT", "fecha_fin TEXT", "precio_noche REAL DEFAULT 0",
        "prioridad INTEGER DEFAULT 10", "activo INTEGER DEFAULT 1", "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP", "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "recepcion_tarifas_tipo", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_reglas_precio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            tipo_habitacion_id INTEGER,
            regimen TEXT,
            aplicar_desde_fecha TEXT,
            aplicar_desde_hora TEXT,
            fecha_inicio_estancia TEXT,
            fecha_fin_estancia TEXT,
            ocupacion_min_pct REAL,
            ocupacion_max_pct REAL,
            ajuste_tipo TEXT DEFAULT 'porcentaje',
            ajuste_valor REAL DEFAULT 0,
            motivo TEXT,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "nombre TEXT", "tipo_habitacion_id INTEGER", "regimen TEXT", "aplicar_desde_fecha TEXT",
        "aplicar_desde_hora TEXT", "fecha_inicio_estancia TEXT", "fecha_fin_estancia TEXT",
        "ocupacion_min_pct REAL", "ocupacion_max_pct REAL",
        "ajuste_tipo TEXT DEFAULT 'porcentaje'", "ajuste_valor REAL DEFAULT 0", "motivo TEXT",
        "activo INTEGER DEFAULT 1", "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP", "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "recepcion_reglas_precio", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_politicas_disponibilidad (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            nombre TEXT DEFAULT 'Politica operativa',
            colchon_pct REAL DEFAULT 0,
            colchon_min_habitaciones INTEGER DEFAULT 0,
            aplicar_canales INTEGER DEFAULT 1,
            aplicar_directo INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1,
            observaciones TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(empresa_id, centro_id)
        )
        """
    )
    for col in [
        "empresa_id INTEGER DEFAULT 1", "centro_id INTEGER", "nombre TEXT DEFAULT 'Politica operativa'",
        "colchon_pct REAL DEFAULT 0", "colchon_min_habitaciones INTEGER DEFAULT 0",
        "aplicar_canales INTEGER DEFAULT 1", "aplicar_directo INTEGER DEFAULT 0",
        "activo INTEGER DEFAULT 1", "observaciones TEXT", "updated_at TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "recepcion_politicas_disponibilidad", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recepcion_regimen_inclusiones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            regimen TEXT NOT NULL,
            servicio TEXT NOT NULL,
            tipo TEXT DEFAULT 'alimento',
            concepto TEXT NOT NULL,
            incluido INTEGER DEFAULT 1,
            limite TEXT,
            condiciones TEXT,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "regimen TEXT", "servicio TEXT", "tipo TEXT DEFAULT 'alimento'", "concepto TEXT",
        "incluido INTEGER DEFAULT 1", "limite TEXT", "condiciones TEXT", "activo INTEGER DEFAULT 1",
        "empresa_id INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "recepcion_regimen_inclusiones", col)
    _seed_regimen_inclusiones(conn, _empresa_id())
    conn.commit()


def _generar_codigo_reserva(conn, empresa_id):
    prefijo = f"R{date.today().strftime('%Y%m')}"
    row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM reservas_recepcion
        WHERE empresa_id = ? AND codigo LIKE ?
        """,
        (empresa_id, f"{prefijo}%"),
    ).fetchone()
    return f"{prefijo}-{(row['total'] or 0) + 1:04d}"


def _servicios_por_regimen(regimen):
    regimen = regimen or "solo_alojamiento"
    if regimen == "alojamiento_desayuno":
        return ["desayuno"]
    if regimen == "media_pension":
        return ["desayuno", "cena"]
    if regimen == "pension_completa":
        return ["desayuno", "comida", "cena"]
    if regimen == "todo_incluido":
        return ["desayuno", "comida", "cena", "bebidas_snacks"]
    return []


def _servicio_aplica_fecha(servicio, entrada, salida, fecha_obj):
    if not entrada or not salida:
        return False
    if servicio == "desayuno":
        return entrada < fecha_obj <= salida
    return entrada <= fecha_obj < salida


def _nombre_habitacion(row):
    if not row:
        return "Sin habitacion"
    nombre = row["habitacion_nombre"] if "habitacion_nombre" in row.keys() else None
    numero = row["habitacion_numero"] if "habitacion_numero" in row.keys() else None
    return numero or nombre or "Sin habitacion"


def _reserva_por_id(conn, reserva_id, empresa_id):
    centro_sql, centro_params = _centro_sql_reserva("r")
    return conn.execute(
        f"""
        SELECT r.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre, h.tipo AS habitacion_tipo,
               h.capacidad AS habitacion_capacidad, h.bloque AS habitacion_bloque, h.planta AS habitacion_planta,
               eq.tipo_cama AS habitacion_tipo_cama, eq.supletoria_posible AS habitacion_supletoria_posible,
               eq.supletorias_max AS habitacion_supletorias_max, eq.cuna_posible AS habitacion_cuna_posible,
               eq.tv AS habitacion_tv, eq.minibar AS habitacion_minibar, eq.aire_tipo AS habitacion_aire_tipo,
               cr.nombre AS canal_nombre, cr.tipo AS canal_tipo
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        LEFT JOIN habitaciones_equipamiento eq ON h.id = eq.habitacion_id AND eq.empresa_id = r.empresa_id
        LEFT JOIN recepcion_canales_reserva cr ON r.canal_id = cr.id
        WHERE r.id = ? AND r.empresa_id = ?
          {centro_sql}
        """,
        [reserva_id, empresa_id] + centro_params,
    ).fetchone()


def _cliente_fiscal_reserva(conn, reserva, empresa_id):
    reserva_id = reserva["id"] if reserva and "id" in reserva.keys() else None
    viajero = None
    if reserva_id:
        viajero = conn.execute(
            """
            SELECT nombre, apellidos, tipo_documento, numero_documento, email, direccion,
                   codigo_postal, localidad, pais, es_titular
            FROM viajeros_recepcion
            WHERE reserva_id = ? AND empresa_id = ?
            ORDER BY COALESCE(es_titular, 0) DESC, id
            LIMIT 1
            """,
            (reserva_id, empresa_id),
        ).fetchone()

    if viajero:
        nombre = " ".join(
            parte.strip()
            for parte in [viajero["nombre"] or "", viajero["apellidos"] or ""]
            if parte and parte.strip()
        )
        direccion = ", ".join(
            parte.strip()
            for parte in [
                viajero["direccion"] or "",
                " ".join(
                    p.strip()
                    for p in [viajero["codigo_postal"] or "", viajero["localidad"] or ""]
                    if p and p.strip()
                ),
                viajero["pais"] or "",
            ]
            if parte and parte.strip()
        )
        return {
            "nombre": nombre or (reserva["titular_nombre"] if reserva and "titular_nombre" in reserva.keys() else "") or "",
            "nif": viajero["numero_documento"] or "",
            "tipo_documento": viajero["tipo_documento"] or "",
            "email": viajero["email"] or (reserva["titular_email"] if reserva and "titular_email" in reserva.keys() else "") or "",
            "direccion": direccion,
            "origen": "viajero_titular" if viajero["es_titular"] else "viajero",
        }

    return {
        "nombre": (reserva["titular_nombre"] if reserva and "titular_nombre" in reserva.keys() else "") or "",
        "nif": "",
        "tipo_documento": "",
        "email": (reserva["titular_email"] if reserva and "titular_email" in reserva.keys() else "") or "",
        "direccion": "",
        "origen": "reserva",
    }


def _habitaciones_select(conn, empresa_id):
    centro_sql, centro_params = _centro_sql_habitacion("h")
    return conn.execute(
        f"""
        SELECT h.*, th.codigo AS tipo_codigo, th.nombre AS tipo_nombre,
               th.capacidad_base AS tipo_capacidad_base, th.capacidad_max AS tipo_capacidad_max,
               eq.tipo_cama, eq.supletoria_posible, eq.supletorias_max, eq.cuna_posible, eq.aire_tipo
        FROM habitaciones_recepcion h
        LEFT JOIN recepcion_tipos_habitacion th ON h.tipo_habitacion_id = th.id
        LEFT JOIN habitaciones_equipamiento eq ON h.id = eq.habitacion_id AND eq.empresa_id = h.empresa_id
        WHERE h.empresa_id = ? AND COALESCE(h.activo, 1) = 1
          {centro_sql}
        ORDER BY h.bloque, h.planta, h.numero, h.nombre
        """,
        [empresa_id] + centro_params,
    ).fetchall()


def _tipos_habitacion_select(conn, empresa_id, solo_activos=True):
    filtro = "AND COALESCE(activo, 1) = 1" if solo_activos else ""
    return conn.execute(
        f"""
        SELECT *
        FROM recepcion_tipos_habitacion
        WHERE empresa_id = ?
        {filtro}
        ORDER BY nombre, codigo
        """,
        (empresa_id,),
    ).fetchall()


def _tipo_habitacion_importado(conn, row, empresa_id):
    tipo_id = _to_int(_row_get(row, "tipo_habitacion_id", "id_tipo"), 0)
    if tipo_id:
        found = conn.execute(
            "SELECT id FROM recepcion_tipos_habitacion WHERE id = ? AND empresa_id = ?",
            (tipo_id, empresa_id),
        ).fetchone()
        if found:
            return found["id"]
    codigo = (_row_get(row, "tipo_codigo", "codigo_tipo", "codigo") or "").strip().upper()
    nombre = (_row_get(row, "tipo_nombre", "nombre_tipo", "tipo_habitacion", "tipo") or "").strip()
    if codigo:
        found = conn.execute(
            """
            SELECT id
            FROM recepcion_tipos_habitacion
            WHERE empresa_id = ? AND UPPER(TRIM(codigo)) = ?
            LIMIT 1
            """,
            (empresa_id, codigo),
        ).fetchone()
        if found:
            return found["id"]
    if nombre:
        found = conn.execute(
            """
            SELECT id
            FROM recepcion_tipos_habitacion
            WHERE empresa_id = ? AND LOWER(TRIM(nombre)) = LOWER(TRIM(?))
            LIMIT 1
            """,
            (empresa_id, nombre),
        ).fetchone()
        if found:
            return found["id"]
    if not codigo and not nombre:
        return None
    codigo = codigo or _csv_key(nombre).upper()[:20] or "TIPO"
    nombre = nombre or codigo
    suffix = 1
    base_codigo = codigo[:18]
    final_codigo = codigo[:20]
    while conn.execute(
        "SELECT id FROM recepcion_tipos_habitacion WHERE empresa_id = ? AND UPPER(TRIM(codigo)) = ?",
        (empresa_id, final_codigo),
    ).fetchone():
        suffix += 1
        final_codigo = f"{base_codigo}{suffix}"[:20]
    conn.execute(
        """
        INSERT INTO recepcion_tipos_habitacion
        (codigo, nombre, capacidad_base, capacidad_max, descripcion, activo, empresa_id)
        VALUES (?, ?, 2, 2, 'Creado desde importacion de tarifas', 1, ?)
        """,
        (final_codigo, nombre, empresa_id),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _puntos_venta_select(conn, empresa_id):
    if not _table_exists(conn, "puntos_venta"):
        return []
    return conn.execute(
        """
        SELECT id, nombre, tipo
        FROM puntos_venta
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY CASE WHEN LOWER(COALESCE(tipo, '')) IN ('restaurante', 'carta') THEN 0 ELSE 1 END, nombre
        """,
        (empresa_id,),
    ).fetchall()


def _canales_reserva_select(conn, empresa_id, solo_activos=True):
    filtro = "AND COALESCE(activo, 1) = 1" if solo_activos else ""
    return conn.execute(
        f"""
        SELECT *
        FROM recepcion_canales_reserva
        WHERE empresa_id = ?
        {filtro}
        ORDER BY tipo, nombre
        """,
        (empresa_id,),
    ).fetchall()


def _canal_por_id(conn, canal_id, empresa_id):
    return conn.execute(
        """
        SELECT *
        FROM recepcion_canales_reserva
        WHERE id = ? AND empresa_id = ?
        """,
        (canal_id, empresa_id),
    ).fetchone()


def _cupos_canal_select(conn, empresa_id):
    return conn.execute(
        """
        SELECT cc.*, c.nombre AS canal_nombre, c.codigo AS canal_codigo,
               th.nombre AS tipo_nombre, th.codigo AS tipo_codigo
        FROM recepcion_cupos_canal cc
        JOIN recepcion_canales_reserva c ON cc.canal_id = c.id
        LEFT JOIN recepcion_tipos_habitacion th ON cc.tipo_habitacion_id = th.id
        WHERE cc.empresa_id = ?
        ORDER BY date(cc.fecha_inicio) DESC, c.nombre, th.nombre
        """,
        (empresa_id,),
    ).fetchall()


def _ultimas_sincronizaciones_canal(conn, empresa_id, limite=20):
    return conn.execute(
        """
        SELECT s.*, c.nombre AS canal_nombre, c.codigo AS canal_codigo
        FROM recepcion_sincronizaciones_canal s
        JOIN recepcion_canales_reserva c ON s.canal_id = c.id
        WHERE s.empresa_id = ?
        ORDER BY s.id DESC
        LIMIT ?
        """,
        (empresa_id, limite),
    ).fetchall()


def _csv_key(value):
    text = (value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    for src, dst in [
        ("Ã¡", "a"), ("Ã ", "a"), ("Ã¤", "a"), ("Ã¢", "a"),
        ("Ã©", "e"), ("Ã¨", "e"), ("Ã«", "e"), ("Ãª", "e"),
        ("Ã­", "i"), ("Ã¬", "i"), ("Ã¯", "i"), ("Ã®", "i"),
        ("Ã³", "o"), ("Ã²", "o"), ("Ã¶", "o"), ("Ã´", "o"),
        ("Ãº", "u"), ("Ã¹", "u"), ("Ã¼", "u"), ("Ã»", "u"),
        ("Ã±", "n"), ("Ã§", "c"),
    ]:
        text = text.replace(src, dst)
    for char in (" ", "-", ".", "/", "\\", "(", ")"):
        text = text.replace(char, "_")
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def _csv_row_normalizada(row):
    return {_csv_key(k): (v or "").strip() for k, v in (row or {}).items()}


def _row_get(row, *keys, default=""):
    for key in keys:
        value = row.get(_csv_key(key))
        if value not in (None, ""):
            return value
    return default


def _csv_rows_from_upload(archivo):
    raw = archivo.read()
    if not raw:
        raise ValueError("El archivo esta vacio.")
    try:
        texto = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = raw.decode("latin-1")
    try:
        dialect = csv.Sniffer().sniff(texto[:4096], delimiters=";,\t")
        reader = csv.DictReader(StringIO(texto), dialect=dialect)
    except Exception:
        reader = csv.DictReader(StringIO(texto), delimiter=";")
    if not reader.fieldnames:
        raise ValueError("El CSV no tiene cabeceras.")
    return [_csv_row_normalizada(row) for row in reader if any((v or "").strip() for v in row.values())]


def _bool_importado(value, default=1):
    text = _csv_key(value)
    if text in {"1", "si", "s", "yes", "y", "true", "activo", "activa", "incluido", "incluida"}:
        return 1
    if text in {"0", "no", "n", "false", "inactivo", "inactiva", "excluido", "excluida"}:
        return 0
    return default


def _csv_response(filename, headers, rows):
    output = StringIO()
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _regimen_desde_texto(value):
    text = _csv_key(value)
    if text in {"sa", "solo", "solo_alojamiento", "room_only", "alojamiento"}:
        return "solo_alojamiento"
    if text in {"ad", "bb", "b_b", "bed_breakfast", "bed_and_breakfast", "alojamiento_desayuno", "alojamiento_y_desayuno", "alojamiento_con_desayuno", "desayuno"}:
        return "alojamiento_desayuno"
    if text in {"mp", "hb", "half_board", "media_pension"}:
        return "media_pension"
    if text in {"pc", "fb", "full_board", "pension_completa"}:
        return "pension_completa"
    if text in {"ti", "ai", "all_inclusive", "todo_incluido"}:
        return "todo_incluido"
    return "solo_alojamiento"


def _estado_reserva_importado(value):
    text = _csv_key(value)
    if text in {"cancelada", "cancelado", "cancelled", "canceled", "anulada", "anulado"}:
        return "cancelada"
    if text in {"no_show", "noshow", "no_presentado", "no_presentada"}:
        return "no_show"
    if text in {"checkout", "check_out", "salida"}:
        return "checkout"
    if text in {"checkin", "check_in", "alojado", "alojada", "in_house"}:
        return "alojado"
    if text in {"pendiente", "pending", "request"}:
        return "pendiente"
    return "confirmada"


def _reservas_activas_select(conn, empresa_id):
    return conn.execute(
        """
        SELECT r.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE r.empresa_id = ?
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
        ORDER BY date(r.fecha_entrada) DESC, r.titular_nombre, r.codigo
        LIMIT 500
        """,
        (empresa_id,),
    ).fetchall()


def _turno_por_hora(hora):
    try:
        hh = int((hora or "00:00")[:2])
    except Exception:
        return "cena"
    if hh < 12:
        return "desayuno"
    if hh < 18:
        return "comida"
    return "cena"


def _tipo_habitacion_por_codigo(conn, empresa_id, codigos):
    placeholders = ",".join("?" for _ in codigos)
    if not placeholders:
        return None
    return conn.execute(
        f"""
        SELECT id
        FROM recepcion_tipos_habitacion
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND UPPER(TRIM(codigo)) IN ({placeholders})
        ORDER BY id
        LIMIT 1
        """,
        [empresa_id] + [c.upper() for c in codigos],
    ).fetchone()


def _inferir_tipo_habitacion_id(conn, habitacion, empresa_id):
    if not habitacion:
        return None
    texto = _csv_key(f"{habitacion['tipo'] or ''} {habitacion['nombre'] or ''} {habitacion['numero'] or ''}")
    capacidad = max(_to_int(habitacion["capacidad"] if "capacidad" in habitacion.keys() else 2, 2), 1)
    reglas = [
        (("suite", "junior"), ["SUI"]),
        (("individual", "single", "sencilla"), ["SGL"]),
        (("triple", "tpl"), ["TPL"]),
        (("cuadruple", "quad", "apartamento", "apartment"), ["CUA", "SUI"]),
        (("doble", "double", "dbl"), ["DBL"]),
    ]
    for palabras, codigos in reglas:
        if any(p in texto for p in palabras):
            row = _tipo_habitacion_por_codigo(conn, empresa_id, codigos)
            if row:
                return row["id"]
    row = conn.execute(
        """
        SELECT id
        FROM recepcion_tipos_habitacion
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
        ORDER BY
          CASE WHEN COALESCE(capacidad_base, 0) = ? THEN 0 ELSE 1 END,
          CASE WHEN COALESCE(capacidad_max, capacidad_base, 0) >= ? THEN 0 ELSE 1 END,
          ABS(COALESCE(capacidad_base, 2) - ?),
          COALESCE(capacidad_max, capacidad_base, 2),
          id
        LIMIT 1
        """,
        (empresa_id, capacidad, capacidad, capacidad),
    ).fetchone()
    return row["id"] if row else None


def _habitacion_tipo_id(conn, habitacion_id, empresa_id):
    if not habitacion_id:
        return None
    row = conn.execute(
        """
        SELECT id, tipo_habitacion_id, tipo, nombre, numero, capacidad
        FROM habitaciones_recepcion
        WHERE id = ? AND empresa_id = ?
        """,
        (habitacion_id, empresa_id),
    ).fetchone()
    if not row:
        return None
    if row["tipo_habitacion_id"]:
        return row["tipo_habitacion_id"]
    tipo_id = _inferir_tipo_habitacion_id(conn, row, empresa_id)
    if tipo_id:
        conn.execute(
            "UPDATE habitaciones_recepcion SET tipo_habitacion_id = ? WHERE id = ? AND empresa_id = ?",
            (tipo_id, habitacion_id, empresa_id),
        )
    return tipo_id


def _habitacion_por_numero(conn, numero, empresa_id):
    numero = (numero or "").strip()
    if not numero:
        return None
    return conn.execute(
        """
        SELECT *
        FROM habitaciones_recepcion
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND (TRIM(numero) = ? OR TRIM(COALESCE(nombre, '')) = ?)
        ORDER BY id
        LIMIT 1
        """,
        (empresa_id, numero, numero),
    ).fetchone()


def _conflictos_habitacion(conn, habitacion_id, entrada, salida, empresa_id, reserva_id=None):
    if not habitacion_id:
        return []
    entrada_dt = _parse_date(entrada)
    salida_dt = _parse_date(salida)
    if not entrada_dt or not salida_dt or salida_dt <= entrada_dt:
        return []
    params = [habitacion_id, empresa_id, salida_dt.isoformat(), entrada_dt.isoformat()]
    extra = ""
    if reserva_id:
        extra = "AND r.id <> ?"
        params.append(reserva_id)
    reservas = conn.execute(
        f"""
        SELECT r.id, r.codigo, r.titular_nombre, r.fecha_entrada, r.fecha_salida, r.estado
        FROM reservas_recepcion r
        WHERE r.habitacion_id = ?
          AND r.empresa_id = ?
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND date(r.fecha_entrada) < date(?)
          AND date(r.fecha_salida) > date(?)
          {extra}
        ORDER BY date(r.fecha_entrada), r.id
        """,
        params,
    ).fetchall()
    bloqueos = conn.execute(
        """
        SELECT id, motivo, descripcion, fecha_inicio, fecha_fin, estado
        FROM bloqueos_habitacion_recepcion
        WHERE habitacion_id = ?
          AND empresa_id = ?
          AND COALESCE(estado, 'activo') = 'activo'
          AND date(fecha_inicio) < date(?)
          AND date(fecha_fin) > date(?)
        ORDER BY date(fecha_inicio), id
        """,
        (habitacion_id, empresa_id, salida_dt.isoformat(), entrada_dt.isoformat()),
    ).fetchall()
    conflictos = []
    for row in reservas:
        conflictos.append({"tipo": "reserva", "row": row})
    for row in bloqueos:
        conflictos.append({"tipo": "bloqueo", "row": row})
    return conflictos


def _descripcion_conflictos_habitacion(conflictos):
    partes = []
    for item in conflictos[:3]:
        row = item["row"]
        if item["tipo"] == "reserva":
            partes.append(
                f"reserva {row['codigo'] or ('#' + str(row['id']))} "
                f"({row['fecha_entrada']} a {row['fecha_salida']})"
            )
        else:
            partes.append(
                f"bloqueo {row['motivo'] or ('#' + str(row['id']))} "
                f"({row['fecha_inicio']} a {row['fecha_fin']})"
            )
    if len(conflictos) > 3:
        partes.append(f"{len(conflictos) - 3} mas")
    return "; ".join(partes)


def _habitacion_disponible(conn, habitacion_id, entrada, salida, empresa_id, reserva_id=None):
    if not habitacion_id:
        return True
    return not _conflictos_habitacion(conn, habitacion_id, entrada, salida, empresa_id, reserva_id)


def _habitacion_libre_para_rango(conn, habitacion_id, entrada, salida, empresa_id, excluir_reservas=None):
    excluir_reservas = [int(r) for r in (excluir_reservas or []) if r]
    params = [habitacion_id, empresa_id, salida, entrada]
    extra = ""
    if excluir_reservas:
        extra = f"AND r.id NOT IN ({','.join('?' for _ in excluir_reservas)})"
        params.extend(excluir_reservas)
    reserva = conn.execute(
        f"""
        SELECT 1
        FROM reservas_recepcion r
        WHERE r.habitacion_id = ?
          AND r.empresa_id = ?
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND date(r.fecha_entrada) < date(?)
          AND date(r.fecha_salida) > date(?)
          {extra}
        LIMIT 1
        """,
        params,
    ).fetchone()
    if reserva:
        return False
    bloqueo = conn.execute(
        """
        SELECT 1
        FROM bloqueos_habitacion_recepcion b
        WHERE b.habitacion_id = ?
          AND b.empresa_id = ?
          AND COALESCE(b.estado, 'activo') = 'activo'
          AND date(b.fecha_inicio) < date(?)
          AND date(b.fecha_fin) > date(?)
        LIMIT 1
        """,
        (habitacion_id, empresa_id, salida, entrada),
    ).fetchone()
    return bloqueo is None


def _buscar_habitacion_alternativa(conn, reserva, empresa_id, centro_id=None, permitir_upgrade=False, excluir_reservas=None):
    entrada = reserva["fecha_entrada"]
    salida = reserva["fecha_salida"]
    pax = max(1, _to_int(reserva["adultos"], 1) + _to_int(reserva["menores"], 0))
    tipo_id = reserva["tipo_habitacion_id"] if "tipo_habitacion_id" in reserva.keys() else None
    filtros = ["h.empresa_id = ?", "COALESCE(h.activo, 1) = 1", "COALESCE(h.capacidad, 1) >= ?"]
    params = [empresa_id, pax]
    if centro_id:
        filtros.append("h.centro_id = ?")
        params.append(centro_id)
    if tipo_id and not permitir_upgrade:
        filtros.append("h.tipo_habitacion_id = ?")
        params.append(tipo_id)
    habitaciones = conn.execute(
        f"""
        SELECT h.*, th.nombre AS tipo_nombre, th.codigo AS tipo_codigo, ct.nombre AS centro_nombre
        FROM habitaciones_recepcion h
        LEFT JOIN recepcion_tipos_habitacion th ON th.id = h.tipo_habitacion_id
        LEFT JOIN centros_trabajo ct ON ct.id = h.centro_id
        WHERE {" AND ".join(filtros)}
        ORDER BY CASE WHEN h.tipo_habitacion_id = ? THEN 0 ELSE 1 END,
                 COALESCE(h.capacidad, 1), h.centro_id, h.numero
        LIMIT 250
        """,
        params + [tipo_id or 0],
    ).fetchall()
    habitacion_origen_id = reserva["habitacion_id"] if "habitacion_id" in reserva.keys() else None
    for habitacion in habitaciones:
        if habitacion_origen_id and habitacion["id"] == habitacion_origen_id:
            continue
        if _habitacion_libre_para_rango(conn, habitacion["id"], entrada, salida, empresa_id, excluir_reservas):
            if tipo_id and permitir_upgrade and habitacion["tipo_habitacion_id"] != tipo_id:
                return habitacion, "upgrade"
            return habitacion, "mismo_tipo"
    return None, None


def _hotel_apoyo_sugerido(conn):
    if not _table_exists(conn, "simulador_hoteles_apoyo"):
        return None
    return conn.execute(
        """
        SELECT *
        FROM simulador_hoteles_apoyo
        WHERE COALESCE(activo, 1) = 1 AND COALESCE(habitaciones_apoyo, 0) > 0
        ORDER BY prioridad, coste_desvio, distancia_km
        LIMIT 1
        """
    ).fetchone()


def _propuesta_resolucion_overbooking(conn, reserva, empresa_id, excluir_reservas=None):
    centro_id = reserva["centro_id"] if "centro_id" in reserva.keys() else centro_activo_id()
    habitacion, _ = _buscar_habitacion_alternativa(
        conn, reserva, empresa_id, centro_id=centro_id, permitir_upgrade=False, excluir_reservas=excluir_reservas
    )
    if habitacion:
        return {
            "estrategia": "reasignacion_interna",
            "prioridad": "alta",
            "destino": f"Hab. {habitacion['numero']} ({habitacion['centro_nombre'] or 'centro actual'})",
            "accion": "Mover una reserva a una habitacion libre del mismo tipo y sincronizar inventario.",
            "coste": 0,
        }
    habitacion, _ = _buscar_habitacion_alternativa(
        conn, reserva, empresa_id, centro_id=centro_id, permitir_upgrade=True, excluir_reservas=excluir_reservas
    )
    if habitacion:
        return {
            "estrategia": "upgrade_interno",
            "prioridad": "alta",
            "destino": f"Hab. {habitacion['numero']} ({habitacion['tipo_nombre'] or 'tipo superior'})",
            "accion": "Aplicar upgrade sin coste, registrar compensacion y cerrar venta externa de ese tipo.",
            "coste": 45,
        }
    habitacion, _ = _buscar_habitacion_alternativa(
        conn, reserva, empresa_id, centro_id=None, permitir_upgrade=True, excluir_reservas=excluir_reservas
    )
    if habitacion and habitacion["centro_id"] != centro_id:
        return {
            "estrategia": "desvio_cadena",
            "prioridad": "critica",
            "destino": f"{habitacion['centro_nombre'] or 'otro centro'} - hab. {habitacion['numero']}",
            "accion": "Confirmar espacio en hotel de la cadena y mantener condiciones pactadas.",
            "coste": 75,
        }
    apoyo = _hotel_apoyo_sugerido(conn)
    if apoyo:
        return {
            "estrategia": "hotel_apoyo_externo",
            "prioridad": "critica",
            "destino": apoyo["nombre"],
            "accion": "Bloquear habitacion en hotel de apoyo antes de contactar al cliente.",
            "coste": apoyo["coste_desvio"] if "coste_desvio" in apoyo.keys() else 120,
        }
    return {
        "estrategia": "pendiente_direccion",
        "prioridad": "critica",
        "destino": "Direccion",
        "accion": "Cerrar canales externos, revisar cupos y buscar alternativa manual.",
        "coste": 180,
    }


def _politica_disponibilidad(conn, empresa_id, centro_id=None):
    if not _table_exists(conn, "recepcion_politicas_disponibilidad"):
        return {
            "nombre": "Sin politica",
            "colchon_pct": 0,
            "colchon_min_habitaciones": 0,
            "aplicar_canales": 1,
            "aplicar_directo": 0,
            "activo": 0,
            "observaciones": "",
        }
    row = conn.execute(
        """
        SELECT *
        FROM recepcion_politicas_disponibilidad
        WHERE empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
        ORDER BY id DESC
        LIMIT 1
        """,
        (empresa_id, centro_id),
    ).fetchone()
    if row:
        return dict(row)
    return {
        "nombre": "Politica operativa",
        "colchon_pct": 0,
        "colchon_min_habitaciones": 0,
        "aplicar_canales": 1,
        "aplicar_directo": 0,
        "activo": 0,
        "observaciones": "",
    }


def _colchon_operativo_habitaciones(politica, total_tipo, canal_tipo=""):
    if not politica or not int(politica.get("activo") or 0):
        return 0
    canal_tipo = (canal_tipo or "").lower()
    es_directo = canal_tipo == "directo"
    if es_directo and not int(politica.get("aplicar_directo") or 0):
        return 0
    if not es_directo and not int(politica.get("aplicar_canales") or 0):
        return 0
    total_tipo = max(0, int(total_tipo or 0))
    pct = max(0.0, _to_float(politica.get("colchon_pct"), 0))
    minimo = max(0, _to_int(politica.get("colchon_min_habitaciones"), 0))
    por_pct = int((total_tipo * pct + 99.999) // 100) if pct > 0 and total_tipo else 0
    por_minimo = 1 if minimo > 0 and total_tipo > 0 else 0
    return min(total_tipo, max(por_pct, por_minimo))


def _control_overbooking_live(conn, empresa_id, desde, dias, centro_id=None):
    hasta = desde + timedelta(days=dias)
    politica = _politica_disponibilidad(conn, empresa_id, centro_id)
    centro_reserva = "AND r.centro_id = ?" if centro_id else ""
    centro_params = [centro_id] if centro_id else []
    sin_asignar = conn.execute(
        f"""
        SELECT r.*, cr.nombre AS canal_nombre, cr.codigo AS canal_codigo
        FROM reservas_recepcion r
        LEFT JOIN recepcion_canales_reserva cr ON cr.id = r.canal_id
        WHERE r.empresa_id = ?
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND r.habitacion_id IS NULL
          AND date(r.fecha_entrada) < date(?)
          AND date(r.fecha_salida) > date(?)
          {centro_reserva}
        ORDER BY date(r.fecha_entrada), r.id
        LIMIT 100
        """,
        [empresa_id, hasta.isoformat(), desde.isoformat()] + centro_params,
    ).fetchall()
    solapes = conn.execute(
        f"""
        SELECT r1.id AS reserva_1_id, r1.codigo AS reserva_1_codigo, r1.titular_nombre AS reserva_1_titular,
               r1.fecha_entrada, r1.fecha_salida, r1.habitacion_id, r1.tipo_habitacion_id,
               r1.adultos, r1.menores, r1.centro_id,
               r2.id AS reserva_2_id, r2.codigo AS reserva_2_codigo, r2.titular_nombre AS reserva_2_titular,
               h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               cr.nombre AS canal_nombre, cr.codigo AS canal_codigo
        FROM reservas_recepcion r1
        JOIN reservas_recepcion r2 ON r1.habitacion_id = r2.habitacion_id AND r1.id < r2.id
        LEFT JOIN habitaciones_recepcion h ON h.id = r1.habitacion_id
        LEFT JOIN recepcion_canales_reserva cr ON cr.id = r1.canal_id
        WHERE r1.empresa_id = ?
          AND r2.empresa_id = ?
          AND r1.habitacion_id IS NOT NULL
          AND COALESCE(r1.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND COALESCE(r2.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND date(r1.fecha_entrada) < date(r2.fecha_salida)
          AND date(r2.fecha_entrada) < date(r1.fecha_salida)
          AND date(r1.fecha_entrada) < date(?)
          AND date(r1.fecha_salida) > date(?)
          {"AND r1.centro_id = ?" if centro_id else ""}
        ORDER BY date(r1.fecha_entrada), h.numero, r1.id
        LIMIT 100
        """,
        [empresa_id, empresa_id, hasta.isoformat(), desde.isoformat()] + ([centro_id] if centro_id else []),
    ).fetchall()
    bloqueos = conn.execute(
        f"""
        SELECT r.id AS reserva_id, r.codigo AS reserva_codigo, r.titular_nombre,
               r.fecha_entrada, r.fecha_salida, r.habitacion_id, r.tipo_habitacion_id,
               r.adultos, r.menores, r.centro_id,
               h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               b.id AS bloqueo_id, b.motivo, b.descripcion, b.fecha_inicio, b.fecha_fin
        FROM reservas_recepcion r
        JOIN bloqueos_habitacion_recepcion b ON b.habitacion_id = r.habitacion_id
        LEFT JOIN habitaciones_recepcion h ON h.id = r.habitacion_id
        WHERE r.empresa_id = ?
          AND b.empresa_id = ?
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND COALESCE(b.estado, 'activo') = 'activo'
          AND date(r.fecha_entrada) < date(b.fecha_fin)
          AND date(b.fecha_inicio) < date(r.fecha_salida)
          AND date(r.fecha_entrada) < date(?)
          AND date(r.fecha_salida) > date(?)
          {"AND r.centro_id = ?" if centro_id else ""}
        ORDER BY date(r.fecha_entrada), h.numero, r.id
        LIMIT 100
        """,
        [empresa_id, empresa_id, hasta.isoformat(), desde.isoformat()] + ([centro_id] if centro_id else []),
    ).fetchall()
    conflictos = []
    for row in solapes:
        propuesta = _propuesta_resolucion_overbooking(
            conn, row, empresa_id, excluir_reservas=[row["reserva_1_id"], row["reserva_2_id"]]
        )
        conflictos.append({"tipo": "doble_venta", "row": dict(row), "propuesta": propuesta})
    for row in bloqueos:
        propuesta = _propuesta_resolucion_overbooking(conn, row, empresa_id, excluir_reservas=[row["reserva_id"]])
        conflictos.append({"tipo": "bloqueo_sobre_reserva", "row": dict(row), "propuesta": propuesta})
    for row in sin_asignar:
        propuesta = _propuesta_resolucion_overbooking(conn, row, empresa_id, excluir_reservas=[row["id"]])
        conflictos.append({"tipo": "sin_asignar", "row": dict(row), "propuesta": propuesta})

    canales = _canales_reserva_select(conn, empresa_id, solo_activos=True)
    tipos = _tipos_habitacion_select(conn, empresa_id)
    riesgos_canal = []
    for i in range(dias):
        fecha = desde + timedelta(days=i)
        fecha_iso = fecha.isoformat()
        for tipo in tipos:
            tipo_id = tipo["id"]
            total = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM habitaciones_recepcion
                WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
                  AND tipo_habitacion_id = ?
                  AND (? IS NULL OR centro_id = ?)
                """,
                (empresa_id, tipo_id, centro_id, centro_id),
            ).fetchone()["total"]
            if not total:
                continue
            bloqueadas = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM bloqueos_habitacion_recepcion b
                JOIN habitaciones_recepcion h ON h.id = b.habitacion_id
                WHERE b.empresa_id = ? AND COALESCE(b.estado, 'activo') = 'activo'
                  AND h.tipo_habitacion_id = ?
                  AND (? IS NULL OR h.centro_id = ?)
                  AND date(b.fecha_inicio) <= date(?) AND date(b.fecha_fin) > date(?)
                """,
                (empresa_id, tipo_id, centro_id, centro_id, fecha_iso, fecha_iso),
            ).fetchone()["total"]
            reservas_activas = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM reservas_recepcion r
                WHERE r.empresa_id = ?
                  AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
                  AND r.tipo_habitacion_id = ?
                  AND (? IS NULL OR r.centro_id = ?)
                  AND date(r.fecha_entrada) <= date(?) AND date(r.fecha_salida) > date(?)
                """,
                (empresa_id, tipo_id, centro_id, centro_id, fecha_iso, fecha_iso),
            ).fetchone()["total"]
            disponibles = max(0, int(total or 0) - int(bloqueadas or 0) - int(reservas_activas or 0))
            for canal in canales:
                canal_tipo = (canal["tipo"] or "").lower()
                colchon_base = 1 if canal_tipo == "directo" else 0 if "tour" in canal_tipo else 2
                colchon_operativo = _colchon_operativo_habitaciones(politica, total, canal_tipo)
                colchon = max(colchon_base, colchon_operativo)
                venta_segura = max(0, disponibles - colchon)
                riesgo = "critico" if disponibles <= 0 else "alto" if venta_segura <= 0 else "controlado"
                if riesgo == "controlado":
                    continue
                riesgos_canal.append({
                    "fecha": fecha_iso,
                    "tipo": tipo,
                    "canal": canal,
                    "total": int(total or 0),
                    "bloqueadas": int(bloqueadas or 0),
                    "reservas": int(reservas_activas or 0),
                    "disponibles": disponibles,
                    "colchon": colchon,
                    "colchon_base": colchon_base,
                    "colchon_operativo": colchon_operativo,
                    "venta_segura": venta_segura,
                    "riesgo": riesgo,
                    "accion": "Cerrar canal externo y revisar asignaciones." if canal_tipo != "directo" else "Solo venta manual con confirmacion.",
                })
    return {
        "desde": desde,
        "hasta": hasta,
        "dias": dias,
        "resumen": {
            "conflictos": len(conflictos),
            "sin_asignar": len(sin_asignar),
            "solapes": len(solapes),
            "bloqueos": len(bloqueos),
            "riesgos_canal": len(riesgos_canal),
            "criticos": sum(1 for row in riesgos_canal if row["riesgo"] == "critico"),
        },
        "conflictos": conflictos[:120],
        "riesgos_canal": riesgos_canal[:160],
        "politica": politica,
    }


def _parse_fecha_importada(value):
    text = (value or "").strip()
    if not text:
        return None
    parsed = _parse_date(text)
    if parsed:
        return parsed
    text = text.split()[0]
    for sep in ("/", "-"):
        parts = text.split(sep)
        if len(parts) == 3 and len(parts[2]) == 4:
            try:
                return date(int(parts[2]), int(parts[1]), int(parts[0]))
            except Exception:
                return None
    return None


def _importe_importado(value, default=0.0):
    text = (value or "").strip()
    if not text:
        return default
    filtered = "".join(ch for ch in text if ch.isdigit() or ch in {",", ".", "-"})
    if "," in filtered and "." in filtered:
        if filtered.rfind(",") > filtered.rfind("."):
            filtered = filtered.replace(".", "").replace(",", ".")
        else:
            filtered = filtered.replace(",", "")
    else:
        filtered = filtered.replace(",", ".")
    return _to_float(filtered, default)


def _reserva_importada_existente(conn, canal_id, localizador, empresa_id):
    return conn.execute(
        """
        SELECT *
        FROM reservas_recepcion
        WHERE empresa_id = ?
          AND canal_id = ?
          AND TRIM(COALESCE(localizador_externo, '')) = ?
        LIMIT 1
        """,
        (empresa_id, canal_id, localizador),
    ).fetchone()


def _estado_timestamps(estado):
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "cancelada_at": now if estado == "cancelada" else None,
        "no_show_at": now if estado == "no_show" else None,
        "checkout_at": now if estado == "checkout" else None,
    }


def _importar_reservas_csv(conn, canal, empresa_id, archivo):
    raw = archivo.read()
    if not raw:
        return {
            "total": 0,
            "creadas": 0,
            "actualizadas": 0,
            "omitidas": 0,
            "errores": 1,
            "mensajes": ["El archivo esta vacio."],
        }
    try:
        texto = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = raw.decode("latin-1")

    sample = texto[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        reader = csv.DictReader(StringIO(texto), dialect=dialect)
    except Exception:
        reader = csv.DictReader(StringIO(texto), delimiter=";")
    result = {"total": 0, "creadas": 0, "actualizadas": 0, "omitidas": 0, "errores": 0, "mensajes": []}
    if not reader.fieldnames:
        result["errores"] = 1
        result["mensajes"].append("El CSV no tiene cabeceras.")
        return result

    for line_no, raw_row in enumerate(reader, start=2):
        row = _csv_row_normalizada(raw_row)
        if not any(row.values()):
            continue
        result["total"] += 1

        localizador = _row_get(
            row,
            "localizador",
            "localizador_externo",
            "locator",
            "booking_id",
            "reservation_id",
            "id_reserva",
            "codigo",
        )
        entrada = _parse_fecha_importada(_row_get(row, "fecha_entrada", "entrada", "checkin", "check_in", "arrival", "arrival_date"))
        salida = _parse_fecha_importada(_row_get(row, "fecha_salida", "salida", "checkout", "check_out", "departure", "departure_date"))
        titular = _row_get(row, "titular", "titular_nombre", "cliente", "guest", "guest_name", "nombre")

        errores_fila = []
        if not localizador:
            errores_fila.append("sin localizador")
        if not entrada or not salida or salida <= entrada:
            errores_fila.append("fechas no validas")
        if not titular:
            errores_fila.append("sin titular")
        if errores_fila:
            result["errores"] += 1
            result["omitidas"] += 1
            result["mensajes"].append(f"Fila {line_no}: {', '.join(errores_fila)}.")
            continue
        if entrada is None or salida is None:
            result["errores"] += 1
            result["omitidas"] += 1
            result["mensajes"].append(f"Fila {line_no}: fechas no validas.")
            continue
        entrada_iso = entrada.isoformat()
        salida_iso = salida.isoformat()

        existente = _reserva_importada_existente(conn, canal["id"], localizador, empresa_id)
        reserva_id = existente["id"] if existente else None
        habitacion_id = existente["habitacion_id"] if existente else None
        tipo_habitacion_id = existente["tipo_habitacion_id"] if existente and "tipo_habitacion_id" in existente.keys() else _tipo_habitacion_importado(conn, row, empresa_id)
        habitacion_texto = _row_get(row, "habitacion", "room", "room_number", "numero_habitacion", "room_name")
        if habitacion_texto:
            habitacion = _habitacion_por_numero(conn, habitacion_texto, empresa_id)
            if habitacion:
                if _habitacion_disponible(conn, habitacion["id"], entrada_iso, salida_iso, empresa_id, reserva_id):
                    habitacion_id = habitacion["id"]
                    tipo_habitacion_id = habitacion["tipo_habitacion_id"] or tipo_habitacion_id
                else:
                    result["mensajes"].append(
                        f"Fila {line_no}: habitacion {habitacion_texto} ocupada o bloqueada; revisar asignacion."
                    )
            else:
                result["mensajes"].append(f"Fila {line_no}: habitacion {habitacion_texto} no encontrada.")
        if habitacion_id and not _habitacion_disponible(conn, habitacion_id, entrada_iso, salida_iso, empresa_id, reserva_id):
            result["mensajes"].append(
                f"Fila {line_no}: la habitacion asignada queda solapada con otra reserva o bloqueo; se deja sin asignar."
            )
            habitacion_id = None

        regimen = _regimen_desde_texto(_row_get(row, "regimen", "board", "meal_plan", "plan"))
        estado = _estado_reserva_importado(_row_get(row, "estado", "status"))
        adultos = max(1, _to_int(_row_get(row, "adultos", "adults", default="1"), 1))
        menores = max(0, _to_int(_row_get(row, "menores", "ninos", "children", "kids", default="0"), 0))
        precio_total = _importe_importado(_row_get(row, "precio_total", "total", "importe", "amount", default="0"))
        comision = _importe_importado(_row_get(row, "comision", "commission", "comision_canal", default=str(canal["comision_pct"] or 0)))
        email = _row_get(row, "email", "correo", "mail")
        telefono = _row_get(row, "telefono", "phone", "movil", "mobile")
        observaciones = _row_get(row, "observaciones", "notas", "notes", "comments")
        alergias = _row_get(row, "alergias", "intolerancias", "allergies", "allergy_notes")
        estado_externo = _row_get(row, "estado", "status")
        timestamps = _estado_timestamps(estado)

        if existente:
            conn.execute(
                """
                UPDATE reservas_recepcion
                SET habitacion_id = ?, tipo_habitacion_id = ?, fecha_entrada = ?, fecha_salida = ?, regimen = ?,
                    adultos = ?, menores = ?, estado = ?, origen = 'importado',
                    canal_id = ?, localizador_externo = ?, comision_canal = ?, estado_externo = ?,
                    ultima_sincronizacion = CURRENT_TIMESTAMP,
                    titular_nombre = ?, titular_email = ?, titular_telefono = ?,
                    precio_total = ?, observaciones = ?, alergias_resumen = ?,
                    cancelada_at = COALESCE(cancelada_at, ?),
                    no_show_at = COALESCE(no_show_at, ?),
                    checkout_at = COALESCE(checkout_at, ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                (
                    habitacion_id,
                    tipo_habitacion_id,
                    entrada_iso,
                    salida_iso,
                    regimen,
                    adultos,
                    menores,
                    estado,
                    canal["id"],
                    localizador,
                    comision,
                    estado_externo,
                    titular,
                    email,
                    telefono,
                    precio_total,
                    observaciones,
                    alergias,
                    timestamps["cancelada_at"],
                    timestamps["no_show_at"],
                    timestamps["checkout_at"],
                    reserva_id,
                    empresa_id,
                ),
            )
            _recalcular_pagos_reserva(conn, reserva_id, empresa_id)
            result["actualizadas"] += 1
        else:
            codigo = _generar_codigo_reserva(conn, empresa_id)
            conn.execute(
                """
                INSERT INTO reservas_recepcion
                (codigo, habitacion_id, tipo_habitacion_id, fecha_entrada, fecha_salida, hora_llegada, hora_salida, regimen,
                 adultos, menores, estado, origen, canal_id, localizador_externo, comision_canal,
                 estado_externo, ultima_sincronizacion, titular_nombre, titular_email, titular_telefono,
                 precio_total, deposito, saldo, observaciones, alergias_resumen,
                 cancelada_at, no_show_at, checkout_at, empresa_id, creado_por)
                VALUES (?, ?, ?, ?, ?, '', '', ?, ?, ?, ?, 'importado', ?, ?, ?, ?, CURRENT_TIMESTAMP,
                        ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    codigo,
                    habitacion_id,
                    tipo_habitacion_id,
                    entrada_iso,
                    salida_iso,
                    regimen,
                    adultos,
                    menores,
                    estado,
                    canal["id"],
                    localizador,
                    comision,
                    estado_externo,
                    titular,
                    email,
                    telefono,
                    precio_total,
                    precio_total,
                    observaciones,
                    alergias,
                    timestamps["cancelada_at"],
                    timestamps["no_show_at"],
                    timestamps["checkout_at"],
                    empresa_id,
                    session.get("user_id"),
                ),
            )
            reserva_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            _recalcular_pagos_reserva(conn, reserva_id, empresa_id)
            result["creadas"] += 1

    return result


def _leer_archivo_texto(archivo):
    raw = archivo.read()
    if not raw:
        return ""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _descargar_texto_url(url):
    if not (url or "").lower().startswith(("http://", "https://")):
        raise ValueError("La URL iCal debe empezar por http:// o https://.")
    req = Request(url, headers={"User-Agent": "ElRacoERP/1.0"})
    with urlopen(req, timeout=20) as response:
        raw = response.read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _unfold_ics_lines(texto):
    lines = texto.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded = []
    for line in lines:
        if not line:
            continue
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def _ics_line_key_value(line):
    if ":" not in line:
        return "", ""
    key, value = line.split(":", 1)
    return key.split(";", 1)[0].strip().upper(), value.strip()


def _parse_ics_date(value):
    text = (value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "")
    if "T" in text:
        text = text.split("T", 1)[0]
    if len(text) == 8 and text.isdigit():
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        except ValueError:
            return None
    return _parse_fecha_importada(text)


def _ics_unescape(value):
    return (
        (value or "")
        .replace("\\n", " ")
        .replace("\\N", " ")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
        .strip()
    )


def _ics_events(texto):
    events = []
    current = None
    for line in _unfold_ics_lines(texto):
        key, value = _ics_line_key_value(line)
        if key == "BEGIN" and value.upper() == "VEVENT":
            current = {}
            continue
        if key == "END" and value.upper() == "VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is not None and key:
            current[key] = value
    return events


def _importar_reservas_ics(conn, canal, empresa_id, texto, habitacion_id, cancelar_no_presentes=False):
    result = {"total": 0, "creadas": 0, "actualizadas": 0, "omitidas": 0, "errores": 0, "mensajes": [], "canceladas": 0}
    if not texto.strip():
        result["errores"] = 1
        result["mensajes"].append("El calendario ICS esta vacio.")
        return result
    habitacion = conn.execute(
        "SELECT * FROM habitaciones_recepcion WHERE id = ? AND empresa_id = ? AND COALESCE(activo, 1) = 1",
        (habitacion_id, empresa_id),
    ).fetchone()
    if not habitacion:
        result["errores"] = 1
        result["mensajes"].append("Selecciona una habitacion activa para importar el calendario.")
        return result

    vistos = set()
    for idx, event in enumerate(_ics_events(texto), start=1):
        result["total"] += 1
        uid = _ics_unescape(event.get("UID") or "")
        entrada = _parse_ics_date(event.get("DTSTART"))
        salida = _parse_ics_date(event.get("DTEND"))
        summary = _ics_unescape(event.get("SUMMARY") or "")
        description = _ics_unescape(event.get("DESCRIPTION") or "")
        if not uid:
            uid = f"ICS-{habitacion_id}-{entrada.isoformat() if entrada else idx}-{salida.isoformat() if salida else idx}-{idx}"
        localizador = uid[:120]
        vistos.add(localizador)
        if not entrada or not salida or salida <= entrada:
            result["errores"] += 1
            result["omitidas"] += 1
            result["mensajes"].append(f"Evento {idx}: fechas no validas.")
            continue
        existente = _reserva_importada_existente(conn, canal["id"], localizador, empresa_id)
        reserva_id = existente["id"] if existente else None
        if reserva_id and existente["habitacion_id"] and existente["habitacion_id"] != habitacion_id:
            result["mensajes"].append(f"Evento {idx}: la reserva estaba en otra habitacion y se ha movido.")
        titular = summary or canal["nombre"] or "Reserva externa"
        if len(titular) > 120:
            titular = titular[:120]
        observaciones = description or f"Importado por iCal desde {canal['nombre']}"
        conflictos = _conflictos_habitacion(conn, habitacion_id, entrada.isoformat(), salida.isoformat(), empresa_id, reserva_id)
        if conflictos:
            result["errores"] += 1
            result["omitidas"] += 1
            result["mensajes"].append(
                f"Evento {idx}: solape detectado en habitacion {habitacion['numero']}; "
                + _descripcion_conflictos_habitacion(conflictos)
            )
            continue
        if existente:
            conn.execute(
                """
                UPDATE reservas_recepcion
                SET habitacion_id = ?, tipo_habitacion_id = ?, fecha_entrada = ?, fecha_salida = ?, estado = 'confirmada',
                    origen = 'ical', canal_id = ?, localizador_externo = ?, estado_externo = 'ics',
                    ultima_sincronizacion = CURRENT_TIMESTAMP, titular_nombre = ?,
                    observaciones = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                (
                    habitacion_id,
                    habitacion["tipo_habitacion_id"],
                    entrada.isoformat(),
                    salida.isoformat(),
                    canal["id"],
                    localizador,
                    titular,
                    observaciones,
                    reserva_id,
                    empresa_id,
                ),
            )
            _recalcular_pagos_reserva(conn, reserva_id, empresa_id)
            result["actualizadas"] += 1
        else:
            codigo = _generar_codigo_reserva(conn, empresa_id)
            conn.execute(
                """
                INSERT INTO reservas_recepcion
                (codigo, habitacion_id, tipo_habitacion_id, fecha_entrada, fecha_salida, hora_llegada, hora_salida, regimen,
                 adultos, menores, estado, origen, canal_id, localizador_externo, comision_canal,
                 estado_externo, ultima_sincronizacion, titular_nombre, precio_total, deposito, saldo,
                 observaciones, empresa_id, creado_por)
                VALUES (?, ?, ?, ?, ?, '', '', 'solo_alojamiento', 1, 0, 'confirmada', 'ical',
                        ?, ?, ?, 'ics', CURRENT_TIMESTAMP, ?, 0, 0, 0, ?, ?, ?)
                """,
                (
                    codigo,
                    habitacion_id,
                    habitacion["tipo_habitacion_id"],
                    entrada.isoformat(),
                    salida.isoformat(),
                    canal["id"],
                    localizador,
                    _to_float(canal["comision_pct"], 0),
                    titular,
                    observaciones,
                    empresa_id,
                    session.get("user_id"),
                ),
            )
            reserva_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            _recalcular_pagos_reserva(conn, reserva_id, empresa_id)
            result["creadas"] += 1

    if cancelar_no_presentes and vistos:
        placeholders = ",".join("?" for _ in vistos)
        params = [empresa_id, canal["id"], habitacion_id, date.today().isoformat()] + list(vistos)
        result["canceladas"] = conn.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM reservas_recepcion
            WHERE empresa_id = ? AND canal_id = ? AND habitacion_id = ?
              AND estado_externo = 'ics'
              AND COALESCE(estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
              AND date(fecha_salida) >= date(?)
              AND localizador_externo NOT IN ({placeholders})
            """,
            params,
        ).fetchone()["total"] or 0
        conn.execute(
            f"""
            UPDATE reservas_recepcion
            SET estado = 'cancelada', cancelada_at = COALESCE(cancelada_at, CURRENT_TIMESTAMP),
                ultima_sincronizacion = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE empresa_id = ? AND canal_id = ? AND habitacion_id = ?
              AND estado_externo = 'ics'
              AND COALESCE(estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
              AND date(fecha_salida) >= date(?)
              AND localizador_externo NOT IN ({placeholders})
            """,
            params,
        )
        if result["canceladas"]:
            result["mensajes"].append(f"{result['canceladas']} reservas iCal ausentes se han marcado como canceladas.")
    elif cancelar_no_presentes:
        result["mensajes"].append("No se cancelo nada porque el ICS no contenia eventos validos.")
    return result


def _ics_escape(value):
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r", " ")
        .replace("\n", "\\n")
    )


def _ics_date(value):
    parsed = _parse_date(value)
    return parsed.strftime("%Y%m%d") if parsed else date.today().strftime("%Y%m%d")


def _habitacion_ical_token(conn, habitacion_id, empresa_id):
    row = conn.execute(
        "SELECT ical_token FROM habitaciones_recepcion WHERE id = ? AND empresa_id = ?",
        (habitacion_id, empresa_id),
    ).fetchone()
    if not row:
        return ""
    token = row["ical_token"] or ""
    if not token:
        token = secrets.token_urlsafe(24)
        conn.execute(
            "UPDATE habitaciones_recepcion SET ical_token = ? WHERE id = ? AND empresa_id = ?",
            (token, habitacion_id, empresa_id),
        )
    return token


def _calendario_habitacion_ics(conn, habitacion, empresa_id):
    reservas = conn.execute(
        """
        SELECT r.*, c.nombre AS canal_nombre
        FROM reservas_recepcion r
        LEFT JOIN recepcion_canales_reserva c ON r.canal_id = c.id
        WHERE r.empresa_id = ?
          AND r.habitacion_id = ?
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND date(r.fecha_salida) >= date(?)
        ORDER BY date(r.fecha_entrada), r.id
        """,
        (empresa_id, habitacion["id"], date.today().isoformat()),
    ).fetchall()
    bloqueos = conn.execute(
        """
        SELECT *
        FROM bloqueos_habitacion_recepcion
        WHERE empresa_id = ?
          AND habitacion_id = ?
          AND COALESCE(estado, 'activo') = 'activo'
          AND date(fecha_fin) >= date(?)
        ORDER BY date(fecha_inicio), id
        """,
        (empresa_id, habitacion["id"], date.today().isoformat()),
    ).fetchall()
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    nombre_habitacion = habitacion["numero"] or habitacion["nombre"] or habitacion["id"]
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//El Raco ERP//Recepcion//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape('Habitacion ' + str(nombre_habitacion))}",
    ]
    for reserva in reservas:
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:reserva-{reserva['id']}@pepe4.local",
                f"DTSTAMP:{now}",
                f"DTSTART;VALUE=DATE:{_ics_date(reserva['fecha_entrada'])}",
                f"DTEND;VALUE=DATE:{_ics_date(reserva['fecha_salida'])}",
                "SUMMARY:Ocupado",
                f"DESCRIPTION:{_ics_escape('Reserva ERP ' + (reserva['codigo'] or str(reserva['id'])))}",
                "TRANSP:OPAQUE",
                "END:VEVENT",
            ]
        )
    for bloqueo in bloqueos:
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:bloqueo-{bloqueo['id']}@pepe4.local",
                f"DTSTAMP:{now}",
                f"DTSTART;VALUE=DATE:{_ics_date(bloqueo['fecha_inicio'])}",
                f"DTEND;VALUE=DATE:{_ics_date(bloqueo['fecha_fin'])}",
                "SUMMARY:Bloqueado",
                f"DESCRIPTION:{_ics_escape(bloqueo['motivo'] or 'Bloqueo ERP')}",
                "TRANSP:OPAQUE",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _inventario_tipo_fecha(conn, empresa_id, tipo_id, fecha_obj, centro_id=None):
    fecha_txt = fecha_obj.isoformat()
    centro_habitacion = "AND centro_id = ?" if centro_id else ""
    centro_params = [centro_id] if centro_id else []
    total = conn.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM habitaciones_recepcion
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND tipo_habitacion_id = ?
          {centro_habitacion}
        """,
        [empresa_id, tipo_id] + centro_params,
    ).fetchone()["total"] or 0
    ocupadas_asignadas = conn.execute(
        f"""
        SELECT COUNT(DISTINCT habitacion_id) AS total
        FROM reservas_recepcion
        WHERE empresa_id = ?
          AND habitacion_id IN (
              SELECT id FROM habitaciones_recepcion
              WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 AND tipo_habitacion_id = ?
              {centro_habitacion}
          )
          AND COALESCE(estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND date(fecha_entrada) <= date(?)
          AND date(fecha_salida) > date(?)
        """,
        [empresa_id, empresa_id, tipo_id] + centro_params + [fecha_txt, fecha_txt],
    ).fetchone()["total"] or 0
    reservas_tipo_sin_habitacion = conn.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM reservas_recepcion
        WHERE empresa_id = ?
          AND habitacion_id IS NULL
          AND tipo_habitacion_id = ?
          {"AND centro_id = ?" if centro_id else ""}
          AND COALESCE(estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND date(fecha_entrada) <= date(?)
          AND date(fecha_salida) > date(?)
        """,
        [empresa_id, tipo_id] + ([centro_id] if centro_id else []) + [fecha_txt, fecha_txt],
    ).fetchone()["total"] or 0
    ocupadas = min(total, ocupadas_asignadas + reservas_tipo_sin_habitacion)
    bloqueadas = conn.execute(
        f"""
        SELECT COUNT(DISTINCT habitacion_id) AS total
        FROM bloqueos_habitacion_recepcion
        WHERE empresa_id = ?
          AND habitacion_id IN (
              SELECT id FROM habitaciones_recepcion
              WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 AND tipo_habitacion_id = ?
              {centro_habitacion}
          )
          AND COALESCE(estado, 'activo') = 'activo'
          AND date(fecha_inicio) <= date(?)
          AND date(fecha_fin) > date(?)
          AND habitacion_id NOT IN (
              SELECT habitacion_id
              FROM reservas_recepcion
              WHERE empresa_id = ?
                AND habitacion_id IS NOT NULL
                AND COALESCE(estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
                AND date(fecha_entrada) <= date(?)
                AND date(fecha_salida) > date(?)
          )
        """,
        [empresa_id, empresa_id, tipo_id] + centro_params + [fecha_txt, fecha_txt, empresa_id, fecha_txt, fecha_txt],
    ).fetchone()["total"] or 0
    return {
        "total": total,
        "ocupadas": ocupadas,
        "bloqueadas": bloqueadas,
        "disponibles": max(total - ocupadas - bloqueadas, 0),
    }


def _cupo_canal_fecha_tipo(conn, empresa_id, canal_id, tipo_id, fecha_obj, total_habitaciones):
    if not canal_id or not tipo_id:
        return None
    row = conn.execute(
        """
        SELECT *
        FROM recepcion_cupos_canal
        WHERE empresa_id = ?
          AND canal_id = ?
          AND COALESCE(activo, 1) = 1
          AND (tipo_habitacion_id IS NULL OR tipo_habitacion_id = ?)
          AND date(fecha_inicio) <= date(?)
          AND date(fecha_fin) >= date(?)
        ORDER BY CASE WHEN tipo_habitacion_id = ? THEN 0 ELSE 1 END, prioridad, id
        LIMIT 1
        """,
        (empresa_id, canal_id, tipo_id, fecha_obj.isoformat(), fecha_obj.isoformat(), tipo_id),
    ).fetchone()
    if not row:
        return None
    if _to_int(row["release_dias"], 0) > 0 and fecha_obj <= date.today() + timedelta(days=_to_int(row["release_dias"], 0)):
        cupo = 0
    elif _to_int(row["habitaciones"], 0) > 0:
        cupo = _to_int(row["habitaciones"], 0)
    else:
        porcentaje = max(0.0, _to_float(row["porcentaje"], 0.0))
        cupo = int((float(total_habitaciones or 0) * porcentaje / 100.0) + 0.9999) if porcentaje else 0
    return max(0, min(cupo, total_habitaciones or cupo))


def _reservas_canal_tipo_fecha(conn, empresa_id, canal_id, tipo_id, fecha_obj):
    if not canal_id or not tipo_id:
        return 0
    fecha_txt = fecha_obj.isoformat()
    return conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE r.empresa_id = ?
          AND r.canal_id = ?
          AND COALESCE(r.tipo_habitacion_id, h.tipo_habitacion_id) = ?
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND date(r.fecha_entrada) <= date(?)
          AND date(r.fecha_salida) > date(?)
        """,
        (empresa_id, canal_id, tipo_id, fecha_txt, fecha_txt),
    ).fetchone()["total"] or 0


def _inventario_precios_rows(conn, empresa_id, desde, hasta, regimen="", canal_id=None):
    desde_dt = _parse_date(desde) or date.today()
    hasta_dt = _parse_date(hasta) or (desde_dt + timedelta(days=30))
    if hasta_dt < desde_dt:
        hasta_dt = desde_dt
    if (hasta_dt - desde_dt).days > 370:
        hasta_dt = desde_dt + timedelta(days=370)
    regimenes = [regimen] if regimen in REGIMENES else list(REGIMENES.keys())
    tipos = _tipos_habitacion_select(conn, empresa_id)
    centro_id = centro_activo_id()
    politica = _politica_disponibilidad(conn, empresa_id, centro_id)
    canal = _canal_por_id(conn, canal_id, empresa_id) if canal_id else None
    canal_tipo = (canal["tipo"] if canal and "tipo" in canal.keys() else "") or ""
    rows = []
    fecha_actual = desde_dt
    while fecha_actual <= hasta_dt:
        for tipo in tipos:
            inventario = _inventario_tipo_fecha(conn, empresa_id, tipo["id"], fecha_actual, centro_id=centro_id)
            colchon_operativo = _colchon_operativo_habitaciones(politica, inventario["total"], canal_tipo or "ota")
            disponibles_operativos = max(0, inventario["disponibles"] - colchon_operativo)
            for regimen_key in regimenes:
                tarifa = _buscar_tarifa_noche(conn, tipo["id"], regimen_key, fecha_actual, empresa_id)
                precio_base = float(tarifa["precio_noche"] or 0) if tarifa else 0.0
                ajuste_total = 0.0
                reglas_txt = []
                for regla in _reglas_precio_activas(conn, tipo["id"], regimen_key, fecha_actual, empresa_id):
                    valor = float(regla["ajuste_valor"] or 0)
                    ajuste = valor if regla["ajuste_tipo"] == "importe" else round(precio_base * valor / 100, 2)
                    ajuste_total += ajuste
                    reglas_txt.append(f"{regla['nombre']} ({ajuste:+.2f})")
                precio_final = max(precio_base + ajuste_total, 0) if tarifa else 0.0
                cupo_canal = ""
                reservas_canal = ""
                disponible_canal = disponibles_operativos
                if canal_id:
                    cupo = _cupo_canal_fecha_tipo(conn, empresa_id, canal_id, tipo["id"], fecha_actual, inventario["total"])
                    reservas = _reservas_canal_tipo_fecha(conn, empresa_id, canal_id, tipo["id"], fecha_actual)
                    cupo_canal = "" if cupo is None else cupo
                    reservas_canal = reservas
                    disponible_canal = min(disponibles_operativos, max((cupo if cupo is not None else inventario["total"]) - reservas - colchon_operativo, 0))
                rows.append(
                    [
                        fecha_actual.isoformat(),
                        tipo["codigo"] or tipo["id"],
                        tipo["nombre"] or tipo["codigo"] or "",
                        regimen_key,
                        REGIMENES.get(regimen_key, regimen_key),
                        inventario["total"],
                        inventario["ocupadas"],
                        inventario["bloqueadas"],
                        inventario["disponibles"],
                        cupo_canal,
                        reservas_canal,
                        disponible_canal,
                        f"{precio_base:.2f}",
                        f"{ajuste_total:.2f}",
                        f"{precio_final:.2f}",
                        "cerrado" if disponible_canal <= 0 else "abierto",
                        " | ".join(reglas_txt),
                        "" if tarifa else "sin tarifa",
                    ]
                )
        fecha_actual += timedelta(days=1)
    return rows


def _habitacion_tiene_bloqueo(conn, habitacion_id, entrada, salida, empresa_id, bloqueo_id=None):
    params = [habitacion_id, empresa_id, salida, entrada]
    extra = ""
    if bloqueo_id:
        extra = "AND id <> ?"
        params.append(bloqueo_id)
    return conn.execute(
        f"""
        SELECT id
        FROM bloqueos_habitacion_recepcion
        WHERE habitacion_id = ?
          AND empresa_id = ?
          AND COALESCE(estado, 'activo') = 'activo'
          AND date(fecha_inicio) < date(?)
          AND date(fecha_fin) > date(?)
          {extra}
        LIMIT 1
        """,
        params,
    ).fetchone() is not None


def _habitacion_tiene_reserva(conn, habitacion_id, entrada, salida, empresa_id):
    return conn.execute(
        """
        SELECT id
        FROM reservas_recepcion
        WHERE habitacion_id = ?
          AND empresa_id = ?
          AND COALESCE(estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND date(fecha_entrada) < date(?)
          AND date(fecha_salida) > date(?)
        LIMIT 1
        """,
        (habitacion_id, empresa_id, salida, entrada),
    ).fetchone() is not None


def _pagos_reserva(conn, reserva_id, empresa_id):
    return conn.execute(
        """
        SELECT *
        FROM pagos_reserva_recepcion
        WHERE reserva_id = ? AND empresa_id = ?
        ORDER BY date(fecha) DESC, id DESC
        """,
        (reserva_id, empresa_id),
    ).fetchall()


def _cargos_reserva(conn, reserva_id, empresa_id):
    return conn.execute(
        """
        SELECT c.*, pv.nombre AS punto_venta_nombre
        FROM recepcion_cargos_reserva c
        LEFT JOIN puntos_venta pv ON c.punto_venta_id = pv.id
        WHERE c.reserva_id = ? AND c.empresa_id = ?
        ORDER BY date(c.fecha) DESC, c.id DESC
        """,
        (reserva_id, empresa_id),
    ).fetchall()


def _restaurante_reservas_reserva(conn, reserva_id, empresa_id):
    return conn.execute(
        """
        SELECT rr.*, pv.nombre AS punto_venta_nombre, pv.tipo AS punto_venta_tipo
        FROM recepcion_reservas_restaurante rr
        LEFT JOIN puntos_venta pv ON rr.punto_venta_id = pv.id
        WHERE rr.reserva_id = ? AND rr.empresa_id = ?
        ORDER BY date(rr.fecha) DESC, rr.hora DESC, rr.id DESC
        """,
        (reserva_id, empresa_id),
    ).fetchall()


def _inclusiones_regimen(conn, regimen, empresa_id):
    return conn.execute(
        """
        SELECT *
        FROM recepcion_regimen_inclusiones
        WHERE empresa_id = ? AND regimen = ? AND COALESCE(activo, 1) = 1
        ORDER BY servicio, tipo, concepto
        """,
        (empresa_id, regimen or "solo_alojamiento"),
    ).fetchall()


def _buscar_tarifa_noche(conn, tipo_habitacion_id, regimen, fecha_obj, empresa_id):
    if not tipo_habitacion_id or not fecha_obj:
        return None
    return conn.execute(
        """
        SELECT *
        FROM recepcion_tarifas_tipo
        WHERE empresa_id = ?
          AND tipo_habitacion_id = ?
          AND regimen = ?
          AND COALESCE(activo, 1) = 1
          AND date(fecha_inicio) <= date(?)
          AND date(fecha_fin) >= date(?)
        ORDER BY prioridad DESC, id DESC
        LIMIT 1
        """,
        (empresa_id, tipo_habitacion_id, regimen or "solo_alojamiento", fecha_obj.isoformat(), fecha_obj.isoformat()),
    ).fetchone()


def _reglas_precio_activas(conn, tipo_habitacion_id, regimen, fecha_obj, empresa_id, ahora=None):
    ahora = ahora or datetime.now()
    rows = conn.execute(
        """
        SELECT *
        FROM recepcion_reglas_precio
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND (tipo_habitacion_id IS NULL OR tipo_habitacion_id = ?)
          AND (regimen IS NULL OR regimen = '' OR regimen = ?)
          AND (fecha_inicio_estancia IS NULL OR fecha_inicio_estancia = '' OR date(fecha_inicio_estancia) <= date(?))
          AND (fecha_fin_estancia IS NULL OR fecha_fin_estancia = '' OR date(fecha_fin_estancia) >= date(?))
        ORDER BY id
        """,
        (empresa_id, tipo_habitacion_id, regimen or "", fecha_obj.isoformat(), fecha_obj.isoformat()),
    ).fetchall()
    activas = []
    ocupacion_pct = None
    for row in rows:
        desde_fecha = row["aplicar_desde_fecha"] or date.today().isoformat()
        desde_hora = row["aplicar_desde_hora"] or "00:00"
        try:
            aplicar_desde = datetime.fromisoformat(f"{desde_fecha}T{desde_hora[:5]}")
        except Exception:
            aplicar_desde = datetime.min
        if ahora >= aplicar_desde:
            min_pct = row["ocupacion_min_pct"] if "ocupacion_min_pct" in row.keys() else None
            max_pct = row["ocupacion_max_pct"] if "ocupacion_max_pct" in row.keys() else None
            if min_pct is not None or max_pct is not None:
                if ocupacion_pct is None:
                    ocupacion_pct = _ocupacion_pct_fecha(conn, fecha_obj, empresa_id)
                if min_pct is not None and ocupacion_pct < float(min_pct):
                    continue
                if max_pct is not None and ocupacion_pct > float(max_pct):
                    continue
            activas.append(row)
    return activas


def _ocupacion_pct_fecha(conn, fecha_obj, empresa_id):
    total = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM habitaciones_recepcion
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        """,
        (empresa_id,),
    ).fetchone()["total"] or 0
    if total <= 0:
        return 0.0
    ocupadas = conn.execute(
        """
        SELECT COUNT(DISTINCT habitacion_id) AS total
        FROM reservas_recepcion
        WHERE empresa_id = ?
          AND habitacion_id IS NOT NULL
          AND COALESCE(estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND date(fecha_entrada) <= date(?)
          AND date(fecha_salida) > date(?)
        """,
        (empresa_id, fecha_obj.isoformat(), fecha_obj.isoformat()),
    ).fetchone()["total"] or 0
    return round((ocupadas / total) * 100, 2)


def _sugerir_precio_reserva(conn, habitacion_id, regimen, entrada, salida, empresa_id):
    entrada_dt = _parse_date(entrada)
    salida_dt = _parse_date(salida)
    tipo_habitacion_id = _habitacion_tipo_id(conn, habitacion_id, empresa_id)
    if not entrada_dt or not salida_dt or salida_dt <= entrada_dt or not tipo_habitacion_id:
        return None
    noches = (salida_dt - entrada_dt).days
    detalle = []
    total_base = 0.0
    total_ajustes = 0.0
    faltan_tarifas = []
    for i in range(noches):
        fecha_obj = entrada_dt + timedelta(days=i)
        tarifa = _buscar_tarifa_noche(conn, tipo_habitacion_id, regimen, fecha_obj, empresa_id)
        if not tarifa:
            faltan_tarifas.append(fecha_obj.isoformat())
            detalle.append({"fecha": fecha_obj.isoformat(), "base": 0, "ajuste": 0, "total": 0, "reglas": []})
            continue
        base = float(tarifa["precio_noche"] or 0)
        ajuste_total = 0.0
        reglas_aplicadas = []
        for regla in _reglas_precio_activas(conn, tipo_habitacion_id, regimen, fecha_obj, empresa_id):
            valor = float(regla["ajuste_valor"] or 0)
            if regla["ajuste_tipo"] == "importe":
                ajuste = valor
            else:
                ajuste = round(base * valor / 100, 2)
            ajuste_total += ajuste
            reglas_aplicadas.append(
                {
                    "nombre": regla["nombre"],
                    "ajuste": ajuste,
                    "motivo": regla["motivo"],
                    "ocupacion_min_pct": regla["ocupacion_min_pct"] if "ocupacion_min_pct" in regla.keys() else None,
                    "ocupacion_max_pct": regla["ocupacion_max_pct"] if "ocupacion_max_pct" in regla.keys() else None,
                }
            )
        total_noche = max(base + ajuste_total, 0)
        total_base += base
        total_ajustes += ajuste_total
        detalle.append(
            {
                "fecha": fecha_obj.isoformat(),
                "base": base,
                "ajuste": ajuste_total,
                "total": total_noche,
                "reglas": reglas_aplicadas,
            }
        )
    return {
        "tipo_habitacion_id": tipo_habitacion_id,
        "regimen": regimen,
        "noches": noches,
        "total_base": round(total_base, 2),
        "total_ajustes": round(total_ajustes, 2),
        "total": round(total_base + total_ajustes, 2),
        "faltan_tarifas": faltan_tarifas,
        "detalle": detalle,
    }


def _autocompletar_precio_reserva(conn, reserva, empresa_id):
    if not reserva or _to_float(reserva["precio_total"] if "precio_total" in reserva.keys() else 0, 0) > 0:
        return reserva
    sugerido = _sugerir_precio_reserva(
        conn,
        reserva["habitacion_id"],
        reserva["regimen"] or "solo_alojamiento",
        reserva["fecha_entrada"],
        reserva["fecha_salida"],
        empresa_id,
    )
    if not sugerido or _to_float(sugerido.get("total"), 0) <= 0:
        return reserva
    conn.execute(
        """
        UPDATE reservas_recepcion
        SET precio_total = ?,
            updated_at = CURRENT_TIMESTAMP,
            observaciones_operativas = TRIM(COALESCE(observaciones_operativas, '') || CASE
                WHEN COALESCE(observaciones_operativas, '') = '' THEN ''
                ELSE '\n'
            END || ?)
        WHERE id = ? AND empresa_id = ?
        """,
        (
            sugerido["total"],
            f"Precio autocalculado para facturacion: {sugerido['total']:.2f} EUR.",
            reserva["id"],
            empresa_id,
        ),
    )
    _recalcular_pagos_reserva(conn, reserva["id"], empresa_id)
    return _reserva_por_id(conn, reserva["id"], empresa_id) or reserva


def _temporadas_demo_tarifas(year, year_offset=0):
    subida = 1 + (max(year_offset, 0) * 0.04)
    temporadas = [
        ("navidad_reyes", f"{year}-01-01", f"{year}-01-06", 215),
        ("invierno_alto", f"{year}-01-07", f"{year}-03-31", 170),
        ("media_primavera", f"{year}-04-01", f"{year}-06-14", 145),
        ("alta_verano", f"{year}-06-15", f"{year}-09-15", 185),
        ("media_otono", f"{year}-09-16", f"{year}-10-31", 155),
        ("invierno", f"{year}-11-01", f"{year}-12-22", 175),
        ("navidad", f"{year}-12-23", f"{year}-12-31", 220),
    ]
    return [(nombre, inicio, fin, round(base * subida, 2)) for nombre, inicio, fin, base in temporadas]


def _multiplicador_tipo_demo(tipo):
    codigo = ((tipo["codigo"] if "codigo" in tipo.keys() else "") or "").strip().upper()
    if codigo in {"SGL", "IND", "SENCILLA"}:
        return 0.72
    if codigo in {"DBL", "DOB", "DOBLE"}:
        return 1.0
    if codigo in {"TPL", "TRP", "TRIPLE"}:
        return 1.28
    if codigo in {"CUA", "QUAD", "CUADRUPLE"}:
        return 1.48
    if codigo in {"SUI", "SUITE", "JSU"}:
        return 1.85
    capacidad = _to_int(tipo["capacidad_base"] if "capacidad_base" in tipo.keys() else 2, 2)
    if capacidad <= 1:
        return 0.72
    return 1.0 + max(capacidad - 2, 0) * 0.24


def _precio_demo_tarifa(tipo, regimen, base_doble):
    capacidad = _to_int(tipo["capacidad_base"] if "capacidad_base" in tipo.keys() else 2, 2)
    capacidad = max(capacidad, 1)
    suplemento_persona = {
        "solo_alojamiento": 0,
        "alojamiento_desayuno": 13,
        "media_pension": 39,
        "pension_completa": 59,
        "todo_incluido": 82,
    }.get(regimen, 0)
    precio = (base_doble * _multiplicador_tipo_demo(tipo)) + (suplemento_persona * capacidad)
    return round(precio)


def _crear_tarifas_demo_mercado(conn, empresa_id, inicio_year=None, num_years=2, sobrescribir=False):
    inicio_year = inicio_year or date.today().year
    num_years = max(1, min(_to_int(num_years, 2), 4))
    _seed_tipos_habitacion(conn, empresa_id)
    tipos = _tipos_habitacion_select(conn, empresa_id)
    result = {
        "creadas": 0,
        "actualizadas": 0,
        "omitidas": 0,
        "reglas_creadas": 0,
        "reglas_actualizadas": 0,
        "reglas_omitidas": 0,
    }
    for year_offset in range(num_years):
        year = inicio_year + year_offset
        for _nombre, inicio, fin, base_doble in _temporadas_demo_tarifas(year, year_offset):
            for tipo in tipos:
                for regimen in REGIMENES:
                    precio = _precio_demo_tarifa(tipo, regimen, base_doble)
                    existente = conn.execute(
                        """
                        SELECT id
                        FROM recepcion_tarifas_tipo
                        WHERE empresa_id = ?
                          AND tipo_habitacion_id = ?
                          AND regimen = ?
                          AND date(fecha_inicio) = date(?)
                          AND date(fecha_fin) = date(?)
                        LIMIT 1
                        """,
                        (empresa_id, tipo["id"], regimen, inicio, fin),
                    ).fetchone()
                    if existente:
                        if sobrescribir:
                            conn.execute(
                                """
                                UPDATE recepcion_tarifas_tipo
                                SET precio_noche = ?, prioridad = 5, activo = 1, updated_at = CURRENT_TIMESTAMP
                                WHERE id = ? AND empresa_id = ?
                                """,
                                (precio, existente["id"], empresa_id),
                            )
                            result["actualizadas"] += 1
                        else:
                            result["omitidas"] += 1
                        continue
                    conn.execute(
                        """
                        INSERT INTO recepcion_tarifas_tipo
                        (tipo_habitacion_id, regimen, fecha_inicio, fecha_fin, precio_noche, prioridad, activo, empresa_id)
                        VALUES (?, ?, ?, ?, ?, 5, 1, ?)
                        """,
                        (tipo["id"], regimen, inicio, fin, precio, empresa_id),
                    )
                    result["creadas"] += 1
    reglas = [
        (
            "Demo mercado - alta ocupacion +12%",
            80,
            None,
            12,
            "Ajuste automatico demo cuando la ocupacion supera el 80%.",
        ),
        (
            "Demo mercado - baja ocupacion -8%",
            None,
            35,
            -8,
            "Descuento demo cuando la ocupacion esta por debajo del 35%.",
        ),
    ]
    for nombre, min_pct, max_pct, valor, motivo in reglas:
        existente = conn.execute(
            """
            SELECT id
            FROM recepcion_reglas_precio
            WHERE empresa_id = ? AND nombre = ?
            LIMIT 1
            """,
            (empresa_id, nombre),
        ).fetchone()
        data = (
            None,
            "",
            date.today().isoformat(),
            "00:00",
            f"{inicio_year}-01-01",
            f"{inicio_year + num_years - 1}-12-31",
            min_pct,
            max_pct,
            "porcentaje",
            valor,
            motivo,
            1,
            empresa_id,
        )
        if existente:
            if sobrescribir:
                conn.execute(
                    """
                    UPDATE recepcion_reglas_precio
                    SET tipo_habitacion_id = ?, regimen = ?, aplicar_desde_fecha = ?, aplicar_desde_hora = ?,
                        fecha_inicio_estancia = ?, fecha_fin_estancia = ?, ocupacion_min_pct = ?,
                        ocupacion_max_pct = ?, ajuste_tipo = ?, ajuste_valor = ?, motivo = ?, activo = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND empresa_id = ?
                    """,
                    data[:-1] + (existente["id"], empresa_id),
                )
                result["reglas_actualizadas"] += 1
            else:
                result["reglas_omitidas"] += 1
            continue
        conn.execute(
            """
            INSERT INTO recepcion_reglas_precio
            (tipo_habitacion_id, regimen, aplicar_desde_fecha, aplicar_desde_hora,
             fecha_inicio_estancia, fecha_fin_estancia, ocupacion_min_pct, ocupacion_max_pct,
             ajuste_tipo, ajuste_valor, motivo, activo, empresa_id, nombre)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            data + (nombre,),
        )
        result["reglas_creadas"] += 1
    return result


def _habitaciones_disponibles_para_reserva(conn, reserva, empresa_id, limite=80):
    if not reserva:
        return []
    entrada = reserva["fecha_entrada"]
    salida = reserva["fecha_salida"]
    entrada_dt = _parse_date(entrada)
    salida_dt = _parse_date(salida)
    if not entrada_dt or not salida_dt or salida_dt <= entrada_dt:
        return []
    pax = max(1, int(reserva["adultos"] or 0) + int(reserva["menores"] or 0))
    reserva_id = reserva["id"]
    actual_id = reserva["habitacion_id"]
    tipo_reserva_id = reserva["tipo_habitacion_id"] if "tipo_habitacion_id" in reserva.keys() else None
    rows = conn.execute(
        """
        SELECT h.*, th.codigo AS tipo_codigo, th.nombre AS tipo_nombre,
               th.capacidad_base AS tipo_capacidad_base, th.capacidad_max AS tipo_capacidad_max,
               eq.tipo_cama, eq.supletoria_posible, eq.supletorias_max, eq.cuna_posible, eq.aire_tipo,
               CASE WHEN h.id = ? THEN 0 ELSE 1 END AS orden_actual
        FROM habitaciones_recepcion h
        LEFT JOIN recepcion_tipos_habitacion th ON h.tipo_habitacion_id = th.id
        LEFT JOIN habitaciones_equipamiento eq ON h.id = eq.habitacion_id AND eq.empresa_id = h.empresa_id
        WHERE h.empresa_id = ?
          AND COALESCE(h.activo, 1) = 1
          AND (h.id = ? OR COALESCE(h.capacidad, 0) + COALESCE(eq.supletorias_max, 0) >= ?)
          AND (? IS NULL OR h.id = ? OR h.tipo_habitacion_id = ?)
        ORDER BY orden_actual, h.bloque, h.planta, h.numero, h.nombre
        LIMIT ?
        """,
        (actual_id or 0, empresa_id, actual_id or 0, pax, tipo_reserva_id, actual_id or 0, tipo_reserva_id, limite),
    ).fetchall()
    disponibles = []
    for row in rows:
        if not _habitacion_disponible(conn, row["id"], entrada, salida, empresa_id, reserva_id):
            continue
        item = dict(row)
        item["actual"] = actual_id == row["id"]
        item["pax_reserva"] = pax
        item["precio_sugerido"] = _sugerir_precio_reserva(
            conn,
            row["id"],
            reserva["regimen"] or "solo_alojamiento",
            entrada,
            salida,
            empresa_id,
        )
        disponibles.append(item)
    return disponibles


def _importar_tarifas_csv(conn, archivo, empresa_id):
    rows = _csv_rows_from_upload(archivo)
    result = {"total": len(rows), "creadas": 0, "actualizadas": 0, "omitidas": 0, "errores": 0, "mensajes": []}
    for index, row in enumerate(rows, start=2):
        tipo_id = _tipo_habitacion_importado(conn, row, empresa_id)
        regimen = _regimen_desde_texto(_row_get(row, "regimen", "regimen_codigo", "board", "meal_plan"))
        inicio_dt = _parse_fecha_importada(_row_get(row, "fecha_inicio", "desde", "inicio", "fecha_desde"))
        fin_dt = _parse_fecha_importada(_row_get(row, "fecha_fin", "hasta", "fin", "fecha_hasta"))
        precio = _importe_importado(_row_get(row, "precio_noche", "precio", "tarifa", "importe", "price"))
        prioridad = _to_int(_row_get(row, "prioridad", "priority", default="10"), 10)
        activo = _bool_importado(_row_get(row, "activo", "activa", "estado", default="1"), 1)
        if tipo_id is None or inicio_dt is None or fin_dt is None or fin_dt < inicio_dt or precio < 0:
            result["errores"] += 1
            result["omitidas"] += 1
            result["mensajes"].append(f"Fila {index}: revisa tipo, fechas y precio.")
            continue
        inicio = inicio_dt.isoformat()
        fin = fin_dt.isoformat()
        existing = conn.execute(
            """
            SELECT id
            FROM recepcion_tarifas_tipo
            WHERE empresa_id = ?
              AND tipo_habitacion_id = ?
              AND regimen = ?
              AND fecha_inicio = ?
              AND fecha_fin = ?
            LIMIT 1
            """,
            (empresa_id, tipo_id, regimen, inicio, fin),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE recepcion_tarifas_tipo
                SET precio_noche = ?, prioridad = ?, activo = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                (precio, prioridad, activo, existing["id"], empresa_id),
            )
            result["actualizadas"] += 1
        else:
            conn.execute(
                """
                INSERT INTO recepcion_tarifas_tipo
                (tipo_habitacion_id, regimen, fecha_inicio, fecha_fin, precio_noche, prioridad, activo, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tipo_id, regimen, inicio, fin, precio, prioridad, activo, empresa_id),
            )
            result["creadas"] += 1
    return result


def _servicio_desde_texto(value):
    text = _csv_key(value)
    if text in {"desayuno", "breakfast"}:
        return "desayuno"
    if text in {"comida", "almuerzo", "lunch"}:
        return "comida"
    if text in {"cena", "dinner"}:
        return "cena"
    if text in {"bebidas", "snacks", "bebidas_snacks", "bebidas_y_snacks", "drinks"}:
        return "bebidas_snacks"
    return text


def _importar_regimenes_csv(conn, archivo, empresa_id):
    rows = _csv_rows_from_upload(archivo)
    result = {"total": len(rows), "creadas": 0, "actualizadas": 0, "omitidas": 0, "errores": 0, "mensajes": []}
    for index, row in enumerate(rows, start=2):
        regimen = _regimen_desde_texto(_row_get(row, "regimen", "regimen_codigo", "board", "meal_plan"))
        servicio = _servicio_desde_texto(_row_get(row, "servicio", "service", "turno"))
        tipo = _csv_key(_row_get(row, "tipo", "tipo_inclusion", default="alimento")) or "alimento"
        concepto = _row_get(row, "concepto", "descripcion", "incluido", "nombre")
        incluido = _bool_importado(_row_get(row, "incluido", "activo", "estado", default="1"), 1)
        activo = _bool_importado(_row_get(row, "activo", "activa", "estado", default="1"), 1)
        limite = _row_get(row, "limite", "cantidad", "limit")
        condiciones = _row_get(row, "condiciones", "notas", "observaciones", "conditions")
        if regimen not in REGIMENES or servicio not in SERVICIOS_COMIDA or not concepto:
            result["errores"] += 1
            result["omitidas"] += 1
            result["mensajes"].append(f"Fila {index}: revisa regimen, servicio y concepto.")
            continue
        existing = conn.execute(
            """
            SELECT id
            FROM recepcion_regimen_inclusiones
            WHERE empresa_id = ?
              AND regimen = ?
              AND servicio = ?
              AND LOWER(TRIM(concepto)) = LOWER(TRIM(?))
            LIMIT 1
            """,
            (empresa_id, regimen, servicio, concepto),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE recepcion_regimen_inclusiones
                SET tipo = ?, incluido = ?, limite = ?, condiciones = ?, activo = ?
                WHERE id = ? AND empresa_id = ?
                """,
                (tipo, incluido, limite, condiciones, activo, existing["id"], empresa_id),
            )
            result["actualizadas"] += 1
        else:
            conn.execute(
                """
                INSERT INTO recepcion_regimen_inclusiones
                (regimen, servicio, tipo, concepto, incluido, limite, condiciones, activo, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (regimen, servicio, tipo, concepto, incluido, limite, condiciones, activo, empresa_id),
            )
            result["creadas"] += 1
    return result


def _auditar_huecos_tarifas(conn, empresa_id, desde, hasta, tipo_id=0, regimen=""):
    desde_dt = _parse_date(desde)
    hasta_dt = _parse_date(hasta)
    if desde_dt is None or hasta_dt is None or hasta_dt < desde_dt:
        return {"ok": False, "mensaje": "Revisa las fechas de auditoria.", "huecos": [], "total_noches": 0}
    if (hasta_dt - desde_dt).days > 370:
        return {"ok": False, "mensaje": "La auditoria esta limitada a 370 dias.", "huecos": [], "total_noches": 0}
    tipos = conn.execute(
        """
        SELECT id, codigo, nombre
        FROM recepcion_tipos_habitacion
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND (? = 0 OR id = ?)
        ORDER BY nombre, codigo
        """,
        (empresa_id, tipo_id or 0, tipo_id or 0),
    ).fetchall()
    regimenes = [regimen] if regimen in REGIMENES else list(REGIMENES.keys())
    huecos = []
    for tipo in tipos:
        for regimen_key in regimenes:
            tramo_inicio = None
            tramo_fin = None
            fecha_actual = desde_dt
            while fecha_actual <= hasta_dt:
                tarifa = _buscar_tarifa_noche(conn, tipo["id"], regimen_key, fecha_actual, empresa_id)
                if tarifa is None:
                    if tramo_inicio is None:
                        tramo_inicio = fecha_actual
                    tramo_fin = fecha_actual
                elif tramo_inicio is not None:
                    huecos.append(
                        {
                            "tipo_id": tipo["id"],
                            "tipo_codigo": tipo["codigo"],
                            "tipo_nombre": tipo["nombre"],
                            "regimen": regimen_key,
                            "desde": tramo_inicio.isoformat(),
                            "hasta": tramo_fin.isoformat(),
                            "noches": (tramo_fin - tramo_inicio).days + 1,
                        }
                    )
                    tramo_inicio = None
                    tramo_fin = None
                fecha_actual += timedelta(days=1)
            if tramo_inicio is not None:
                huecos.append(
                    {
                        "tipo_id": tipo["id"],
                        "tipo_codigo": tipo["codigo"],
                        "tipo_nombre": tipo["nombre"],
                        "regimen": regimen_key,
                        "desde": tramo_inicio.isoformat(),
                        "hasta": tramo_fin.isoformat(),
                        "noches": (tramo_fin - tramo_inicio).days + 1,
                    }
                )
    return {
        "ok": True,
        "mensaje": "",
        "huecos": huecos,
        "total_noches": sum(item["noches"] for item in huecos),
    }


def _clonar_temporada_tarifas(conn, empresa_id, origen_inicio, origen_fin, destino_inicio, tipo_id=0, regimen="", sobrescribir=False):
    origen_inicio_dt = _parse_date(origen_inicio)
    origen_fin_dt = _parse_date(origen_fin)
    destino_inicio_dt = _parse_date(destino_inicio)
    if origen_inicio_dt is None or origen_fin_dt is None or destino_inicio_dt is None or origen_fin_dt < origen_inicio_dt:
        return {"ok": False, "mensaje": "Revisa fechas de origen y destino.", "creadas": 0, "actualizadas": 0, "omitidas": 0}
    if (origen_fin_dt - origen_inicio_dt).days > 370:
        return {"ok": False, "mensaje": "El clonado esta limitado a temporadas de 370 dias.", "creadas": 0, "actualizadas": 0, "omitidas": 0}
    delta = destino_inicio_dt - origen_inicio_dt
    filtros = [
        "t.empresa_id = ?",
        "date(t.fecha_inicio) >= date(?)",
        "date(t.fecha_fin) <= date(?)",
    ]
    params = [empresa_id, origen_inicio_dt.isoformat(), origen_fin_dt.isoformat()]
    if tipo_id:
        filtros.append("t.tipo_habitacion_id = ?")
        params.append(tipo_id)
    if regimen in REGIMENES:
        filtros.append("t.regimen = ?")
        params.append(regimen)
    tarifas = conn.execute(
        f"""
        SELECT t.*
        FROM recepcion_tarifas_tipo t
        WHERE {" AND ".join(filtros)}
        ORDER BY date(t.fecha_inicio), t.tipo_habitacion_id, t.regimen
        """,
        params,
    ).fetchall()
    result = {"ok": True, "mensaje": "", "creadas": 0, "actualizadas": 0, "omitidas": 0}
    for tarifa in tarifas:
        inicio_dt = _parse_date(tarifa["fecha_inicio"])
        fin_dt = _parse_date(tarifa["fecha_fin"])
        if inicio_dt is None or fin_dt is None:
            result["omitidas"] += 1
            continue
        nuevo_inicio = (inicio_dt + delta).isoformat()
        nuevo_fin = (fin_dt + delta).isoformat()
        existing = conn.execute(
            """
            SELECT id
            FROM recepcion_tarifas_tipo
            WHERE empresa_id = ?
              AND tipo_habitacion_id = ?
              AND regimen = ?
              AND fecha_inicio = ?
              AND fecha_fin = ?
            LIMIT 1
            """,
            (empresa_id, tarifa["tipo_habitacion_id"], tarifa["regimen"], nuevo_inicio, nuevo_fin),
        ).fetchone()
        if existing and sobrescribir:
            conn.execute(
                """
                UPDATE recepcion_tarifas_tipo
                SET precio_noche = ?, prioridad = ?, activo = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                (
                    tarifa["precio_noche"],
                    tarifa["prioridad"],
                    tarifa["activo"],
                    existing["id"],
                    empresa_id,
                ),
            )
            result["actualizadas"] += 1
        elif existing:
            result["omitidas"] += 1
        else:
            conn.execute(
                """
                INSERT INTO recepcion_tarifas_tipo
                (tipo_habitacion_id, regimen, fecha_inicio, fecha_fin, precio_noche, prioridad, activo, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tarifa["tipo_habitacion_id"],
                    tarifa["regimen"],
                    nuevo_inicio,
                    nuevo_fin,
                    tarifa["precio_noche"],
                    tarifa["prioridad"],
                    tarifa["activo"],
                    empresa_id,
                ),
            )
            result["creadas"] += 1
    return result


def _facturas_reserva(conn, reserva_id, empresa_id):
    return conn.execute(
        """
        SELECT *
        FROM recepcion_facturas
        WHERE reserva_id = ? AND empresa_id = ?
        ORDER BY date(fecha) DESC, id DESC
        """,
        (reserva_id, empresa_id),
    ).fetchall()


def _factura_por_id(conn, factura_id, empresa_id):
    return conn.execute(
        """
        SELECT f.*, r.codigo AS reserva_codigo, r.fecha_entrada, r.fecha_salida, r.titular_nombre,
               h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               (
                   SELECT fr.numero_completo
                   FROM recepcion_facturas fr
                   WHERE fr.id = f.factura_rectificada_id AND fr.empresa_id = f.empresa_id
                   LIMIT 1
               ) AS factura_rectificada_numero,
               (
                   SELECT fx.id
                   FROM recepcion_facturas fx
                   WHERE fx.factura_rectificada_id = f.id
                     AND fx.empresa_id = f.empresa_id
                     AND COALESCE(fx.estado, 'emitida') <> 'anulada'
                   ORDER BY fx.id DESC
                   LIMIT 1
               ) AS rectificativa_id,
               (
                   SELECT fx.numero_completo
                   FROM recepcion_facturas fx
                   WHERE fx.factura_rectificada_id = f.id
                     AND fx.empresa_id = f.empresa_id
                     AND COALESCE(fx.estado, 'emitida') <> 'anulada'
                   ORDER BY fx.id DESC
                   LIMIT 1
               ) AS rectificativa_numero
        FROM recepcion_facturas f
        JOIN reservas_recepcion r ON f.reserva_id = r.id
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE f.id = ? AND f.empresa_id = ?
        """,
        (factura_id, empresa_id),
    ).fetchone()


def _lineas_factura(conn, factura_id, empresa_id):
    return conn.execute(
        """
        SELECT *
        FROM recepcion_factura_lineas
        WHERE factura_id = ? AND empresa_id = ?
        ORDER BY orden, id
        """,
        (factura_id, empresa_id),
    ).fetchall()


def _idioma_documento(valor):
    valor = (valor or "es").strip().lower()
    return valor if valor in IDIOMAS_DOCUMENTO else "es"


def _factura_textos(idioma):
    idioma = _idioma_documento(idioma)
    return TEXTOS_FACTURA.get(idioma) or TEXTOS_FACTURA["es"]


def _recogida_datos_textos(idioma):
    idioma = _idioma_documento(idioma)
    return TEXTOS_RECOGIDA_DATOS.get(idioma) or TEXTOS_RECOGIDA_DATOS["es"]


def _documentacion_cliente_textos(idioma):
    idioma = _idioma_documento(idioma)
    return TEXTOS_DOCUMENTACION_CLIENTE.get(idioma) or TEXTOS_DOCUMENTACION_CLIENTE["es"]


def _textos_checkin_select(conn, empresa_id):
    return conn.execute(
        """
        SELECT *
        FROM recepcion_textos_checkin
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY idioma, version DESC, id DESC
        """,
        (empresa_id,),
    ).fetchall()


def _textos_checkin_admin_select(conn, empresa_id):
    return conn.execute(
        """
        SELECT *
        FROM recepcion_textos_checkin
        WHERE empresa_id = ?
        ORDER BY idioma, version DESC, id DESC
        """,
        (empresa_id,),
    ).fetchall()


def _texto_checkin(conn, idioma, empresa_id):
    idioma = _idioma_documento(idioma)
    row = conn.execute(
        """
        SELECT *
        FROM recepcion_textos_checkin
        WHERE empresa_id = ? AND idioma = ? AND COALESCE(activo, 1) = 1
        ORDER BY version DESC, id DESC
        LIMIT 1
        """,
        (empresa_id, idioma),
    ).fetchone()
    if row:
        return row
    return conn.execute(
        """
        SELECT *
        FROM recepcion_textos_checkin
        WHERE empresa_id = ? AND idioma = 'es' AND COALESCE(activo, 1) = 1
        ORDER BY version DESC, id DESC
        LIMIT 1
        """,
        (empresa_id,),
    ).fetchone()


def _documentos_checkin_reserva(conn, reserva_id, empresa_id):
    return conn.execute(
        """
        SELECT *
        FROM recepcion_documentos_checkin
        WHERE reserva_id = ? AND empresa_id = ?
        ORDER BY fecha_creacion DESC, id DESC
        """,
        (reserva_id, empresa_id),
    ).fetchall()


def _impuestos_select(conn, empresa_id, solo_activos=True):
    where = ["empresa_id = ?"]
    params = [empresa_id]
    if solo_activos:
        where.append("COALESCE(activo, 1) = 1")
    return conn.execute(
        f"""
        SELECT *
        FROM recepcion_impuestos
        WHERE {" AND ".join(where)}
        ORDER BY jurisdiccion, porcentaje, nombre
        """,
        params,
    ).fetchall()


def _impuesto_por_id(conn, impuesto_id, empresa_id):
    impuesto_id = _to_int(impuesto_id, 0)
    if not impuesto_id:
        return None
    return conn.execute(
        """
        SELECT *
        FROM recepcion_impuestos
        WHERE id = ? AND empresa_id = ? AND COALESCE(activo, 1) = 1
        """,
        (impuesto_id, empresa_id),
    ).fetchone()


def _impuesto_manual(porcentaje, nombre="Impuesto manual"):
    porcentaje = _to_float(porcentaje, 0)
    return {
        "id": None,
        "codigo": "MANUAL",
        "nombre": nombre,
        "jurisdiccion": "manual",
        "porcentaje": porcentaje,
    }


def _config_fiscal(conn, empresa_id):
    row = conn.execute(
        """
        SELECT cfg.*,
               ie.codigo AS impuesto_estancia_codigo,
               ie.nombre AS impuesto_estancia_nombre,
               ie.porcentaje AS impuesto_estancia_pct,
               ix.codigo AS impuesto_extras_codigo,
               ix.nombre AS impuesto_extras_nombre,
               ix.porcentaje AS impuesto_extras_pct
        FROM recepcion_config_fiscal cfg
        LEFT JOIN recepcion_impuestos ie ON ie.id = cfg.impuesto_estancia_id AND ie.empresa_id = cfg.empresa_id
        LEFT JOIN recepcion_impuestos ix ON ix.id = cfg.impuesto_extras_id AND ix.empresa_id = cfg.empresa_id
        WHERE cfg.empresa_id = ?
        ORDER BY cfg.id DESC
        LIMIT 1
        """,
        (empresa_id,),
    ).fetchone()
    if row:
        return row
    _seed_config_fiscal(conn, empresa_id)
    return conn.execute(
        "SELECT * FROM recepcion_config_fiscal WHERE empresa_id = ? ORDER BY id DESC LIMIT 1",
        (empresa_id,),
    ).fetchone()


def _normalizar_politica_credito(valor):
    valor = (valor or "abierto").strip().lower()
    if valor in {"sin_credito", "fianza_virtual", "abierto"}:
        return valor
    return "abierto"


def _credito_reserva(conn, reserva_id, empresa_id):
    reserva = conn.execute(
        """
        SELECT politica_credito, limite_credito, fianza_virtual, fianza_referencia,
               fianza_estado, fianza_liberada_at, fianza_cobrada_at, fianza_liquidada_importe
        FROM reservas_recepcion
        WHERE id = ? AND empresa_id = ?
        """,
        (reserva_id, empresa_id),
    ).fetchone()
    if not reserva:
        return {
            "politica": "abierto",
            "limite": None,
            "consumido": 0.0,
            "disponible": None,
            "fianza_virtual": 0.0,
            "referencia": "",
            "fianza_estado": "sin_fianza",
            "fianza_liberada_at": "",
            "fianza_cobrada_at": "",
            "fianza_liquidada_importe": 0.0,
            "bloqueado": False,
        }
    consumido = conn.execute(
        """
        SELECT COALESCE(SUM(total), 0) AS total
        FROM recepcion_cargos_reserva
        WHERE reserva_id = ?
          AND empresa_id = ?
          AND COALESCE(estado, 'pendiente') <> 'anulado'
          AND COALESCE(incluido, 0) = 0
          AND COALESCE(cargo_habitacion, 1) = 1
        """,
        (reserva_id, empresa_id),
    ).fetchone()["total"] or 0
    politica = _normalizar_politica_credito(reserva["politica_credito"] if "politica_credito" in reserva.keys() else "abierto")
    fianza_virtual = max(_to_float(reserva["fianza_virtual"] if "fianza_virtual" in reserva.keys() else 0), 0)
    limite_credito = max(_to_float(reserva["limite_credito"] if "limite_credito" in reserva.keys() else 0), 0)
    if politica == "abierto":
        limite = None
        disponible = None
    elif politica == "sin_credito":
        limite = 0.0
        disponible = max(0.0 - float(consumido), 0)
    else:
        limite = max(limite_credito, fianza_virtual)
        disponible = max(limite - float(consumido), 0)
    return {
        "politica": politica,
        "limite": limite,
        "consumido": round(float(consumido or 0), 2),
        "disponible": None if disponible is None else round(disponible, 2),
        "fianza_virtual": fianza_virtual,
        "referencia": reserva["fianza_referencia"] if "fianza_referencia" in reserva.keys() else "",
        "fianza_estado": reserva["fianza_estado"] if "fianza_estado" in reserva.keys() else "sin_fianza",
        "fianza_liberada_at": reserva["fianza_liberada_at"] if "fianza_liberada_at" in reserva.keys() else "",
        "fianza_cobrada_at": reserva["fianza_cobrada_at"] if "fianza_cobrada_at" in reserva.keys() else "",
        "fianza_liquidada_importe": _to_float(reserva["fianza_liquidada_importe"] if "fianza_liquidada_importe" in reserva.keys() else 0),
        "bloqueado": politica == "sin_credito",
    }


def _factura_reserva_emitida(conn, reserva_id, empresa_id):
    return conn.execute(
        """
        SELECT id, numero_completo
        FROM recepcion_facturas
        WHERE reserva_id = ?
          AND empresa_id = ?
          AND tipo = 'factura'
          AND COALESCE(estado, 'emitida') NOT IN ('anulada', 'rectificada')
        ORDER BY id DESC
        LIMIT 1
        """,
        (reserva_id, empresa_id),
    ).fetchone()


def _cargos_pendientes_facturar(conn, reserva_id, empresa_id):
    return conn.execute(
        """
        SELECT COUNT(*) AS total_cargos, COALESCE(SUM(total), 0) AS total_importe
        FROM recepcion_cargos_reserva
        WHERE reserva_id = ?
          AND empresa_id = ?
          AND COALESCE(estado, 'pendiente') <> 'anulado'
          AND COALESCE(incluido, 0) = 0
          AND COALESCE(cargo_habitacion, 1) = 1
          AND COALESCE(total, 0) > 0
          AND factura_id IS NULL
        """,
        (reserva_id, empresa_id),
    ).fetchone()


def _estado_fianza_para_credito(politica, fianza_virtual):
    if politica == "fianza_virtual" and _to_float(fianza_virtual, 0) > 0:
        return "autorizada"
    return "sin_fianza"


def _categoria_forma_pago(forma_pago):
    forma = (forma_pago or "").strip().lower()
    replacements = {
        "Ã¡": "a", "Ã©": "e", "Ã­": "i", "Ã³": "o", "Ãº": "u",
        "Ã ": "a", "Ã¨": "e", "Ã¬": "i", "Ã²": "o", "Ã¹": "u",
    }
    for origen, destino in replacements.items():
        forma = forma.replace(origen, destino)
    if any(x in forma for x in ["efect", "cash", "contado"]):
        return "efectivo"
    if any(x in forma for x in ["tarj", "tpv", "visa", "master", "dat", "fianza"]):
        return "tarjeta"
    if any(x in forma for x in ["trans", "banco", "transfer"]):
        return "transferencia"
    if "bizum" in forma:
        return "bizum"
    return "otros"


def _pagos_caja(conn, empresa_id, fecha):
    return conn.execute(
        """
        SELECT p.*, r.codigo AS reserva_codigo, r.titular_nombre,
               h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
        FROM pagos_reserva_recepcion p
        LEFT JOIN reservas_recepcion r ON p.reserva_id = r.id
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE p.empresa_id = ?
          AND date(p.fecha) = date(?)
          AND COALESCE(p.estado, 'confirmado') <> 'anulado'
        ORDER BY p.fecha_creacion, p.id
        """,
        (empresa_id, fecha),
    ).fetchall()


def _resumen_caja(pagos):
    resumen = {
        "efectivo": 0.0,
        "tarjeta": 0.0,
        "transferencia": 0.0,
        "bizum": 0.0,
        "otros": 0.0,
        "total": 0.0,
        "movimientos": 0,
    }
    for pago in pagos:
        importe = _to_float(pago["importe"] if "importe" in pago.keys() else 0, 0)
        categoria = _categoria_forma_pago(pago["forma_pago"] if "forma_pago" in pago.keys() else "")
        resumen[categoria] += importe
        resumen["total"] += importe
        resumen["movimientos"] += 1
    for key in ["efectivo", "tarjeta", "transferencia", "bizum", "otros", "total"]:
        resumen[key] = round(resumen[key], 2)
    return resumen


def _cierres_caja_fecha(conn, empresa_id, fecha):
    return conn.execute(
        """
        SELECT *
        FROM recepcion_cierres_caja
        WHERE empresa_id = ? AND date(fecha) = date(?)
        ORDER BY id DESC
        """,
        (empresa_id, fecha),
    ).fetchall()


def _pagado_reserva(conn, reserva_id, empresa_id):
    row = conn.execute(
        """
        SELECT COALESCE(SUM(importe), 0) AS total
        FROM pagos_reserva_recepcion
        WHERE reserva_id = ? AND empresa_id = ? AND COALESCE(estado, 'confirmado') <> 'anulado'
        """,
        (reserva_id, empresa_id),
    ).fetchone()
    return float(row["total"] or 0)


def _asegurar_deposito_historico_como_pago(conn, reserva, reserva_id, empresa_id):
    deposito = _to_float(reserva["deposito"] if reserva and "deposito" in reserva.keys() else 0, 0)
    if deposito <= 0:
        return
    movimientos = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM pagos_reserva_recepcion
        WHERE reserva_id = ? AND empresa_id = ?
        """,
        (reserva_id, empresa_id),
    ).fetchone()["total"] or 0
    if movimientos:
        return
    conn.execute(
        """
        INSERT INTO pagos_reserva_recepcion
        (reserva_id, fecha, concepto, importe, forma_pago, referencia, estado, observaciones, empresa_id, usuario_id)
        VALUES (?, ?, ?, ?, ?, ?, 'confirmado', ?, ?, ?)
        """,
        (
            reserva_id,
            date.today().isoformat(),
            "Deposito registrado en reserva",
            deposito,
            "Historico",
            "",
            "Movimiento creado automaticamente para conservar el deposito previo al registrar nuevos pagos.",
            empresa_id,
            session.get("user_id"),
        ),
    )


def _generar_numero_factura(conn, empresa_id, tipo, serie, fecha_txt):
    # Mantener el bloqueo hasta insertar y confirmar la factura en el llamador.
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    ejercicio = (_parse_date(fecha_txt, date.today()) or date.today()).year
    row = conn.execute(
        """
        SELECT COALESCE(MAX(numero), 0) AS ultimo
        FROM recepcion_facturas
        WHERE empresa_id = ? AND serie = ? AND ejercicio = ? AND tipo = ?
        """,
        (empresa_id, serie, ejercicio, tipo),
    ).fetchone()
    numero = (row["ultimo"] or 0) + 1
    numero_completo = f"{serie}-{ejercicio}-{numero:04d}"
    return ejercicio, numero, numero_completo


def _desglosar_total_con_impuesto(total, impuesto_pct):
    total = round(float(total or 0), 2)
    impuesto_pct = float(impuesto_pct or 0)
    if impuesto_pct <= 0:
        return total, 0.0, total
    base = round(total / (1 + impuesto_pct / 100), 2)
    impuesto = round(total - base, 2)
    return base, impuesto, total


def _actualizar_huella_factura(conn, factura_id, empresa_id):
    factura = conn.execute(
        "SELECT * FROM recepcion_facturas WHERE id = ? AND empresa_id = ?",
        (factura_id, empresa_id),
    ).fetchone()
    if not factura:
        return None
    lineas = conn.execute(
        """
        SELECT concepto, cantidad, precio_unitario, base, impuesto_pct, impuesto_codigo, impuesto, total, orden
        FROM recepcion_factura_lineas
        WHERE factura_id = ? AND empresa_id = ?
        ORDER BY orden, id
        """,
        (factura_id, empresa_id),
    ).fetchall()
    anterior = conn.execute(
        """
        SELECT hash_factura
        FROM recepcion_facturas
        WHERE empresa_id = ?
          AND id <> ?
          AND hash_factura IS NOT NULL
          AND TRIM(hash_factura) <> ''
        ORDER BY datetime(COALESCE(fecha_creacion, fecha, '1900-01-01')) DESC, id DESC
        LIMIT 1
        """,
        (empresa_id, factura_id),
    ).fetchone()
    hash_anterior = anterior["hash_factura"] if anterior else ""
    payload = {
        "factura": {
            "numero": factura["numero_completo"],
            "tipo": factura["tipo"],
            "fecha": factura["fecha"],
            "cliente_nif": factura["cliente_nif"],
            "base": round(_to_float(factura["base"], 0), 2),
            "impuestos": round(_to_float(factura["impuestos"], 0), 2),
            "total": round(_to_float(factura["total"], 0), 2),
            "estado": factura["estado"],
            "rectifica": factura["factura_rectificada_id"] if "factura_rectificada_id" in factura.keys() else None,
        },
        "lineas": [
            {
                "concepto": l["concepto"],
                "cantidad": round(_to_float(l["cantidad"], 0), 4),
                "base": round(_to_float(l["base"], 0), 2),
                "impuesto_pct": round(_to_float(l["impuesto_pct"], 0), 2),
                "impuesto_codigo": l["impuesto_codigo"],
                "impuesto": round(_to_float(l["impuesto"], 0), 2),
                "total": round(_to_float(l["total"], 0), 2),
            }
            for l in lineas
        ],
        "hash_anterior": hash_anterior,
    }
    serializado = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    huella = hashlib.sha256(serializado.encode("utf-8")).hexdigest()
    qr_texto = f"{factura['numero_completo']}|{factura['fecha']}|{_to_float(factura['total'], 0):.2f}|{huella[:16]}"
    conn.execute(
        """
        UPDATE recepcion_facturas
        SET hash_factura = ?,
            hash_anterior = ?,
            qr_texto = ?,
            verifactu_estado = COALESCE(NULLIF(verifactu_estado, ''), 'pendiente'),
            verifactu_payload = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (huella, hash_anterior, qr_texto, serializado, factura_id, empresa_id),
    )
    return huella


def _crear_factura_reserva(conn, reserva, empresa_id, datos):
    tipo = datos.get("tipo") or "factura"
    if tipo not in {"factura", "proforma"}:
        tipo = "factura"
    config = _config_fiscal(conn, empresa_id)
    serie_defecto = "PRO" if tipo == "proforma" else "REC"
    if config:
        serie_defecto = config["serie_proforma"] if tipo == "proforma" else config["serie_factura"]
    serie = (datos.get("serie") or serie_defecto or ("PRO" if tipo == "proforma" else "REC")).strip().upper()
    fecha_txt = datos.get("fecha") or date.today().isoformat()
    idioma = _idioma_documento(datos.get("idioma"))
    factura_txt = _factura_textos(idioma)
    ejercicio, numero, numero_completo = _generar_numero_factura(conn, empresa_id, tipo, serie, fecha_txt)
    incluir_estancia = datos.get("incluir_estancia", True)
    if isinstance(incluir_estancia, str):
        incluir_estancia = incluir_estancia.lower() not in {"0", "false", "no"}
    if incluir_estancia:
        reserva = _autocompletar_precio_reserva(conn, reserva, empresa_id)
    cliente_fiscal = _cliente_fiscal_reserva(conn, reserva, empresa_id)
    impuesto_estancia_id = datos.get("impuesto_estancia_id") or (config["impuesto_estancia_id"] if config else None)
    impuesto_extras_id = datos.get("impuesto_extras_id") or (config["impuesto_extras_id"] if config else None)
    impuesto_estancia = _impuesto_por_id(conn, impuesto_estancia_id, empresa_id)
    impuesto_extras = _impuesto_por_id(conn, impuesto_extras_id, empresa_id)
    if not impuesto_estancia:
        impuesto_estancia = _impuesto_manual(datos.get("iva_estancia"), "Impuesto estancia")
    if not impuesto_extras:
        impuesto_extras = impuesto_estancia if not datos.get("iva_extras") else _impuesto_manual(datos.get("iva_extras"), "Impuesto extras")
    iva_estancia = float(impuesto_estancia["porcentaje"] or 0)
    iva_extras = float(impuesto_extras["porcentaje"] or 0)
    lineas = []
    noches = max(((_parse_date(reserva["fecha_salida"]) or date.today()) - (_parse_date(reserva["fecha_entrada"]) or date.today())).days, 1)
    estancia_total = float(reserva["precio_total"] or 0)
    if incluir_estancia and estancia_total > 0:
        base, impuesto, total = _desglosar_total_con_impuesto(estancia_total, iva_estancia)
        lineas.append(
            {
                "origen": "estancia",
                "origen_id": reserva["id"],
                "concepto": factura_txt["estancia"].format(
                    entrada=reserva["fecha_entrada"],
                    salida=reserva["fecha_salida"],
                    noches=noches,
                ),
                "cantidad": 1,
                "precio_unitario": total,
                "base": base,
                "impuesto_pct": iva_estancia,
                "impuesto_id": impuesto_estancia["id"],
                "impuesto_codigo": impuesto_estancia["codigo"],
                "impuesto_nombre": impuesto_estancia["nombre"],
                "impuesto": impuesto,
                "total": total,
            }
        )
    cargos = conn.execute(
        """
        SELECT *
        FROM recepcion_cargos_reserva
        WHERE reserva_id = ?
          AND empresa_id = ?
          AND COALESCE(estado, 'pendiente') <> 'anulado'
          AND COALESCE(incluido, 0) = 0
          AND COALESCE(cargo_habitacion, 1) = 1
          AND COALESCE(total, 0) > 0
          AND factura_id IS NULL
        ORDER BY date(fecha), id
        """,
        (reserva["id"], empresa_id),
    ).fetchall()
    cargo_ids_facturados = []
    for cargo in cargos:
        base, impuesto, total = _desglosar_total_con_impuesto(cargo["total"], iva_extras)
        cargo_ids_facturados.append(cargo["id"])
        lineas.append(
            {
                "origen": "cargo",
                "origen_id": cargo["id"],
                "concepto": cargo["concepto"] or factura_txt["cargo"],
                "cantidad": cargo["cantidad"] or 1,
                "precio_unitario": cargo["precio_unitario"] or cargo["total"] or 0,
                "base": base,
                "impuesto_pct": iva_extras,
                "impuesto_id": impuesto_extras["id"],
                "impuesto_codigo": impuesto_extras["codigo"],
                "impuesto_nombre": impuesto_extras["nombre"],
                "impuesto": impuesto,
                "total": total,
            }
        )
    if not lineas:
        return None
    base_total = round(sum(l["base"] for l in lineas), 2)
    impuestos_total = round(sum(l["impuesto"] for l in lineas), 2)
    total_factura = round(sum(l["total"] for l in lineas), 2)
    pagado = _pagado_reserva(conn, reserva["id"], empresa_id)
    saldo = max(total_factura - pagado, 0)
    estado = "cobrada" if total_factura > 0 and saldo <= 0 else "emitida"
    jurisdicciones = {
        (impuesto_estancia["jurisdiccion"] or "").strip(),
        (impuesto_extras["jurisdiccion"] or "").strip(),
    } - {""}
    impuesto_jurisdiccion = jurisdicciones.pop() if len(jurisdicciones) == 1 else ("mixto" if jurisdicciones else "")
    conn.execute(
        """
        INSERT INTO recepcion_facturas
        (reserva_id, tipo, serie, ejercicio, numero, numero_completo, fecha, cliente_nombre,
         cliente_nif, cliente_direccion, cliente_email, idioma, impuesto_jurisdiccion, base, impuestos,
         total, pagado, saldo, estado, observaciones, empresa_id, usuario_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            reserva["id"],
            tipo,
            serie,
            ejercicio,
            numero,
            numero_completo,
            fecha_txt,
            datos.get("cliente_nombre") or cliente_fiscal["nombre"] or reserva["titular_nombre"] or "",
            datos.get("cliente_nif") or cliente_fiscal["nif"] or "",
            datos.get("cliente_direccion") or cliente_fiscal["direccion"] or "",
            datos.get("cliente_email") or cliente_fiscal["email"] or reserva["titular_email"] or "",
            idioma,
            impuesto_jurisdiccion,
            base_total,
            impuestos_total,
            total_factura,
            pagado,
            saldo,
            estado,
            datos.get("observaciones") or "",
            empresa_id,
            session.get("user_id"),
        ),
    )
    factura_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    for idx, linea in enumerate(lineas, start=1):
        conn.execute(
            """
            INSERT INTO recepcion_factura_lineas
            (factura_id, origen, origen_id, concepto, cantidad, precio_unitario, base,
             impuesto_pct, impuesto_id, impuesto_codigo, impuesto_nombre, impuesto, total, orden, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                factura_id,
                linea["origen"],
                linea["origen_id"],
                linea["concepto"],
                linea["cantidad"],
                linea["precio_unitario"],
                linea["base"],
                linea["impuesto_pct"],
                linea["impuesto_id"],
                linea["impuesto_codigo"],
                linea["impuesto_nombre"],
                linea["impuesto"],
                linea["total"],
                idx,
                empresa_id,
            ),
        )
    if cargo_ids_facturados and tipo == 'factura':
        placeholders = ",".join("?" for _ in cargo_ids_facturados)
        conn.execute(
            f"""
            UPDATE recepcion_cargos_reserva
            SET factura_id = ?
            WHERE empresa_id = ? AND id IN ({placeholders})
            """,
            [factura_id, empresa_id] + cargo_ids_facturados,
        )
    _actualizar_huella_factura(conn, factura_id, empresa_id)
    _actualizar_facturas_reserva(conn, reserva["id"], empresa_id)
    return factura_id


def _rellenar_factura_vacia_reserva(conn, factura_id, reserva, empresa_id, datos):
    factura = conn.execute(
        "SELECT * FROM recepcion_facturas WHERE id = ? AND empresa_id = ?",
        (factura_id, empresa_id),
    ).fetchone()
    if not factura:
        return None
    lineas_actuales = conn.execute(
        "SELECT COUNT(*) AS total FROM recepcion_factura_lineas WHERE factura_id = ? AND empresa_id = ?",
        (factura_id, empresa_id),
    ).fetchone()["total"] or 0
    if lineas_actuales or _to_float(factura["total"], 0) > 0:
        return factura_id

    reserva = _autocompletar_precio_reserva(conn, reserva, empresa_id)
    cliente_fiscal = _cliente_fiscal_reserva(conn, reserva, empresa_id)
    config = _config_fiscal(conn, empresa_id)
    idioma = _idioma_documento(datos.get("idioma") or factura["idioma"] or "es")
    factura_txt = _factura_textos(idioma)
    impuesto_estancia_id = datos.get("impuesto_estancia_id") or (config["impuesto_estancia_id"] if config else None)
    impuesto_extras_id = datos.get("impuesto_extras_id") or (config["impuesto_extras_id"] if config else None)
    impuesto_estancia = _impuesto_por_id(conn, impuesto_estancia_id, empresa_id) or _impuesto_manual(datos.get("iva_estancia"), "Impuesto estancia")
    impuesto_extras = _impuesto_por_id(conn, impuesto_extras_id, empresa_id) or impuesto_estancia
    iva_estancia = float(impuesto_estancia["porcentaje"] or 0)
    iva_extras = float(impuesto_extras["porcentaje"] or 0)
    noches = max(((_parse_date(reserva["fecha_salida"]) or date.today()) - (_parse_date(reserva["fecha_entrada"]) or date.today())).days, 1)
    lineas = []
    estancia_total = _to_float(reserva["precio_total"] if "precio_total" in reserva.keys() else 0, 0)
    if estancia_total > 0:
        base, impuesto, total = _desglosar_total_con_impuesto(estancia_total, iva_estancia)
        lineas.append(
            {
                "origen": "estancia",
                "origen_id": reserva["id"],
                "concepto": factura_txt["estancia"].format(
                    entrada=reserva["fecha_entrada"],
                    salida=reserva["fecha_salida"],
                    noches=noches,
                ),
                "cantidad": 1,
                "precio_unitario": total,
                "base": base,
                "impuesto_pct": iva_estancia,
                "impuesto_id": impuesto_estancia["id"],
                "impuesto_codigo": impuesto_estancia["codigo"],
                "impuesto_nombre": impuesto_estancia["nombre"],
                "impuesto": impuesto,
                "total": total,
            }
        )
    cargos = conn.execute(
        """
        SELECT *
        FROM recepcion_cargos_reserva
        WHERE reserva_id = ?
          AND empresa_id = ?
          AND COALESCE(estado, 'pendiente') <> 'anulado'
          AND COALESCE(incluido, 0) = 0
          AND COALESCE(cargo_habitacion, 1) = 1
          AND COALESCE(total, 0) > 0
          AND factura_id IS NULL
        ORDER BY date(fecha), id
        """,
        (reserva["id"], empresa_id),
    ).fetchall()
    cargo_ids_facturados = []
    for cargo in cargos:
        base, impuesto, total = _desglosar_total_con_impuesto(cargo["total"], iva_extras)
        cargo_ids_facturados.append(cargo["id"])
        lineas.append(
            {
                "origen": "cargo",
                "origen_id": cargo["id"],
                "concepto": cargo["concepto"] or factura_txt["cargo"],
                "cantidad": cargo["cantidad"] or 1,
                "precio_unitario": cargo["precio_unitario"] or cargo["total"] or 0,
                "base": base,
                "impuesto_pct": iva_extras,
                "impuesto_id": impuesto_extras["id"],
                "impuesto_codigo": impuesto_extras["codigo"],
                "impuesto_nombre": impuesto_extras["nombre"],
                "impuesto": impuesto,
                "total": total,
            }
        )
    if not lineas:
        return None

    base_total = round(sum(l["base"] for l in lineas), 2)
    impuestos_total = round(sum(l["impuesto"] for l in lineas), 2)
    total_factura = round(sum(l["total"] for l in lineas), 2)
    pagado = _pagado_reserva(conn, reserva["id"], empresa_id)
    saldo = max(total_factura - pagado, 0)
    estado = "cobrada" if total_factura > 0 and saldo <= 0 else "emitida"
    jurisdicciones = {
        (impuesto_estancia["jurisdiccion"] or "").strip(),
        (impuesto_extras["jurisdiccion"] or "").strip(),
    } - {""}
    impuesto_jurisdiccion = jurisdicciones.pop() if len(jurisdicciones) == 1 else ("mixto" if jurisdicciones else "")
    conn.execute(
        """
        UPDATE recepcion_facturas
        SET cliente_nombre = ?,
            cliente_nif = ?,
            cliente_direccion = ?,
            cliente_email = ?,
            idioma = ?,
            impuesto_jurisdiccion = ?,
            base = ?,
            impuestos = ?,
            total = ?,
            pagado = ?,
            saldo = ?,
            estado = ?,
            observaciones = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (
            datos.get("cliente_nombre") or cliente_fiscal["nombre"] or reserva["titular_nombre"] or factura["cliente_nombre"] or "",
            datos.get("cliente_nif") or cliente_fiscal["nif"] or factura["cliente_nif"] or "",
            datos.get("cliente_direccion") or cliente_fiscal["direccion"] or factura["cliente_direccion"] or "",
            datos.get("cliente_email") or cliente_fiscal["email"] or reserva["titular_email"] or factura["cliente_email"] or "",
            idioma,
            impuesto_jurisdiccion,
            base_total,
            impuestos_total,
            total_factura,
            pagado,
            saldo,
            estado,
            datos.get("observaciones") or factura["observaciones"] or "Factura recalculada desde reserva",
            factura_id,
            empresa_id,
        ),
    )
    for idx, linea in enumerate(lineas, start=1):
        conn.execute(
            """
            INSERT INTO recepcion_factura_lineas
            (factura_id, origen, origen_id, concepto, cantidad, precio_unitario, base,
             impuesto_pct, impuesto_id, impuesto_codigo, impuesto_nombre, impuesto, total, orden, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                factura_id,
                linea["origen"],
                linea["origen_id"],
                linea["concepto"],
                linea["cantidad"],
                linea["precio_unitario"],
                linea["base"],
                linea["impuesto_pct"],
                linea["impuesto_id"],
                linea["impuesto_codigo"],
                linea["impuesto_nombre"],
                linea["impuesto"],
                linea["total"],
                idx,
                empresa_id,
            ),
        )
    if cargo_ids_facturados:
        placeholders = ",".join("?" for _ in cargo_ids_facturados)
        conn.execute(
            f"""
            UPDATE recepcion_cargos_reserva
            SET factura_id = ?
            WHERE empresa_id = ? AND id IN ({placeholders})
            """,
            [factura_id, empresa_id] + cargo_ids_facturados,
        )
    _actualizar_huella_factura(conn, factura_id, empresa_id)
    return factura_id


def _crear_factura_rectificativa(conn, factura, lineas, empresa_id, datos):
    if not factura or not lineas:
        return None
    original = conn.execute(
        'SELECT tipo, estado FROM recepcion_facturas WHERE id = ? AND empresa_id = ?',
        (factura['id'], empresa_id),
    ).fetchone()
    if not original or original['tipo'] != 'factura' or original['estado'] in {'anulada', 'rectificada'}:
        return None
    existente = conn.execute("""
        SELECT id FROM recepcion_facturas
        WHERE factura_rectificada_id = ? AND empresa_id = ?
          AND COALESCE(estado, 'emitida') <> 'anulada'
    """, (factura['id'], empresa_id)).fetchone()
    if existente:
        return None
    fecha_txt = datos.get("fecha") or date.today().isoformat()
    serie = (datos.get("serie") or "REC-R").strip().upper()
    idioma = _idioma_documento(datos.get("idioma") or factura["idioma"])
    ejercicio, numero, numero_completo = _generar_numero_factura(conn, empresa_id, "rectificativa", serie, fecha_txt)
    motivo = (datos.get("motivo") or "Rectificacion de factura").strip()
    nuevas_lineas = []
    for linea in lineas:
        nuevas_lineas.append(
            {
                "origen": "rectificativa",
                "origen_id": linea["id"],
                "concepto": f"Rectificacion {factura['numero_completo']}: {linea['concepto']}",
                "cantidad": -(float(linea["cantidad"] or 0)),
                "precio_unitario": float(linea["precio_unitario"] or 0),
                "base": -(float(linea["base"] or 0)),
                "impuesto_pct": float(linea["impuesto_pct"] or 0),
                "impuesto_id": linea["impuesto_id"],
                "impuesto_codigo": linea["impuesto_codigo"],
                "impuesto_nombre": linea["impuesto_nombre"],
                "impuesto": -(float(linea["impuesto"] or 0)),
                "total": -(float(linea["total"] or 0)),
            }
        )
    base_total = round(sum(l["base"] for l in nuevas_lineas), 2)
    impuestos_total = round(sum(l["impuesto"] for l in nuevas_lineas), 2)
    total_factura = round(sum(l["total"] for l in nuevas_lineas), 2)
    conn.execute(
        """
        INSERT INTO recepcion_facturas
        (reserva_id, tipo, serie, ejercicio, numero, numero_completo, fecha, cliente_nombre,
         cliente_nif, cliente_direccion, cliente_email, idioma, impuesto_jurisdiccion,
         factura_rectificada_id, motivo_rectificacion, base, impuestos, total, pagado, saldo,
         estado, observaciones, empresa_id, usuario_id)
        VALUES (?, 'rectificativa', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0,
                'emitida', ?, ?, ?)
        """,
        (
            factura["reserva_id"],
            serie,
            ejercicio,
            numero,
            numero_completo,
            fecha_txt,
            factura["cliente_nombre"] or "",
            factura["cliente_nif"] or "",
            factura["cliente_direccion"] or "",
            factura["cliente_email"] or "",
            idioma,
            factura["impuesto_jurisdiccion"] or "",
            factura["id"],
            motivo,
            base_total,
            impuestos_total,
            total_factura,
            datos.get("observaciones") or motivo,
            empresa_id,
            session.get("user_id"),
        ),
    )
    rectificativa_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    for idx, linea in enumerate(nuevas_lineas, start=1):
        conn.execute(
            """
            INSERT INTO recepcion_factura_lineas
            (factura_id, origen, origen_id, concepto, cantidad, precio_unitario, base,
             impuesto_pct, impuesto_id, impuesto_codigo, impuesto_nombre, impuesto, total, orden, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rectificativa_id,
                linea["origen"],
                linea["origen_id"],
                linea["concepto"],
                linea["cantidad"],
                linea["precio_unitario"],
                linea["base"],
                linea["impuesto_pct"],
                linea["impuesto_id"],
                linea["impuesto_codigo"],
                linea["impuesto_nombre"],
                linea["impuesto"],
                linea["total"],
                idx,
                empresa_id,
            ),
        )
    conn.execute(
        """
        UPDATE recepcion_facturas
        SET estado = 'rectificada',
            motivo_rectificacion = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (motivo, factura["id"], empresa_id),
    )
    _actualizar_huella_factura(conn, factura["id"], empresa_id)
    _actualizar_huella_factura(conn, rectificativa_id, empresa_id)
    return rectificativa_id


def _actualizar_facturas_reserva(conn, reserva_id, empresa_id):
    pagado = _pagado_reserva(conn, reserva_id, empresa_id)
    facturas = _facturas_reserva(conn, reserva_id, empresa_id)
    disponible = max(float(pagado or 0), 0)
    for factura in sorted(facturas, key=lambda f: (str(f['fecha'] or ''), f['id'])):
        if factura["estado"] in {"anulada", "rectificada"}:
            continue
        total = max(float(factura['total'] or 0), 0)
        if factura['tipo'] == 'proforma':
            imputado = min(max(float(pagado or 0), 0), total)
        elif factura['tipo'] == 'factura':
            imputado = min(disponible, total)
            disponible = round(disponible - imputado, 2)
        else:
            continue
        saldo = round(max(total - imputado, 0), 2)
        estado = factura["estado"]
        if factura["estado"] in {"emitida", "cobrada"}:
            estado = "cobrada" if float(factura["total"] or 0) > 0 and saldo <= 0 else "emitida"
        conn.execute(
            """
            UPDATE recepcion_facturas
            SET pagado = ?, saldo = ?, estado = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND empresa_id = ?
            """,
            (imputado, saldo, estado, factura["id"], empresa_id),
        )


def _filtros_registro_viajeros(args):
    estado = args.get("estado")
    if estado is None:
        estado = "pendiente"
    return {
        "estado": estado.strip(),
        "desde": args.get("desde") or "",
        "hasta": args.get("hasta") or "",
        "q": (args.get("q") or "").strip(),
    }


def _query_registro_viajeros(conn, empresa_id, filtros):
    where = ["v.empresa_id = ?"]
    params = [empresa_id]
    estado = filtros.get("estado") or ""
    if estado:
        where.append("COALESCE(v.registro_viajeros_estado, 'pendiente') = ?")
        params.append(estado)
    if filtros.get("desde"):
        where.append("date(r.fecha_entrada) >= date(?)")
        params.append(filtros["desde"])
    if filtros.get("hasta"):
        where.append("date(r.fecha_entrada) <= date(?)")
        params.append(filtros["hasta"])
    if filtros.get("q"):
        like = f"%{filtros['q']}%"
        where.append(
            "(v.nombre LIKE ? OR v.apellidos LIKE ? OR v.numero_documento LIKE ? OR r.codigo LIKE ? OR r.titular_nombre LIKE ? OR h.numero LIKE ? OR h.nombre LIKE ?)"
        )
        params.extend([like, like, like, like, like, like, like])
    return conn.execute(
        f"""
        SELECT v.*, r.codigo, r.fecha_entrada, r.fecha_salida, r.titular_nombre,
               h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
        FROM viajeros_recepcion v
        JOIN reservas_recepcion r ON v.reserva_id = r.id
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE {" AND ".join(where)}
        ORDER BY date(r.fecha_entrada), h.numero, v.es_titular DESC, v.apellidos, v.nombre
        """,
        params,
    ).fetchall()


def _edad_min_documento_config(conn, empresa_id):
    config = _config_fiscal(conn, empresa_id)
    if config and "edad_min_documento" in config.keys():
        edad = _to_int(config["edad_min_documento"], EDAD_DOCUMENTO_OBLIGATORIO_DEFAULT)
        return max(0, edad)
    return EDAD_DOCUMENTO_OBLIGATORIO_DEFAULT


def _edad_en_fecha(fecha_nacimiento, fecha_referencia):
    nacimiento = _parse_date(fecha_nacimiento)
    referencia = _parse_date(fecha_referencia, date.today())
    if not nacimiento or not referencia:
        return None
    return referencia.year - nacimiento.year - ((referencia.month, referencia.day) < (nacimiento.month, nacimiento.day))


def _documento_obligatorio_viajero(viajero, edad_min_documento=EDAD_DOCUMENTO_OBLIGATORIO_DEFAULT):
    fecha_referencia = viajero["fecha_entrada"] if "fecha_entrada" in viajero.keys() else date.today().isoformat()
    edad = _edad_en_fecha(viajero["fecha_nacimiento"], fecha_referencia)
    return edad is None or edad >= edad_min_documento


def _viajero_faltantes(viajero, edad_min_documento=EDAD_DOCUMENTO_OBLIGATORIO_DEFAULT):
    checks = [
        ("nombre", "nombre"),
        ("apellidos", "apellidos"),
        ("fecha_nacimiento", "fecha nacimiento"),
        ("nacionalidad", "nacionalidad"),
        ("pais", "pais"),
    ]
    if _documento_obligatorio_viajero(viajero, edad_min_documento):
        checks[3:3] = [
            ("tipo_documento", "tipo documento"),
            ("numero_documento", "numero documento"),
        ]
    return [label for key, label in checks if not (viajero[key] or "").strip()]


def _viajeros_para_alerta_registro(conn, empresa_id, hasta_fecha):
    hoy = date.today().isoformat()
    return conn.execute(
        """
        SELECT v.*, r.codigo, r.fecha_entrada, r.fecha_salida, r.titular_nombre,
               h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
        FROM viajeros_recepcion v
        JOIN reservas_recepcion r ON v.reserva_id = r.id
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE v.empresa_id = ?
          AND COALESCE(v.registro_viajeros_estado, 'pendiente') IN ('pendiente', 'preparado', 'rechazado')
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND date(r.fecha_entrada) <= date(?)
          AND date(r.fecha_salida) >= date(?)
        ORDER BY date(r.fecha_entrada), h.numero, v.es_titular DESC, v.apellidos, v.nombre
        """,
        (empresa_id, hasta_fecha, hoy),
    ).fetchall()


def _reservas_sin_viajeros_alerta(conn, empresa_id, hasta_fecha):
    hoy = date.today().isoformat()
    return conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM reservas_recepcion r
        WHERE r.empresa_id = ?
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND date(r.fecha_entrada) <= date(?)
          AND date(r.fecha_salida) >= date(?)
          AND NOT EXISTS (
              SELECT 1
              FROM viajeros_recepcion v
              WHERE v.reserva_id = r.id AND v.empresa_id = r.empresa_id
          )
        """,
        (empresa_id, hasta_fecha, hoy),
    ).fetchone()["total"] or 0


def _resumen_alerta_registro_viajeros(conn, empresa_id):
    config = _config_fiscal(conn, empresa_id)
    dias = 1
    hora = "22:00"
    if config:
        dias = max(0, _to_int(config["registro_viajeros_alerta_dias"] if "registro_viajeros_alerta_dias" in config.keys() else 1, 1))
        hora = (config["registro_viajeros_alerta_hora"] if "registro_viajeros_alerta_hora" in config.keys() else "") or "22:00"
    hasta = (date.today() + timedelta(days=dias)).isoformat()
    edad_min = _edad_min_documento_config(conn, empresa_id)
    viajeros = _viajeros_para_alerta_registro(conn, empresa_id, hasta)
    completos = 0
    incompletos = 0
    for viajero in viajeros:
        if _viajero_faltantes(viajero, edad_min):
            incompletos += 1
        else:
            completos += 1
    return {
        "hasta": hasta,
        "hora": hora,
        "dias": dias,
        "total": len(viajeros),
        "completos": completos,
        "incompletos": incompletos,
        "reservas_sin_viajeros": _reservas_sin_viajeros_alerta(conn, empresa_id, hasta),
    }


def _csv_registro_viajeros_texto(viajeros, edad_min_documento):
    output = StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "reserva",
            "fecha_entrada",
            "fecha_salida",
            "habitacion",
            "titular_reserva",
            "nombre",
            "apellidos",
            "fecha_nacimiento",
            "sexo",
            "nacionalidad",
            "tipo_documento",
            "numero_documento",
            "soporte_documento",
            "telefono",
            "email",
            "direccion",
            "codigo_postal",
            "localidad",
            "pais",
            "estado_registro",
            "fecha_registro",
            "faltantes",
        ]
    )
    for v in viajeros:
        writer.writerow(
            [
                v["codigo"] or v["reserva_id"],
                v["fecha_entrada"] or "",
                v["fecha_salida"] or "",
                v["habitacion_numero"] or v["habitacion_nombre"] or "",
                v["titular_nombre"] or "",
                v["nombre"] or "",
                v["apellidos"] or "",
                v["fecha_nacimiento"] or "",
                v["sexo"] or "",
                v["nacionalidad"] or "",
                v["tipo_documento"] or "",
                v["numero_documento"] or "",
                v["soporte_documento"] or "",
                v["telefono"] or "",
                v["email"] or "",
                v["direccion"] or "",
                v["codigo_postal"] or "",
                v["localidad"] or "",
                v["pais"] or "",
                v["registro_viajeros_estado"] or "pendiente",
                v["registro_viajeros_fecha"] or "",
                ", ".join(_viajero_faltantes(v, edad_min_documento)),
            ]
        )
    return output.getvalue()


def _recalcular_pagos_reserva(conn, reserva_id, empresa_id):
    reserva = conn.execute(
        "SELECT precio_total, extras_total, deposito FROM reservas_recepcion WHERE id = ? AND empresa_id = ?",
        (reserva_id, empresa_id),
    ).fetchone()
    if not reserva:
        return
    pagos_info = conn.execute(
        """
        SELECT COUNT(*) AS movimientos,
               COALESCE(SUM(CASE WHEN COALESCE(estado, 'confirmado') <> 'anulado' THEN importe ELSE 0 END), 0) AS total
        FROM pagos_reserva_recepcion
        WHERE reserva_id = ? AND empresa_id = ?
        """,
        (reserva_id, empresa_id),
    ).fetchone()
    if (pagos_info["movimientos"] or 0) > 0:
        pagado = pagos_info["total"] or 0
    else:
        pagado = reserva["deposito"] or 0
    total = float(reserva["precio_total"] or 0) + float(reserva["extras_total"] or 0)
    saldo = max(total - float(pagado), 0)
    conn.execute(
        """
        UPDATE reservas_recepcion
        SET deposito = ?, saldo = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (pagado, saldo, reserva_id, empresa_id),
    )
    _actualizar_facturas_reserva(conn, reserva_id, empresa_id)


def _recalcular_cargos_reserva(conn, reserva_id, empresa_id):
    extras = conn.execute(
        """
        SELECT COALESCE(SUM(total), 0) AS total
        FROM recepcion_cargos_reserva
        WHERE reserva_id = ?
          AND empresa_id = ?
          AND COALESCE(estado, 'pendiente') <> 'anulado'
          AND COALESCE(incluido, 0) = 0
          AND COALESCE(cargo_habitacion, 1) = 1
        """,
        (reserva_id, empresa_id),
    ).fetchone()["total"] or 0
    conn.execute(
        """
        UPDATE reservas_recepcion
        SET extras_total = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (extras, reserva_id, empresa_id),
    )
    _recalcular_pagos_reserva(conn, reserva_id, empresa_id)


def _marcar_habitacion_estado(conn, habitacion_id, empresa_id, estado):
    if not habitacion_id:
        return
    conn.execute(
        "UPDATE habitaciones_recepcion SET estado = ? WHERE id = ? AND empresa_id = ?",
        (estado, habitacion_id, empresa_id),
    )


def _guardar_avisos_viajero(conn, viajero_id, reserva_id, empresa_id, descripcion, departamentos):
    conn.execute(
        "DELETE FROM recepcion_avisos WHERE viajero_id = ? AND origen = 'auto_viajero'",
        (viajero_id,),
    )
    descripcion = (descripcion or "").strip()
    if not descripcion:
        return
    departamentos_txt = ",".join(departamentos or ["cocina"])
    conn.execute(
        """
        INSERT INTO recepcion_avisos
        (reserva_id, viajero_id, servicio, tipo, descripcion, departamentos, prioridad, origen, empresa_id)
        VALUES (?, ?, 'todos', 'alergia_intolerancia', ?, ?, 'alta', 'auto_viajero', ?)
        """,
        (reserva_id, viajero_id, descripcion, departamentos_txt, empresa_id),
    )


def _avisos_reserva(conn, reserva_id, empresa_id):
    return conn.execute(
        """
        SELECT a.*, v.nombre, v.apellidos
        FROM recepcion_avisos a
        LEFT JOIN viajeros_recepcion v ON a.viajero_id = v.id
        WHERE a.reserva_id = ? AND a.empresa_id = ?
        ORDER BY CASE a.prioridad WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END, a.id
        """,
        (reserva_id, empresa_id),
    ).fetchall()


def _servicios_regimen_activos(conn, regimen, empresa_id):
    rows = conn.execute(
        """
        SELECT DISTINCT servicio
        FROM recepcion_regimen_inclusiones
        WHERE empresa_id = ?
          AND regimen = ?
          AND COALESCE(activo, 1) = 1
          AND COALESCE(incluido, 1) = 1
        ORDER BY servicio
        """,
        (empresa_id, regimen or "solo_alojamiento"),
    ).fetchall()
    servicios = [row["servicio"] for row in rows if row["servicio"]]
    return servicios or _servicios_por_regimen(regimen)


def _estado_dia_estancia(reserva, fecha_obj):
    entrada = _parse_date(reserva["fecha_entrada"])
    salida = _parse_date(reserva["fecha_salida"])
    if entrada == fecha_obj and salida == fecha_obj:
        return "entrada/salida"
    if entrada == fecha_obj:
        return "llegada"
    if salida == fecha_obj:
        return "salida"
    return "estancia"


def _prevision_servicios_periodo(conn, desde_txt, hasta_txt=None, servicio_filtro=""):
    empresa_id = _empresa_id()
    desde_obj = _parse_date(desde_txt, date.today())
    hasta_obj = _parse_date(hasta_txt, desde_obj) if hasta_txt else desde_obj
    if hasta_obj < desde_obj:
        hasta_obj = desde_obj
    if (hasta_obj - desde_obj).days > 31:
        hasta_obj = desde_obj + timedelta(days=31)
    reservas = conn.execute(
        """
        SELECT r.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE r.empresa_id = ?
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'no_show')
          AND date(r.fecha_entrada) <= date(?)
          AND date(r.fecha_salida) >= date(?)
        ORDER BY date(r.fecha_entrada), h.numero, r.titular_nombre
        """,
        (empresa_id, hasta_obj.isoformat(), desde_obj.isoformat()),
    ).fetchall()
    resumen = {}
    resumen_dias = {}
    dias_operativos = {}
    total_periodo = {
        "dias": 0,
        "reservas_estancia": 0,
        "pax_estancia": 0,
        "pax_alojados": 0,
        "entradas_reservas": 0,
        "entradas_pax": 0,
        "salidas_reservas": 0,
        "salidas_pax": 0,
        "regimenes": {key: 0 for key in REGIMENES.keys()},
        "servicios": {key: 0 for key in SERVICIOS_COMIDA.keys()},
        "avisos": 0,
    }
    estancias = []
    detalle = []
    fecha_actual = desde_obj
    while fecha_actual <= hasta_obj:
        dias_operativos[fecha_actual.isoformat()] = {
            "fecha": fecha_actual.isoformat(),
            "pax_alojados": 0,
            "entradas_reservas": 0,
            "entradas_pax": 0,
            "salidas_reservas": 0,
            "salidas_pax": 0,
            "regimenes": {key: 0 for key in REGIMENES.keys()},
            "servicios": {key: 0 for key in SERVICIOS_COMIDA.keys()},
            "avisos": 0,
        }
        fecha_actual += timedelta(days=1)
    total_periodo["dias"] = len(dias_operativos)
    for reserva in reservas:
        entrada = _parse_date(reserva["fecha_entrada"])
        salida = _parse_date(reserva["fecha_salida"])
        if not entrada or not salida:
            continue
        personas = _to_int(reserva["adultos"], 0) + _to_int(reserva["menores"], 0)
        avisos = _avisos_reserva(conn, reserva["id"], empresa_id)
        avisos_txt = "; ".join(
            f"{(a['nombre'] or '').strip()} {(a['apellidos'] or '').strip()}: {a['descripcion']}"
            for a in avisos
            if "cocina" in (a["departamentos"] or "") or not a["departamentos"]
        )
        servicios_regimen = _servicios_regimen_activos(conn, reserva["regimen"], empresa_id)
        cuenta_en_periodo = entrada <= hasta_obj and salida >= desde_obj
        if cuenta_en_periodo:
            total_periodo["reservas_estancia"] += 1
            total_periodo["pax_estancia"] += personas
        fecha_actual = desde_obj
        while fecha_actual <= hasta_obj:
            fecha_key = fecha_actual.isoformat()
            dia_operativo = dias_operativos[fecha_key]
            if entrada <= fecha_actual < salida:
                dia_operativo["pax_alojados"] += personas
                total_periodo["pax_alojados"] += personas
                regimen_key = reserva["regimen"] or "solo_alojamiento"
                dia_operativo["regimenes"][regimen_key] = dia_operativo["regimenes"].get(regimen_key, 0) + personas
                total_periodo["regimenes"][regimen_key] = total_periodo["regimenes"].get(regimen_key, 0) + personas
                if avisos_txt:
                    dia_operativo["avisos"] += 1
                    total_periodo["avisos"] += 1
            if entrada == fecha_actual:
                dia_operativo["entradas_reservas"] += 1
                dia_operativo["entradas_pax"] += personas
                total_periodo["entradas_reservas"] += 1
                total_periodo["entradas_pax"] += personas
            if salida == fecha_actual:
                dia_operativo["salidas_reservas"] += 1
                dia_operativo["salidas_pax"] += personas
                total_periodo["salidas_reservas"] += 1
                total_periodo["salidas_pax"] += personas
            fecha_actual += timedelta(days=1)
        estancias.append(
            {
                "reserva": reserva,
                "habitacion": _nombre_habitacion(reserva),
                "personas": personas,
                "regimen": REGIMENES.get(reserva["regimen"], reserva["regimen"]),
                "servicios": ", ".join(SERVICIOS_COMIDA.get(s, s) for s in servicios_regimen) or "-",
                "avisos": avisos_txt,
            }
        )
        fecha_actual = desde_obj
        while fecha_actual <= hasta_obj:
            fecha_key = fecha_actual.isoformat()
            for servicio in servicios_regimen:
                if servicio_filtro and servicio != servicio_filtro:
                    continue
                if not _servicio_aplica_fecha(servicio, entrada, salida, fecha_actual):
                    continue
                dias_operativos[fecha_key]["servicios"][servicio] = dias_operativos[fecha_key]["servicios"].get(servicio, 0) + personas
                total_periodo["servicios"][servicio] = total_periodo["servicios"].get(servicio, 0) + personas
                if servicio not in resumen:
                    resumen[servicio] = {
                        "servicio": servicio,
                        "nombre": SERVICIOS_COMIDA.get(servicio, servicio),
                        "personas": 0,
                        "reservas": 0,
                        "avisos": 0,
                    }
                resumen[servicio]["personas"] += personas
                resumen[servicio]["reservas"] += 1
                if avisos_txt:
                    resumen[servicio]["avisos"] += 1

                if fecha_key not in resumen_dias:
                    resumen_dias[fecha_key] = {
                        "fecha": fecha_key,
                        "personas": 0,
                        "reservas": 0,
                        "avisos": 0,
                        "servicios": {},
                    }
                resumen_dias[fecha_key]["personas"] += personas
                resumen_dias[fecha_key]["reservas"] += 1
                if avisos_txt:
                    resumen_dias[fecha_key]["avisos"] += 1
                resumen_dias[fecha_key]["servicios"][servicio] = resumen_dias[fecha_key]["servicios"].get(servicio, 0) + personas

                detalle.append(
                    {
                        "fecha": fecha_key,
                        "servicio": servicio,
                        "servicio_nombre": SERVICIOS_COMIDA.get(servicio, servicio),
                        "reserva": reserva,
                        "habitacion": _nombre_habitacion(reserva),
                        "personas": personas,
                        "adultos": _to_int(reserva["adultos"], 0),
                        "menores": _to_int(reserva["menores"], 0),
                        "regimen": REGIMENES.get(reserva["regimen"], reserva["regimen"]),
                        "estado_dia": _estado_dia_estancia(reserva, fecha_actual),
                        "avisos": avisos_txt,
                    }
                )
            fecha_actual += timedelta(days=1)

    resumen_lista = [resumen[key] for key in SERVICIOS_COMIDA.keys() if key in resumen]
    resumen_lista.extend(row for key, row in resumen.items() if key not in SERVICIOS_COMIDA)
    resumen_dias_lista = []
    for key in sorted(resumen_dias.keys()):
        dia = resumen_dias[key]
        dia["servicios_txt"] = " / ".join(
            f"{SERVICIOS_COMIDA.get(servicio, servicio)}: {dia['servicios'][servicio]}"
            for servicio in SERVICIOS_COMIDA.keys()
            if servicio in dia["servicios"]
        )
        resumen_dias_lista.append(dia)
    dias_operativos_lista = []
    for key in sorted(dias_operativos.keys()):
        dia = dias_operativos[key]
        dia["regimenes_txt"] = " / ".join(
            f"{label}: {dia['regimenes'].get(regimen, 0)}"
            for regimen, label in REGIMENES.items()
            if dia["regimenes"].get(regimen, 0)
        ) or "-"
        dia["servicios_txt"] = " / ".join(
            f"{label}: {dia['servicios'].get(servicio, 0)}"
            for servicio, label in SERVICIOS_COMIDA.items()
            if dia["servicios"].get(servicio, 0)
        ) or "-"
        dias_operativos_lista.append(dia)
    total_periodo["regimenes_txt"] = " / ".join(
        f"{label}: {total_periodo['regimenes'].get(regimen, 0)}"
        for regimen, label in REGIMENES.items()
        if total_periodo["regimenes"].get(regimen, 0)
    ) or "-"
    total_periodo["servicios_txt"] = " / ".join(
        f"{label}: {total_periodo['servicios'].get(servicio, 0)}"
        for servicio, label in SERVICIOS_COMIDA.items()
        if total_periodo["servicios"].get(servicio, 0)
    ) or "-"
    detalle.sort(key=lambda d: (d["fecha"], d["servicio_nombre"], d["habitacion"], d["reserva"]["titular_nombre"] or ""))
    return desde_obj, hasta_obj, resumen_lista, resumen_dias_lista, dias_operativos_lista, total_periodo, estancias, detalle


def _prevision_servicios(conn, fecha_txt, servicio_filtro=""):
    fecha_obj, _hasta, resumen, _resumen_dias, _dias_operativos, _total_periodo, _estancias, detalle = _prevision_servicios_periodo(
        conn,
        fecha_txt,
        fecha_txt,
        servicio_filtro,
    )
    return fecha_obj, resumen, detalle


def _centro_sql_habitacion(alias="h"):
    centro_id = centro_activo_id()
    if not centro_id:
        return "", []
    return f" AND {alias}.centro_id = ?", [centro_id]


def _centro_sql_reserva(alias="r"):
    centro_id = centro_activo_id()
    if not centro_id:
        return "", []
    return (
        f" AND (COALESCE({alias}.centro_id, 0) = ? OR {alias}.habitacion_id IN "
        "(SELECT id FROM habitaciones_recepcion WHERE centro_id = ?))",
        [centro_id, centro_id],
    )


def _pms_operativo_dashboard(conn, empresa_id, hoy, reserva_centro_sql, reserva_centro_params, habitacion_centro_sql, habitacion_centro_params):
    hoy_obj = date.fromisoformat(hoy)
    manana = (hoy_obj + timedelta(days=1)).isoformat()
    kpis = {}
    kpis["reservas_sin_habitacion"] = conn.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM reservas_recepcion r
        WHERE r.empresa_id = ?
          AND r.habitacion_id IS NULL
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND date(r.fecha_salida) >= date(?)
          {reserva_centro_sql}
        """,
        [empresa_id, hoy] + reserva_centro_params,
    ).fetchone()["total"] or 0
    kpis["llegadas_manana"] = conn.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM reservas_recepcion r
        WHERE r.empresa_id = ?
          AND date(r.fecha_entrada) = date(?)
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'no_show')
          {reserva_centro_sql}
        """,
        [empresa_id, manana] + reserva_centro_params,
    ).fetchone()["total"] or 0
    kpis["salidas_manana"] = conn.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM reservas_recepcion r
        WHERE r.empresa_id = ?
          AND date(r.fecha_salida) = date(?)
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'no_show')
          {reserva_centro_sql}
        """,
        [empresa_id, manana] + reserva_centro_params,
    ).fetchone()["total"] or 0
    kpis["habitaciones_sucias"] = conn.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM habitaciones_recepcion h
        WHERE h.empresa_id = ?
          AND COALESCE(h.activo,1)=1
          AND LOWER(COALESCE(h.estado,'')) IN ('sucia','limpieza','repaso','mantenimiento')
          {habitacion_centro_sql}
        """,
        [empresa_id] + habitacion_centro_params,
    ).fetchone()["total"] or 0
    kpis["habitaciones_bloqueadas"] = 0
    if _table_exists(conn, "bloqueos_habitacion_recepcion"):
        kpis["habitaciones_bloqueadas"] = conn.execute(
            f"""
            SELECT COUNT(DISTINCT b.habitacion_id) AS total
            FROM bloqueos_habitacion_recepcion b
            JOIN habitaciones_recepcion h ON h.id = b.habitacion_id
            WHERE b.empresa_id = ?
              AND COALESCE(b.estado, 'activo') = 'activo'
              AND date(b.fecha_inicio) <= date(?)
              AND date(b.fecha_fin) >= date(?)
              {habitacion_centro_sql}
            """,
            [empresa_id, hoy, hoy] + habitacion_centro_params,
        ).fetchone()["total"] or 0
    kpis["saldo_pendiente"] = conn.execute(
        f"""
        SELECT ROUND(SUM(COALESCE(r.saldo, 0)), 2) AS total
        FROM reservas_recepcion r
        WHERE r.empresa_id = ?
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'no_show')
          AND date(r.fecha_salida) >= date(?)
          {reserva_centro_sql}
        """,
        [empresa_id, hoy] + reserva_centro_params,
    ).fetchone()["total"] or 0
    kpis["facturas_abiertas"] = 0
    if _table_exists(conn, "recepcion_facturas"):
        kpis["facturas_abiertas"] = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM recepcion_facturas
            WHERE empresa_id = ?
              AND COALESCE(estado, 'pendiente') NOT IN ('pagada', 'anulada', 'rectificada')
            """,
            (empresa_id,),
        ).fetchone()["total"] or 0
    caja_hoy = {"total": 0, "diferencia": 0}
    if _table_exists(conn, "pagos_reserva_recepcion"):
        caja_hoy = conn.execute(
            """
            SELECT ROUND(SUM(CASE WHEN COALESCE(estado,'activo') <> 'anulado' THEN COALESCE(importe,0) ELSE 0 END), 2) AS total,
                   0 AS diferencia
            FROM pagos_reserva_recepcion
            WHERE empresa_id = ? AND date(fecha) = date(?)
            """,
            (empresa_id, hoy),
        ).fetchone()
    if _table_exists(conn, "recepcion_cierres_caja"):
        cierre = conn.execute(
            """
            SELECT ROUND(SUM(COALESCE(total_sistema,0)), 2) AS total_sistema,
                   ROUND(SUM(COALESCE(diferencia,0)), 2) AS diferencia
            FROM recepcion_cierres_caja
            WHERE empresa_id = ? AND date(fecha) = date(?)
            """,
            (empresa_id, hoy),
        ).fetchone()
        if cierre and cierre["total_sistema"] is not None:
            caja_hoy = {"total": cierre["total_sistema"] or 0, "diferencia": cierre["diferencia"] or 0}
    kpis["caja_hoy"] = caja_hoy["total"] if isinstance(caja_hoy, dict) else (caja_hoy["total"] or 0)
    kpis["caja_diferencia"] = caja_hoy["diferencia"] if isinstance(caja_hoy, dict) else (caja_hoy["diferencia"] or 0)

    overbooking = _control_overbooking_live(conn, empresa_id, hoy_obj, 7, centro_id=centro_activo_id())
    kpis["conflictos_overbooking"] = len(overbooking.get("conflictos", [])) + len(overbooking.get("sin_asignar", []))
    kpis["dias_sin_disponibilidad"] = sum(1 for d in overbooking.get("disponibilidad", []) if d.get("disponibles", 0) <= 0)
    kpis["ocupacion_hoy_pct"] = round((float(kpis.get("alojados", 0)) / max(float(kpis.get("habitaciones", 0)), 1)) * 100, 1)

    agenda = []
    proximas_llegadas = conn.execute(
        f"""
        SELECT r.id, r.codigo, r.titular_nombre, r.fecha_entrada, r.fecha_salida,
               r.regimen, r.adultos, r.menores, r.estado, r.saldo,
               h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               h.estado AS habitacion_estado,
               (SELECT COUNT(*)
                FROM viajeros_recepcion v
                WHERE v.reserva_id = r.id
                  AND v.empresa_id = r.empresa_id
                  AND COALESCE(v.registro_viajeros_estado, 'pendiente') IN ('pendiente', 'preparado', 'rechazado')) AS viajeros_pendientes
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON h.id = r.habitacion_id
        WHERE r.empresa_id = ?
          AND date(r.fecha_entrada) BETWEEN date(?) AND date(?)
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'no_show', 'checkout')
          {reserva_centro_sql}
        ORDER BY date(r.fecha_entrada), h.numero, r.titular_nombre
        LIMIT 12
        """,
        [empresa_id, hoy, manana] + reserva_centro_params,
    ).fetchall()
    bloqueantes_checkin = []
    for row in proximas_llegadas:
        habitacion_estado = (row["habitacion_estado"] or "").lower()
        viajeros_pend = int(row["viajeros_pendientes"] or 0)
        motivos = []
        if not row["habitacion_numero"] and not row["habitacion_nombre"]:
            motivos.append("sin habitacion")
        if habitacion_estado in {"sucia", "limpieza", "repaso", "mantenimiento", "bloqueada", "fuera_servicio"}:
            motivos.append(f"habitacion {habitacion_estado}")
        if viajeros_pend:
            motivos.append(f"{viajeros_pend} viajeros pendientes")
        if motivos:
            bloqueantes_checkin.append({
                "nivel": "alta" if "sin habitacion" in motivos or habitacion_estado in {"mantenimiento", "bloqueada", "fuera_servicio"} else "media",
                "reserva_id": row["id"],
                "codigo": row["codigo"] or f"#{row['id']}",
                "cliente": row["titular_nombre"] or "Sin titular",
                "habitacion": row["habitacion_numero"] or row["habitacion_nombre"] or "sin asignar",
                "fecha": row["fecha_entrada"],
                "motivos": ", ".join(motivos),
            })
        agenda.append({
            "tipo": "llegada",
            "nivel": "alta" if not row["habitacion_numero"] and not row["habitacion_nombre"] else "ok",
            "titulo": f"Llegada {row['fecha_entrada']}",
            "detalle": f"{row['codigo'] or ('#' + str(row['id']))} - {row['titular_nombre'] or 'Sin titular'}",
            "extra": f"Hab. {row['habitacion_numero'] or row['habitacion_nombre'] or 'sin asignar'} - {row['regimen'] or '-'}",
            "reserva_id": row["id"],
        })
    proximas_salidas = conn.execute(
        f"""
        SELECT r.id, r.codigo, r.titular_nombre, r.fecha_salida,
               r.estado, r.saldo, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON h.id = r.habitacion_id
        WHERE r.empresa_id = ?
          AND date(r.fecha_salida) BETWEEN date(?) AND date(?)
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'no_show')
          {reserva_centro_sql}
        ORDER BY date(r.fecha_salida), h.numero, r.titular_nombre
        LIMIT 12
        """,
        [empresa_id, hoy, manana] + reserva_centro_params,
    ).fetchall()
    for row in proximas_salidas:
        saldo = float(row["saldo"] or 0)
        agenda.append({
            "tipo": "salida",
            "nivel": "media" if saldo > 0 else "ok",
            "titulo": f"Salida {row['fecha_salida']}",
            "detalle": f"{row['codigo'] or ('#' + str(row['id']))} - {row['titular_nombre'] or 'Sin titular'}",
            "extra": f"Hab. {row['habitacion_numero'] or row['habitacion_nombre'] or '-'} - saldo {saldo:.2f} EUR",
            "reserva_id": row["id"],
        })
    viajeros_incompletos = conn.execute(
        f"""
        SELECT r.id, r.codigo, r.titular_nombre, COUNT(v.id) AS pendientes
        FROM viajeros_recepcion v
        JOIN reservas_recepcion r ON r.id = v.reserva_id
        WHERE v.empresa_id = ?
          AND date(r.fecha_entrada) BETWEEN date(?) AND date(?)
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND COALESCE(v.registro_viajeros_estado, 'pendiente') IN ('pendiente', 'preparado', 'rechazado')
          {reserva_centro_sql}
        GROUP BY r.id, r.codigo, r.titular_nombre
        ORDER BY pendientes DESC, r.fecha_entrada
        LIMIT 8
        """,
        [empresa_id, hoy, manana] + reserva_centro_params,
    ).fetchall()
    for row in viajeros_incompletos:
        agenda.append({
            "tipo": "viajeros",
            "nivel": "media",
            "titulo": "Viajeros pendientes",
            "detalle": f"{row['codigo'] or ('#' + str(row['id']))} - {row['titular_nombre'] or 'Sin titular'}",
            "extra": f"{row['pendientes']} fichas pendientes/preparadas/rechazadas",
            "reserva_id": row["id"],
        })

    agenda.sort(key=lambda item: ({"alta": 1, "media": 2, "ok": 3}.get(item["nivel"], 4), item["tipo"], item["titulo"]))

    acciones = []
    if kpis["conflictos_overbooking"]:
        acciones.append({
            "nivel": "critica",
            "titulo": "Revisar overbooking antes de vender mas",
            "detalle": f"{kpis['conflictos_overbooking']} reservas o conflictos necesitan resolucion.",
            "url": url_for("recepcion.overbooking_control", desde=hoy, dias=7),
        })
    if bloqueantes_checkin:
        acciones.append({
            "nivel": "alta",
            "titulo": "Desbloquear check-in antes de llegada",
            "detalle": f"{len(bloqueantes_checkin)} llegadas tienen habitacion, viajeros o limpieza pendiente.",
            "url": url_for("recepcion.dashboard"),
        })
    if kpis["reservas_sin_habitacion"]:
        acciones.append({
            "nivel": "alta",
            "titulo": "Asignar habitaciones pendientes",
            "detalle": f"{kpis['reservas_sin_habitacion']} reservas activas no tienen habitacion.",
            "url": url_for("recepcion.ocupacion", desde=hoy, dias=7),
        })
    if kpis["habitaciones_sucias"] or kpis["habitaciones_bloqueadas"]:
        acciones.append({
            "nivel": "alta",
            "titulo": "Cruzar pisos y mantenimiento",
            "detalle": f"{kpis['habitaciones_sucias']} habitaciones en limpieza/mantenimiento y {kpis['habitaciones_bloqueadas']} bloqueadas.",
            "url": url_for("recepcion.ocupacion", desde=hoy, dias=7),
        })
    if kpis["saldo_pendiente"] > 0:
        acciones.append({
            "nivel": "media",
            "titulo": "Controlar saldos antes de check-out",
            "detalle": f"Saldo vivo PMS: {kpis['saldo_pendiente']:.2f} EUR.",
            "url": url_for("recepcion.caja_recepcion", fecha=hoy),
        })
    if kpis["facturas_abiertas"]:
        acciones.append({
            "nivel": "media",
            "titulo": "Cerrar facturacion pendiente",
            "detalle": f"{kpis['facturas_abiertas']} facturas siguen abiertas.",
            "url": url_for("recepcion.facturas"),
        })
    if not acciones:
        acciones.append({
            "nivel": "ok",
            "titulo": "Recepcion sin bloqueos criticos",
            "detalle": "Llegadas, salidas, habitaciones, caja y viajeros no muestran urgencias fuertes.",
            "url": url_for("recepcion.dashboard"),
        })
    return kpis, acciones[:6], overbooking, agenda[:18], bloqueantes_checkin[:12]


@recepcion_bp.route("/")
@login_required
@_require_recepcion
def dashboard():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    hoy = date.today().isoformat()
    reserva_centro_sql, reserva_centro_params = _centro_sql_reserva("r")
    habitacion_centro_sql, habitacion_centro_params = _centro_sql_habitacion("h")
    llegadas = conn.execute(
        f"""
        SELECT r.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE r.empresa_id = ? AND date(r.fecha_entrada) = date(?) AND COALESCE(r.estado, '') NOT IN ('cancelada', 'no_show')
          {reserva_centro_sql}
        ORDER BY h.numero, r.titular_nombre
        """,
        [empresa_id, hoy] + reserva_centro_params,
    ).fetchall()
    salidas = conn.execute(
        f"""
        SELECT r.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE r.empresa_id = ? AND date(r.fecha_salida) = date(?) AND COALESCE(r.estado, '') NOT IN ('cancelada', 'no_show')
          {reserva_centro_sql}
        ORDER BY h.numero, r.titular_nombre
        """,
        [empresa_id, hoy] + reserva_centro_params,
    ).fetchall()
    restaurante_hoy = conn.execute(
        f"""
        SELECT rr.*, pv.nombre AS punto_venta_nombre, r.codigo, r.titular_nombre,
               h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
        FROM recepcion_reservas_restaurante rr
        LEFT JOIN puntos_venta pv ON rr.punto_venta_id = pv.id
        LEFT JOIN reservas_recepcion r ON rr.reserva_id = r.id
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE rr.empresa_id = ?
          AND date(rr.fecha) = date(?)
          AND COALESCE(rr.estado, 'pendiente') NOT IN ('cancelada')
          {reserva_centro_sql}
        ORDER BY rr.hora, pv.nombre, rr.id
        """,
        [empresa_id, hoy] + reserva_centro_params,
    ).fetchall()
    alojados = conn.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM reservas_recepcion r
        WHERE r.empresa_id = ?
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND date(r.fecha_entrada) <= date(?)
          AND date(r.fecha_salida) > date(?)
          {reserva_centro_sql}
        """,
        [empresa_id, hoy, hoy] + reserva_centro_params,
    ).fetchone()["total"] or 0
    viajeros_pendientes = conn.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM viajeros_recepcion v
        JOIN reservas_recepcion r ON v.reserva_id = r.id
        WHERE v.empresa_id = ?
          AND COALESCE(v.registro_viajeros_estado, 'pendiente') IN ('pendiente', 'preparado', 'rechazado')
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          {reserva_centro_sql}
        """,
        [empresa_id] + reserva_centro_params,
    ).fetchone()["total"] or 0
    alerta_registro_viajeros = _resumen_alerta_registro_viajeros(conn, empresa_id)
    fecha_prev, resumen_servicios, detalle_servicios = _prevision_servicios(conn, hoy)
    habitaciones = conn.execute(
        f"SELECT COUNT(*) AS total FROM habitaciones_recepcion h WHERE h.empresa_id = ? AND COALESCE(h.activo,1)=1 {habitacion_centro_sql}",
        [empresa_id] + habitacion_centro_params,
    ).fetchone()["total"] or 0
    pms_kpis, pms_acciones, pms_overbooking, pms_agenda, pms_bloqueantes_checkin = _pms_operativo_dashboard(
        conn,
        empresa_id,
        hoy,
        reserva_centro_sql,
        reserva_centro_params,
        habitacion_centro_sql,
        habitacion_centro_params,
    )
    pms_kpis["alojados"] = alojados
    pms_kpis["habitaciones"] = habitaciones
    pms_kpis["ocupacion_hoy_pct"] = round((float(alojados or 0) / max(float(habitaciones or 0), 1)) * 100, 1)
    conn.close()
    return render_template(
        "recepcion/dashboard.html",
        hoy=hoy,
        llegadas=llegadas,
        salidas=salidas,
        alojados=alojados,
        viajeros_pendientes=viajeros_pendientes,
        alerta_registro_viajeros=alerta_registro_viajeros,
        habitaciones=habitaciones,
        resumen_servicios=resumen_servicios,
        detalle_servicios=detalle_servicios,
        restaurante_hoy=restaurante_hoy,
        pms_kpis=pms_kpis,
        pms_acciones=pms_acciones,
        pms_overbooking=pms_overbooking,
        pms_agenda=pms_agenda,
        pms_bloqueantes_checkin=pms_bloqueantes_checkin,
    )


@recepcion_bp.route("/habitaciones")
@login_required
@_require_recepcion
def habitaciones():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    habitaciones = _habitaciones_select(conn, _empresa_id())
    conn.close()
    return render_template("recepcion/habitaciones.html", habitaciones=habitaciones)


@recepcion_bp.route("/habitaciones/<int:id>/activar-ical", methods=["POST"])
@login_required
@_require_recepcion
def activar_ical_habitacion(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    token = _habitacion_ical_token(conn, id, empresa_id)
    if not token:
        conn.close()
        abort(404)
    conn.commit()
    conn.close()
    flash("Calendario iCal activado para la habitacion.", "success")
    return redirect(url_for("recepcion.habitaciones"))


@recepcion_bp.route("/habitaciones/<int:id>/regenerar-ical", methods=["POST"])
@login_required
@_require_recepcion
def regenerar_ical_habitacion(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    row = conn.execute(
        "SELECT id FROM habitaciones_recepcion WHERE id = ? AND empresa_id = ?",
        (id, empresa_id),
    ).fetchone()
    if not row:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE habitaciones_recepcion SET ical_token = ? WHERE id = ? AND empresa_id = ?",
        (secrets.token_urlsafe(24), id, empresa_id),
    )
    conn.commit()
    conn.close()
    flash("Enlace iCal regenerado.", "success")
    return redirect(url_for("recepcion.habitaciones"))


@recepcion_bp.route("/ical/habitaciones/<int:id>/<token>.ics")
def habitacion_ical_public(id, token):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    habitacion = conn.execute(
        """
        SELECT *
        FROM habitaciones_recepcion
        WHERE id = ? AND ical_token = ? AND COALESCE(activo, 1) = 1
        LIMIT 1
        """,
        (id, token),
    ).fetchone()
    if not habitacion:
        conn.close()
        abort(404)
    calendario = _calendario_habitacion_ics(conn, habitacion, habitacion["empresa_id"] or 1)
    conn.close()
    filename = f"habitacion_{habitacion['numero'] or id}.ics"
    return Response(
        calendario,
        mimetype="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@recepcion_bp.route("/tipos-habitacion", methods=["GET", "POST"])
@login_required
@_require_recepcion
def tipos_habitacion():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        codigo = (request.form.get("codigo") or "").strip().upper()
        nombre = (request.form.get("nombre") or "").strip()
        if not codigo or not nombre:
            flash("Codigo y nombre del tipo son obligatorios.", "warning")
        else:
            conn.execute(
                """
                INSERT INTO recepcion_tipos_habitacion
                (codigo, nombre, capacidad_base, capacidad_max, descripcion, activo, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    codigo,
                    nombre,
                    max(1, _to_int(request.form.get("capacidad_base"), 2)),
                    max(1, _to_int(request.form.get("capacidad_max"), 2)),
                    request.form.get("descripcion") or "",
                    1 if request.form.get("activo", "1") else 0,
                    empresa_id,
                ),
            )
            conn.commit()
            flash("Tipo de habitacion guardado.", "success")
            return redirect(url_for("recepcion.tipos_habitacion"))
    tipos = _tipos_habitacion_select(conn, empresa_id, solo_activos=False)
    conn.close()
    return render_template("recepcion/tipos_habitacion.html", tipos=tipos)


@recepcion_bp.route("/tipos-habitacion/<int:id>/toggle", methods=["POST"])
@login_required
@_require_recepcion
def toggle_tipo_habitacion(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    row = conn.execute(
        "SELECT activo FROM recepcion_tipos_habitacion WHERE id = ? AND empresa_id = ?",
        (id, empresa_id),
    ).fetchone()
    if not row:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE recepcion_tipos_habitacion SET activo = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND empresa_id = ?",
        (0 if row["activo"] else 1, id, empresa_id),
    )
    conn.commit()
    conn.close()
    flash("Tipo de habitacion actualizado.", "success")
    return redirect(url_for("recepcion.tipos_habitacion"))


@recepcion_bp.route("/habitaciones/alta-rapida", methods=["GET", "POST"])
@login_required
@_require_recepcion
def habitaciones_alta_rapida():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    tipos = _tipos_habitacion_select(conn, empresa_id)
    if request.method == "POST":
        bloque = (request.form.get("bloque") or "").strip()
        planta = (request.form.get("planta") or request.form.get("planta_desde") or "").strip()
        planta_hasta = (request.form.get("planta_hasta") or planta).strip()
        prefijo = (request.form.get("prefijo") or "").strip()
        desde = _to_int(request.form.get("desde"), 1)
        hasta = _to_int(request.form.get("hasta"), desde)
        ancho = max(1, _to_int(request.form.get("ancho"), 2))
        tipo_id = _to_int(request.form.get("tipo_habitacion_id"), 0) or None
        tipo_row = conn.execute(
            "SELECT * FROM recepcion_tipos_habitacion WHERE id = ? AND empresa_id = ?",
            (tipo_id, empresa_id),
        ).fetchone() if tipo_id else None
        capacidad = max(1, _to_int(request.form.get("capacidad"), tipo_row["capacidad_base"] if tipo_row else 2))
        if hasta < desde:
            desde, hasta = hasta, desde
        try:
            planta_inicio = int(planta)
            planta_fin = int(planta_hasta)
            if planta_fin < planta_inicio:
                planta_inicio, planta_fin = planta_fin, planta_inicio
            plantas = [str(p) for p in range(planta_inicio, planta_fin + 1)]
        except Exception:
            plantas = [planta or planta_hasta or ""]
        creadas = 0
        omitidas = []
        for planta_actual in plantas:
            prefijo_base = prefijo or planta_actual
            prefijo_base = prefijo_base.replace("{bloque}", bloque).replace("{planta}", planta_actual)
            for numero in range(desde, hasta + 1):
                codigo_numero = f"{prefijo_base}{numero:0{ancho}d}" if prefijo_base else f"{numero:0{ancho}d}"
                exists = conn.execute(
                    """
                    SELECT id FROM habitaciones_recepcion
                    WHERE empresa_id = ? AND TRIM(numero) = ?
                      AND COALESCE(centro_id, 0) = COALESCE(?, 0)
                    """,
                    (empresa_id, codigo_numero, centro_id),
                ).fetchone()
                if exists:
                    omitidas.append(codigo_numero)
                    continue
                conn.execute(
                    """
                    INSERT INTO habitaciones_recepcion
                    (numero, nombre, tipo, tipo_habitacion_id, bloque, planta, capacidad, estado, observaciones, activo, empresa_id, centro_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'disponible', ?, 1, ?, ?)
                    """,
                    (
                        codigo_numero,
                        request.form.get("nombre_base") or "",
                        tipo_row["nombre"] if tipo_row else "",
                        tipo_id,
                        bloque,
                        planta_actual,
                        capacidad,
                        request.form.get("observaciones") or "",
                        empresa_id,
                        centro_id,
                    ),
                )
                creadas += 1
        conn.commit()
        msg = f"Alta rapida completada: {creadas} habitaciones creadas."
        if omitidas:
            msg += f" Omitidas por existir: {', '.join(omitidas[:10])}"
            if len(omitidas) > 10:
                msg += "..."
        flash(msg, "success" if creadas else "warning")
        conn.close()
        return redirect(url_for("recepcion.habitaciones"))
    conn.close()
    return render_template("recepcion/habitaciones_alta_rapida.html", tipos=tipos)


@recepcion_bp.route("/habitaciones/nueva", methods=["GET", "POST"])
@recepcion_bp.route("/habitaciones/<int:id>/editar", methods=["GET", "POST"])
@login_required
@_require_recepcion
def habitacion_form(id=None):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    habitacion = None
    if id:
        centro_sql, centro_params = _centro_sql_habitacion("h")
        habitacion = conn.execute(
            f"SELECT * FROM habitaciones_recepcion h WHERE h.id = ? AND h.empresa_id = ? {centro_sql}",
            [id, empresa_id] + centro_params,
        ).fetchone()
        if not habitacion:
            conn.close()
            abort(404)
    tipos = _tipos_habitacion_select(conn, empresa_id)
    if request.method == "POST":
        tipo_id = _to_int(request.form.get("tipo_habitacion_id"), 0) or None
        tipo_row = conn.execute(
            "SELECT nombre FROM recepcion_tipos_habitacion WHERE id = ? AND empresa_id = ?",
            (tipo_id, empresa_id),
        ).fetchone() if tipo_id else None
        numero = (request.form.get("numero") or "").strip()
        if not numero:
            flash("El numero de habitacion es obligatorio.", "warning")
            return render_template("recepcion/habitacion_form.html", habitacion=habitacion, tipos=tipos)
        existente = conn.execute(
            """
            SELECT id
            FROM habitaciones_recepcion
            WHERE empresa_id = ?
              AND TRIM(numero) = ?
              AND COALESCE(centro_id, 0) = COALESCE(?, 0)
              AND (? IS NULL OR id <> ?)
            LIMIT 1
            """,
            (empresa_id, numero, centro_id, id, id),
        ).fetchone()
        if existente:
            flash("Ya existe una habitacion con ese numero.", "danger")
            return render_template("recepcion/habitacion_form.html", habitacion=habitacion, tipos=tipos)
        datos = (
            numero,
            request.form.get("nombre") or "",
            request.form.get("tipo") or (tipo_row["nombre"] if tipo_row else ""),
            tipo_id,
            request.form.get("bloque") or "",
            request.form.get("planta") or "",
            max(1, _to_int(request.form.get("capacidad"), 2)),
            request.form.get("estado") or "disponible",
            request.form.get("observaciones") or "",
            1 if request.form.get("activo", "1") else 0,
        )
        if id:
            conn.execute(
                """
                UPDATE habitaciones_recepcion
                SET numero = ?, nombre = ?, tipo = ?, tipo_habitacion_id = ?, bloque = ?, planta = ?, capacidad = ?, estado = ?, observaciones = ?, activo = ?, centro_id = ?
                WHERE id = ? AND empresa_id = ?
                """,
                datos + (centro_id, id, empresa_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO habitaciones_recepcion
                (numero, nombre, tipo, tipo_habitacion_id, bloque, planta, capacidad, estado, observaciones, activo, empresa_id, centro_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                datos + (empresa_id, centro_id),
            )
        conn.commit()
        conn.close()
        flash("Habitacion guardada.", "success")
        return redirect(url_for("recepcion.habitaciones"))
    conn.close()
    return render_template("recepcion/habitacion_form.html", habitacion=habitacion, tipos=tipos)


@recepcion_bp.route("/reservas")
@login_required
@_require_recepcion
def reservas():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    estado = (request.args.get("estado") or "").strip()
    q = (request.args.get("q") or "").strip()
    desde = request.args.get("desde") or ""
    hasta = request.args.get("hasta") or ""
    filtros = ["r.empresa_id = ?"]
    params = [empresa_id]
    centro_sql, centro_params = _centro_sql_reserva("r")
    if centro_sql:
        filtros.append(centro_sql.replace(" AND ", "", 1))
        params.extend(centro_params)
    if estado:
        filtros.append("r.estado = ?")
        params.append(estado)
    if desde:
        filtros.append("date(r.fecha_entrada) >= date(?)")
        params.append(desde)
    if hasta:
        filtros.append("date(r.fecha_salida) <= date(?)")
        params.append(hasta)
    if q:
        filtros.append("(r.codigo LIKE ? OR r.titular_nombre LIKE ? OR h.numero LIKE ? OR h.nombre LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like, like]
    filas = conn.execute(
        f"""
        SELECT r.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               cr.nombre AS canal_nombre,
               (SELECT COUNT(*) FROM viajeros_recepcion v WHERE v.reserva_id = r.id) AS viajeros
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        LEFT JOIN recepcion_canales_reserva cr ON r.canal_id = cr.id
        WHERE {" AND ".join(filtros)}
        ORDER BY date(r.fecha_entrada) DESC, r.id DESC
        """,
        params,
    ).fetchall()
    conn.close()
    return render_template(
        "recepcion/reservas.html",
        reservas=filas,
        estados=ESTADOS_RESERVA,
        regimenes=REGIMENES,
        filtros={"estado": estado, "q": q, "desde": desde, "hasta": hasta},
    )


@recepcion_bp.route("/reservas/nueva", methods=["GET", "POST"])
@recepcion_bp.route("/reservas/<int:id>/editar", methods=["GET", "POST"])
@login_required
@_require_recepcion
def reserva_form(id=None):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    if request.method == 'POST':
        # Serializar la comprobacion de disponibilidad y su escritura en SQLite.
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
    empresa_id = _empresa_id()
    reserva = None
    if id:
        reserva = _reserva_por_id(conn, id, empresa_id)
        if not reserva:
            conn.close()
            abort(404)
    habitaciones = _habitaciones_select(conn, empresa_id)
    canales = _canales_reserva_select(conn, empresa_id)
    tipos_habitacion = _tipos_habitacion_select(conn, empresa_id)
    precio_sugerido = None
    if request.method == "POST":
        entrada = request.form.get("fecha_entrada")
        salida = request.form.get("fecha_salida")
        habitacion_id = _to_int(request.form.get("habitacion_id"), 0) or None
        tipo_habitacion_id = _to_int(request.form.get("tipo_habitacion_id"), 0) or None
        entrada_dt = _parse_date(entrada)
        salida_dt = _parse_date(salida)
        if not entrada_dt or not salida_dt or salida_dt <= entrada_dt:
            conn.rollback()
            flash("Las fechas de entrada y salida no son validas.", "warning")
            return render_template("recepcion/reserva_form.html", reserva=reserva, habitaciones=habitaciones, tipos_habitacion=tipos_habitacion, canales=canales, regimenes=REGIMENES, estados=ESTADOS_RESERVA, form_cache=request.form, precio_sugerido=precio_sugerido)
        if habitacion_id and habitacion_id not in {h['id'] for h in habitaciones}:
            conn.close()
            abort(404)
        if habitacion_id and not tipo_habitacion_id:
            habitacion_tipo = conn.execute(
                "SELECT tipo_habitacion_id FROM habitaciones_recepcion WHERE id = ? AND empresa_id = ?",
                (habitacion_id, empresa_id),
            ).fetchone()
            if habitacion_tipo:
                tipo_habitacion_id = habitacion_tipo["tipo_habitacion_id"]
        conflictos = _conflictos_habitacion(conn, habitacion_id, entrada, salida, empresa_id, id)
        if conflictos:
            conn.rollback()
            flash(
                "La habitacion ya esta ocupada o bloqueada en esas fechas: "
                + _descripcion_conflictos_habitacion(conflictos),
                "danger",
            )
            return render_template("recepcion/reserva_form.html", reserva=reserva, habitaciones=habitaciones, tipos_habitacion=tipos_habitacion, canales=canales, regimenes=REGIMENES, estados=ESTADOS_RESERVA, form_cache=request.form, precio_sugerido=precio_sugerido)

        precio_sugerido = _sugerir_precio_reserva(
            conn,
            habitacion_id,
            request.form.get("regimen") or "solo_alojamiento",
            entrada,
            salida,
            empresa_id,
        )
        if request.form.get("calcular_precio"):
            conn.rollback()
            conn.close()
            return render_template(
                "recepcion/reserva_form.html",
                reserva=reserva,
                habitaciones=habitaciones,
                tipos_habitacion=tipos_habitacion,
                canales=canales,
                regimenes=REGIMENES,
                estados=ESTADOS_RESERVA,
                form_cache=request.form,
                precio_sugerido=precio_sugerido,
            )

        if request.form.get("usar_precio_sugerido") and precio_sugerido:
            precio_total = _to_float(request.form.get("precio_sugerido_total"), precio_sugerido["total"])
        else:
            precio_total = _to_float(request.form.get("precio_total"))
        deposito = _to_float(request.form.get("deposito"))
        saldo = max(0.0, precio_total - deposito)
        datos = (
            habitacion_id,
            tipo_habitacion_id,
            entrada,
            salida,
            request.form.get("hora_llegada") or "",
            request.form.get("hora_salida") or "",
            request.form.get("regimen") or "solo_alojamiento",
            max(1, _to_int(request.form.get("adultos"), 1)),
            max(0, _to_int(request.form.get("menores"), 0)),
            request.form.get("preferencia_cama") or "",
            1 if request.form.get("supletoria_solicitada") else 0,
            1 if request.form.get("cuna_solicitada") else 0,
            request.form.get("observaciones_habitacion") or "",
            request.form.get("estado") or "confirmada",
            request.form.get("origen") or "manual",
            _to_int(request.form.get("canal_id"), 0) or None,
            request.form.get("localizador_externo") or "",
            _to_float(request.form.get("comision_canal")),
            request.form.get("estado_externo") or "",
            request.form.get("ultima_sincronizacion") or "",
            request.form.get("titular_nombre") or "",
            request.form.get("titular_email") or "",
            request.form.get("titular_telefono") or "",
            precio_total,
            deposito,
            saldo,
            request.form.get("observaciones") or "",
            request.form.get("observaciones_operativas") or "",
            request.form.get("alergias_resumen") or "",
        )
        centro_id = centro_activo_id()
        if habitacion_id:
            habitacion_centro = conn.execute(
                "SELECT centro_id FROM habitaciones_recepcion WHERE id = ? AND empresa_id = ?",
                (habitacion_id, empresa_id),
            ).fetchone()
            if habitacion_centro and habitacion_centro["centro_id"]:
                centro_id = habitacion_centro["centro_id"]
        if id:
            conn.execute(
                """
                UPDATE reservas_recepcion
                SET habitacion_id = ?, tipo_habitacion_id = ?, fecha_entrada = ?, fecha_salida = ?, hora_llegada = ?, hora_salida = ?,
                    regimen = ?, adultos = ?, menores = ?, preferencia_cama = ?, supletoria_solicitada = ?,
                    cuna_solicitada = ?, observaciones_habitacion = ?, estado = ?, origen = ?, canal_id = ?,
                    localizador_externo = ?, comision_canal = ?, estado_externo = ?, ultima_sincronizacion = ?,
                    titular_nombre = ?, titular_email = ?, titular_telefono = ?, precio_total = ?, deposito = ?, saldo = ?,
                    observaciones = ?, observaciones_operativas = ?, alergias_resumen = ?, updated_at = CURRENT_TIMESTAMP
                    , centro_id = ?
                WHERE id = ? AND empresa_id = ?
                """,
                datos + (centro_id, id, empresa_id),
            )
            reserva_id = id
        else:
            codigo = _generar_codigo_reserva(conn, empresa_id)
            conn.execute(
                """
                INSERT INTO reservas_recepcion
                (codigo, habitacion_id, tipo_habitacion_id, fecha_entrada, fecha_salida, hora_llegada, hora_salida, regimen,
                 adultos, menores, preferencia_cama, supletoria_solicitada, cuna_solicitada, observaciones_habitacion,
                 estado, origen, canal_id, localizador_externo, comision_canal,
                 estado_externo, ultima_sincronizacion, titular_nombre, titular_email, titular_telefono,
                 precio_total, deposito, saldo, observaciones, observaciones_operativas, alergias_resumen,
                 empresa_id, creado_por, centro_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (codigo,) + datos + (empresa_id, session.get("user_id"), centro_id),
            )
            reserva_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            if deposito > 0:
                conn.execute(
                    """
                    INSERT INTO pagos_reserva_recepcion
                    (reserva_id, fecha, concepto, importe, forma_pago, estado, empresa_id, usuario_id)
                    VALUES (?, ?, 'Deposito inicial', ?, 'pendiente definir', 'confirmado', ?, ?)
                    """,
                    (reserva_id, date.today().isoformat(), deposito, empresa_id, session.get("user_id")),
                )
                _recalcular_pagos_reserva(conn, reserva_id, empresa_id)
        if id:
            tiene_movimientos = conn.execute(
                """
                SELECT 1 AS existe
                WHERE EXISTS (
                    SELECT 1
                    FROM pagos_reserva_recepcion
                    WHERE reserva_id = ? AND empresa_id = ?
                )
                OR EXISTS (
                    SELECT 1
                    FROM recepcion_cargos_reserva
                    WHERE reserva_id = ? AND empresa_id = ?
                )
                LIMIT 1
                """,
                (reserva_id, empresa_id, reserva_id, empresa_id),
            ).fetchone()
            if tiene_movimientos:
                _recalcular_cargos_reserva(conn, reserva_id, empresa_id)
        conn.commit()
        conn.close()
        flash("Reserva guardada.", "success")
        return redirect(url_for("recepcion.reserva_detalle", id=reserva_id))
    conn.close()
    return render_template("recepcion/reserva_form.html", reserva=reserva, habitaciones=habitaciones, tipos_habitacion=tipos_habitacion, canales=canales, regimenes=REGIMENES, estados=ESTADOS_RESERVA, form_cache=None, precio_sugerido=None)


@recepcion_bp.route("/reservas/<int:id>")
@login_required
@_require_recepcion
def reserva_detalle(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    precio_sugerido_reserva = None
    if _to_float(reserva["precio_total"] if "precio_total" in reserva.keys() else 0, 0) <= 0:
        precio_sugerido_reserva = _sugerir_precio_reserva(
            conn,
            reserva["habitacion_id"],
            reserva["regimen"] or "solo_alojamiento",
            reserva["fecha_entrada"],
            reserva["fecha_salida"],
            empresa_id,
        )
    viajeros = conn.execute(
        "SELECT * FROM viajeros_recepcion WHERE reserva_id = ? AND empresa_id = ? ORDER BY es_titular DESC, id",
        (id, empresa_id),
    ).fetchall()
    avisos = _avisos_reserva(conn, id, empresa_id)
    pagos = _pagos_reserva(conn, id, empresa_id)
    cargos = _cargos_reserva(conn, id, empresa_id)
    restaurante_reservas = _restaurante_reservas_reserva(conn, id, empresa_id)
    inclusiones_regimen = _inclusiones_regimen(conn, reserva["regimen"], empresa_id)
    puntos_venta = _puntos_venta_select(conn, empresa_id)
    facturas = _facturas_reserva(conn, id, empresa_id)
    pasarelas_pago = _pasarelas_pago_activas(conn, empresa_id)
    operaciones_pasarela = _operaciones_pago_reserva(conn, id, empresa_id)
    documentos_checkin = _documentos_checkin_reserva(conn, id, empresa_id)
    textos_checkin = _textos_checkin_select(conn, empresa_id)
    impuestos = _impuestos_select(conn, empresa_id)
    cliente_fiscal = _cliente_fiscal_reserva(conn, reserva, empresa_id)
    credito_reserva = _credito_reserva(conn, id, empresa_id)
    habitaciones_disponibles = _habitaciones_disponibles_para_reserva(conn, reserva, empresa_id)
    conn.close()
    return render_template(
        "recepcion/reserva_detalle.html",
        clave_cobro=secrets.token_urlsafe(32),
        reserva=reserva,
        viajeros=viajeros,
        avisos=avisos,
        pagos=pagos,
        cargos=cargos,
        restaurante_reservas=restaurante_reservas,
        inclusiones_regimen=inclusiones_regimen,
        puntos_venta=puntos_venta,
        facturas=facturas,
        pasarelas_pago=pasarelas_pago,
        operaciones_pasarela=operaciones_pasarela,
        documentos_checkin=documentos_checkin,
        textos_checkin=textos_checkin,
        impuestos=impuestos,
        cliente_fiscal=cliente_fiscal,
        credito_reserva=credito_reserva,
        habitaciones_disponibles=habitaciones_disponibles,
        precio_sugerido_reserva=precio_sugerido_reserva,
        idiomas_documento=IDIOMAS_DOCUMENTO,
        hoy=date.today().isoformat(),
        regimenes=REGIMENES,
        estados=ESTADOS_RESERVA,
        departamentos_aviso=DEPARTAMENTOS_AVISO,
    )


@recepcion_bp.route("/reservas/<int:id>/aplicar-precio-sugerido", methods=["POST"])
@login_required
@_require_recepcion
def aplicar_precio_sugerido_reserva(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    precio_anterior = _to_float(reserva["precio_total"] if "precio_total" in reserva.keys() else 0, 0)
    reserva_actualizada = _autocompletar_precio_reserva(conn, reserva, empresa_id)
    precio_nuevo = _to_float(reserva_actualizada["precio_total"] if reserva_actualizada and "precio_total" in reserva_actualizada.keys() else 0, 0)
    if precio_anterior <= 0 < precio_nuevo:
        conn.commit()
        flash(f"Precio sugerido aplicado: {precio_nuevo:.2f} EUR.", "success")
    else:
        flash("No se ha podido aplicar precio sugerido: revisa habitacion, fechas y tarifas.", "warning")
    conn.close()
    return redirect(url_for("recepcion.reserva_detalle", id=id))


@recepcion_bp.route("/reservas/<int:id>/documento-checkin/generar", methods=["POST"])
@login_required
@_require_recepcion
def generar_documento_checkin(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    idioma = _idioma_documento(request.form.get("idioma"))
    texto = _texto_checkin(conn, idioma, empresa_id)
    if not texto:
        conn.close()
        flash("No hay texto legal de check-in configurado.", "warning")
        return redirect(url_for("recepcion.reserva_detalle", id=id))
    documento = conn.execute(
        """
        SELECT *
        FROM recepcion_documentos_checkin
        WHERE reserva_id = ? AND empresa_id = ? AND idioma = ? AND version = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (id, empresa_id, idioma, texto["version"]),
    ).fetchone()
    if not documento:
        conn.execute(
            """
            INSERT INTO recepcion_documentos_checkin
            (reserva_id, idioma, version, titulo, estado, firmante_nombre, empresa_id, usuario_id)
            VALUES (?, ?, ?, ?, 'generado', ?, ?, ?)
            """,
            (
                id,
                idioma,
                texto["version"],
                texto["titulo"],
                reserva["titular_nombre"] or "",
                empresa_id,
                session.get("user_id"),
            ),
        )
        documento_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.commit()
        flash("Parte de check-in generado.", "success")
    else:
        flash("Ya existia un parte de check-in para ese idioma y version.", "info")
    conn.close()
    return redirect(url_for("recepcion.documento_checkin", id=id, idioma=idioma))


@recepcion_bp.route("/reservas/<int:id>/documento-checkin")
@login_required
@_require_recepcion
def documento_checkin(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    idioma = _idioma_documento(request.args.get("idioma"))
    texto = _texto_checkin(conn, idioma, empresa_id)
    if not texto:
        conn.close()
        flash("No hay texto legal de check-in configurado.", "warning")
        return redirect(url_for("recepcion.reserva_detalle", id=id))
    documento = conn.execute(
        """
        SELECT *
        FROM recepcion_documentos_checkin
        WHERE reserva_id = ? AND empresa_id = ? AND idioma = ? AND version = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (id, empresa_id, idioma, texto["version"]),
    ).fetchone()
    if not documento:
        conn.close()
        flash("No hay parte de check-in generado para ese idioma. Genera el parte primero.", "warning")
        return redirect(url_for("recepcion.reserva_detalle", id=id))
    viajeros = conn.execute(
        "SELECT * FROM viajeros_recepcion WHERE reserva_id = ? AND empresa_id = ? ORDER BY es_titular DESC, id",
        (id, empresa_id),
    ).fetchall()
    empresa = _empresa_datos(conn, empresa_id)
    empresa_logo_url = _empresa_logo_url(empresa)
    conn.close()
    return render_template(
        "recepcion/checkin_documento.html",
        reserva=reserva,
        viajeros=viajeros,
        texto=texto,
        documento=documento,
        idiomas_documento=IDIOMAS_DOCUMENTO,
        idioma=idioma,
        empresa=empresa,
        empresa_logo_url=empresa_logo_url,
        hoy=date.today().isoformat(),
    )


@recepcion_bp.route("/reservas/<int:id>/recogida-datos")
@login_required
@_require_recepcion
def recogida_datos_checkin(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    idioma = _idioma_documento(request.args.get("idioma"))
    viajeros = conn.execute(
        "SELECT * FROM viajeros_recepcion WHERE reserva_id = ? AND empresa_id = ? ORDER BY es_titular DESC, id",
        (id, empresa_id),
    ).fetchall()
    empresa = _empresa_datos(conn, empresa_id)
    empresa_logo_url = _empresa_logo_url(empresa)
    conn.close()
    pax_reserva = max(_to_int(reserva["adultos"], 0) + _to_int(reserva["menores"], 0), 1)
    copias = _to_int(request.args.get("copias"), pax_reserva)
    total_fichas = max(copias, pax_reserva, len(viajeros), 1)
    fichas = list(viajeros) + [None] * max(total_fichas - len(viajeros), 0)
    return render_template(
        "recepcion/recogida_datos_print.html",
        reserva=reserva,
        fichas=fichas,
        idioma=idioma,
        idiomas_documento=IDIOMAS_DOCUMENTO,
        txt=_recogida_datos_textos(idioma),
        empresa=empresa,
        empresa_logo_url=empresa_logo_url,
        hoy=date.today().isoformat(),
    )


@recepcion_bp.route("/reservas/<int:id>/documentacion-cliente")
@login_required
@_require_recepcion
def documentacion_cliente_checkin(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    idioma = _idioma_documento(request.args.get("idioma") or reserva["documentacion_entregada_idioma"] or "es")
    viajeros = conn.execute(
        "SELECT * FROM viajeros_recepcion WHERE reserva_id = ? AND empresa_id = ? ORDER BY es_titular DESC, id",
        (id, empresa_id),
    ).fetchall()
    avisos_viajeros = [
        v for v in viajeros
        if (v["alergias_intolerancias"] if "alergias_intolerancias" in v.keys() else "")
        or (v["observaciones_salud"] if "observaciones_salud" in v.keys() else "")
    ]
    inclusiones = _inclusiones_regimen(conn, reserva["regimen"], empresa_id)
    avisos = _avisos_reserva(conn, id, empresa_id)
    facturas = _facturas_reserva(conn, id, empresa_id)
    texto_legal = _texto_checkin(conn, idioma, empresa_id)
    empresa = _empresa_datos(conn, empresa_id)
    empresa_logo_url = _empresa_logo_url(empresa)
    conn.close()
    return render_template(
        "recepcion/documentacion_cliente.html",
        reserva=reserva,
        viajeros=viajeros,
        avisos_viajeros=avisos_viajeros,
        inclusiones=inclusiones,
        avisos=avisos,
        facturas=facturas,
        texto_legal=texto_legal,
        idioma=idioma,
        idiomas_documento=IDIOMAS_DOCUMENTO,
        regimenes=REGIMENES,
        txt=_documentacion_cliente_textos(idioma),
        empresa=empresa,
        empresa_logo_url=empresa_logo_url,
        autoprint=request.args.get("autoprint") == "1",
        hoy=date.today().isoformat(),
    )


@recepcion_bp.route("/reservas/<int:id>/documentacion-cliente/entregar", methods=["POST"])
@login_required
@_require_recepcion
def marcar_documentacion_cliente_entregada(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    idioma = _idioma_documento(request.form.get("idioma"))
    conn.execute(
        """
        UPDATE reservas_recepcion
        SET documentacion_entregada_at = CURRENT_TIMESTAMP,
            documentacion_entregada_idioma = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (idioma, id, empresa_id),
    )
    conn.commit()
    conn.close()
    flash("Documentacion de check-in marcada como entregada.", "success")
    return redirect(url_for("recepcion.documentacion_cliente_checkin", id=id, idioma=idioma))


@recepcion_bp.route("/documentos-checkin/<int:id>/firmar", methods=["POST"])
@login_required
@_require_recepcion
def firmar_documento_checkin(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    documento = conn.execute(
        "SELECT * FROM recepcion_documentos_checkin WHERE id = ? AND empresa_id = ?",
        (id, empresa_id),
    ).fetchone()
    if not documento:
        conn.close()
        abort(404)
    firmante = (request.form.get("firmante_nombre") or documento["firmante_nombre"] or "").strip()
    conn.execute(
        """
        UPDATE recepcion_documentos_checkin
        SET estado = 'firmado', firmante_nombre = ?, firmado_at = CURRENT_TIMESTAMP,
            firma_cliente_data = COALESCE(NULLIF(?, ''), firma_cliente_data),
            firma_cliente_mime = CASE WHEN NULLIF(?, '') IS NOT NULL THEN 'image/png' ELSE firma_cliente_mime END,
            firma_metodo = CASE WHEN NULLIF(?, '') IS NOT NULL THEN 'tablet' ELSE COALESCE(firma_metodo, 'papel') END,
            observaciones = ?, usuario_id = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (
            firmante,
            request.form.get("firma_data") or "",
            request.form.get("firma_data") or "",
            request.form.get("firma_data") or "",
            request.form.get("observaciones") or documento["observaciones"] or "",
            session.get("user_id"),
            id,
            empresa_id,
        ),
    )
    conn.commit()
    reserva_id = documento["reserva_id"]
    idioma = documento["idioma"] or "es"
    conn.close()
    flash("Documento de check-in marcado como firmado.", "success")
    return redirect(url_for("recepcion.documento_checkin", id=reserva_id, idioma=idioma))


@recepcion_bp.route("/textos-checkin", methods=["GET", "POST"])
@login_required
@_require_recepcion
def textos_checkin():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        texto_id = _to_int(request.form.get("texto_id"), 0)
        idioma = _idioma_documento(request.form.get("idioma"))
        titulo = (request.form.get("titulo") or "").strip()
        contenido = (request.form.get("contenido") or "").strip()
        version = (request.form.get("version") or date.today().strftime("%Y-%m")).strip()
        activo = 1 if request.form.get("activo") else 0
        if not titulo or not contenido:
            flash("Titulo y contenido son obligatorios.", "warning")
        elif texto_id:
            conn.execute(
                """
                UPDATE recepcion_textos_checkin
                SET idioma = ?, titulo = ?, contenido = ?, version = ?, activo = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                (idioma, titulo, contenido, version, activo, texto_id, empresa_id),
            )
            conn.commit()
            flash("Texto de check-in actualizado.", "success")
            return redirect(url_for("recepcion.textos_checkin"))
        else:
            conn.execute(
                """
                INSERT INTO recepcion_textos_checkin
                (idioma, titulo, contenido, version, activo, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (idioma, titulo, contenido, version, activo, empresa_id),
            )
            conn.commit()
            flash("Texto de check-in creado.", "success")
            return redirect(url_for("recepcion.textos_checkin"))
    textos = _textos_checkin_admin_select(conn, empresa_id)
    conn.close()
    return render_template(
        "recepcion/textos_checkin.html",
        textos=textos,
        idiomas_documento=IDIOMAS_DOCUMENTO,
        version_default=date.today().strftime("%Y-%m"),
    )


@recepcion_bp.route("/reservas/<int:id>/asignar-habitacion", methods=["POST"])
@login_required
@_require_recepcion
def asignar_habitacion_reserva(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    if reserva["estado"] in {"checkout", "cancelada", "no_show"}:
        conn.close()
        flash("No se puede asignar habitacion a una reserva cerrada o anulada.", "warning")
        return redirect(url_for("recepcion.reserva_detalle", id=id))

    habitacion_id = _to_int(request.form.get("habitacion_id"), 0)
    if not habitacion_id:
        conn.close()
        flash("Selecciona una habitacion para asignar.", "warning")
        return redirect(url_for("recepcion.reserva_detalle", id=id))

    habitacion = conn.execute(
        """
        SELECT *
        FROM habitaciones_recepcion
        WHERE id = ? AND empresa_id = ? AND COALESCE(activo, 1) = 1
        """,
        (habitacion_id, empresa_id),
    ).fetchone()
    if not habitacion:
        conn.close()
        flash("La habitacion seleccionada no existe o esta inactiva.", "danger")
        return redirect(url_for("recepcion.reserva_detalle", id=id))

    if habitacion_id not in {h['id'] for h in _habitaciones_select(conn, empresa_id)}:
        conn.close()
        abort(404)

    pax = max(1, int(reserva["adultos"] or 0) + int(reserva["menores"] or 0))
    if (habitacion["capacidad"] or 0) < pax:
        conn.close()
        flash("La habitacion no tiene capacidad suficiente para esta reserva.", "warning")
        return redirect(url_for("recepcion.reserva_detalle", id=id))

    conflictos = _conflictos_habitacion(conn, habitacion_id, reserva["fecha_entrada"], reserva["fecha_salida"], empresa_id, id)
    if conflictos:
        conn.close()
        flash(
            "La habitacion ya esta ocupada o bloqueada en esas fechas: "
            + _descripcion_conflictos_habitacion(conflictos),
            "danger",
        )
        return redirect(url_for("recepcion.reserva_detalle", id=id))

    anterior_id = reserva["habitacion_id"]
    conn.execute(
        """
        UPDATE reservas_recepcion
        SET habitacion_id = ?, tipo_habitacion_id = COALESCE(tipo_habitacion_id, ?), updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (habitacion_id, habitacion["tipo_habitacion_id"], id, empresa_id),
    )
    if reserva["estado"] in {"checkin", "alojado"}:
        _marcar_habitacion_estado(conn, habitacion_id, empresa_id, "ocupada")
        if anterior_id and anterior_id != habitacion_id:
            _marcar_habitacion_estado(conn, anterior_id, empresa_id, "disponible")
    conn.commit()
    conn.close()
    flash("Habitacion asignada a la reserva.", "success")
    return redirect(url_for("recepcion.reserva_detalle", id=id))


@recepcion_bp.route("/reservas/<int:id>/estado", methods=["POST"])
@login_required
@_require_recepcion
def cambiar_estado_reserva(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    estado = request.form.get("estado") or "confirmada"
    if estado not in ESTADOS_RESERVA:
        conn.close()
        flash('Estado de reserva no valido.', 'warning')
        return redirect(url_for('recepcion.reserva_detalle', id=id))
    if estado in {'checkin', 'alojado', 'checkout'} or reserva['estado'] in {'checkin', 'alojado', 'checkout', 'cancelada', 'no_show'}:
        conn.close()
        flash('Usa el proceso de entrada o salida. Las reservas alojadas o finalizadas no admiten cambios genericos.', 'warning')
        return redirect(url_for('recepcion.reserva_detalle', id=id))
    timestamp_col = ""
    if estado == "cancelada":
        timestamp_col = ", cancelada_at = COALESCE(cancelada_at, CURRENT_TIMESTAMP)"
    elif estado == "no_show":
        timestamp_col = ", no_show_at = COALESCE(no_show_at, CURRENT_TIMESTAMP)"
    elif estado == "checkout":
        timestamp_col = ", checkout_at = COALESCE(checkout_at, CURRENT_TIMESTAMP)"
    elif estado in {"checkin", "alojado"}:
        timestamp_col = ", checkin_at = COALESCE(checkin_at, CURRENT_TIMESTAMP)"
    conn.execute(
        f"UPDATE reservas_recepcion SET estado = ?, updated_at = CURRENT_TIMESTAMP {timestamp_col} WHERE id = ? AND empresa_id = ?",
        (estado, id, empresa_id),
    )
    if estado in {"checkin", "alojado"}:
        _marcar_habitacion_estado(conn, reserva["habitacion_id"], empresa_id, "ocupada")
    elif estado in {"checkout", "no_show", "cancelada"}:
        _marcar_habitacion_estado(conn, reserva["habitacion_id"], empresa_id, "limpieza" if estado == "checkout" else "disponible")
    conn.commit()
    conn.close()
    flash("Estado de reserva actualizado.", "success")
    return redirect(url_for("recepcion.reserva_detalle", id=id))


@recepcion_bp.route("/reservas/<int:id>/checkin", methods=["POST"])
@login_required
@_require_recepcion
def checkin_reserva(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    estado = (reserva['estado'] or '').strip().lower()
    if estado in {'checkin', 'alojado'}:
        conn.close()
        flash('La entrada ya estaba registrada. Usa los cobros de la reserva para pagos adicionales.', 'info')
        return redirect(url_for('recepcion.reserva_detalle', id=id))
    if estado in {'cancelada', 'checkout', 'no_show'}:
        conn.close()
        flash('No se puede registrar la entrada de una reserva cancelada o finalizada.', 'warning')
        return redirect(url_for('recepcion.reserva_detalle', id=id))
    importe_pago = _to_float(request.form.get("importe_pago"), 0)
    politica_base = reserva["politica_credito"] if "politica_credito" in reserva.keys() else "abierto"
    politica_credito = _normalizar_politica_credito(request.form.get("politica_credito") or politica_base)
    limite_credito = max(_to_float(request.form.get("limite_credito"), 0), 0)
    fianza_virtual = max(_to_float(request.form.get("fianza_virtual"), 0), 0)
    if politica_credito == "sin_credito":
        limite_credito = 0
        fianza_virtual = 0
    elif politica_credito == "fianza_virtual":
        limite_credito = max(limite_credito, fianza_virtual)
    conn.execute(
        """
        UPDATE reservas_recepcion
        SET estado = 'alojado',
            checkin_at = COALESCE(checkin_at, CURRENT_TIMESTAMP),
            politica_credito = ?,
            limite_credito = ?,
            fianza_virtual = ?,
            fianza_referencia = ?,
            fianza_estado = ?,
            fianza_liberada_at = NULL,
            fianza_cobrada_at = NULL,
            fianza_liquidada_importe = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (
            politica_credito,
            limite_credito,
            fianza_virtual,
            request.form.get("fianza_referencia") or "",
            _estado_fianza_para_credito(politica_credito, fianza_virtual),
            id,
            empresa_id,
        ),
    )
    if importe_pago > 0:
        _asegurar_deposito_historico_como_pago(conn, reserva, id, empresa_id)
        conn.execute(
            """
            INSERT INTO pagos_reserva_recepcion
            (reserva_id, fecha, concepto, importe, forma_pago, referencia, observaciones, empresa_id, usuario_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                id,
                request.form.get("fecha_pago") or date.today().isoformat(),
                request.form.get("concepto_pago") or "Pago check-in",
                importe_pago,
                request.form.get("forma_pago") or "",
                request.form.get("referencia_pago") or "",
                request.form.get("observaciones_pago") or "",
                empresa_id,
                session.get("user_id"),
            ),
        )
        _recalcular_pagos_reserva(conn, id, empresa_id)
    _marcar_habitacion_estado(conn, reserva["habitacion_id"], empresa_id, "ocupada")
    factura_id = None
    if importe_pago > 0 and request.form.get("emitir_factura"):
        existente = _factura_reserva_emitida(conn, id, empresa_id)
        if existente:
            factura_id = _rellenar_factura_vacia_reserva(
                conn,
                existente["id"],
                reserva,
                empresa_id,
                {
                    "fecha": request.form.get("fecha_pago") or date.today().isoformat(),
                    "cliente_nombre": request.form.get("cliente_nombre") or reserva["titular_nombre"],
                    "cliente_nif": request.form.get("cliente_nif"),
                    "cliente_direccion": request.form.get("cliente_direccion"),
                    "cliente_email": request.form.get("cliente_email") or reserva["titular_email"],
                    "idioma": request.form.get("idioma") or "es",
                    "impuesto_estancia_id": request.form.get("impuesto_estancia_id"),
                    "impuesto_extras_id": request.form.get("impuesto_extras_id"),
                    "iva_estancia": request.form.get("iva_estancia"),
                    "iva_extras": request.form.get("iva_extras"),
                    "observaciones": "Factura emitida en check-in",
                },
            )
        else:
            _recalcular_cargos_reserva(conn, id, empresa_id)
            reserva_actualizada = _reserva_por_id(conn, id, empresa_id)
            factura_id = _crear_factura_reserva(
                conn,
                reserva_actualizada,
                empresa_id,
                {
                    "tipo": "factura",
                    "serie": request.form.get("serie") or "REC",
                    "fecha": request.form.get("fecha_pago") or date.today().isoformat(),
                    "cliente_nombre": request.form.get("cliente_nombre") or reserva["titular_nombre"],
                    "cliente_nif": request.form.get("cliente_nif"),
                    "cliente_direccion": request.form.get("cliente_direccion"),
                    "cliente_email": request.form.get("cliente_email") or reserva["titular_email"],
                    "idioma": request.form.get("idioma") or "es",
                    "impuesto_estancia_id": request.form.get("impuesto_estancia_id"),
                    "impuesto_extras_id": request.form.get("impuesto_extras_id"),
                    "iva_estancia": request.form.get("iva_estancia"),
                    "iva_extras": request.form.get("iva_extras"),
                    "observaciones": "Factura emitida en check-in",
                },
            )
        if factura_id:
            conn.execute(
                """
                UPDATE reservas_recepcion
                SET checkin_factura_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                (factura_id, id, empresa_id),
            )
    conn.commit()
    conn.close()
    if importe_pago > 0 and factura_id:
        flash("Check-in realizado, pago registrado y factura preparada.", "success")
        return redirect(url_for("recepcion.factura_detalle", id=factura_id, autoprint=1, next_doc=id))
    if importe_pago > 0 and request.form.get("emitir_factura"):
        flash("Check-in realizado y pago registrado, pero no se pudo generar factura porque la reserva no tiene precio ni cargos facturables.", "warning")
        return redirect(url_for("recepcion.reserva_detalle", id=id))
    flash("Check-in realizado.", "success")
    return redirect(url_for("recepcion.documentacion_cliente_checkin", id=id, idioma="es", autoprint=1))


@recepcion_bp.route("/reservas/<int:id>/checkout", methods=["POST"])
@login_required
@_require_recepcion
def checkout_reserva(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    estado = (reserva['estado'] or '').strip().lower()
    if estado == 'checkout':
        conn.close()
        flash('La salida de esta reserva ya estaba registrada.', 'info')
        return redirect(url_for('recepcion.reserva_detalle', id=id))
    if estado != 'alojado':
        conn.close()
        flash('Solo se puede registrar la salida de una reserva alojada.', 'warning')
        return redirect(url_for('recepcion.reserva_detalle', id=id))
    _recalcular_cargos_reserva(conn, id, empresa_id)
    reserva = _reserva_por_id(conn, id, empresa_id)
    if float(reserva["saldo"] or 0) > 0:
        conn.close()
        flash("No se puede hacer check-out con saldo pendiente. Liquida los extras o registra el cobro primero.", "warning")
        return redirect(url_for("recepcion.reserva_detalle", id=id))
    credito = _credito_reserva(conn, id, empresa_id)
    fianza_estado_actual = credito.get("fianza_estado") or "sin_fianza"
    liberar_fianza = (
        credito["politica"] == "fianza_virtual"
        and credito["fianza_virtual"] > 0
        and fianza_estado_actual not in {"liberada", "cobrada", "cobrada_parcial"}
    )
    conn.execute(
        """
        UPDATE reservas_recepcion
        SET estado = 'checkout',
            checkout_at = COALESCE(checkout_at, CURRENT_TIMESTAMP),
            fianza_estado = CASE WHEN ? THEN 'liberada' ELSE fianza_estado END,
            fianza_liberada_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE fianza_liberada_at END,
            politica_credito = CASE WHEN ? THEN 'sin_credito' ELSE politica_credito END,
            limite_credito = CASE WHEN ? THEN 0 ELSE limite_credito END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (1 if liberar_fianza else 0, 1 if liberar_fianza else 0, 1 if liberar_fianza else 0, 1 if liberar_fianza else 0, id, empresa_id),
    )
    _marcar_habitacion_estado(conn, reserva["habitacion_id"], empresa_id, "limpieza")
    conn.commit()
    conn.close()
    flash("Check-out realizado." + (" Fianza virtual liberada." if liberar_fianza else ""), "success")
    return redirect(url_for("recepcion.reserva_detalle", id=id))


@recepcion_bp.route("/reservas/<int:id>/no-show", methods=["POST"])
@login_required
@_require_recepcion
def no_show_reserva(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    if reserva['estado'] not in {'pendiente', 'confirmada'}:
        conn.close()
        flash('Solo una reserva pendiente de entrada puede marcarse como no-show.', 'warning')
        return redirect(url_for('recepcion.reserva_detalle', id=id))
    conn.execute(
        """
        UPDATE reservas_recepcion
        SET estado = 'no_show', no_show_at = COALESCE(no_show_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (id, empresa_id),
    )
    _marcar_habitacion_estado(conn, reserva["habitacion_id"], empresa_id, "disponible")
    conn.commit()
    conn.close()
    flash("Reserva marcada como no-show.", "success")
    return redirect(url_for("recepcion.reserva_detalle", id=id))


def _registrar_clave_cobro(conn, empresa_id, reserva_id, clave):
    return conn.execute('''
        INSERT OR IGNORE INTO recepcion_claves_cobro(empresa_id, reserva_id, clave)
        VALUES (?, ?, ?)
    ''', (empresa_id, reserva_id, clave)).rowcount == 1


@recepcion_bp.route("/reservas/<int:id>/pagos", methods=["POST"])
@login_required
@_require_recepcion
def anadir_pago_reserva(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    importe = _to_float(request.form.get("importe"))
    if importe <= 0:
        conn.close()
        flash("El importe del pago debe ser mayor que cero.", "warning")
        return redirect(url_for("recepcion.reserva_detalle", id=id))
    clave = (request.form.get('clave_cobro') or '').strip()
    if clave:
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        if not _registrar_clave_cobro(conn, empresa_id, id, clave):
            conn.rollback()
            conn.close()
            flash('Este cobro ya estaba registrado.', 'info')
            return redirect(url_for('recepcion.reserva_detalle', id=id))
        reserva = _reserva_por_id(conn, id, empresa_id)
        if not reserva:
            conn.rollback()
            conn.close()
            abort(404)
    _asegurar_deposito_historico_como_pago(conn, reserva, id, empresa_id)
    conn.execute(
        """
        INSERT INTO pagos_reserva_recepcion
        (reserva_id, fecha, concepto, importe, forma_pago, referencia, estado, observaciones, empresa_id, usuario_id)
        VALUES (?, ?, ?, ?, ?, ?, 'confirmado', ?, ?, ?)
        """,
        (
            id,
            request.form.get("fecha") or date.today().isoformat(),
            request.form.get("concepto") or "Pago reserva",
            importe,
            request.form.get("forma_pago") or "",
            request.form.get("referencia") or "",
            request.form.get("observaciones") or "",
            empresa_id,
            session.get("user_id"),
        ),
    )
    _recalcular_pagos_reserva(conn, id, empresa_id)
    conn.commit()
    conn.close()
    flash("Pago registrado.", "success")
    return redirect(url_for("recepcion.reserva_detalle", id=id))


@recepcion_bp.route("/reservas/<int:id>/pagos/link", methods=["POST"])
@login_required
@_require_recepcion
def crear_link_pago_reserva(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    service = _payment_service(conn, empresa_id)
    if not service:
        conn.close()
        flash("El modulo de pasarelas no esta disponible.", "danger")
        return redirect(url_for("recepcion.reserva_detalle", id=id))
    importe = _to_float(request.form.get("importe_link"), 0)
    if importe <= 0:
        conn.close()
        flash("El importe del link de pago debe ser mayor que cero.", "warning")
        return redirect(url_for("recepcion.reserva_detalle", id=id))
    metodo = request.form.get("metodo_link") or "link"
    concepto = request.form.get("concepto_link") or "Pago reserva"
    try:
        op = service.create_operation(
            config_id=_to_int(request.form.get("config_id"), 0) or None,
            importe=importe,
            descripcion=concepto,
            metodo=metodo,
            origen_modulo="recepcion",
            origen_tipo=request.form.get("origen_tipo") or "reserva",
            origen_id=id,
            cliente_nombre=reserva["titular_nombre"] if "titular_nombre" in reserva.keys() else "",
            cliente_email=reserva["titular_email"] if "titular_email" in reserva.keys() else "",
            extra={
                "reserva_codigo": reserva["codigo"] if "codigo" in reserva.keys() else "",
                "habitacion_id": reserva["habitacion_id"] if "habitacion_id" in reserva.keys() else None,
            },
        )
        conn.commit()
        flash(f"Link de pago creado: {op.get('checkout_url') or op.get('referencia_interna')}", "success")
    except Exception as exc:
        conn.rollback()
        flash(f"No se pudo crear el link de pago: {exc}", "danger")
    finally:
        conn.close()
    return redirect(url_for("recepcion.reserva_detalle", id=id))


@recepcion_bp.route("/reservas/<int:id>/credito", methods=["POST"])
@login_required
@_require_recepcion
def actualizar_credito_reserva(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    politica = _normalizar_politica_credito(request.form.get("politica_credito"))
    limite_credito = max(_to_float(request.form.get("limite_credito"), 0), 0)
    fianza_virtual = max(_to_float(request.form.get("fianza_virtual"), 0), 0)
    if politica == "sin_credito":
        limite_credito = 0
        fianza_virtual = 0
    elif politica == "fianza_virtual":
        limite_credito = max(limite_credito, fianza_virtual)
    conn.execute(
        """
        UPDATE reservas_recepcion
        SET politica_credito = ?, limite_credito = ?, fianza_virtual = ?,
            fianza_referencia = ?, fianza_estado = ?,
            fianza_liberada_at = NULL, fianza_cobrada_at = NULL,
            fianza_liquidada_importe = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (
            politica,
            limite_credito,
            fianza_virtual,
            request.form.get("fianza_referencia") or "",
            _estado_fianza_para_credito(politica, fianza_virtual),
            id,
            empresa_id,
        ),
    )
    conn.commit()
    conn.close()
    flash("Credito de habitacion actualizado.", "success")
    return redirect(url_for("recepcion.reserva_detalle", id=id))


@recepcion_bp.route("/reservas/<int:id>/fianza/liquidar", methods=["POST"])
@login_required
@_require_recepcion
def liquidar_fianza_reserva(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    _recalcular_cargos_reserva(conn, id, empresa_id)
    reserva = _reserva_por_id(conn, id, empresa_id)
    credito = _credito_reserva(conn, id, empresa_id)
    saldo = round(_to_float(reserva["saldo"], 0), 2)
    if credito["politica"] != "fianza_virtual" or credito["fianza_virtual"] <= 0:
        conn.close()
        flash("La reserva no tiene fianza virtual activa.", "warning")
        return redirect(url_for("recepcion.reserva_detalle", id=id))
    if saldo <= 0:
        conn.execute(
            """
            UPDATE reservas_recepcion
            SET fianza_estado = 'liberada',
                fianza_liberada_at = CURRENT_TIMESTAMP,
                politica_credito = 'sin_credito',
                limite_credito = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND empresa_id = ?
            """,
            (id, empresa_id),
        )
        conn.commit()
        conn.close()
        flash("Fianza virtual liberada: no hay saldo pendiente.", "success")
        return redirect(url_for("recepcion.reserva_detalle", id=id))
    if saldo > credito["fianza_virtual"]:
        conn.close()
        flash(
            f"Saldo pendiente {saldo:.2f} euros superior a la fianza virtual autorizada ({credito['fianza_virtual']:.2f} euros).",
            "warning",
        )
        return redirect(url_for("recepcion.reserva_detalle", id=id))

    _asegurar_deposito_historico_como_pago(conn, reserva, id, empresa_id)
    conn.execute(
        """
        INSERT INTO pagos_reserva_recepcion
        (reserva_id, fecha, concepto, importe, forma_pago, referencia, observaciones, empresa_id, usuario_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            id,
            request.form.get("fecha") or date.today().isoformat(),
            request.form.get("concepto") or "Liquidacion fianza virtual",
            saldo,
            request.form.get("forma_pago") or "Tarjeta fianza virtual",
            request.form.get("referencia") or credito["referencia"] or "",
            request.form.get("observaciones") or "Cobro de extras contra fianza virtual",
            empresa_id,
            session.get("user_id"),
        ),
    )
    factura_id = None
    pendientes = _cargos_pendientes_facturar(conn, id, empresa_id)
    if pendientes and (pendientes["total_cargos"] or 0):
        reserva_actualizada = _reserva_por_id(conn, id, empresa_id)
        factura_id = _crear_factura_reserva(
            conn,
            reserva_actualizada,
            empresa_id,
            {
                "tipo": "factura",
                "serie": request.form.get("serie") or "REC",
                "fecha": request.form.get("fecha") or date.today().isoformat(),
                "cliente_nombre": request.form.get("cliente_nombre") or reserva["titular_nombre"],
                "cliente_nif": request.form.get("cliente_nif"),
                "cliente_direccion": request.form.get("cliente_direccion"),
                "cliente_email": request.form.get("cliente_email") or reserva["titular_email"],
                "idioma": request.form.get("idioma") or "es",
                "impuesto_extras_id": request.form.get("impuesto_extras_id"),
                "iva_extras": request.form.get("iva_extras"),
                "observaciones": "Factura de extras liquidada contra fianza virtual",
                "incluir_estancia": False,
            },
        )
    _recalcular_pagos_reserva(conn, id, empresa_id)
    conn.execute(
        """
        UPDATE reservas_recepcion
        SET fianza_estado = ?,
            fianza_cobrada_at = CURRENT_TIMESTAMP,
            fianza_liberada_at = CURRENT_TIMESTAMP,
            fianza_liquidada_importe = ?,
            politica_credito = 'sin_credito',
            limite_credito = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        ("cobrada" if saldo >= credito["fianza_virtual"] else "cobrada_parcial", saldo, id, empresa_id),
    )
    conn.commit()
    conn.close()
    flash("Fianza virtual liquidada y extras cobrados.", "success")
    if factura_id:
        return redirect(url_for("recepcion.factura_detalle", id=factura_id, autoprint=1))
    return redirect(url_for("recepcion.reserva_detalle", id=id))


@recepcion_bp.route("/reservas/<int:reserva_id>/pagos/<int:pago_id>/anular", methods=["POST"])
@login_required
@_require_recepcion
def anular_pago_reserva(reserva_id, pago_id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, reserva_id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    pago = conn.execute(
        "SELECT id, estado FROM pagos_reserva_recepcion WHERE id = ? AND reserva_id = ? AND empresa_id = ?",
        (pago_id, reserva_id, empresa_id),
    ).fetchone()
    if not pago:
        conn.close()
        abort(404)
    if pago['estado'] == 'anulado':
        conn.close()
        flash('Este pago ya estaba anulado.', 'info')
        return redirect(url_for('recepcion.reserva_detalle', id=reserva_id))
    conn.execute(
        """
        UPDATE pagos_reserva_recepcion
        SET estado = 'anulado'
        WHERE id = ? AND reserva_id = ? AND empresa_id = ?
        """,
        (pago_id, reserva_id, empresa_id),
    )
    _recalcular_pagos_reserva(conn, reserva_id, empresa_id)
    conn.commit()
    conn.close()
    flash("Pago anulado.", "success")
    return redirect(url_for("recepcion.reserva_detalle", id=reserva_id))


@recepcion_bp.route("/reservas/<int:id>/cargos", methods=["POST"])
@login_required
@_require_recepcion
def anadir_cargo_reserva(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    cantidad = max(_to_float(request.form.get("cantidad"), 1), 0)
    precio_unitario = max(_to_float(request.form.get("precio_unitario")), 0)
    total = round(cantidad * precio_unitario, 2)
    concepto = (request.form.get("concepto") or "").strip()
    if not concepto:
        conn.close()
        flash("Indica el concepto del cargo.", "warning")
        return redirect(url_for("recepcion.reserva_detalle", id=id))
    incluido = 1 if request.form.get("incluido") else 0
    cargo_habitacion = 1 if request.form.get("cargo_habitacion") else 0
    if cargo_habitacion and not incluido and total > 0:
        credito = _credito_reserva(conn, id, empresa_id)
        if credito["politica"] == "sin_credito":
            conn.close()
            flash("La habitacion no tiene credito activo. Cobra el consumo directamente o autoriza una fianza virtual.", "warning")
            return redirect(url_for("recepcion.reserva_detalle", id=id))
        if credito["disponible"] is not None and total > float(credito["disponible"] or 0):
            conn.close()
            flash(
                f"Cargo rechazado: supera el credito disponible ({credito['disponible']:.2f} euros).",
                "warning",
            )
            return redirect(url_for("recepcion.reserva_detalle", id=id))
    conn.execute(
        """
        INSERT INTO recepcion_cargos_reserva
        (reserva_id, punto_venta_id, restaurante_reserva_id, fecha, servicio, concepto,
         cantidad, precio_unitario, total, incluido, cargo_habitacion, estado, observaciones, empresa_id, usuario_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', ?, ?, ?)
        """,
        (
            id,
            _to_int(request.form.get("punto_venta_id"), 0) or None,
            _to_int(request.form.get("restaurante_reserva_id"), 0) or None,
            request.form.get("fecha") or date.today().isoformat(),
            request.form.get("servicio") or "",
            concepto,
            cantidad,
            precio_unitario,
            total,
            incluido,
            cargo_habitacion,
            request.form.get("observaciones") or "",
            empresa_id,
            session.get("user_id"),
        ),
    )
    _recalcular_cargos_reserva(conn, id, empresa_id)
    conn.commit()
    conn.close()
    flash("Cargo registrado en la reserva.", "success")
    return redirect(url_for("recepcion.reserva_detalle", id=id))


@recepcion_bp.route("/reservas/<int:reserva_id>/cargos/<int:cargo_id>/anular", methods=["POST"])
@login_required
@_require_recepcion
def anular_cargo_reserva(reserva_id, cargo_id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    if not _reserva_por_id(conn, reserva_id, empresa_id):
        conn.close()
        abort(404)
    cargo = conn.execute(
        "SELECT id, estado, factura_id FROM recepcion_cargos_reserva WHERE id = ? AND reserva_id = ? AND empresa_id = ?",
        (cargo_id, reserva_id, empresa_id),
    ).fetchone()
    if not cargo:
        conn.close()
        abort(404)
    if cargo['estado'] == 'anulado':
        conn.close()
        flash('Este cargo ya estaba anulado.', 'info')
        return redirect(url_for('recepcion.reserva_detalle', id=reserva_id))
    if cargo['factura_id'] is not None:
        conn.close()
        flash('El cargo ya esta facturado. Revisa la factura y realiza su rectificacion.', 'warning')
        return redirect(url_for('recepcion.factura_detalle', id=cargo['factura_id']))
    conn.execute(
        "UPDATE recepcion_cargos_reserva SET estado = 'anulado' WHERE id = ? AND reserva_id = ? AND empresa_id = ?",
        (cargo_id, reserva_id, empresa_id),
    )
    _recalcular_cargos_reserva(conn, reserva_id, empresa_id)
    conn.commit()
    conn.close()
    flash("Cargo anulado.", "success")
    return redirect(url_for("recepcion.reserva_detalle", id=reserva_id))


@recepcion_bp.route("/reservas/<int:id>/facturas/generar", methods=["POST"])
@login_required
@_require_recepcion
def generar_factura_reserva(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    tipo = (request.form.get("tipo") or "factura").strip().lower()
    if tipo not in {'factura', 'proforma'}:
        tipo = 'factura'
    if tipo == "factura":
        existente = _factura_reserva_emitida(conn, id, empresa_id)
        if existente:
            estado_existente = conn.execute(
                """
                SELECT f.total,
                       (SELECT COUNT(*) FROM recepcion_factura_lineas l WHERE l.factura_id = f.id AND l.empresa_id = f.empresa_id) AS lineas
                FROM recepcion_facturas f
                WHERE f.id = ? AND f.empresa_id = ?
                """,
                (existente["id"], empresa_id),
            ).fetchone()
            if estado_existente and _to_float(estado_existente["total"], 0) <= 0 and not (estado_existente["lineas"] or 0):
                factura_id = _rellenar_factura_vacia_reserva(
                    conn,
                    existente["id"],
                    reserva,
                    empresa_id,
                    {
                        "cliente_nombre": request.form.get("cliente_nombre"),
                        "cliente_nif": request.form.get("cliente_nif"),
                        "cliente_direccion": request.form.get("cliente_direccion"),
                        "cliente_email": request.form.get("cliente_email"),
                        "idioma": request.form.get("idioma"),
                        "impuesto_estancia_id": request.form.get("impuesto_estancia_id"),
                        "impuesto_extras_id": request.form.get("impuesto_extras_id"),
                        "iva_estancia": request.form.get("iva_estancia"),
                        "iva_extras": request.form.get("iva_extras"),
                        "observaciones": request.form.get("observaciones") or "Factura recalculada desde reserva",
                    },
                )
                if factura_id:
                    conn.commit()
                    conn.close()
                    flash("Factura existente recalculada con el precio de la reserva.", "success")
                    return redirect(url_for("recepcion.factura_detalle", id=factura_id))
                conn.close()
                flash("La factura existente esta vacia y no se pudo recalcular: revisa tipo de habitacion, tarifas o precio de la reserva.", "warning")
                return redirect(url_for("recepcion.reserva_detalle", id=id))
            pendientes = _cargos_pendientes_facturar(conn, id, empresa_id)
            if not pendientes or not (pendientes["total_cargos"] or 0):
                conn.close()
                flash(f"La reserva ya tiene factura {existente['numero_completo']} y no hay cargos nuevos pendientes.", "warning")
                return redirect(url_for("recepcion.factura_detalle", id=existente["id"]))
            incluir_estancia = False
        else:
            incluir_estancia = True
    else:
        incluir_estancia = True
    _recalcular_cargos_reserva(conn, id, empresa_id)
    factura_id = _crear_factura_reserva(
        conn,
        reserva,
        empresa_id,
        {
            "tipo": tipo,
            "serie": request.form.get("serie"),
            "fecha": request.form.get("fecha"),
            "cliente_nombre": request.form.get("cliente_nombre"),
            "cliente_nif": request.form.get("cliente_nif"),
            "cliente_direccion": request.form.get("cliente_direccion"),
            "cliente_email": request.form.get("cliente_email"),
            "idioma": request.form.get("idioma"),
            "impuesto_estancia_id": request.form.get("impuesto_estancia_id"),
            "impuesto_extras_id": request.form.get("impuesto_extras_id"),
            "iva_estancia": request.form.get("iva_estancia"),
            "iva_extras": request.form.get("iva_extras"),
            "observaciones": request.form.get("observaciones"),
            "incluir_estancia": incluir_estancia,
        },
    )
    if not factura_id:
        conn.commit()
        conn.close()
        flash("No se ha generado factura: la reserva no tiene precio ni cargos pendientes. Revisa tipo de habitacion, tarifas o introduce precio en la reserva.", "warning")
        return redirect(url_for("recepcion.reserva_detalle", id=id))
    conn.commit()
    conn.close()
    flash("Documento generado.", "success")
    return redirect(url_for("recepcion.factura_detalle", id=factura_id))


@recepcion_bp.route("/reservas/<int:id>/cerrar-cuenta", methods=["POST"])
@login_required
@_require_recepcion
def cerrar_cuenta_reserva(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva = _reserva_por_id(conn, id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    _recalcular_cargos_reserva(conn, id, empresa_id)
    reserva = _reserva_por_id(conn, id, empresa_id)
    if float(reserva["saldo"] or 0) > 0:
        conn.close()
        flash("No se puede cerrar la cuenta: aun hay saldo pendiente.", "warning")
        return redirect(url_for("recepcion.reserva_detalle", id=id))
    factura = conn.execute(
        """
        SELECT id
        FROM recepcion_facturas
        WHERE reserva_id = ? AND empresa_id = ? AND tipo = 'factura' AND COALESCE(estado, 'emitida') <> 'anulada'
        ORDER BY id DESC
        LIMIT 1
        """,
        (id, empresa_id),
    ).fetchone()
    if not factura:
        factura_id = _crear_factura_reserva(
            conn,
            reserva,
            empresa_id,
            {
                "tipo": "factura",
                "serie": "REC",
                "fecha": date.today().isoformat(),
                "cliente_nombre": reserva["titular_nombre"],
                "cliente_email": reserva["titular_email"],
                "iva_estancia": "10",
                "iva_extras": "10",
                "observaciones": "Generada al cerrar cuenta",
            },
        )
        if not factura_id:
            conn.close()
            flash("No se puede cerrar la cuenta sin factura: la reserva no tiene precio ni cargos facturables.", "warning")
            return redirect(url_for("recepcion.reserva_detalle", id=id))
        factura = {"id": factura_id}
    else:
        factura_id = _rellenar_factura_vacia_reserva(
            conn,
            factura["id"],
            reserva,
            empresa_id,
            {
                "cliente_nombre": reserva["titular_nombre"],
                "cliente_email": reserva["titular_email"],
                "iva_estancia": "10",
                "iva_extras": "10",
                "observaciones": "Recalculada al cerrar cuenta",
            },
        )
        if not factura_id:
            conn.close()
            flash("No se puede cerrar la cuenta: la factura existente esta vacia y no se pudo recalcular.", "warning")
            return redirect(url_for("recepcion.reserva_detalle", id=id))
    conn.execute(
        """
        UPDATE recepcion_facturas
        SET estado = 'cobrada', pagado = total, saldo = 0, updated_at = CURRENT_TIMESTAMP
        WHERE reserva_id = ? AND empresa_id = ? AND COALESCE(estado, 'emitida') <> 'anulada'
        """,
        (id, empresa_id),
    )
    conn.execute(
        """
        UPDATE reservas_recepcion
        SET estado = 'checkout',
            checkout_at = COALESCE(checkout_at, CURRENT_TIMESTAMP),
            cuenta_cerrada_at = COALESCE(cuenta_cerrada_at, CURRENT_TIMESTAMP),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (id, empresa_id),
    )
    _marcar_habitacion_estado(conn, reserva["habitacion_id"], empresa_id, "limpieza")
    conn.commit()
    conn.close()
    flash("Cuenta cerrada y reserva en check-out.", "success")
    return redirect(url_for("recepcion.reserva_detalle", id=id))


@recepcion_bp.route("/reservas/<int:reserva_id>/viajeros/nuevo", methods=["GET", "POST"])
@recepcion_bp.route("/viajeros/<int:id>/editar", methods=["GET", "POST"])
@login_required
@_require_recepcion
def viajero_form(reserva_id=None, id=None):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    edad_min_documento = _edad_min_documento_config(conn, empresa_id)
    viajero = None
    if id:
        viajero = conn.execute("SELECT * FROM viajeros_recepcion WHERE id = ? AND empresa_id = ?", (id, empresa_id)).fetchone()
        if not viajero:
            conn.close()
            abort(404)
        reserva_id = viajero["reserva_id"]
    reserva = _reserva_por_id(conn, reserva_id, empresa_id)
    if not reserva:
        conn.close()
        abort(404)
    if request.method == "POST":
        departamentos = request.form.getlist("avisos_departamentos")
        if not departamentos and (request.form.get("alergias_intolerancias") or "").strip():
            departamentos = ["cocina"]
        datos = (
            reserva_id,
            request.form.get("nombre") or "",
            request.form.get("apellidos") or "",
            request.form.get("fecha_nacimiento") or "",
            request.form.get("nacionalidad") or "",
            request.form.get("sexo") or "",
            request.form.get("tipo_documento") or "",
            request.form.get("numero_documento") or "",
            request.form.get("soporte_documento") or "",
            request.form.get("parentesco") or "",
            request.form.get("telefono") or "",
            request.form.get("email") or "",
            request.form.get("direccion") or "",
            request.form.get("codigo_postal") or "",
            request.form.get("localidad") or "",
            request.form.get("pais") or "Espana",
            1 if request.form.get("es_titular") else 0,
            request.form.get("alergias_intolerancias") or "",
            request.form.get("observaciones_salud") or "",
            ",".join(departamentos),
            request.form.get("registro_viajeros_estado") or "pendiente",
            request.form.get("registro_viajeros_fecha") or "",
        )
        if id:
            conn.execute(
                """
                UPDATE viajeros_recepcion
                SET reserva_id = ?, nombre = ?, apellidos = ?, fecha_nacimiento = ?, nacionalidad = ?, sexo = ?,
                    tipo_documento = ?, numero_documento = ?, soporte_documento = ?, parentesco = ?, telefono = ?,
                    email = ?, direccion = ?, codigo_postal = ?, localidad = ?, pais = ?, es_titular = ?,
                    alergias_intolerancias = ?, observaciones_salud = ?, avisos_departamentos = ?,
                    registro_viajeros_estado = ?, registro_viajeros_fecha = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                datos + (id, empresa_id),
            )
            viajero_id = id
        else:
            conn.execute(
                """
                INSERT INTO viajeros_recepcion
                (reserva_id, nombre, apellidos, fecha_nacimiento, nacionalidad, sexo, tipo_documento,
                 numero_documento, soporte_documento, parentesco, telefono, email, direccion, codigo_postal,
                 localidad, pais, es_titular, alergias_intolerancias, observaciones_salud, avisos_departamentos,
                 registro_viajeros_estado, registro_viajeros_fecha, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                datos + (empresa_id,),
            )
            viajero_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        aviso_desc = "; ".join(
            p for p in [request.form.get("alergias_intolerancias"), request.form.get("observaciones_salud")] if p
        )
        _guardar_avisos_viajero(conn, viajero_id, reserva_id, empresa_id, aviso_desc, departamentos)
        conn.commit()
        conn.close()
        flash("Viajero guardado.", "success")
        return redirect(url_for("recepcion.reserva_detalle", id=reserva_id))
    conn.close()
    return render_template(
        "recepcion/viajero_form.html",
        reserva=reserva,
        viajero=viajero,
        departamentos_aviso=DEPARTAMENTOS_AVISO,
        edad_min_documento=edad_min_documento,
    )


@recepcion_bp.route("/registro-viajeros")
@login_required
@_require_recepcion
def registro_viajeros():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    filtros = _filtros_registro_viajeros(request.args)
    viajeros = _query_registro_viajeros(conn, empresa_id, filtros)
    resumen_rows = conn.execute(
        """
        SELECT COALESCE(registro_viajeros_estado, 'pendiente') AS estado, COUNT(*) AS total
        FROM viajeros_recepcion
        WHERE empresa_id = ?
        GROUP BY COALESCE(registro_viajeros_estado, 'pendiente')
        """,
        (empresa_id,),
    ).fetchall()
    resumen = {row["estado"]: row["total"] for row in resumen_rows}
    edad_min_documento = _edad_min_documento_config(conn, empresa_id)
    viajeros_detalle = [
        {
            "row": v,
            "faltantes": _viajero_faltantes(v, edad_min_documento),
            "documento_obligatorio": _documento_obligatorio_viajero(v, edad_min_documento),
            "edad": _edad_en_fecha(v["fecha_nacimiento"], v["fecha_entrada"]),
        }
        for v in viajeros
    ]
    alerta = _resumen_alerta_registro_viajeros(conn, empresa_id)
    envios_demo = conn.execute(
        """
        SELECT *
        FROM recepcion_registro_viajeros_envios_demo
        WHERE empresa_id = ?
        ORDER BY id DESC
        LIMIT 8
        """,
        (empresa_id,),
    ).fetchall()
    conn.close()
    return render_template(
        "recepcion/registro_viajeros.html",
        viajeros=viajeros_detalle,
        filtros=filtros,
        resumen=resumen,
        edad_min_documento=edad_min_documento,
        alerta=alerta,
        envios_demo=envios_demo,
    )


@recepcion_bp.route("/registro-viajeros/estado", methods=["POST"])
@login_required
@_require_recepcion
def actualizar_registro_viajeros():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    estado = request.form.get("estado") or "pendiente"
    if estado not in {"pendiente", "preparado", "enviado", "aceptado", "rechazado"}:
        estado = "pendiente"
    fecha = request.form.get("registro_viajeros_fecha") or (date.today().isoformat() if estado in {"enviado", "aceptado"} else "")
    ids = request.form.getlist("viajero_ids")
    if not ids and request.form.get("viajero_id"):
        ids = [request.form.get("viajero_id")]
    ids = [_to_int(i, 0) for i in ids if _to_int(i, 0)]
    if not ids:
        conn.close()
        flash("Selecciona al menos un viajero.", "warning")
        return redirect(request.referrer or url_for("recepcion.registro_viajeros"))
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"""
        UPDATE viajeros_recepcion
        SET registro_viajeros_estado = ?, registro_viajeros_fecha = ?, updated_at = CURRENT_TIMESTAMP
        WHERE empresa_id = ? AND id IN ({placeholders})
        """,
        [estado, fecha, empresa_id] + ids,
    )
    conn.commit()
    conn.close()
    flash("Registro de viajeros actualizado.", "success")
    return redirect(request.referrer or url_for("recepcion.registro_viajeros"))


@recepcion_bp.route("/registro-viajeros/preparar-envio", methods=["POST"])
@login_required
@_require_recepcion
def preparar_envio_registro_viajeros():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    config = _config_fiscal(conn, empresa_id)
    dias = max(0, _to_int(config["registro_viajeros_alerta_dias"] if config and "registro_viajeros_alerta_dias" in config.keys() else 1, 1))
    hora = (config["registro_viajeros_alerta_hora"] if config and "registro_viajeros_alerta_hora" in config.keys() else "") or "22:00"
    hasta = request.form.get("hasta") or (date.today() + timedelta(days=dias)).isoformat()
    hasta_dt = _parse_date(hasta) or (date.today() + timedelta(days=dias))
    hasta = hasta_dt.isoformat()
    edad_min = _edad_min_documento_config(conn, empresa_id)
    viajeros = _viajeros_para_alerta_registro(conn, empresa_id, hasta)
    ids_preparar = []
    incompletos = 0
    for viajero in viajeros:
        if _viajero_faltantes(viajero, edad_min):
            incompletos += 1
        else:
            ids_preparar.append(viajero["id"])
    if ids_preparar:
        placeholders = ",".join("?" for _ in ids_preparar)
        conn.execute(
            f"""
            UPDATE viajeros_recepcion
            SET registro_viajeros_estado = 'preparado',
                registro_viajeros_fecha = COALESCE(NULLIF(registro_viajeros_fecha, ''), ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE empresa_id = ?
              AND id IN ({placeholders})
              AND COALESCE(registro_viajeros_estado, 'pendiente') IN ('pendiente', 'preparado', 'rechazado')
            """,
            [date.today().isoformat(), empresa_id] + ids_preparar,
        )
    reservas_sin_viajeros = _reservas_sin_viajeros_alerta(conn, empresa_id, hasta)
    resumen = (
        f"Hasta {hasta}: {len(ids_preparar)} preparados, {incompletos} incompletos, "
        f"{reservas_sin_viajeros} reservas sin viajeros."
    )
    conn.execute(
        """
        INSERT INTO recepcion_registro_viajeros_alertas
        (fecha_objetivo, hora_programada, estado, total_viajeros, preparados, incompletos,
         reservas_sin_viajeros, resumen, empresa_id, usuario_id)
        VALUES (?, ?, 'preparada', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            hasta,
            hora,
            len(viajeros),
            len(ids_preparar),
            incompletos,
            reservas_sin_viajeros,
            resumen,
            empresa_id,
            session.get("user_id"),
        ),
    )
    conn.commit()
    conn.close()
    flash(resumen, "success" if ids_preparar and not incompletos and not reservas_sin_viajeros else "warning")
    return redirect(url_for("recepcion.registro_viajeros", estado="preparado", hasta=hasta))


@recepcion_bp.route("/registro-viajeros/enviar-demo", methods=["POST"])
@login_required
@_require_recepcion
def enviar_demo_registro_viajeros():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    config = _config_fiscal(conn, empresa_id)
    dias = max(0, _to_int(config["registro_viajeros_alerta_dias"] if config and "registro_viajeros_alerta_dias" in config.keys() else 1, 1))
    hasta = request.form.get("hasta") or (date.today() + timedelta(days=dias)).isoformat()
    hasta_dt = _parse_date(hasta) or (date.today() + timedelta(days=dias))
    hasta = hasta_dt.isoformat()
    destinatario = (request.form.get("destinatario") or "demo@nexoraia.studio").strip() or "demo@nexoraia.studio"
    edad_min = _edad_min_documento_config(conn, empresa_id)
    viajeros = _viajeros_para_alerta_registro(conn, empresa_id, hasta)
    completos = []
    incompletos = 0
    for viajero in viajeros:
        if _viajero_faltantes(viajero, edad_min):
            incompletos += 1
        else:
            completos.append(viajero)
    reservas_sin_viajeros = _reservas_sin_viajeros_alerta(conn, empresa_id, hasta)
    resumen = (
        f"Demo registro viajeros hasta {hasta}: {len(completos)} enviados, "
        f"{incompletos} incompletos, {reservas_sin_viajeros} reservas sin viajeros."
    )
    estado_envio = "sin_datos"
    error_envio = ""
    if completos:
        csv_text = _csv_registro_viajeros_texto(completos, edad_min)
        cuerpo = f"""
        <h2>Registro de viajeros - envio demo</h2>
        <p>{escape(resumen)}</p>
        <p>Este envio es una prueba interna de El Raco ERP. No conecta con Guardia Civil ni Ministerio.</p>
        <pre style="white-space:pre-wrap;font-family:monospace;border:1px solid #ddd;padding:12px;">{escape(csv_text)}</pre>
        """
        mail_instance = current_app.extensions.get("mail")
        ok, mensaje = enviar_email(destinatario, f"Demo registro viajeros hasta {hasta}", cuerpo, html=True, mail_instance=mail_instance)
        estado_envio = "enviado" if ok else "error_email"
        error_envio = "" if ok else mensaje
        if ok:
            ids = [v["id"] for v in completos]
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"""
                UPDATE viajeros_recepcion
                SET registro_viajeros_estado = 'enviado',
                    registro_viajeros_fecha = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE empresa_id = ? AND id IN ({placeholders})
                """,
                [date.today().isoformat(), empresa_id] + ids,
            )
    conn.execute(
        """
        INSERT INTO recepcion_registro_viajeros_envios_demo
        (fecha_objetivo, destinatario, estado, total_viajeros, enviados, incompletos,
         reservas_sin_viajeros, resumen, error, empresa_id, usuario_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            hasta,
            destinatario,
            estado_envio,
            len(viajeros),
            len(completos) if estado_envio == "enviado" else 0,
            incompletos,
            reservas_sin_viajeros,
            resumen,
            error_envio,
            empresa_id,
            session.get("user_id"),
        ),
    )
    conn.commit()
    conn.close()
    if estado_envio == "enviado":
        flash(resumen, "success")
    elif estado_envio == "sin_datos":
        flash("No hay viajeros completos para enviar en esta ventana.", "warning")
    else:
        flash(f"No se pudo enviar el demo: {error_envio}", "danger")
    return redirect(url_for("recepcion.registro_viajeros", estado="enviado" if estado_envio == "enviado" else "", hasta=hasta))


@recepcion_bp.route("/registro-viajeros/exportar")
@login_required
@_require_recepcion
def exportar_registro_viajeros():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    filtros = _filtros_registro_viajeros(request.args)
    viajeros = _query_registro_viajeros(conn, empresa_id, filtros)
    edad_min_documento = _edad_min_documento_config(conn, empresa_id)
    output = _csv_registro_viajeros_texto(viajeros, edad_min_documento)
    conn.close()
    filename = f"registro_viajeros_{date.today().isoformat()}.csv"
    return Response(
        output,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@recepcion_bp.route("/bloqueos")
@login_required
@_require_recepcion
def bloqueos():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    estado = request.args.get("estado") or "activo"
    filtros = ["b.empresa_id = ?"]
    params = [empresa_id]
    if estado:
        filtros.append("COALESCE(b.estado, 'activo') = ?")
        params.append(estado)
    filas = conn.execute(
        f"""
        SELECT b.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre, h.bloque AS habitacion_bloque
        FROM bloqueos_habitacion_recepcion b
        JOIN habitaciones_recepcion h ON b.habitacion_id = h.id
        WHERE {" AND ".join(filtros)}
        ORDER BY date(b.fecha_inicio) DESC, h.numero
        """,
        params,
    ).fetchall()
    conn.close()
    return render_template("recepcion/bloqueos.html", bloqueos=filas, estado=estado)


@recepcion_bp.route("/bloqueos/nuevo", methods=["GET", "POST"])
@recepcion_bp.route("/bloqueos/<int:id>/editar", methods=["GET", "POST"])
@login_required
@_require_recepcion
def bloqueo_form(id=None):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    habitaciones = _habitaciones_select(conn, empresa_id)
    bloqueo = None
    if id:
        bloqueo = conn.execute(
            "SELECT * FROM bloqueos_habitacion_recepcion WHERE id = ? AND empresa_id = ?",
            (id, empresa_id),
        ).fetchone()
        if not bloqueo:
            conn.close()
            abort(404)
    if request.method == "POST":
        habitacion_id = _to_int(request.form.get("habitacion_id"))
        inicio = request.form.get("fecha_inicio")
        fin = request.form.get("fecha_fin")
        inicio_dt = _parse_date(inicio)
        fin_dt = _parse_date(fin)
        estado = request.form.get("estado") or "activo"
        if not habitacion_id or not inicio_dt or not fin_dt or fin_dt <= inicio_dt:
            flash("Revisa habitacion y fechas del bloqueo.", "warning")
            return render_template("recepcion/bloqueo_form.html", bloqueo=bloqueo, habitaciones=habitaciones)
        if estado == "activo" and _habitacion_tiene_reserva(conn, habitacion_id, inicio, fin, empresa_id):
            flash("No se puede bloquear: hay una reserva solapada en esas fechas.", "danger")
            return render_template("recepcion/bloqueo_form.html", bloqueo=bloqueo, habitaciones=habitaciones)
        if estado == "activo" and _habitacion_tiene_bloqueo(conn, habitacion_id, inicio, fin, empresa_id, id):
            flash("Ya existe otro bloqueo activo para esa habitacion y fechas.", "danger")
            return render_template("recepcion/bloqueo_form.html", bloqueo=bloqueo, habitaciones=habitaciones)
        datos = (
            habitacion_id,
            inicio,
            fin,
            request.form.get("motivo") or "bloqueo",
            request.form.get("descripcion") or "",
            estado,
        )
        if id:
            conn.execute(
                """
                UPDATE bloqueos_habitacion_recepcion
                SET habitacion_id = ?, fecha_inicio = ?, fecha_fin = ?, motivo = ?, descripcion = ?, estado = ?
                WHERE id = ? AND empresa_id = ?
                """,
                datos + (id, empresa_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO bloqueos_habitacion_recepcion
                (habitacion_id, fecha_inicio, fecha_fin, motivo, descripcion, estado, empresa_id, creado_por)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                datos + (empresa_id, session.get("user_id")),
            )
        conn.commit()
        conn.close()
        flash("Bloqueo guardado.", "success")
        return redirect(url_for("recepcion.bloqueos"))
    conn.close()
    return render_template("recepcion/bloqueo_form.html", bloqueo=bloqueo, habitaciones=habitaciones)


@recepcion_bp.route("/bloqueos/masivo", methods=["GET", "POST"])
@login_required
@_require_recepcion
def bloqueo_masivo():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    tipos = _tipos_habitacion_select(conn, empresa_id)
    if request.method == "POST":
        inicio = request.form.get("fecha_inicio")
        fin = request.form.get("fecha_fin")
        inicio_dt = _parse_date(inicio)
        fin_dt = _parse_date(fin)
        if not inicio_dt or not fin_dt or fin_dt <= inicio_dt:
            flash("Revisa las fechas del bloqueo masivo.", "warning")
            conn.close()
            return render_template("recepcion/bloqueo_masivo.html", tipos=tipos)
        bloque = (request.form.get("bloque") or "").strip()
        planta = (request.form.get("planta") or "").strip()
        tipo_id = _to_int(request.form.get("tipo_habitacion_id"), 0)
        cantidad = _to_int(request.form.get("cantidad"), 0)
        filtros = ["empresa_id = ?", "COALESCE(activo, 1) = 1"]
        params = [empresa_id]
        if bloque:
            filtros.append("bloque = ?")
            params.append(bloque)
        if planta:
            filtros.append("planta = ?")
            params.append(planta)
        if tipo_id:
            filtros.append("tipo_habitacion_id = ?")
            params.append(tipo_id)
        habitaciones = conn.execute(
            f"""
            SELECT *
            FROM habitaciones_recepcion
            WHERE {" AND ".join(filtros)}
            ORDER BY bloque, planta, numero
            """,
            params,
        ).fetchall()
        creados = 0
        saltadas = []
        for habitacion in habitaciones:
            if cantidad and creados >= cantidad:
                break
            if _habitacion_tiene_reserva(conn, habitacion["id"], inicio, fin, empresa_id):
                saltadas.append(f"{habitacion['numero']} (reservada)")
                continue
            if _habitacion_tiene_bloqueo(conn, habitacion["id"], inicio, fin, empresa_id):
                saltadas.append(f"{habitacion['numero']} (bloqueada)")
                continue
            conn.execute(
                """
                INSERT INTO bloqueos_habitacion_recepcion
                (habitacion_id, fecha_inicio, fecha_fin, motivo, descripcion, estado, empresa_id, creado_por)
                VALUES (?, ?, ?, ?, ?, 'activo', ?, ?)
                """,
                (
                    habitacion["id"],
                    inicio,
                    fin,
                    request.form.get("motivo") or "bloqueo",
                    request.form.get("descripcion") or "",
                    empresa_id,
                    session.get("user_id"),
                ),
            )
            creados += 1
        conn.commit()
        msg = f"Bloqueo masivo creado en {creados} habitaciones."
        if saltadas:
            msg += f" Saltadas: {', '.join(saltadas[:10])}"
            if len(saltadas) > 10:
                msg += "..."
        flash(msg, "success" if creados else "warning")
        conn.close()
        return redirect(url_for("recepcion.bloqueos"))
    conn.close()
    return render_template("recepcion/bloqueo_masivo.html", tipos=tipos)


@recepcion_bp.route("/bloqueos/<int:id>/anular", methods=["POST"])
@login_required
@_require_recepcion
def anular_bloqueo(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    conn.execute(
        "UPDATE bloqueos_habitacion_recepcion SET estado = 'anulado' WHERE id = ? AND empresa_id = ?",
        (id, empresa_id),
    )
    conn.commit()
    conn.close()
    flash("Bloqueo anulado.", "success")
    return redirect(url_for("recepcion.bloqueos"))


@recepcion_bp.route("/avisos")
@login_required
@_require_recepcion
def avisos():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    departamento = (request.args.get("departamento") or "").strip()
    estado = (request.args.get("estado") or "pendiente").strip()
    filtros = ["a.empresa_id = ?"]
    params = [empresa_id]
    if departamento:
        filtros.append("(a.departamentos LIKE ? OR a.departamentos = '')")
        params.append(f"%{departamento}%")
    if estado:
        filtros.append("COALESCE(a.estado, 'pendiente') = ?")
        params.append(estado)
    filas = conn.execute(
        f"""
        SELECT a.*, r.codigo, r.fecha_entrada, r.fecha_salida, r.regimen,
               h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               v.nombre AS viajero_nombre, v.apellidos AS viajero_apellidos
        FROM recepcion_avisos a
        LEFT JOIN reservas_recepcion r ON a.reserva_id = r.id
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        LEFT JOIN viajeros_recepcion v ON a.viajero_id = v.id
        WHERE {" AND ".join(filtros)}
        ORDER BY CASE a.prioridad WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END,
                 date(a.fecha_servicio), a.id DESC
        """,
        params,
    ).fetchall()
    conn.close()
    return render_template(
        "recepcion/avisos.html",
        avisos=filas,
        departamentos=DEPARTAMENTOS_AVISO,
        filtros={"departamento": departamento, "estado": estado},
    )


@recepcion_bp.route("/avisos/<int:id>/estado", methods=["POST"])
@login_required
@_require_recepcion
def cambiar_estado_aviso(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    estado = request.form.get("estado") or "pendiente"
    if estado not in {"pendiente", "en_revision", "resuelto", "descartado"}:
        estado = "pendiente"
    conn.execute(
        "UPDATE recepcion_avisos SET estado = ? WHERE id = ? AND empresa_id = ?",
        (estado, id, empresa_id),
    )
    conn.commit()
    conn.close()
    flash("Aviso actualizado.", "success")
    return redirect(request.referrer or url_for("recepcion.avisos"))


@recepcion_bp.route("/overbooking-control", methods=["GET", "POST"])
@login_required
@_require_recepcion
def overbooking_control():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    if request.method == "POST":
        colchon_pct = max(0.0, min(_to_float(request.form.get("colchon_pct"), 0), 30.0))
        colchon_min = max(0, min(_to_int(request.form.get("colchon_min_habitaciones"), 0), 20))
        aplicar_canales = 1 if request.form.get("aplicar_canales") else 0
        aplicar_directo = 1 if request.form.get("aplicar_directo") else 0
        activo = 1 if request.form.get("activo") else 0
        nombre = (request.form.get("nombre") or "Politica operativa").strip()[:120]
        observaciones = (request.form.get("observaciones") or "").strip()
        existente = conn.execute(
            """
            SELECT id
            FROM recepcion_politicas_disponibilidad
            WHERE empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
            ORDER BY id DESC
            LIMIT 1
            """,
            (empresa_id, centro_id),
        ).fetchone()
        if existente:
            conn.execute(
                """
                UPDATE recepcion_politicas_disponibilidad
                SET nombre = ?, colchon_pct = ?, colchon_min_habitaciones = ?,
                    aplicar_canales = ?, aplicar_directo = ?, activo = ?,
                    observaciones = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                (nombre, colchon_pct, colchon_min, aplicar_canales, aplicar_directo, activo, observaciones, existente["id"], empresa_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO recepcion_politicas_disponibilidad
                (empresa_id, centro_id, nombre, colchon_pct, colchon_min_habitaciones,
                 aplicar_canales, aplicar_directo, activo, observaciones)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (empresa_id, centro_id, nombre, colchon_pct, colchon_min, aplicar_canales, aplicar_directo, activo, observaciones),
            )
        conn.commit()
        conn.close()
        flash("Politica de margen de maniobra guardada para el centro activo.", "success")
        return redirect(url_for("recepcion.overbooking_control", desde=request.form.get("desde") or date.today().isoformat(), dias=request.form.get("dias") or 30))
    desde = _parse_date(request.args.get("desde")) or date.today()
    dias = max(1, min(_to_int(request.args.get("dias"), 30), 120))
    estado = _control_overbooking_live(conn, empresa_id, desde, dias, centro_id=centro_id)
    centros = conn.execute(
        """
        SELECT id, nombre
        FROM centros_trabajo
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY nombre
        """,
        (empresa_id,),
    ).fetchall() if _table_exists(conn, "centros_trabajo") else []
    conn.close()
    return render_template(
        "recepcion/overbooking_control.html",
        estado=estado,
        desde=desde.isoformat(),
        dias=dias,
        centro_id=centro_id,
        centros=centros,
    )


@recepcion_bp.route("/ocupacion")
@login_required
@_require_recepcion
def ocupacion():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    desde = _parse_date(request.args.get("desde")) or date.today()
    dias = max(1, min(_to_int(request.args.get("dias"), 14), 31))
    hasta = desde + timedelta(days=dias)

    filtro_bloque = (request.args.get("bloque") or "").strip()
    filtro_planta = (request.args.get("planta") or "").strip()
    filtro_tipo_id = _to_int(request.args.get("tipo_habitacion_id"), 0)

    filtros_habitacion = ["h.empresa_id = ?", "COALESCE(h.activo, 1) = 1"]
    params_habitacion = [empresa_id]
    if filtro_bloque:
        filtros_habitacion.append("COALESCE(h.bloque, '') = ?")
        params_habitacion.append(filtro_bloque)
    if filtro_planta:
        filtros_habitacion.append("COALESCE(h.planta, '') = ?")
        params_habitacion.append(filtro_planta)
    if filtro_tipo_id:
        filtros_habitacion.append("h.tipo_habitacion_id = ?")
        params_habitacion.append(filtro_tipo_id)

    habitaciones = conn.execute(
        f"""
        SELECT h.*, th.codigo AS tipo_codigo, th.nombre AS tipo_nombre,
               th.capacidad_base AS tipo_capacidad_base, th.capacidad_max AS tipo_capacidad_max
        FROM habitaciones_recepcion h
        LEFT JOIN recepcion_tipos_habitacion th ON h.tipo_habitacion_id = th.id
        WHERE {" AND ".join(filtros_habitacion)}
        ORDER BY h.bloque, h.planta, h.numero, h.nombre
        """,
        params_habitacion,
    ).fetchall()
    habitacion_ids = {h["id"] for h in habitaciones}

    reservas = conn.execute(
        """
        SELECT r.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               h.bloque AS habitacion_bloque, h.planta AS habitacion_planta,
               h.activo AS habitacion_activa, cr.nombre AS canal_nombre
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        LEFT JOIN recepcion_canales_reserva cr ON r.canal_id = cr.id
        WHERE r.empresa_id = ?
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'checkout', 'no_show')
          AND date(r.fecha_entrada) < date(?)
          AND date(r.fecha_salida) > date(?)
        ORDER BY date(r.fecha_entrada), r.id
        """,
        (empresa_id, hasta.isoformat(), desde.isoformat()),
    ).fetchall()
    bloqueos_rows = conn.execute(
        """
        SELECT b.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               h.bloque AS habitacion_bloque, h.planta AS habitacion_planta
        FROM bloqueos_habitacion_recepcion b
        JOIN habitaciones_recepcion h ON b.habitacion_id = h.id
        WHERE b.empresa_id = ?
          AND COALESCE(b.estado, 'activo') = 'activo'
          AND date(b.fecha_inicio) < date(?)
          AND date(b.fecha_fin) > date(?)
        ORDER BY date(b.fecha_inicio), b.id
        """,
        (empresa_id, hasta.isoformat(), desde.isoformat()),
    ).fetchall()

    bloques = conn.execute(
        """
        SELECT DISTINCT COALESCE(bloque, '') AS bloque
        FROM habitaciones_recepcion
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 AND COALESCE(bloque, '') <> ''
        ORDER BY bloque
        """,
        (empresa_id,),
    ).fetchall()
    plantas = conn.execute(
        """
        SELECT DISTINCT COALESCE(planta, '') AS planta
        FROM habitaciones_recepcion
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 AND COALESCE(planta, '') <> ''
        ORDER BY planta
        """,
        (empresa_id,),
    ).fetchall()
    tipos = _tipos_habitacion_select(conn, empresa_id)

    fechas = [desde + timedelta(days=i) for i in range(dias)]
    matriz = []
    ocupadas = 0
    bloqueadas = 0
    conflictos = []
    reservas_sin_asignar = [r for r in reservas if not r["habitacion_id"]]
    reservas_habitacion_invalida = [
        r for r in reservas
        if r["habitacion_id"] and (not r["habitacion_numero"] or not (r["habitacion_activa"] in (1, True, "1")))
    ]

    def _rango_activo(row, inicio_key, fin_key, fecha):
        inicio = _parse_date(row[inicio_key])
        fin = _parse_date(row[fin_key])
        return bool(inicio and fin and inicio <= fecha < fin)

    def _misma_fecha(row, key, fecha):
        fecha_row = _parse_date(row[key])
        return fecha_row == fecha

    for habitacion in habitaciones:
        celdas = []
        for fecha in fechas:
            reservas_dia = [
                r for r in reservas
                if r["habitacion_id"] == habitacion["id"] and _rango_activo(r, "fecha_entrada", "fecha_salida", fecha)
            ]
            bloqueos_dia = [
                b for b in bloqueos_rows
                if b["habitacion_id"] == habitacion["id"] and _rango_activo(b, "fecha_inicio", "fecha_fin", fecha)
            ]
            salidas_dia = [
                r for r in reservas
                if r["habitacion_id"] == habitacion["id"] and _misma_fecha(r, "fecha_salida", fecha)
            ]
            llegadas_dia = [r for r in reservas_dia if _misma_fecha(r, "fecha_entrada", fecha)]

            tipo = "libre"
            texto = "Libre"
            if len(reservas_dia) > 1 or (reservas_dia and bloqueos_dia):
                tipo = "conflicto"
                texto = "Conflicto"
                conflictos.append(
                    {
                        "fecha": fecha.isoformat(),
                        "habitacion": _nombre_habitacion({
                            "habitacion_nombre": habitacion["nombre"],
                            "habitacion_numero": habitacion["numero"],
                        }),
                        "descripcion": "Reserva solapada con otra reserva o bloqueo.",
                        "reservas": reservas_dia,
                        "bloqueos": bloqueos_dia,
                    }
                )
            elif reservas_dia:
                tipo = "ocupada"
                texto = reservas_dia[0]["titular_nombre"] or reservas_dia[0]["codigo"] or "Reserva"
            elif bloqueos_dia:
                tipo = "bloqueo"
                texto = bloqueos_dia[0]["motivo"] or "Bloqueo"
            elif salidas_dia:
                tipo = "salida"
                texto = "Salida"

            if reservas_dia:
                ocupadas += 1
            if bloqueos_dia and not reservas_dia:
                bloqueadas += 1
            celda = {
                "fecha": fecha.isoformat(),
                "tipo": tipo,
                "texto": texto,
                "reservas": reservas_dia,
                "bloqueos": bloqueos_dia,
                "salidas": salidas_dia,
                "llegadas": llegadas_dia,
                "salida_manana": any(_parse_date(r["fecha_salida"]) == fecha + timedelta(days=1) for r in reservas_dia),
            }
            celdas.append(celda)
        matriz.append({"habitacion": habitacion, "celdas": celdas})

    for reserva in reservas_habitacion_invalida:
        conflictos.append(
            {
                "fecha": reserva["fecha_entrada"],
                "habitacion": f"ID {reserva['habitacion_id']}",
                "descripcion": "Reserva asignada a una habitacion inexistente o inactiva.",
                "reservas": [reserva],
                "bloqueos": [],
            }
        )

    total_celdas = max(len(habitaciones) * len(fechas), 1)
    ocupacion_pct = round((ocupadas / total_celdas) * 100, 1)
    bloqueos_pct = round((bloqueadas / total_celdas) * 100, 1)
    llegadas = [r for r in reservas if _parse_date(r["fecha_entrada"]) in fechas]
    salidas = [r for r in reservas if _parse_date(r["fecha_salida"]) in fechas]
    resumen = {
        "habitaciones": len(habitaciones),
        "ocupadas": ocupadas,
        "bloqueadas": bloqueadas,
        "ocupacion_pct": ocupacion_pct,
        "bloqueos_pct": bloqueos_pct,
        "llegadas": len(llegadas),
        "salidas": len(salidas),
        "sin_asignar": len(reservas_sin_asignar),
        "conflictos": len(conflictos),
    }
    conn.close()
    return render_template(
        "recepcion/ocupacion.html",
        desde=desde.isoformat(),
        dias=dias,
        fechas=fechas,
        matriz=matriz,
        bloques=bloques,
        plantas=plantas,
        tipos=tipos,
        filtros={
            "bloque": filtro_bloque,
            "planta": filtro_planta,
            "tipo_habitacion_id": filtro_tipo_id,
        },
        reservas_sin_asignar=reservas_sin_asignar,
        conflictos=conflictos,
        resumen=resumen,
    )


@recepcion_bp.route("/regimenes", methods=["GET", "POST"])
@login_required
@_require_recepcion
def regimenes():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        regimen = request.form.get("regimen") or ""
        servicio = request.form.get("servicio") or ""
        concepto = (request.form.get("concepto") or "").strip()
        if regimen not in REGIMENES or servicio not in SERVICIOS_COMIDA or not concepto:
            flash("Revisa regimen, servicio y concepto.", "warning")
        else:
            conn.execute(
                """
                INSERT INTO recepcion_regimen_inclusiones
                (regimen, servicio, tipo, concepto, incluido, limite, condiciones, activo, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    regimen,
                    servicio,
                    request.form.get("tipo") or "alimento",
                    concepto,
                    1 if request.form.get("incluido", "1") else 0,
                    request.form.get("limite") or "",
                    request.form.get("condiciones") or "",
                    empresa_id,
                ),
            )
            conn.commit()
            flash("Inclusion de regimen guardada.", "success")
            return redirect(url_for("recepcion.regimenes"))
    inclusiones = conn.execute(
        """
        SELECT *
        FROM recepcion_regimen_inclusiones
        WHERE empresa_id = ?
        ORDER BY regimen, servicio, tipo, concepto
        """,
        (empresa_id,),
    ).fetchall()
    conn.close()
    return render_template(
        "recepcion/regimenes.html",
        inclusiones=inclusiones,
        regimenes=REGIMENES,
        servicios=SERVICIOS_COMIDA,
    )


@recepcion_bp.route("/regimenes/exportar-csv")
@login_required
@_require_recepcion
def exportar_regimenes_csv():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    rows = conn.execute(
        """
        SELECT regimen, servicio, tipo, concepto, incluido, limite, condiciones, activo
        FROM recepcion_regimen_inclusiones
        WHERE empresa_id = ?
        ORDER BY regimen, servicio, tipo, concepto
        """,
        (empresa_id,),
    ).fetchall()
    conn.close()
    return _csv_response(
        f"regimenes_recepcion_{date.today().isoformat()}.csv",
        ["regimen", "servicio", "tipo", "concepto", "incluido", "limite", "condiciones", "activo"],
        [
            [
                row["regimen"],
                row["servicio"],
                row["tipo"],
                row["concepto"],
                row["incluido"],
                row["limite"],
                row["condiciones"],
                row["activo"],
            ]
            for row in rows
        ],
    )


@recepcion_bp.route("/regimenes/importar-csv", methods=["POST"])
@login_required
@_require_recepcion
def importar_regimenes_csv():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    archivo = request.files.get("archivo_csv")
    if archivo is None or not (getattr(archivo, "filename", "") or "").strip():
        conn.close()
        flash("Selecciona un CSV de regimenes.", "warning")
        return redirect(url_for("recepcion.regimenes"))
    try:
        resultado = _importar_regimenes_csv(conn, archivo, empresa_id)
        conn.commit()
        msg = f"Regimenes importados: {resultado['creadas']} nuevos, {resultado['actualizadas']} actualizados, {resultado['errores']} errores."
        if resultado["mensajes"]:
            msg += " " + " ".join(resultado["mensajes"][:3])
        flash(msg, "warning" if resultado["errores"] else "success")
    except Exception as exc:
        conn.rollback()
        flash(f"No se pudo importar regimenes: {exc}", "danger")
    finally:
        conn.close()
    return redirect(url_for("recepcion.regimenes"))


@recepcion_bp.route("/regimenes/<int:id>/toggle", methods=["POST"])
@login_required
@_require_recepcion
def toggle_inclusion_regimen(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    row = conn.execute(
        "SELECT activo FROM recepcion_regimen_inclusiones WHERE id = ? AND empresa_id = ?",
        (id, empresa_id),
    ).fetchone()
    if not row:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE recepcion_regimen_inclusiones SET activo = ? WHERE id = ? AND empresa_id = ?",
        (0 if row["activo"] else 1, id, empresa_id),
    )
    conn.commit()
    conn.close()
    flash("Regimen actualizado.", "success")
    return redirect(url_for("recepcion.regimenes"))


@recepcion_bp.route("/tarifas", methods=["GET", "POST"])
@login_required
@_require_recepcion
def tarifas():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    tipos = _tipos_habitacion_select(conn, empresa_id)
    habitaciones = _habitaciones_select(conn, empresa_id)
    if request.method == "POST":
        tipo_id = _to_int(request.form.get("tipo_habitacion_id"), 0)
        regimen = request.form.get("regimen") or "solo_alojamiento"
        inicio = request.form.get("fecha_inicio")
        fin = request.form.get("fecha_fin")
        inicio_dt = _parse_date(inicio)
        fin_dt = _parse_date(fin)
        if not tipo_id or regimen not in REGIMENES or inicio_dt is None or fin_dt is None or fin_dt < inicio_dt:
            flash("Revisa tipo, regimen y fechas de la tarifa.", "warning")
        else:
            conn.execute(
                """
                INSERT INTO recepcion_tarifas_tipo
                (tipo_habitacion_id, regimen, fecha_inicio, fecha_fin, precio_noche, prioridad, activo, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tipo_id,
                    regimen,
                    inicio,
                    fin,
                    _to_float(request.form.get("precio_noche")),
                    _to_int(request.form.get("prioridad"), 10),
                    1 if request.form.get("activo", "1") else 0,
                    empresa_id,
                ),
            )
            conn.commit()
            flash("Tarifa guardada.", "success")
            return redirect(url_for("recepcion.tarifas"))
    tarifas_rows = conn.execute(
        """
        SELECT t.*, th.codigo AS tipo_codigo, th.nombre AS tipo_nombre
        FROM recepcion_tarifas_tipo t
        JOIN recepcion_tipos_habitacion th ON t.tipo_habitacion_id = th.id
        WHERE t.empresa_id = ?
        ORDER BY date(t.fecha_inicio) DESC, th.nombre, t.regimen
        """,
        (empresa_id,),
    ).fetchall()
    simulador = None
    simulador_form = {
        "habitacion_id": _to_int(request.args.get("habitacion_id"), 0),
        "regimen": request.args.get("regimen") or "solo_alojamiento",
        "fecha_entrada": request.args.get("fecha_entrada") or "",
        "fecha_salida": request.args.get("fecha_salida") or "",
    }
    if request.args.get("simular_precio"):
        simulador = _sugerir_precio_reserva(
            conn,
            simulador_form["habitacion_id"],
            simulador_form["regimen"],
            simulador_form["fecha_entrada"],
            simulador_form["fecha_salida"],
            empresa_id,
        )
        if not simulador:
            flash("No se pudo simular: revisa habitacion, regimen y fechas.", "warning")
    auditoria_form = {
        "desde": request.args.get("auditoria_desde") or date.today().isoformat(),
        "hasta": request.args.get("auditoria_hasta") or (date.today() + timedelta(days=30)).isoformat(),
        "tipo_habitacion_id": _to_int(request.args.get("auditoria_tipo_habitacion_id"), 0),
        "regimen": request.args.get("auditoria_regimen") or "",
    }
    auditoria = None
    if request.args.get("auditar_tarifas"):
        auditoria = _auditar_huecos_tarifas(
            conn,
            empresa_id,
            auditoria_form["desde"],
            auditoria_form["hasta"],
            auditoria_form["tipo_habitacion_id"],
            auditoria_form["regimen"],
        )
        if auditoria and not auditoria["ok"]:
            flash(auditoria["mensaje"], "warning")
    conn.close()
    return render_template(
        "recepcion/tarifas.html",
        tarifas=tarifas_rows,
        tipos=tipos,
        habitaciones=habitaciones,
        regimenes=REGIMENES,
        simulador=simulador,
        simulador_form=simulador_form,
        auditoria=auditoria,
        auditoria_form=auditoria_form,
        demo_year=date.today().year,
    )


@recepcion_bp.route("/tarifas/clonar-temporada", methods=["POST"])
@login_required
@_require_recepcion
def clonar_temporada_tarifas():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    try:
        resultado = _clonar_temporada_tarifas(
            conn,
            empresa_id,
            request.form.get("origen_inicio"),
            request.form.get("origen_fin"),
            request.form.get("destino_inicio"),
            _to_int(request.form.get("tipo_habitacion_id"), 0),
            request.form.get("regimen") or "",
            bool(request.form.get("sobrescribir")),
        )
        if not resultado["ok"]:
            flash(resultado["mensaje"], "warning")
        else:
            conn.commit()
            flash(
                f"Temporada clonada: {resultado['creadas']} creadas, {resultado['actualizadas']} actualizadas, {resultado['omitidas']} omitidas.",
                "success" if resultado["creadas"] or resultado["actualizadas"] else "warning",
            )
    except Exception as exc:
        conn.rollback()
        flash(f"No se pudo clonar la temporada: {exc}", "danger")
    finally:
        conn.close()
    return redirect(url_for("recepcion.tarifas"))


@recepcion_bp.route("/tarifas/demo-mercado", methods=["POST"])
@login_required
@_require_recepcion
def crear_tarifas_demo_mercado():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    try:
        resultado = _crear_tarifas_demo_mercado(
            conn,
            empresa_id,
            _to_int(request.form.get("inicio_year"), date.today().year),
            _to_int(request.form.get("num_years"), 2),
            bool(request.form.get("sobrescribir")),
        )
        conn.commit()
        flash(
            "Tarifas demo creadas: "
            f"{resultado['creadas']} nuevas, {resultado['actualizadas']} actualizadas, "
            f"{resultado['omitidas']} omitidas. Reglas: {resultado['reglas_creadas']} nuevas, "
            f"{resultado['reglas_actualizadas']} actualizadas.",
            "success",
        )
    except Exception as exc:
        conn.rollback()
        flash(f"No se pudieron crear las tarifas demo: {exc}", "danger")
    finally:
        conn.close()
    return redirect(url_for("recepcion.tarifas"))


@recepcion_bp.route("/tarifas/exportar-csv")
@login_required
@_require_recepcion
def exportar_tarifas_csv():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    rows = conn.execute(
        """
        SELECT th.codigo AS tipo_codigo, th.nombre AS tipo_nombre, t.regimen,
               t.fecha_inicio, t.fecha_fin, t.precio_noche, t.prioridad, t.activo
        FROM recepcion_tarifas_tipo t
        JOIN recepcion_tipos_habitacion th ON t.tipo_habitacion_id = th.id
        WHERE t.empresa_id = ?
        ORDER BY th.codigo, t.regimen, date(t.fecha_inicio), date(t.fecha_fin)
        """,
        (empresa_id,),
    ).fetchall()
    conn.close()
    return _csv_response(
        f"tarifas_recepcion_{date.today().isoformat()}.csv",
        ["tipo_codigo", "tipo_nombre", "regimen", "fecha_inicio", "fecha_fin", "precio_noche", "prioridad", "activo"],
        [
            [
                row["tipo_codigo"],
                row["tipo_nombre"],
                row["regimen"],
                row["fecha_inicio"],
                row["fecha_fin"],
                row["precio_noche"],
                row["prioridad"],
                row["activo"],
            ]
            for row in rows
        ],
    )


@recepcion_bp.route("/tarifas/importar-csv", methods=["POST"])
@login_required
@_require_recepcion
def importar_tarifas_csv():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    archivo = request.files.get("archivo_csv")
    if archivo is None or not (getattr(archivo, "filename", "") or "").strip():
        conn.close()
        flash("Selecciona un CSV de tarifas.", "warning")
        return redirect(url_for("recepcion.tarifas"))
    try:
        resultado = _importar_tarifas_csv(conn, archivo, empresa_id)
        conn.commit()
        msg = f"Tarifas importadas: {resultado['creadas']} nuevas, {resultado['actualizadas']} actualizadas, {resultado['errores']} errores."
        if resultado["mensajes"]:
            msg += " " + " ".join(resultado["mensajes"][:3])
        flash(msg, "warning" if resultado["errores"] else "success")
    except Exception as exc:
        conn.rollback()
        flash(f"No se pudo importar tarifas: {exc}", "danger")
    finally:
        conn.close()
    return redirect(url_for("recepcion.tarifas"))


@recepcion_bp.route("/tarifas/<int:id>/toggle", methods=["POST"])
@login_required
@_require_recepcion
def toggle_tarifa(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    row = conn.execute(
        "SELECT activo FROM recepcion_tarifas_tipo WHERE id = ? AND empresa_id = ?",
        (id, empresa_id),
    ).fetchone()
    if not row:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE recepcion_tarifas_tipo SET activo = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND empresa_id = ?",
        (0 if row["activo"] else 1, id, empresa_id),
    )
    conn.commit()
    conn.close()
    flash("Tarifa actualizada.", "success")
    return redirect(url_for("recepcion.tarifas"))


@recepcion_bp.route("/reglas-precio", methods=["GET", "POST"])
@login_required
@_require_recepcion
def reglas_precio():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    tipos = _tipos_habitacion_select(conn, empresa_id)
    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("El nombre de la regla es obligatorio.", "warning")
        else:
            conn.execute(
                """
                INSERT INTO recepcion_reglas_precio
                (nombre, tipo_habitacion_id, regimen, aplicar_desde_fecha, aplicar_desde_hora,
                 fecha_inicio_estancia, fecha_fin_estancia, ocupacion_min_pct, ocupacion_max_pct,
                 ajuste_tipo, ajuste_valor, motivo, activo, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    nombre,
                    _to_int(request.form.get("tipo_habitacion_id"), 0) or None,
                    request.form.get("regimen") or "",
                    request.form.get("aplicar_desde_fecha") or "",
                    request.form.get("aplicar_desde_hora") or "",
                    request.form.get("fecha_inicio_estancia") or "",
                    request.form.get("fecha_fin_estancia") or "",
                    _to_float(request.form.get("ocupacion_min_pct")) if request.form.get("ocupacion_min_pct") not in (None, "") else None,
                    _to_float(request.form.get("ocupacion_max_pct")) if request.form.get("ocupacion_max_pct") not in (None, "") else None,
                    request.form.get("ajuste_tipo") or "porcentaje",
                    _to_float(request.form.get("ajuste_valor")),
                    request.form.get("motivo") or "",
                    1 if request.form.get("activo", "1") else 0,
                    empresa_id,
                ),
            )
            conn.commit()
            flash("Regla de precio guardada.", "success")
            return redirect(url_for("recepcion.reglas_precio"))
    reglas = conn.execute(
        """
        SELECT r.*, th.codigo AS tipo_codigo, th.nombre AS tipo_nombre
        FROM recepcion_reglas_precio r
        LEFT JOIN recepcion_tipos_habitacion th ON r.tipo_habitacion_id = th.id
        WHERE r.empresa_id = ?
        ORDER BY COALESCE(r.activo, 1) DESC, date(r.aplicar_desde_fecha) DESC, r.id DESC
        """,
        (empresa_id,),
    ).fetchall()
    conn.close()
    return render_template("recepcion/reglas_precio.html", reglas=reglas, tipos=tipos, regimenes=REGIMENES)


@recepcion_bp.route("/reglas-precio/<int:id>/toggle", methods=["POST"])
@login_required
@_require_recepcion
def toggle_regla_precio(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    row = conn.execute(
        "SELECT activo FROM recepcion_reglas_precio WHERE id = ? AND empresa_id = ?",
        (id, empresa_id),
    ).fetchone()
    if not row:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE recepcion_reglas_precio SET activo = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND empresa_id = ?",
        (0 if row["activo"] else 1, id, empresa_id),
    )
    conn.commit()
    conn.close()
    flash("Regla actualizada.", "success")
    return redirect(url_for("recepcion.reglas_precio"))


@recepcion_bp.route("/canales", methods=["GET", "POST"])
@login_required
@_require_recepcion
def canales_reserva():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        accion = request.form.get("accion") or "canal"
        if accion == "cupo":
            canal_id = _to_int(request.form.get("canal_id"), 0)
            tipo_habitacion_id = _to_int(request.form.get("tipo_habitacion_id"), 0) or None
            fecha_inicio = request.form.get("fecha_inicio") or ""
            fecha_fin = request.form.get("fecha_fin") or ""
            inicio_dt = _parse_date(fecha_inicio)
            fin_dt = _parse_date(fecha_fin)
            if not canal_id or not inicio_dt or not fin_dt or fin_dt < inicio_dt:
                flash("Revisa canal y fechas del cupo.", "warning")
            else:
                conn.execute(
                    """
                    INSERT INTO recepcion_cupos_canal
                    (canal_id, tipo_habitacion_id, fecha_inicio, fecha_fin, porcentaje, habitaciones,
                     prioridad, release_dias, activo, empresa_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        canal_id,
                        tipo_habitacion_id,
                        inicio_dt.isoformat(),
                        fin_dt.isoformat(),
                        _to_float(request.form.get("porcentaje"), 0),
                        max(0, _to_int(request.form.get("habitaciones"), 0)),
                        max(1, _to_int(request.form.get("prioridad"), 100)),
                        max(0, _to_int(request.form.get("release_dias"), 0)),
                        1 if request.form.get("activo", "1") else 0,
                        empresa_id,
                    ),
                )
                conn.commit()
                flash("Cupo de canal guardado.", "success")
                return redirect(url_for("recepcion.canales_reserva"))
        else:
            codigo = (request.form.get("codigo") or "").strip().lower().replace(" ", "_")
            nombre = (request.form.get("nombre") or "").strip()
            if not codigo or not nombre:
                flash("Codigo y nombre del canal son obligatorios.", "warning")
            else:
                conn.execute(
                    """
                    INSERT INTO recepcion_canales_reserva
                    (codigo, nombre, tipo, comision_pct, activo, config_json, empresa_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        codigo,
                        nombre,
                        request.form.get("tipo") or "directo",
                        _to_float(request.form.get("comision_pct")),
                        1 if request.form.get("activo", "1") else 0,
                        request.form.get("config_json") or "",
                        empresa_id,
                    ),
                )
                conn.commit()
                flash("Canal guardado.", "success")
                return redirect(url_for("recepcion.canales_reserva"))
    canales = _canales_reserva_select(conn, empresa_id, solo_activos=False)
    habitaciones = _habitaciones_select(conn, empresa_id)
    tipos_habitacion = _tipos_habitacion_select(conn, empresa_id)
    cupos = _cupos_canal_select(conn, empresa_id)
    sincronizaciones = _ultimas_sincronizaciones_canal(conn, empresa_id)
    conn.close()
    return render_template(
        "recepcion/canales.html",
        canales=canales,
        habitaciones=habitaciones,
        regimenes=REGIMENES,
        inventario_form={
            "desde": date.today().isoformat(),
            "hasta": (date.today() + timedelta(days=90)).isoformat(),
            "regimen": "",
            "canal_id": "",
        },
        tipos_habitacion=tipos_habitacion,
        cupos=cupos,
        sincronizaciones=sincronizaciones,
    )


@recepcion_bp.route("/canales/plantilla-csv")
@login_required
@_require_recepcion
def plantilla_canal_csv():
    contenido = (
        "localizador;fecha_entrada;fecha_salida;habitacion;titular;email;telefono;"
        "regimen;adultos;menores;precio_total;comision;estado;alergias;observaciones\n"
        "BK-1001;2026-06-01;2026-06-05;101;Cliente Ejemplo;cliente@example.com;"
        "+34900000000;alojamiento_desayuno;2;0;480,00;72,00;confirmada;Celiaco;Cama supletoria\n"
    )
    return Response(
        contenido,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=plantilla_reservas_canal.csv"},
    )


@recepcion_bp.route("/canales/plantilla-ics")
@login_required
@_require_recepcion
def plantilla_canal_ics():
    contenido = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//El Raco ERP//Plantilla iCal//ES",
            "CALSCALE:GREGORIAN",
            "BEGIN:VEVENT",
            "UID:OTA-1001@example.com",
            "DTSTAMP:20260509T120000Z",
            "DTSTART;VALUE=DATE:20260601",
            "DTEND;VALUE=DATE:20260605",
            "SUMMARY:Reserva externa",
            "DESCRIPTION:Importacion iCal de ejemplo",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )
    return Response(
        contenido,
        mimetype="text/calendar; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=plantilla_reservas_canal.ics"},
    )


@recepcion_bp.route("/canales/exportar-inventario")
@login_required
@_require_recepcion
def exportar_inventario_canales():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    desde = request.args.get("desde") or date.today().isoformat()
    hasta = request.args.get("hasta") or (date.today() + timedelta(days=90)).isoformat()
    regimen = request.args.get("regimen") or ""
    canal_id = _to_int(request.args.get("canal_id"), 0) or None
    rows = _inventario_precios_rows(conn, empresa_id, desde, hasta, regimen, canal_id)
    conn.close()
    filename = f"inventario_precios_canales_{desde}_{hasta}.csv"
    return _csv_response(
        filename,
        [
            "fecha",
            "tipo_codigo",
            "tipo_nombre",
            "regimen",
            "regimen_nombre",
            "habitaciones_total",
            "ocupadas",
            "bloqueadas",
            "disponibles",
            "cupo_canal",
            "reservas_canal",
            "disponibles_canal",
            "precio_base",
            "ajuste_reglas",
            "precio_final",
            "venta_estado",
            "reglas_aplicadas",
            "avisos",
        ],
        rows,
    )


@recepcion_bp.route("/canales/<int:id>/importar-csv", methods=["POST"])
@login_required
@_require_recepcion
def importar_canal_csv(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    canal = _canal_por_id(conn, id, empresa_id)
    if not canal:
        conn.close()
        abort(404)

    archivo = request.files.get("archivo_csv")
    filename = (getattr(archivo, "filename", "") or "").replace("\\", "/").split("/")[-1] if archivo else ""
    if archivo is None or not filename:
        conn.close()
        flash("Selecciona un archivo CSV para importar.", "warning")
        return redirect(url_for("recepcion.canales_reserva"))

    try:
        resultado = _importar_reservas_csv(conn, canal, empresa_id, archivo)
        estado = "con_errores" if resultado["errores"] else ("con_avisos" if resultado["mensajes"] else "ok")
        resumen = "; ".join(resultado["mensajes"][:12])
        if len(resultado["mensajes"]) > 12:
            resumen += f"; ... {len(resultado['mensajes']) - 12} avisos mas"
        conn.execute(
            """
            INSERT INTO recepcion_sincronizaciones_canal
            (canal_id, tipo, estado, archivo, total_filas, creadas, actualizadas, omitidas, errores, resumen, empresa_id, usuario_id)
            VALUES (?, 'csv', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                canal["id"],
                estado,
                filename,
                resultado["total"],
                resultado["creadas"],
                resultado["actualizadas"],
                resultado["omitidas"],
                resultado["errores"],
                resumen,
                empresa_id,
                session.get("user_id"),
            ),
        )
        conn.execute(
            """
            UPDATE recepcion_canales_reserva
            SET ultima_sincronizacion = CURRENT_TIMESTAMP, estado_sincronizacion = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND empresa_id = ?
            """,
            (estado, canal["id"], empresa_id),
        )
        conn.commit()
        if resultado["errores"] or resultado["mensajes"]:
            flash(
                f"CSV importado con avisos: {resultado['creadas']} nuevas, {resultado['actualizadas']} actualizadas, {resultado['errores']} errores.",
                "warning",
            )
        else:
            flash(
                f"CSV importado: {resultado['creadas']} nuevas y {resultado['actualizadas']} actualizadas.",
                "success",
            )
    except Exception as exc:
        conn.rollback()
        conn.execute(
            """
            INSERT INTO recepcion_sincronizaciones_canal
            (canal_id, tipo, estado, archivo, errores, resumen, empresa_id, usuario_id)
            VALUES (?, 'csv', 'error', ?, 1, ?, ?, ?)
            """,
            (canal["id"], filename, str(exc), empresa_id, session.get("user_id")),
        )
        conn.execute(
            """
            UPDATE recepcion_canales_reserva
            SET ultima_sincronizacion = CURRENT_TIMESTAMP, estado_sincronizacion = 'error', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND empresa_id = ?
            """,
            (canal["id"], empresa_id),
        )
        conn.commit()
        flash(f"No se pudo importar el CSV: {exc}", "danger")
    finally:
        conn.close()
    return redirect(url_for("recepcion.canales_reserva"))


@recepcion_bp.route("/canales/<int:id>/importar-ics", methods=["POST"])
@login_required
@_require_recepcion
def importar_canal_ics(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    canal = _canal_por_id(conn, id, empresa_id)
    if not canal:
        conn.close()
        abort(404)

    habitacion_id = _to_int(request.form.get("habitacion_id"), 0)
    archivo = request.files.get("archivo_ics")
    filename = (getattr(archivo, "filename", "") or "").replace("\\", "/").split("/")[-1] if archivo else ""
    url_ics = (request.form.get("url_ics") or "").strip()
    if not filename and not url_ics:
        conn.close()
        flash("Sube un archivo ICS o indica una URL iCal.", "warning")
        return redirect(url_for("recepcion.canales_reserva"))

    try:
        if url_ics:
            texto = _descargar_texto_url(url_ics)
            origen = url_ics[:180]
        else:
            texto = _leer_archivo_texto(archivo)
            origen = filename
        resultado = _importar_reservas_ics(
            conn,
            canal,
            empresa_id,
            texto,
            habitacion_id,
            bool(request.form.get("cancelar_no_presentes")),
        )
        estado = "con_errores" if resultado["errores"] else ("con_avisos" if resultado["mensajes"] else "ok")
        resumen = "; ".join(resultado["mensajes"][:12])
        if len(resultado["mensajes"]) > 12:
            resumen += f"; ... {len(resultado['mensajes']) - 12} avisos mas"
        if resultado.get("canceladas"):
            resumen = (resumen + "; " if resumen else "") + f"{resultado['canceladas']} canceladas no presentes"
        conn.execute(
            """
            INSERT INTO recepcion_sincronizaciones_canal
            (canal_id, tipo, estado, archivo, total_filas, creadas, actualizadas, omitidas, errores, resumen, empresa_id, usuario_id)
            VALUES (?, 'ical', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                canal["id"],
                estado,
                origen,
                resultado["total"],
                resultado["creadas"],
                resultado["actualizadas"],
                resultado["omitidas"] + resultado.get("canceladas", 0),
                resultado["errores"],
                resumen,
                empresa_id,
                session.get("user_id"),
            ),
        )
        conn.execute(
            """
            UPDATE recepcion_canales_reserva
            SET ultima_sincronizacion = CURRENT_TIMESTAMP, estado_sincronizacion = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND empresa_id = ?
            """,
            (estado, canal["id"], empresa_id),
        )
        conn.commit()
        flash(
            f"iCal importado: {resultado['creadas']} nuevas, {resultado['actualizadas']} actualizadas, "
            f"{resultado.get('canceladas', 0)} canceladas, {resultado['errores']} errores.",
            "warning" if resultado["errores"] or resultado["mensajes"] else "success",
        )
    except (URLError, TimeoutError) as exc:
        conn.rollback()
        flash(f"No se pudo descargar el iCal: {exc}", "danger")
    except Exception as exc:
        conn.rollback()
        flash(f"No se pudo importar el iCal: {exc}", "danger")
    finally:
        conn.close()
    return redirect(url_for("recepcion.canales_reserva"))


@recepcion_bp.route("/canales/<int:id>/toggle", methods=["POST"])
@login_required
@_require_recepcion
def toggle_canal_reserva(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    row = conn.execute(
        "SELECT activo FROM recepcion_canales_reserva WHERE id = ? AND empresa_id = ?",
        (id, empresa_id),
    ).fetchone()
    if not row:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE recepcion_canales_reserva SET activo = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND empresa_id = ?",
        (0 if row["activo"] else 1, id, empresa_id),
    )
    conn.commit()
    conn.close()
    flash("Canal actualizado.", "success")
    return redirect(url_for("recepcion.canales_reserva"))


@recepcion_bp.route("/canales/cupos/<int:id>/toggle", methods=["POST"])
@login_required
@_require_recepcion
def toggle_cupo_canal(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    row = conn.execute(
        "SELECT activo FROM recepcion_cupos_canal WHERE id = ? AND empresa_id = ?",
        (id, empresa_id),
    ).fetchone()
    if not row:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE recepcion_cupos_canal SET activo = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND empresa_id = ?",
        (0 if row["activo"] else 1, id, empresa_id),
    )
    conn.commit()
    conn.close()
    flash("Cupo actualizado.", "success")
    return redirect(url_for("recepcion.canales_reserva"))


@recepcion_bp.route("/impuestos", methods=["GET", "POST"])
@login_required
@_require_recepcion
def impuestos():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        impuesto_id = _to_int(request.form.get("impuesto_id"), 0)
        codigo = (request.form.get("codigo") or "").strip().upper()
        nombre = (request.form.get("nombre") or "").strip()
        jurisdiccion = (request.form.get("jurisdiccion") or "general").strip().lower()
        porcentaje = _to_float(request.form.get("porcentaje"), 0)
        descripcion = request.form.get("descripcion") or ""
        activo = 1 if request.form.get("activo") else 0
        if not codigo or not nombre:
            flash("Codigo y nombre son obligatorios.", "warning")
        elif impuesto_id:
            conn.execute(
                """
                UPDATE recepcion_impuestos
                SET codigo = ?, nombre = ?, jurisdiccion = ?, porcentaje = ?, descripcion = ?,
                    activo = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                (codigo, nombre, jurisdiccion, porcentaje, descripcion, activo, impuesto_id, empresa_id),
            )
            conn.commit()
            flash("Impuesto actualizado.", "success")
            return redirect(url_for("recepcion.impuestos"))
        else:
            conn.execute(
                """
                INSERT INTO recepcion_impuestos
                (codigo, nombre, jurisdiccion, porcentaje, descripcion, activo, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (codigo, nombre, jurisdiccion, porcentaje, descripcion, activo, empresa_id),
            )
            conn.commit()
            flash("Impuesto creado.", "success")
            return redirect(url_for("recepcion.impuestos"))
    filas = _impuestos_select(conn, empresa_id, solo_activos=False)
    conn.close()
    return render_template("recepcion/impuestos.html", impuestos=filas)


@recepcion_bp.route("/fiscalidad", methods=["GET", "POST"])
@login_required
@_require_recepcion
def fiscalidad():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    config = _config_fiscal(conn, empresa_id)
    impuestos = _impuestos_select(conn, empresa_id)
    if request.method == "POST":
        territorio = (request.form.get("territorio") or "canarias").strip().lower()
        modos_verifactu = {"preparado_no_enviado", "verifactu", "no_verifactu"}
        modo_verifactu = (request.form.get("modo_verifactu") or "preparado_no_enviado").strip()
        if modo_verifactu not in modos_verifactu:
            modo_verifactu = "preparado_no_enviado"
        modo_factura_electronica = (request.form.get("modo_factura_electronica") or "pendiente_configuracion").strip()
        modo_registro_viajeros = (request.form.get("modo_registro_viajeros") or "ses_hospedajes_manual").strip()
        registro_viajeros_alerta_hora = (request.form.get("registro_viajeros_alerta_hora") or "22:00").strip()[:5]
        registro_viajeros_alerta_dias = max(0, _to_int(request.form.get("registro_viajeros_alerta_dias"), 1))
        edad_min_documento = max(0, _to_int(request.form.get("edad_min_documento"), EDAD_DOCUMENTO_OBLIGATORIO_DEFAULT))
        values = (
            territorio,
            _to_int(request.form.get("impuesto_estancia_id"), 0) or None,
            _to_int(request.form.get("impuesto_extras_id"), 0) or None,
            (request.form.get("serie_factura") or "REC").strip().upper(),
            (request.form.get("serie_proforma") or "PRO").strip().upper(),
            (request.form.get("serie_rectificativa") or "REC-R").strip().upper(),
            modo_verifactu,
            modo_factura_electronica,
            modo_registro_viajeros,
            registro_viajeros_alerta_hora,
            registro_viajeros_alerta_dias,
            edad_min_documento,
            request.form.get("responsable_tratamiento") or "",
            request.form.get("delegado_proteccion_datos") or "",
            request.form.get("texto_pie_factura") or "",
            request.form.get("observaciones") or "",
            empresa_id,
        )
        conn.execute(
            """
            UPDATE recepcion_config_fiscal
            SET territorio = ?,
                impuesto_estancia_id = ?,
                impuesto_extras_id = ?,
                serie_factura = ?,
                serie_proforma = ?,
                serie_rectificativa = ?,
                modo_verifactu = ?,
                modo_factura_electronica = ?,
                modo_registro_viajeros = ?,
                registro_viajeros_alerta_hora = ?,
                registro_viajeros_alerta_dias = ?,
                edad_min_documento = ?,
                responsable_tratamiento = ?,
                delegado_proteccion_datos = ?,
                texto_pie_factura = ?,
                observaciones = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE empresa_id = ?
            """,
            values,
        )
        conn.commit()
        conn.close()
        flash("Configuracion fiscal actualizada.", "success")
        return redirect(url_for("recepcion.fiscalidad"))
    empresa = _empresa_datos(conn, empresa_id)
    conn.close()
    return render_template(
        "recepcion/fiscalidad.html",
        config=config,
        impuestos=impuestos,
        empresa=empresa,
    )


@recepcion_bp.route("/impuestos/<int:id>/toggle", methods=["POST"])
@login_required
@_require_recepcion
def toggle_impuesto(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    row = conn.execute(
        "SELECT activo FROM recepcion_impuestos WHERE id = ? AND empresa_id = ?",
        (id, empresa_id),
    ).fetchone()
    if not row:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE recepcion_impuestos SET activo = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND empresa_id = ?",
        (0 if row["activo"] else 1, id, empresa_id),
    )
    conn.commit()
    conn.close()
    flash("Impuesto actualizado.", "success")
    return redirect(url_for("recepcion.impuestos"))


@recepcion_bp.route("/facturas")
@login_required
@_require_recepcion
def facturas():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    estado = (request.args.get("estado") or "").strip()
    tipo = (request.args.get("tipo") or "").strip()
    q = (request.args.get("q") or "").strip()
    desde = request.args.get("desde") or ""
    hasta = request.args.get("hasta") or ""
    filas = _facturas_filtradas(conn, empresa_id, estado=estado, tipo=tipo, q=q, desde=desde, hasta=hasta)
    conn.close()
    return render_template(
        "recepcion/facturas.html",
        facturas=filas,
        filtros={"estado": estado, "tipo": tipo, "q": q, "desde": desde, "hasta": hasta},
    )


def _facturas_filtradas(conn, empresa_id, estado="", tipo="", q="", desde="", hasta=""):
    filtros = ["f.empresa_id = ?"]
    params = [empresa_id]
    if estado:
        filtros.append("f.estado = ?")
        params.append(estado)
    if tipo:
        filtros.append("f.tipo = ?")
        params.append(tipo)
    if desde:
        filtros.append("date(f.fecha) >= date(?)")
        params.append(desde)
    if hasta:
        filtros.append("date(f.fecha) <= date(?)")
        params.append(hasta)
    if q:
        like = f"%{q}%"
        filtros.append("(f.numero_completo LIKE ? OR f.cliente_nombre LIKE ? OR f.cliente_nif LIKE ? OR r.codigo LIKE ?)")
        params.extend([like, like, like, like])
    return conn.execute(
        f"""
        SELECT f.*, r.codigo AS reserva_codigo, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre,
               (
                   SELECT fr.numero_completo
                   FROM recepcion_facturas fr
                   WHERE fr.id = f.factura_rectificada_id AND fr.empresa_id = f.empresa_id
                   LIMIT 1
               ) AS factura_rectificada_numero
        FROM recepcion_facturas f
        JOIN reservas_recepcion r ON f.reserva_id = r.id
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE {" AND ".join(filtros)}
        ORDER BY date(f.fecha) DESC, f.id DESC
        """,
        params,
    ).fetchall()


@recepcion_bp.route("/facturas/exportar")
@login_required
@_require_recepcion
def exportar_facturas():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    estado = (request.args.get("estado") or "").strip()
    tipo = (request.args.get("tipo") or "").strip()
    q = (request.args.get("q") or "").strip()
    desde = request.args.get("desde") or ""
    hasta = request.args.get("hasta") or ""
    facturas = _facturas_filtradas(conn, empresa_id, estado=estado, tipo=tipo, q=q, desde=desde, hasta=hasta)
    conn.close()
    rows = []
    for factura in facturas:
        rows.append(
            [
                factura["numero_completo"] or "",
                factura["fecha"] or "",
                factura["tipo"] or "",
                factura["estado"] or "",
                factura["reserva_codigo"] or factura["reserva_id"],
                factura["habitacion_numero"] or factura["habitacion_nombre"] or "",
                factura["cliente_nombre"] or "",
                factura["cliente_nif"] or "",
                f"{_to_float(factura['base'], 0):.2f}",
                f"{_to_float(factura['impuestos'], 0):.2f}",
                f"{_to_float(factura['total'], 0):.2f}",
                f"{_to_float(factura['pagado'], 0):.2f}",
                f"{_to_float(factura['saldo'], 0):.2f}",
                factura["impuesto_jurisdiccion"] or "",
                factura["factura_rectificada_numero"] or "",
                factura["motivo_rectificacion"] or "",
                factura["hash_factura"] or "",
                factura["verifactu_estado"] or "",
            ]
        )
    filename = f"facturas_recepcion_{date.today().isoformat()}.csv"
    return _csv_response(
        filename,
        [
            "numero",
            "fecha",
            "tipo",
            "estado",
            "reserva",
            "habitacion",
            "cliente",
            "nif",
            "base",
            "impuestos",
            "total",
            "pagado",
            "saldo",
            "fiscalidad",
            "factura_rectificada",
            "motivo_rectificacion",
            "hash_factura",
            "verifactu_estado",
        ],
        rows,
    )


@recepcion_bp.route("/facturas/<int:id>")
@login_required
@_require_recepcion
def factura_detalle(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    factura = _factura_por_id(conn, id, empresa_id)
    if not factura:
        conn.close()
        abort(404)
    lineas = _lineas_factura(conn, id, empresa_id)
    pagos = _pagos_reserva(conn, factura["reserva_id"], empresa_id)
    factura_textos = _factura_textos(factura["idioma"] if "idioma" in factura.keys() else "es")
    empresa = _empresa_datos(conn, empresa_id)
    empresa_logo_url = _empresa_logo_url(empresa)
    config_fiscal = _config_fiscal(conn, empresa_id)
    conn.close()
    return render_template(
        "recepcion/factura_detalle.html",
        factura=factura,
        lineas=lineas,
        pagos=pagos,
        txt=factura_textos,
        idiomas_documento=IDIOMAS_DOCUMENTO,
        empresa=empresa,
        empresa_logo_url=empresa_logo_url,
        config_fiscal=config_fiscal,
        autoprint=request.args.get("autoprint") == "1",
        next_doc=request.args.get("next_doc"),
    )


@recepcion_bp.route("/facturas/<int:id>/recalcular-vacia", methods=["POST"])
@login_required
@_require_recepcion
def recalcular_factura_vacia(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    factura = _factura_por_id(conn, id, empresa_id)
    if not factura:
        conn.close()
        abort(404)
    lineas = _lineas_factura(conn, id, empresa_id)
    if lineas or _to_float(factura["total"], 0) > 0:
        conn.close()
        flash("La factura ya tiene lineas o importe; no se recalcula automaticamente.", "warning")
        return redirect(url_for("recepcion.factura_detalle", id=id))
    reserva = _reserva_por_id(conn, factura["reserva_id"], empresa_id)
    factura_id = None
    if reserva:
        factura_id = _rellenar_factura_vacia_reserva(
            conn,
            id,
            reserva,
            empresa_id,
            {
                "cliente_nombre": factura["cliente_nombre"],
                "cliente_nif": factura["cliente_nif"],
                "cliente_direccion": factura["cliente_direccion"],
                "cliente_email": factura["cliente_email"],
                "idioma": factura["idioma"],
                "observaciones": factura["observaciones"] or "Factura recalculada manualmente desde detalle",
            },
        )
    if factura_id:
        conn.commit()
        flash("Factura vacia recalculada desde la reserva.", "success")
    else:
        flash("No se pudo recalcular la factura: revisa precio, cargos, tarifas e impuestos.", "warning")
    conn.close()
    return redirect(url_for("recepcion.factura_detalle", id=id))


@recepcion_bp.route("/facturas/<int:id>/estado", methods=["POST"])
@login_required
@_require_recepcion
def cambiar_estado_factura(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    factura = _factura_por_id(conn, id, empresa_id)
    if not factura:
        conn.close()
        abort(404)
    estado = request.form.get("estado") or "emitida"
    if estado not in {"emitida", "cobrada", "anulada"}:
        estado = "emitida"
    pagado = factura["total"] if estado == "cobrada" else factura["pagado"]
    saldo = 0 if estado == "cobrada" else factura["saldo"]
    conn.execute(
        """
        UPDATE recepcion_facturas
        SET estado = ?, pagado = ?, saldo = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (estado, pagado, saldo, id, empresa_id),
    )
    conn.commit()
    conn.close()
    flash("Factura actualizada.", "success")
    return redirect(url_for("recepcion.factura_detalle", id=id))


@recepcion_bp.route("/facturas/<int:id>/rectificar", methods=["POST"])
@login_required
@_require_recepcion
def rectificar_factura(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    factura = _factura_por_id(conn, id, empresa_id)
    if not factura:
        conn.close()
        abort(404)
    if factura["tipo"] == "rectificativa":
        conn.close()
        flash("Una rectificativa no se puede rectificar desde aqui.", "warning")
        return redirect(url_for("recepcion.factura_detalle", id=id))
    if factura["estado"] in {"anulada", "rectificada"}:
        destino = factura["rectificativa_id"] or id
        conn.close()
        flash("Esta factura ya esta anulada o rectificada.", "warning")
        return redirect(url_for("recepcion.factura_detalle", id=destino))
    existente = conn.execute(
        """
        SELECT id, numero_completo
        FROM recepcion_facturas
        WHERE factura_rectificada_id = ?
          AND empresa_id = ?
          AND COALESCE(estado, 'emitida') <> 'anulada'
        ORDER BY id DESC
        LIMIT 1
        """,
        (id, empresa_id),
    ).fetchone()
    if existente:
        conn.close()
        flash(f"La factura ya tiene rectificativa: {existente['numero_completo']}.", "warning")
        return redirect(url_for("recepcion.factura_detalle", id=existente["id"]))
    lineas = _lineas_factura(conn, id, empresa_id)
    fecha = request.form.get("fecha") or date.today().isoformat()
    rectificativa_id = _crear_factura_rectificativa(
        conn,
        factura,
        lineas,
        empresa_id,
        {
            "fecha": fecha,
            "serie": request.form.get("serie") or "REC-R",
            "idioma": request.form.get("idioma") or factura["idioma"],
            "motivo": request.form.get("motivo") or f"Rectificacion total de {factura['numero_completo']}",
            "observaciones": request.form.get("observaciones") or "",
        },
    )
    if not rectificativa_id:
        conn.close()
        flash("No se pudo crear la rectificativa porque la factura no tiene lineas.", "warning")
        return redirect(url_for("recepcion.factura_detalle", id=id))
    conn.commit()
    conn.close()
    flash("Factura rectificativa creada y factura original marcada como rectificada.", "success")
    return redirect(url_for("recepcion.factura_detalle", id=rectificativa_id))


@recepcion_bp.route("/caja", methods=["GET", "POST"])
@login_required
@_require_recepcion
def caja_recepcion():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    fecha = request.values.get("fecha") or date.today().isoformat()
    pagos = _pagos_caja(conn, empresa_id, fecha)
    resumen = _resumen_caja(pagos)
    if request.method == "POST":
        reales = {
            "efectivo": _to_float(request.form.get("efectivo_real"), resumen["efectivo"]),
            "tarjeta": _to_float(request.form.get("tarjeta_real"), resumen["tarjeta"]),
            "transferencia": _to_float(request.form.get("transferencia_real"), resumen["transferencia"]),
            "bizum": _to_float(request.form.get("bizum_real"), resumen["bizum"]),
            "otros": _to_float(request.form.get("otros_real"), resumen["otros"]),
        }
        total_real = round(sum(reales.values()), 2)
        diferencia = round(total_real - resumen["total"], 2)
        conn.execute(
            """
            INSERT INTO recepcion_cierres_caja
            (fecha, turno, efectivo_sistema, tarjeta_sistema, transferencia_sistema, bizum_sistema,
             otros_sistema, total_sistema, efectivo_real, tarjeta_real, transferencia_real, bizum_real,
             otros_real, total_real, diferencia, estado, observaciones, usuario_id, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cerrado', ?, ?, ?)
            """,
            (
                fecha,
                request.form.get("turno") or "dia",
                resumen["efectivo"],
                resumen["tarjeta"],
                resumen["transferencia"],
                resumen["bizum"],
                resumen["otros"],
                resumen["total"],
                reales["efectivo"],
                reales["tarjeta"],
                reales["transferencia"],
                reales["bizum"],
                reales["otros"],
                total_real,
                diferencia,
                request.form.get("observaciones") or "",
                session.get("user_id"),
                empresa_id,
            ),
        )
        conn.commit()
        flash("Cierre de caja guardado.", "success")
        return redirect(url_for("recepcion.caja_recepcion", fecha=fecha))
    cierres = _cierres_caja_fecha(conn, empresa_id, fecha)
    conn.close()
    return render_template(
        "recepcion/caja.html",
        fecha=fecha,
        pagos=pagos,
        resumen=resumen,
        cierres=cierres,
    )


@recepcion_bp.route("/caja/exportar")
@login_required
@_require_recepcion
def exportar_caja_recepcion():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    fecha = request.args.get("fecha") or date.today().isoformat()
    pagos = _pagos_caja(conn, empresa_id, fecha)
    resumen = _resumen_caja(pagos)
    conn.close()
    output = StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Caja recepcion", fecha])
    writer.writerow([])
    writer.writerow(["Forma", "Total"])
    for forma in ["efectivo", "tarjeta", "transferencia", "bizum", "otros", "total"]:
        writer.writerow([forma, f"{resumen[forma]:.2f}"])
    writer.writerow([])
    writer.writerow(["Fecha", "Reserva", "Habitacion", "Cliente", "Concepto", "Forma pago", "Referencia", "Importe"])
    for pago in pagos:
        writer.writerow(
            [
                pago["fecha"],
                pago["reserva_codigo"] or pago["reserva_id"],
                pago["habitacion_numero"] or pago["habitacion_nombre"] or "",
                pago["titular_nombre"] or "",
                pago["concepto"] or "",
                pago["forma_pago"] or "",
                pago["referencia"] or "",
                f"{_to_float(pago['importe'], 0):.2f}",
            ]
        )
    csv_data = output.getvalue()
    filename = f"caja_recepcion_{fecha}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@recepcion_bp.route("/restaurante-reservas")
@login_required
@_require_recepcion
def restaurante_reservas():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    fecha = request.args.get("fecha") or date.today().isoformat()
    estado = (request.args.get("estado") or "").strip()
    punto_venta_id = _to_int(request.args.get("punto_venta_id"), 0)
    filtros = ["rr.empresa_id = ?", "date(rr.fecha) = date(?)"]
    params = [empresa_id, fecha]
    if estado:
        filtros.append("rr.estado = ?")
        params.append(estado)
    if punto_venta_id:
        filtros.append("rr.punto_venta_id = ?")
        params.append(punto_venta_id)
    filas = conn.execute(
        f"""
        SELECT rr.*, pv.nombre AS punto_venta_nombre, pv.tipo AS punto_venta_tipo,
               r.codigo, r.titular_nombre, r.regimen,
               h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
        FROM recepcion_reservas_restaurante rr
        LEFT JOIN puntos_venta pv ON rr.punto_venta_id = pv.id
        LEFT JOIN reservas_recepcion r ON rr.reserva_id = r.id
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE {" AND ".join(filtros)}
        ORDER BY rr.hora, pv.nombre, rr.id
        """,
        params,
    ).fetchall()
    puntos_venta = _puntos_venta_select(conn, empresa_id)
    conn.close()
    return render_template(
        "recepcion/restaurante_reservas.html",
        reservas_restaurante=filas,
        puntos_venta=puntos_venta,
        filtros={"fecha": fecha, "estado": estado, "punto_venta_id": punto_venta_id},
    )


@recepcion_bp.route("/restaurante-reservas/nueva", methods=["GET", "POST"])
@recepcion_bp.route("/restaurante-reservas/<int:id>/editar", methods=["GET", "POST"])
@login_required
@_require_recepcion
def restaurante_reserva_form(id=None):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    reserva_restaurante = None
    reserva_hotel = None
    if id:
        reserva_restaurante = conn.execute(
            """
            SELECT rr.*, r.titular_nombre, r.titular_telefono, r.alergias_resumen AS reserva_alergias
            FROM recepcion_reservas_restaurante rr
            LEFT JOIN reservas_recepcion r ON rr.reserva_id = r.id
            WHERE rr.id = ? AND rr.empresa_id = ?
            """,
            (id, empresa_id),
        ).fetchone()
        if not reserva_restaurante:
            conn.close()
            abort(404)
    reserva_id_prefill = _to_int(request.args.get("reserva_id"), 0)
    if reserva_id_prefill:
        reserva_hotel = _reserva_por_id(conn, reserva_id_prefill, empresa_id)
    puntos_venta = _puntos_venta_select(conn, empresa_id)
    reservas_hotel = _reservas_activas_select(conn, empresa_id)
    if request.method == "POST":
        reserva_id = _to_int(request.form.get("reserva_id"), 0) or None
        reserva_hotel = _reserva_por_id(conn, reserva_id, empresa_id) if reserva_id else None
        fecha = request.form.get("fecha") or date.today().isoformat()
        hora = request.form.get("hora") or ""
        personas = max(1, _to_int(request.form.get("personas"), 1))
        if not _parse_date(fecha):
            flash("La fecha de la reserva de restaurante no es valida.", "warning")
            return render_template(
                "recepcion/restaurante_reserva_form.html",
                reserva_restaurante=reserva_restaurante,
                reserva_hotel=reserva_hotel,
                reservas_hotel=reservas_hotel,
                puntos_venta=puntos_venta,
                hoy=date.today().isoformat(),
            )
        nombre_contacto = request.form.get("nombre_contacto") or (reserva_hotel["titular_nombre"] if reserva_hotel else "")
        telefono_contacto = request.form.get("telefono_contacto") or (reserva_hotel["titular_telefono"] if reserva_hotel else "")
        alergias = request.form.get("alergias_resumen") or (reserva_hotel["alergias_resumen"] if reserva_hotel else "")
        datos = (
            reserva_id,
            _to_int(request.form.get("punto_venta_id"), 0) or None,
            fecha,
            hora,
            request.form.get("turno") or _turno_por_hora(hora),
            personas,
            nombre_contacto,
            telefono_contacto,
            request.form.get("mesa") or "",
            request.form.get("estado") or "pendiente",
            alergias,
            request.form.get("origen") or "recepcion",
            request.form.get("observaciones") or "",
        )
        if id:
            conn.execute(
                """
                UPDATE recepcion_reservas_restaurante
                SET reserva_id = ?, punto_venta_id = ?, fecha = ?, hora = ?, turno = ?, personas = ?,
                    nombre_contacto = ?, telefono_contacto = ?, mesa = ?, estado = ?, alergias_resumen = ?,
                    origen = ?, observaciones = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                datos + (id, empresa_id),
            )
            restaurante_id = id
        else:
            conn.execute(
                """
                INSERT INTO recepcion_reservas_restaurante
                (reserva_id, punto_venta_id, fecha, hora, turno, personas, nombre_contacto, telefono_contacto,
                 mesa, estado, alergias_resumen, origen, observaciones, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                datos + (empresa_id,),
            )
            restaurante_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.commit()
        conn.close()
        flash("Reserva de restaurante guardada.", "success")
        return redirect(url_for("recepcion.restaurante_reservas", fecha=fecha))
    conn.close()
    return render_template(
        "recepcion/restaurante_reserva_form.html",
        reserva_restaurante=reserva_restaurante,
        reserva_hotel=reserva_hotel,
        reservas_hotel=reservas_hotel,
        puntos_venta=puntos_venta,
        hoy=date.today().isoformat(),
    )


@recepcion_bp.route("/restaurante-reservas/<int:id>/estado", methods=["POST"])
@login_required
@_require_recepcion
def cambiar_estado_restaurante_reserva(id):
    conn = get_db()
    _ensure_recepcion_schema(conn)
    empresa_id = _empresa_id()
    estado = request.form.get("estado") or "pendiente"
    if estado not in {"pendiente", "confirmada", "sentada", "finalizada", "cancelada"}:
        estado = "pendiente"
    row = conn.execute(
        "SELECT fecha FROM recepcion_reservas_restaurante WHERE id = ? AND empresa_id = ?",
        (id, empresa_id),
    ).fetchone()
    if not row:
        conn.close()
        abort(404)
    conn.execute(
        """
        UPDATE recepcion_reservas_restaurante
        SET estado = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (estado, id, empresa_id),
    )
    conn.commit()
    conn.close()
    flash("Estado de restaurante actualizado.", "success")
    return redirect(request.referrer or url_for("recepcion.restaurante_reservas", fecha=row["fecha"]))


@recepcion_bp.route("/prevision-servicios")
@login_required
@_require_recepcion
def prevision_servicios():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    fecha_txt = request.args.get("fecha") or date.today().isoformat()
    desde_txt = request.args.get("desde") or fecha_txt
    hasta_txt = request.args.get("hasta") or desde_txt
    servicio = request.args.get("servicio") or ""
    desde_obj, hasta_obj, resumen, resumen_dias, dias_operativos, total_periodo, estancias, detalle = _prevision_servicios_periodo(
        conn,
        desde_txt,
        hasta_txt,
        servicio,
    )
    conn.close()
    return render_template(
        "recepcion/prevision_servicios.html",
        fecha=desde_obj.isoformat(),
        desde=desde_obj.isoformat(),
        hasta=hasta_obj.isoformat(),
        servicio=servicio,
        servicios=SERVICIOS_COMIDA,
        resumen=resumen,
        resumen_dias=resumen_dias,
        dias_operativos=dias_operativos,
        total_periodo=total_periodo,
        estancias=estancias,
        detalle=detalle,
    )


@recepcion_bp.route("/prevision-servicios/exportar")
@login_required
@_require_recepcion
def exportar_prevision_servicios():
    conn = get_db()
    _ensure_recepcion_schema(conn)
    desde_txt = request.args.get("desde") or request.args.get("fecha") or date.today().isoformat()
    hasta_txt = request.args.get("hasta") or desde_txt
    servicio = request.args.get("servicio") or ""
    desde_obj, hasta_obj, _resumen, _resumen_dias, dias_operativos, total_periodo, _estancias, detalle = _prevision_servicios_periodo(
        conn,
        desde_txt,
        hasta_txt,
        servicio,
    )
    conn.close()
    rows = []
    for item in detalle:
        reserva = item["reserva"]
        rows.append(
            [
                item["fecha"],
                item["servicio_nombre"],
                reserva["codigo"] or reserva["id"],
                item["habitacion"],
                reserva["titular_nombre"] or "",
                item["adultos"],
                item["menores"],
                item["personas"],
                item["estado_dia"],
                item["regimen"],
                reserva["fecha_entrada"],
                reserva["fecha_salida"],
                item["avisos"] or "",
            ]
        )
    if request.args.get("tipo") == "operativo":
        rows = []
        rows.append(
            [
                "TOTAL",
                total_periodo["pax_alojados"],
                total_periodo["regimenes_txt"],
                total_periodo["entradas_reservas"],
                total_periodo["entradas_pax"],
                total_periodo["salidas_reservas"],
                total_periodo["salidas_pax"],
                total_periodo["servicios"].get("desayuno", 0),
                total_periodo["servicios"].get("comida", 0),
                total_periodo["servicios"].get("cena", 0),
                total_periodo["servicios"].get("bebidas_snacks", 0),
                total_periodo["avisos"],
            ]
        )
        for dia in dias_operativos:
            rows.append(
                [
                    dia["fecha"],
                    dia["pax_alojados"],
                    dia["regimenes_txt"],
                    dia["entradas_reservas"],
                    dia["entradas_pax"],
                    dia["salidas_reservas"],
                    dia["salidas_pax"],
                    dia["servicios"].get("desayuno", 0),
                    dia["servicios"].get("comida", 0),
                    dia["servicios"].get("cena", 0),
                    dia["servicios"].get("bebidas_snacks", 0),
                    dia["avisos"],
                ]
            )
        filename = f"prevision_operativa_{desde_obj.isoformat()}_{hasta_obj.isoformat()}.csv"
        return _csv_response(
            filename,
            [
                "fecha",
                "pax_alojados",
                "pax_por_regimen",
                "entradas_reservas",
                "entradas_pax",
                "salidas_reservas",
                "salidas_pax",
                "desayunos",
                "comidas",
                "cenas",
                "bebidas_snacks",
                "avisos",
            ],
            rows,
        )
    filename = f"prevision_servicios_{desde_obj.isoformat()}_{hasta_obj.isoformat()}.csv"
    return _csv_response(
        filename,
        [
            "fecha",
            "servicio",
            "reserva",
            "habitacion",
            "cliente",
            "adultos",
            "menores",
            "personas",
            "dia_estancia",
            "regimen",
            "entrada",
            "salida",
            "avisos",
        ],
        rows,
    )

