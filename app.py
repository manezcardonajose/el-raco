# =========================
# IMPORTACIONES
# =========================
import os
import hmac
import secrets
from datetime import datetime, timedelta
from io import BytesIO

import pandas as pd
import pdfkit
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash, make_response, abort
from flask_mail import Mail, Message
from werkzeug.utils import secure_filename

from config_secure import get_config
from database import get_db, init_db
from helpers.centro_ia_helper import aplicar_plan_centro_ia, construir_plan_centro_ia, ensure_centro_ia_schema
from helpers.centros_helper import centro_activo_id, ids_departamentos_centro
from logger_config import setup_logging, log_exception

# =========================
# CARGAR VARIABLES DE ENTORNO
# =========================
load_dotenv()

# =========================
# CREAR APLICACIÓN
# =========================
setup_logging()
app = Flask(__name__)
app.config.from_object(get_config())
app.secret_key = app.config["SECRET_KEY"]
if app.secret_key == "dev-insecure-key-cambiar-en-produccion":
    app.logger.warning("Usando SECRET_KEY de pruebas. Define SECRET_KEY antes de publicar la ERP.")
app.config["TESSERACT_CMD_PATH"] = os.environ.get("TESSERACT_CMD_PATH", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH", 25 * 1024 * 1024))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("ERP_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}


def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def validar_csrf():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if request.endpoint in {"pagos.webhook", "pagos.checkout_simulado"}:
        return None
    esperado = session.get("_csrf_token") or ""
    recibido = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    if not esperado or not recibido or not hmac.compare_digest(str(esperado), str(recibido)):
        abort(400, description="Solicitud rechazada por proteccion CSRF. Recarga la pagina e intentalo de nuevo.")
    return None


@app.after_request
def aplicar_cabeceras_seguridad(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response


@app.template_filter("numero_es")
def formato_numero_es(valor, decimales=2):
    try:
        numero = float(valor or 0)
    except (TypeError, ValueError):
        numero = 0.0
    texto = f"{numero:,.{int(decimales)}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


@app.template_filter("euros")
def formato_euros(valor, decimales=2):
    return f"{formato_numero_es(valor, decimales)}€"


@app.template_filter("fecha_es")
def formato_fecha_es(valor):
    if not valor:
        return "-"
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y")
    texto = str(valor).strip()
    for formato in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            base = texto[:19] if "%H" in formato else texto[:10]
            return datetime.strptime(base, formato).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return texto


# =========================
# CONFIGURACIÓN DE EMAIL
# =========================

from flask_mail import Mail
mail = Mail()

def configurar_mail(flask_app):
    flask_app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    flask_app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
    flask_app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
    flask_app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
    flask_app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
    flask_app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER') or os.environ.get('MAIL_USERNAME')
    mail.init_app(flask_app)


configurar_mail(app)

def crear_app():
    app = Flask(__name__)
    app.config.from_object(get_config())
    app.secret_key = app.config["SECRET_KEY"]
    app.config["TESSERACT_CMD_PATH"] = os.environ.get("TESSERACT_CMD_PATH", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH", 25 * 1024 * 1024))
    configurar_mail(app)
    return app

# =========================
# IMPORTAR BLUEPRINTS
# =========================
from blueprints.auth import auth_bp
from blueprints.auth.routes import login_required, usuario_es_global_operacion, usuario_tiene_permiso, usuario_puede_cambiar_empresa
from blueprints.productos import productos_bp
from blueprints.recetas import recetas_bp
from blueprints.compras import compras_bp
from blueprints.produccion import produccion_bp
from blueprints.appcc import appcc_bp
from blueprints.lotes import lotes_bp
from blueprints.menus import menus_bp
from blueprints.mantenimiento import mantenimiento_bp
from blueprints.proveedores import proveedores_bp
from blueprints.empresas import empresas_bp
from blueprints.almacenes import almacenes_bp
from blueprints.inventario import inventario_bp
from blueprints.stock import stock_bp
from blueprints.movimientos import movimientos_bp
from blueprints.traspasos import traspasos_bp
from blueprints.informes import informes_bp
from blueprints.incidencias import incidencias_bp
from blueprints.utils import utils_bp
from blueprints.importar import importar_bp
from blueprints.departamentos import departamentos_bp
from blueprints.puntos_venta import puntos_venta_bp
from blueprints.tpv import tpv_bp
from blueprints.rrhh import rrhh_bp
from blueprints.recepcion import recepcion_bp
from blueprints.pisos import pisos_bp
from blueprints.contabilidad import contabilidad_bp
from blueprints.pagos import pagos_bp
from blueprints.asistente import asistente_bp
from blueprints.animacion import animacion_bp
from blueprints.experiencia import experiencia_bp
from blueprints.demo import demo_bp
from blueprints.buffet_ia import buffet_ia_bp


# =========================
# REGISTRAR BLUEPRINTS
# =========================
app.register_blueprint(auth_bp)
app.register_blueprint(productos_bp)
app.register_blueprint(recetas_bp)
app.register_blueprint(compras_bp)
app.register_blueprint(produccion_bp)
app.register_blueprint(appcc_bp)
app.register_blueprint(lotes_bp)
app.register_blueprint(menus_bp)
app.register_blueprint(mantenimiento_bp)
app.register_blueprint(proveedores_bp)
app.register_blueprint(empresas_bp)
app.register_blueprint(almacenes_bp)
app.register_blueprint(inventario_bp)
app.register_blueprint(stock_bp)
app.register_blueprint(movimientos_bp)
app.register_blueprint(traspasos_bp)
app.register_blueprint(informes_bp)
app.register_blueprint(incidencias_bp)
app.register_blueprint(utils_bp)
app.register_blueprint(importar_bp)
app.register_blueprint(departamentos_bp)
app.register_blueprint(puntos_venta_bp)
app.register_blueprint(tpv_bp)
app.register_blueprint(rrhh_bp)
app.register_blueprint(recepcion_bp)
app.register_blueprint(pisos_bp)
app.register_blueprint(contabilidad_bp)
app.register_blueprint(pagos_bp)
app.register_blueprint(asistente_bp)
app.register_blueprint(animacion_bp)
app.register_blueprint(experiencia_bp)
app.register_blueprint(demo_bp)
app.register_blueprint(buffet_ia_bp)


@app.route("/establecimiento/almacenes")
@login_required
def alias_establecimiento_almacenes():
    return redirect(url_for("almacenes.almacenes"))



# =========================
# CONTEXTO GLOBAL DE PLANTILLAS
# =========================
@app.context_processor
def permisos_globales_template():
    """
    Utilidades globales para plantillas.
    No elimina datos ni cálculos: solo decide qué se muestra según el rol.
    """
    roles_con_precios = {
        'admin',
        'administrador',
        'director',
        'direccion_general',
        'propietario',
        'gerente',
        'direccion',
        'dirección',
        'compras',
        'jefe_compras',
        'contabilidad',
        'administracion',
        'administración',
    }

    def puede_ver_precios():
        try:
            return usuario_tiene_permiso("ver_precios")
        except Exception:
            rol = (session.get('user_rol') or '').strip().lower()
            return rol in roles_con_precios

    def tiene_permiso(nombre):
        try:
            return usuario_tiene_permiso(nombre)
        except Exception:
            return False

    def _tabla_existe(conn, nombre):
        return conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (nombre,),
        ).fetchone() is not None

    def _columna_existe(conn, tabla, columna):
        try:
            columnas = conn.execute(f"PRAGMA table_info({tabla})").fetchall()
        except Exception:
            return False
        return any(c['name'] == columna for c in columnas)

    def _es_rrhh_global():
        rol = (session.get('user_rol') or '').strip().lower()
        roles_rrhh = {'admin', 'gerente', 'direccion', 'rrhh', 'recursos_humanos'}
        return usuario_es_global_operacion({"rrhh", "recursos_humanos"}) or usuario_tiene_permiso("rrhh_gestionar")

    def _contador_rrhh_pendientes():
        vacio = {
            'correcciones': 0,
            'solicitudes': 0,
            'documentos': 0,
            'contratos': 0,
            'total': 0,
        }
        if 'user_id' not in session or not _es_rrhh_global():
            return vacio
        conn = None
        try:
            conn = get_db()
            empresa_id = session.get('user_empresa_id', 1) or 1
            datos = dict(vacio)
            if _tabla_existe(conn, 'solicitudes_correccion_fichaje'):
                datos['correcciones'] = conn.execute(
                    """
                    SELECT COUNT(*) total
                    FROM solicitudes_correccion_fichaje
                    WHERE empresa_id = ? AND COALESCE(estado, 'pendiente') = 'pendiente'
                    """,
                    (empresa_id,),
                ).fetchone()['total']
            if _tabla_existe(conn, 'vacaciones_rrhh'):
                datos['solicitudes'] = conn.execute(
                    """
                    SELECT COUNT(*) total
                    FROM vacaciones_rrhh
                    WHERE empresa_id = ? AND COALESCE(estado, 'solicitada') = 'solicitada'
                    """,
                    (empresa_id,),
                ).fetchone()['total']
            if _tabla_existe(conn, 'documentos_rrhh'):
                datos['documentos'] = conn.execute(
                    """
                    SELECT COUNT(*) total
                    FROM documentos_rrhh
                    WHERE empresa_id = ? AND LOWER(COALESCE(estado, 'vigente')) = 'pendiente_revision'
                    """,
                    (empresa_id,),
                ).fetchone()['total']
            if _tabla_existe(conn, 'contratos'):
                firma_condicion = "COALESCE(firmado_empleado, 0) = 0" if _columna_existe(conn, 'contratos', 'firmado_empleado') else "1 = 1"
                datos['contratos'] = conn.execute(
                    f"""
                    SELECT COUNT(*) total
                    FROM contratos
                    WHERE empresa_id = ?
                      AND COALESCE(estado, 'activo') NOT IN ('anulado', 'cancelado', 'borrado')
                      AND {firma_condicion}
                    """,
                    (empresa_id,),
                ).fetchone()['total']
            datos['total'] = datos['correcciones'] + datos['solicitudes'] + datos['documentos'] + datos['contratos']
            return datos
        except Exception:
            return vacio
        finally:
            if conn:
                conn.close()

    def _contador_rrhh_alertas_admin():
        vacio = {'documentos_caducidad': 0, 'contratos_fin': 0, 'total': 0}
        if 'user_id' not in session or not _es_rrhh_global():
            return vacio
        conn = None
        try:
            conn = get_db()
            empresa_id = session.get('user_empresa_id', 1) or 1
            datos = dict(vacio)
            if _tabla_existe(conn, 'documentos_rrhh'):
                datos['documentos_caducidad'] = conn.execute(
                    """
                    SELECT COUNT(*) total
                    FROM documentos_rrhh
                    WHERE empresa_id = ?
                      AND fecha_caducidad IS NOT NULL
                      AND TRIM(fecha_caducidad) != ''
                      AND date(fecha_caducidad) <= date('now', '+30 day')
                      AND COALESCE(estado, 'vigente') NOT IN ('anulado', 'rechazado', 'cancelado')
                    """,
                    (empresa_id,),
                ).fetchone()['total']
            if _tabla_existe(conn, 'contratos'):
                datos['contratos_fin'] = conn.execute(
                    """
                    SELECT COUNT(*) total
                    FROM contratos
                    WHERE empresa_id = ?
                      AND fecha_fin IS NOT NULL
                      AND TRIM(fecha_fin) != ''
                      AND date(fecha_fin) <= date('now', '+30 day')
                      AND COALESCE(estado, 'activo') NOT IN ('anulado', 'cancelado', 'borrado')
                    """,
                    (empresa_id,),
                ).fetchone()['total']
            datos['total'] = datos['documentos_caducidad'] + datos['contratos_fin']
            return datos
        except Exception:
            return vacio
        finally:
            if conn:
                conn.close()

    def _modo_pantalla_operativa():
        endpoint = request.endpoint or ""
        rol = (session.get("user_rol") or "").strip().lower()
        if request.args.get("normal") == "1":
            return False
        if request.values.get("kiosco") == "1" or request.values.get("pantalla") == "1":
            return True
        if request.values.get("terminal") == "1" or request.values.get("tablet") == "1" or request.values.get("modo_tablet") == "1":
            return True
        if rol in {"kiosco", "tablet", "fichaje", "tablet_pisos"}:
            return True
        endpoints_operativos = {
            "rrhh.kiosco_fichaje",
            "rrhh.kiosco_registrar_fichaje",
            "pisos.tablet",
            "produccion.terminales_cocina",
            "produccion.terminal_subdepartamento",
            "produccion.terminal_actualizar_linea",
            "tpv.terminal",
            "tpv.crear_comanda",
            "tpv.cobrar_terminal",
            "tpv.buffet",
            "tpv.acceso",
            "tpv.pantalla",
        }
        return endpoint in endpoints_operativos

    def _centro_activo():
        centro_id = session.get("centro_activo_id")
        if not centro_id:
            return None
        conn = None
        try:
            conn = get_db()
            row = conn.execute(
                """
                SELECT id, nombre, codigo
                FROM centros_trabajo
                WHERE id = ? AND empresa_id = ?
                """,
                (centro_id, session.get("user_empresa_id", 1) or 1),
            ).fetchone()
            return dict(row) if row else None
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _empresa_activa():
        empresa_id = session.get("user_empresa_id", 1) or 1
        conn = None
        try:
            conn = get_db()
            row = conn.execute(
                "SELECT id, nombre FROM empresas WHERE id = ? AND COALESCE(activo, 1) = 1",
                (empresa_id,),
            ).fetchone()
            return dict(row) if row else None
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _empresas_disponibles():
        if not usuario_puede_cambiar_empresa():
            return []
        conn = None
        try:
            conn = get_db()
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, nombre
                    FROM empresas
                    WHERE COALESCE(activo, 1) = 1
                    ORDER BY nombre
                    """
                ).fetchall()
            ]
        except Exception:
            return []
        finally:
            if conn:
                conn.close()

    return {
        'puede_ver_precios': puede_ver_precios,
        'tiene_permiso': tiene_permiso,
        'roles_con_precios': roles_con_precios,
        'rrhh_pendientes_menu': _contador_rrhh_pendientes(),
        'rrhh_alertas_admin_menu': _contador_rrhh_alertas_admin(),
        'modo_pantalla_operativa': _modo_pantalla_operativa(),
        'centro_activo': _centro_activo(),
        'empresa_activa': _empresa_activa(),
        'empresas_disponibles': _empresas_disponibles(),
    }

def _asegurar_ambito_centro_usuario():
    usuario_id = session.get("user_id")
    empresa_id = session.get("user_empresa_id", 1) or 1
    if not usuario_id:
        return
    conn = None
    try:
        conn = get_db()
        empleado = conn.execute(
            """
            SELECT e.id, e.centro_id, e.departamento_id, e.subdepartamento_id, e.rol
            FROM empleados e
            LEFT JOIN usuarios u ON u.id = ?
            WHERE e.empresa_id = ?
              AND COALESCE(e.activo, 1) = 1
              AND (e.usuario_id = ? OR e.id = u.empleado_id)
            ORDER BY CASE WHEN e.usuario_id = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (usuario_id, empresa_id, usuario_id, usuario_id),
        ).fetchone()
        if not empleado:
            return
        session["user_empleado_id"] = empleado["id"]
        if empleado["departamento_id"]:
            session["user_departamento_id"] = empleado["departamento_id"]
        if empleado["subdepartamento_id"]:
            session["user_subdepartamento_id"] = empleado["subdepartamento_id"]
        if empleado["rol"]:
            session["user_empleado_rol"] = empleado["rol"]
        if empleado["centro_id"]:
            session["user_centro_id"] = empleado["centro_id"]
            session["centro_activo_id"] = empleado["centro_id"]
            session["user_centro_bloqueado"] = 1
            session["user_es_global"] = 0
    except Exception:
        return
    finally:
        if conn:
            conn.close()


@app.before_request
def proteger_modulos_por_permiso():
    if request.endpoint in {None, "static", "home"}:
        return None
    if request.endpoint.startswith("auth."):
        return None
    if request.endpoint in {"experiencia.publico_cliente", "experiencia.publico_queja"}:
        return None
    if "user_id" not in session:
        return None
    _asegurar_ambito_centro_usuario()

    rol = (session.get("user_rol") or "").lower()
    endpoint = request.endpoint or ""
    if endpoint in {"mantenimiento.aviso_mantenimiento"}:
        return None
    if endpoint.startswith("rrhh."):
        endpoints_empleado = (
            "rrhh.portal_empleado",
            "rrhh.fichar_empleado",
            "rrhh.cambiar_pin_fichaje_empleado",
            "rrhh.descargar_nomina_empleado",
            "rrhh.descargar_documento_empleado",
            "rrhh.descargar_contrato_empleado",
            "rrhh.firmar_contrato_empleado",
            "rrhh.solicitar_ausencia_empleado",
            "rrhh.cancelar_solicitud_empleado",
            "rrhh.kiosco_fichaje",
            "rrhh.kiosco_registrar_fichaje",
        )
        if endpoint in endpoints_empleado and rol in {"empleado", "kiosco", "tablet", "fichaje"}:
            return None

    endpoints_estructura = {
        "recepcion.habitaciones",
        "recepcion.habitacion_form",
        "recepcion.habitaciones_alta_rapida",
        "recepcion.tipos_habitacion",
        "recepcion.toggle_tipo_habitacion",
        "rrhh.centros_trabajo",
        "departamentos.index",
        "departamentos.listar_departamentos",
        "departamentos.listar_subdepartamentos",
        "departamentos.nuevo_subdepartamento",
        "departamentos.ver_departamento",
        "departamentos.nuevo_departamento",
        "departamentos.editar_departamento",
        "puntos_venta.listar_puntos_venta",
        "puntos_venta.nuevo_punto_venta",
        "almacenes.almacenes",
        "almacenes.nuevo_almacen",
    }
    if endpoint in endpoints_estructura and (
        usuario_tiene_permiso("gestionar_estructura") or usuario_tiene_permiso("acceso_global")
    ):
        return None

    if request.blueprint == "compras":
        endpoints_solicitud = {
            "compras.lista_pedidos",
            "compras.ver_pedido",
            "compras.nuevo_pedido",
            "compras.nuevo_pedido_tipo_a",
            "compras.nuevo_pedido_tipo_b",
            "compras.necesidades_compra",
            "compras.imprimir_pedido",
            "compras.solicitar_autorizacion_pedido",
        }
        endpoints_autorizacion = {
            "compras.autorizar_pedido",
            "compras.rechazar_pedido",
            "compras.editar_pedido",
            "compras.cambiar_estado_pedido",
            "compras.cancelar_pedido",
            "compras.cerrar_pedido",
            "compras.eliminar_pedido",
            "compras.enviar_pedido",
            "compras.enviar_pedido_por_email",
        }
        endpoints_validacion = {
            "compras.validar_economica",
            "compras.validar_economico",
            "compras.detalle_validacion_recepcion",
            "compras.pantalla_validacion_economica",
            "compras.guardar_validacion_economica",
            "compras.guardar_validacion_economica_detalle",
        }
        endpoints_recepcion = {
            "compras.recepcion_pedido",
            "compras.recibir_pedido",
            "compras.recepcion_directa",
            "compras.editar_recepcion",
            "compras.hoja_conformidad",
            "compras.hoja_incidencias_recepcion",
            "compras.imprimir_conformidad_recepcion",
            "compras.imprimir_incidencias_recepcion",
            "compras.hoja_conformidad_recepcion_id",
            "compras.hoja_incidencias_recepcion_id",
            "compras.validar_recepciones",
            "compras.validar_operativa",
            "compras.ver_documento_compra",
            "compras.detalle_documento_compra",
            "compras.procesar_documento_compra",
            "compras.detectar_lineas_documento",
            "compras.revisar_lineas_detectadas",
            "compras.asociar_lineas_detectadas",
            "compras.incidencias_compras",
            "compras.auditoria_compras",
        }
        endpoints_precios = {
            "compras.historico_precios",
        }
        if endpoint in endpoints_solicitud:
            permisos_compra = ("compras_solicitar", "compras_autorizar_departamento", "compras_gestionar")
        elif endpoint in endpoints_autorizacion:
            permisos_compra = ("compras_autorizar_departamento", "compras_gestionar")
        elif endpoint in endpoints_validacion:
            permisos_compra = ("compras_validar_economico", "compras_gestionar")
        elif endpoint in endpoints_recepcion:
            permisos_compra = ("compras_recepcionar", "compras_gestionar", "compras_validar_economico")
        elif endpoint in endpoints_precios:
            permisos_compra = ("compras_validar_economico", "compras_gestionar")
        else:
            permisos_compra = ("compras_gestionar", "compras_negociar", "compras_validar_economico")
        if not any(usuario_tiene_permiso(permiso) for permiso in permisos_compra):
            flash("No tienes permisos para esta operacion de compras.", "warning")
            return redirect(url_for("home"))
        return None

    permisos_por_blueprint = {
        "recepcion": ("recepcion_ver", "recepcion_gestionar", "recepcion_facturacion"),
        "pisos": ("pisos_ver", "pisos_gestionar"),
        "tpv": ("tpv_ver", "tpv_gestionar"),
        "asistente": ("asistente_ver", "informes_ver", "contabilidad_ver", "rrhh_ver", "recepcion_gestionar", "acceso_global"),
        "animacion": ("animacion_ver", "animacion_gestionar", "acceso_global"),
        "experiencia": ("experiencia_ver", "experiencia_gestionar", "recepcion_gestionar", "informes_ver", "acceso_global"),
        "contabilidad": ("contabilidad_ver", "contabilidad_gestionar"),
        "pagos": ("pagos_ver", "pagos_operar", "pagos_configurar", "contabilidad_ver", "recepcion_facturacion", "tpv_gestionar", "acceso_global"),
        "mantenimiento": ("mantenimiento_ver", "mantenimiento_gestionar"),
        "rrhh": ("rrhh_ver", "rrhh_gestionar", "ver_rrhh", "ver_cuadrantes", "gestionar_cuadrantes"),
        "compras": ("compras_solicitar", "compras_recepcionar", "compras_gestionar", "compras_negociar", "compras_validar_economico"),
        "productos": ("productos_ver", "productos_gestionar"),
        "proveedores": ("proveedores_ver", "proveedores_gestionar"),
        "produccion": ("produccion_ver", "produccion_editar"),
        "recetas": ("produccion_ver", "produccion_editar", "appcc_ver", "appcc_gestionar", "tpv_ver", "tpv_gestionar"),
        "menus": ("produccion_ver", "produccion_editar", "appcc_ver", "appcc_gestionar", "tpv_ver", "tpv_gestionar"),
        "appcc": ("appcc_ver", "appcc_gestionar"),
        "lotes": ("appcc_ver", "appcc_gestionar"),
        "informes": ("informes_ver",),
        "incidencias": (
            "informes_ver", "appcc_ver", "mantenimiento_ver", "pisos_ver", "recepcion_ver",
            "compras_solicitar", "compras_recepcionar", "compras_gestionar", "compras_validar_economico",
            "produccion_ver", "tpv_ver", "rrhh_ver", "ver_rrhh",
        ),
        "importar": ("acceso_global",),
        "departamentos": ("gestionar_estructura", "acceso_global"),
        "puntos_venta": ("gestionar_estructura", "tpv_gestionar", "acceso_global"),
        "almacenes": ("gestionar_estructura", "compras_gestionar", "acceso_global"),
        "inventario": ("gestionar_estructura", "compras_gestionar", "productos_ver", "acceso_global"),
        "stock": ("compras_gestionar", "productos_ver", "acceso_global"),
        "traspasos": ("compras_gestionar", "acceso_global"),
        "empresas": ("gestionar_empresas", "acceso_global"),
    }
    permisos = permisos_por_blueprint.get(request.blueprint)
    if permisos and not any(usuario_tiene_permiso(permiso) for permiso in permisos):
        flash("No tienes permisos para acceder a este modulo.", "warning")
        return redirect(url_for("home"))
    return None

# =========================
# FUNCIÓN GENERADORA DE CÓDIGOS
# =========================
def generar_codigo_producto(familia, tipo_conservacion):
    """
    Generador temporal de códigos.
    Se puede mejorar más adelante, pero no debe romper el arranque.
    """
    familia_txt = (familia or "GEN").strip().upper()[:3]
    conservacion_txt = (tipo_conservacion or "GEN").strip().upper()[:3]
    timestamp = datetime.now().strftime("%H%M%S")
    return f"{familia_txt}-{conservacion_txt}-{timestamp}"


def rows_to_dicts(rows):
    """
    Convierte una lista de sqlite3.Row a lista de dict.
    Necesario para usar tojson en templates.
    """
    return [dict(r) for r in rows]


def _dashboard_scalar(conn, sql, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return default
        valor = row[0]
        return default if valor is None else valor
    except Exception:
        return default


def _safe_url_for(endpoint, **values):
    try:
        return url_for(endpoint, **values)
    except Exception:
        return None


def _puede_alguno(permisos):
    return any(usuario_tiene_permiso(permiso) for permiso in permisos)


def _modulos_operativos_usuario():
    specs = [
        {
            "titulo": "Recepcion",
            "detalle": "Reservas, check-in, ocupacion, caja y facturacion.",
            "icono": "fa-concierge-bell",
            "endpoint": "recepcion.dashboard",
            "permisos": ("recepcion_ver", "recepcion_gestionar", "recepcion_facturacion"),
        },
        {
            "titulo": "Experiencia cliente",
            "detalle": "RRPP, quejas, ficha 360, comprobaciones y seguimiento.",
            "icono": "fa-user-tie",
            "endpoint": "experiencia.dashboard",
            "permisos": ("experiencia_ver", "experiencia_gestionar", "recepcion_gestionar", "informes_ver"),
        },
        {
            "titulo": "Pisos",
            "detalle": "Partes, habitaciones, lenceria, lavanderia y trazabilidad.",
            "icono": "fa-broom",
            "endpoint": "pisos.dashboard",
            "permisos": ("pisos_ver", "pisos_gestionar"),
        },
        {
            "titulo": "Produccion",
            "detalle": "Partes de produccion, planificacion, mermas y APPCC.",
            "icono": "fa-fire",
            "endpoint": "produccion.produccion_index",
            "permisos": ("produccion_ver", "produccion_editar", "appcc_ver", "appcc_gestionar"),
        },
        {
            "titulo": "Compras y economato",
            "detalle": "Pedidos, recepciones, proveedores, precios y validaciones.",
            "icono": "fa-shopping-cart",
            "endpoint": "compras.lista_pedidos",
            "permisos": ("compras_solicitar", "compras_recepcionar", "compras_gestionar", "compras_negociar", "compras_validar_economico"),
        },
        {
            "titulo": "TPV",
            "detalle": "Terminales, accesos, tickets, buffet y pantalla de cocina.",
            "icono": "fa-cash-register",
            "endpoint": "tpv.dashboard",
            "permisos": ("tpv_ver", "tpv_gestionar"),
        },
        {
            "titulo": "Mantenimiento",
            "detalle": "Ordenes, equipos, preventivos y repuestos.",
            "icono": "fa-wrench",
            "endpoint": "mantenimiento.dashboard",
            "permisos": ("mantenimiento_ver", "mantenimiento_gestionar"),
        },
        {
            "titulo": "RRHH",
            "detalle": "Personal, cuadrantes, jornada, nominas y documentos.",
            "icono": "fa-users",
            "endpoint": "rrhh.panel_rrhh",
            "permisos": ("rrhh_ver", "rrhh_gestionar", "ver_rrhh"),
        },
        {
            "titulo": "Control economico",
            "detalle": "Facturas, gastos, vencimientos, pagos y base contable.",
            "icono": "fa-balance-scale",
            "endpoint": "contabilidad.dashboard",
            "permisos": ("contabilidad_ver", "contabilidad_gestionar"),
        },
        {
            "titulo": "Informes",
            "detalle": "Control de compras, stock, produccion e incidencias.",
            "icono": "fa-chart-pie",
            "endpoint": "informes.stock",
            "permisos": ("informes_ver",),
        },
    ]
    modulos = []
    for spec in specs:
        if not _puede_alguno(spec["permisos"]):
            continue
        url = _safe_url_for(spec["endpoint"])
        if not url:
            continue
        modulo = dict(spec)
        modulo["url"] = url
        modulos.append(modulo)
    return modulos


def _setup_count(conn, table_name, empresa_id=None, where="1 = 1"):
    try:
        empresa_sql = " AND empresa_id = ?" if empresa_id is not None else ""
        params = (empresa_id,) if empresa_id is not None else ()
        return _dashboard_scalar(
            conn,
            f"SELECT COUNT(*) FROM {table_name} WHERE {where}{empresa_sql}",
            params,
        )
    except Exception:
        return 0


def _setup_count_centro(conn, table_name, empresa_id, where="1 = 1", centro_id=None):
    if not centro_id:
        return _setup_count(conn, table_name, empresa_id, where)
    try:
        cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
        params = [empresa_id]
        centro_filter = ""
        if "centro_id" in cols:
            centro_filter = " AND centro_id = ?"
            params.append(centro_id)
        elif "departamento_id" in cols and _app_table_exists(conn, "departamentos"):
            dep_ids = ids_departamentos_centro(conn, empresa_id, centro_id)
            if not dep_ids:
                return 0
            centro_filter = f" AND departamento_id IN ({','.join('?' for _ in dep_ids)})"
            params.extend(dep_ids)
        elif "bloque_id" in cols and _app_table_exists(conn, "establecimiento_bloques"):
            centro_filter = " AND bloque_id IN (SELECT id FROM establecimiento_bloques WHERE empresa_id = ? AND centro_id = ?)"
            params.extend([empresa_id, centro_id])
        else:
            return 0
        return _dashboard_scalar(
            conn,
            f"SELECT COUNT(*) FROM {table_name} WHERE {where} AND empresa_id = ?{centro_filter}",
            params,
        )
    except Exception:
        return 0


def _setup_count_usuarios_centro(conn, empresa_id, centro_id=None):
    if not centro_id:
        return _setup_count(conn, "usuarios", empresa_id, "COALESCE(activo, 1) = 1")
    if not (_app_table_exists(conn, "usuarios") and _app_table_exists(conn, "empleados")):
        return 0
    try:
        return _dashboard_scalar(
            conn,
            """
            SELECT COUNT(DISTINCT u.id)
            FROM usuarios u
            JOIN empleados e ON e.id = u.empleado_id
            WHERE u.empresa_id = ?
              AND COALESCE(u.activo, 1) = 1
              AND COALESCE(e.activo, 1) = 1
              AND e.centro_id = ?
            """,
            (empresa_id, centro_id),
        )
    except Exception:
        return 0


def _setup_count_roles_centro(conn, empresa_id, centro_id=None):
    if not centro_id:
        return _setup_count(conn, "roles")
    if not (_app_table_exists(conn, "usuarios") and _app_table_exists(conn, "empleados") and _app_table_exists(conn, "roles")):
        return 0
    try:
        return _dashboard_scalar(
            conn,
            """
            SELECT COUNT(DISTINCT r.id)
            FROM roles r
            JOIN usuarios u ON u.rol_id = r.id
            JOIN empleados e ON e.id = u.empleado_id
            WHERE u.empresa_id = ?
              AND COALESCE(u.activo, 1) = 1
              AND COALESCE(e.activo, 1) = 1
              AND e.centro_id = ?
            """,
            (empresa_id, centro_id),
        )
    except Exception:
        return 0


def _setup_url(endpoint, fallback="#"):
    return _safe_url_for(endpoint) or fallback


def _app_table_exists(conn, table_name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _app_column_exists(conn, table_name, column_name):
    try:
        cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return False
    return any(col["name"] == column_name for col in cols)


def _app_add_column_if_missing(conn, table_name, column_sql):
    column_name = column_sql.strip().split()[0]
    if _app_table_exists(conn, table_name) and not _app_column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def _estructura_codigo(valor, prefijo="GEN"):
    texto = "".join(ch for ch in (valor or "").upper() if ch.isalnum())
    return (texto or prefijo)[:20]


def _asegurar_estructura_schema(conn, empresa_id):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS establecimiento_bloques (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            centro_id INTEGER,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(empresa_id, codigo)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS establecimiento_plantas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bloque_id INTEGER,
            numero TEXT,
            nombre TEXT,
            descripcion TEXT,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(empresa_id, bloque_id, numero)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS establecimiento_zonas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            tipo TEXT DEFAULT 'zona_comun',
            bloque_id INTEGER,
            planta_id INTEGER,
            bloque TEXT,
            planta TEXT,
            departamento_responsable_id INTEGER,
            frecuencia_limpieza TEXT,
            appcc_critico INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _app_add_column_if_missing(conn, "habitaciones_recepcion", "bloque_id INTEGER")
    _app_add_column_if_missing(conn, "habitaciones_recepcion", "planta_id INTEGER")
    _app_add_column_if_missing(conn, "establecimiento_plantas", "centro_id INTEGER")
    _app_add_column_if_missing(conn, "establecimiento_zonas", "centro_id INTEGER")
    _app_add_column_if_missing(conn, "pisos_zonas", "bloque_id INTEGER")
    _app_add_column_if_missing(conn, "pisos_zonas", "planta_id INTEGER")
    _app_add_column_if_missing(conn, "pisos_zonas", "departamento_responsable_id INTEGER")
    conn.execute(
        """
        UPDATE establecimiento_plantas
        SET centro_id = (
            SELECT centro_id FROM establecimiento_bloques b
            WHERE b.id = establecimiento_plantas.bloque_id
        )
        WHERE centro_id IS NULL
        """
    )
    conn.execute(
        """
        UPDATE establecimiento_zonas
        SET centro_id = COALESCE(
            (SELECT centro_id FROM establecimiento_bloques b WHERE b.id = establecimiento_zonas.bloque_id),
            (SELECT centro_id FROM departamentos d WHERE d.id = establecimiento_zonas.departamento_responsable_id)
        )
        WHERE centro_id IS NULL
        """
    )
    _sincronizar_estructura_desde_datos(conn, empresa_id)


def _obtener_o_crear_bloque(conn, empresa_id, nombre, centro_id=None):
    nombre = (nombre or "General").strip() or "General"
    codigo = _estructura_codigo(nombre, "GENERAL")
    row = conn.execute(
        """
        SELECT id FROM establecimiento_bloques
        WHERE empresa_id = ? AND codigo = ?
        LIMIT 1
        """,
        (empresa_id, codigo),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        """
        INSERT INTO establecimiento_bloques (codigo, nombre, centro_id, empresa_id)
        VALUES (?, ?, ?, ?)
        """,
        (codigo, nombre, centro_id, empresa_id),
    )
    return cur.lastrowid


def _obtener_o_crear_planta(conn, empresa_id, bloque_id, numero):
    numero = str(numero if numero is not None else "").strip()
    if not numero:
        return None
    nombre = "Planta baja" if numero == "0" else f"Planta {numero}"
    row = conn.execute(
        """
        SELECT id FROM establecimiento_plantas
        WHERE empresa_id = ? AND bloque_id = ? AND numero = ?
        LIMIT 1
        """,
        (empresa_id, bloque_id, numero),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        """
        INSERT INTO establecimiento_plantas (bloque_id, numero, nombre, empresa_id)
        VALUES (?, ?, ?, ?)
        """,
        (bloque_id, numero, nombre, empresa_id),
    )
    return cur.lastrowid


def _sincronizar_estructura_desde_datos(conn, empresa_id):
    if _app_table_exists(conn, "habitaciones_recepcion"):
        habitaciones = conn.execute(
            """
            SELECT id, COALESCE(bloque, '') AS bloque, COALESCE(planta, '') AS planta
            FROM habitaciones_recepcion
            WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
            """,
            (empresa_id,),
        ).fetchall()
        for h in habitaciones:
            bloque_txt = h["bloque"] or "General"
            bloque_id = _obtener_o_crear_bloque(conn, empresa_id, bloque_txt)
            planta_id = _obtener_o_crear_planta(conn, empresa_id, bloque_id, h["planta"])
            conn.execute(
                """
                UPDATE habitaciones_recepcion
                SET bloque_id = ?, planta_id = ?
                WHERE id = ? AND empresa_id = ?
                """,
                (bloque_id, planta_id, h["id"], empresa_id),
            )

    if _app_table_exists(conn, "pisos_zonas"):
        zonas = conn.execute(
            """
            SELECT id, nombre, tipo, bloque, planta, frecuencia, appcc_critico, activo
            FROM pisos_zonas
            WHERE empresa_id = ?
            """,
            (empresa_id,),
        ).fetchall()
        for z in zonas:
            bloque_id = _obtener_o_crear_bloque(conn, empresa_id, z["bloque"] or "General")
            planta_id = _obtener_o_crear_planta(conn, empresa_id, bloque_id, z["planta"])
            existe = conn.execute(
                """
                SELECT id FROM establecimiento_zonas
                WHERE empresa_id = ?
                  AND nombre = ?
                  AND COALESCE(bloque, '') = COALESCE(?, '')
                  AND COALESCE(planta, '') = COALESCE(?, '')
                LIMIT 1
                """,
                (empresa_id, z["nombre"], z["bloque"], z["planta"]),
            ).fetchone()
            if not existe:
                conn.execute(
                    """
                    INSERT INTO establecimiento_zonas
                    (nombre, tipo, bloque_id, planta_id, bloque, planta, frecuencia_limpieza, appcc_critico, activo, empresa_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        z["nombre"],
                        z["tipo"] or "zona_comun",
                        bloque_id,
                        planta_id,
                        z["bloque"] or "",
                        z["planta"] or "",
                        z["frecuencia"] or "",
                        z["appcc_critico"] or 0,
                        z["activo"] if z["activo"] is not None else 1,
                        empresa_id,
                    ),
                )
            conn.execute(
                "UPDATE pisos_zonas SET bloque_id = ?, planta_id = ? WHERE id = ? AND empresa_id = ?",
                (bloque_id, planta_id, z["id"], empresa_id),
            )

def _configuracion_inicial_items(conn, empresa_id):
    _asegurar_estructura_schema(conn, empresa_id)
    centro_id = centro_activo_id()
    empresas = _setup_count(conn, "empresas", None, "COALESCE(activo, 1) = 1")
    centros = _setup_count(conn, "centros_trabajo", empresa_id, "COALESCE(activo, 1) = 1")
    bloques = _setup_count_centro(conn, "establecimiento_bloques", empresa_id, "COALESCE(activo, 1) = 1", centro_id)
    plantas = _setup_count_centro(conn, "establecimiento_plantas", empresa_id, "COALESCE(activo, 1) = 1", centro_id)
    zonas = _setup_count_centro(conn, "establecimiento_zonas", empresa_id, "COALESCE(activo, 1) = 1", centro_id)
    tipos_habitacion = _setup_count_centro(conn, "recepcion_tipos_habitacion", empresa_id, "COALESCE(activo, 1) = 1", centro_id)
    habitaciones = _setup_count_centro(conn, "habitaciones_recepcion", empresa_id, "COALESCE(activo, 1) = 1", centro_id)
    departamentos = _setup_count_centro(conn, "departamentos", empresa_id, "COALESCE(activo, 1) = 1", centro_id)
    subdepartamentos = _setup_count_centro(conn, "subdepartamentos", empresa_id, "COALESCE(activo, 1) = 1", centro_id)
    almacenes = _setup_count_centro(conn, "almacenes", empresa_id, "COALESCE(activo, 1) = 1", centro_id)
    puntos_venta = _setup_count_centro(conn, "puntos_venta", empresa_id, "COALESCE(activo, 1) = 1", centro_id)
    inventario_operativo = _setup_count_centro(conn, "inventario_articulos", empresa_id, "COALESCE(activo, 1) = 1", centro_id)
    usuarios = _setup_count_usuarios_centro(conn, empresa_id, centro_id)
    roles = _setup_count_roles_centro(conn, empresa_id, centro_id)
    productos = _setup_count_centro(conn, "productos", empresa_id, "COALESCE(activo, 1) = 1", centro_id)
    proveedores = 0 if centro_id else _setup_count(conn, "proveedores", empresa_id, "COALESCE(activo, 1) = 1")
    appcc_equipos = _setup_count_centro(conn, "equipos", empresa_id, "COALESCE(activo, 1) = 1", centro_id)
    appcc_limpieza = _setup_count_centro(conn, "plan_limpieza", empresa_id, "COALESCE(activo, 1) = 1", centro_id)

    return [
        {
            "bloque": "Empresa",
            "titulo": "Empresa y establecimiento",
            "detalle": "Datos fiscales, logo, centros de trabajo y datos base del hotel.",
            "icono": "fa-hotel",
            "estado": "listo" if empresas and centros else "pendiente",
            "progreso": min(100, (50 if empresas else 0) + (50 if centros else 0)),
            "metricas": [("Empresas", empresas), ("Centros", centros)],
            "acciones": [
                ("Empresas", _setup_url("empresas.listar_empresas")),
                ("Centros", _setup_url("rrhh.centros_trabajo")),
            ],
        },
        {
            "bloque": "Mapa",
            "titulo": "Bloques, plantas y zonas",
            "detalle": "Mapa maestro del establecimiento: edificios, plantas, zonas comunes y areas APPCC.",
            "icono": "fa-map",
            "estado": "listo" if bloques and plantas and zonas else "pendiente",
            "progreso": min(100, (35 if bloques else 0) + (35 if plantas else 0) + (30 if zonas else 0)),
            "metricas": [("Bloques", bloques), ("Plantas", plantas), ("Zonas", zonas)],
            "acciones": [
                ("Mapa del establecimiento", _setup_url("estructura_establecimiento")),
                ("Zonas pisos", _setup_url("pisos.configuracion")),
            ],
        },
        {
            "bloque": "Habitaciones",
            "titulo": "Alojamiento fisico",
            "detalle": "Tipos, habitaciones, plantas, bloques y capacidades.",
            "icono": "fa-bed",
            "estado": "listo" if tipos_habitacion and habitaciones else "pendiente",
            "progreso": min(100, (40 if tipos_habitacion else 0) + min(60, habitaciones * 6)),
            "metricas": [("Tipos", tipos_habitacion), ("Habitaciones", habitaciones)],
            "acciones": [
                ("Tipos", _setup_url("recepcion.tipos_habitacion")),
                ("Habitaciones", _setup_url("recepcion.habitaciones")),
                ("Alta rapida", _setup_url("recepcion.habitaciones_alta_rapida")),
            ],
        },
        {
            "bloque": "Organizacion",
            "titulo": "Departamentos y puestos",
            "detalle": "Departamentos, subdepartamentos y estructura operativa por area.",
            "icono": "fa-sitemap",
            "estado": "listo" if departamentos and subdepartamentos else "pendiente",
            "progreso": min(100, (50 if departamentos else 0) + (50 if subdepartamentos else 0)),
            "metricas": [("Departamentos", departamentos), ("Subdepartamentos", subdepartamentos)],
            "acciones": [
                ("Departamentos", _setup_url("departamentos.index")),
                ("Subdepartamentos", _setup_url("departamentos.listar_subdepartamentos")),
            ],
        },
        {
            "bloque": "Operacion",
            "titulo": "Puntos de venta y almacenes",
            "detalle": "Restaurantes, bares, economato, almacenes e inventario operativo por ubicacion.",
            "icono": "fa-store",
            "estado": "listo" if puntos_venta and almacenes else "pendiente",
            "progreso": min(100, (50 if puntos_venta else 0) + (50 if almacenes else 0)),
            "metricas": [("Puntos venta", puntos_venta), ("Almacenes", almacenes), ("Inventario", inventario_operativo)],
            "acciones": [
                ("Puntos de venta", _setup_url("puntos_venta.listar_puntos_venta")),
                ("Almacenes", _setup_url("almacenes.almacenes")),
                ("Inventario", _setup_url("inventario.inventario")),
            ],
        },
        {
            "bloque": "Seguridad",
            "titulo": "Usuarios, roles y permisos",
            "detalle": "Accesos globales, jefaturas, permisos por departamento y usuarios operativos.",
            "icono": "fa-user-shield",
            "estado": "listo" if usuarios and roles else "pendiente",
            "progreso": min(100, (50 if usuarios else 0) + (50 if roles else 0)),
            "metricas": [("Usuarios centro", usuarios), ("Roles usados", roles)],
            "acciones": [
                ("Usuarios", _setup_url("auth.listar_usuarios")),
                ("Roles", _setup_url("rrhh.lista_roles")),
            ],
        },
        {
            "bloque": "Compras",
            "titulo": "Catalogo y proveedores",
            "detalle": "Productos, proveedores, precios base y trazabilidad de compras.",
            "icono": "fa-boxes",
            "estado": "listo" if productos and proveedores else "pendiente",
            "progreso": min(100, (50 if productos else 0) + (50 if proveedores else 0)),
            "metricas": [("Productos", productos), ("Proveedores", proveedores)],
            "acciones": [
                ("Productos", _setup_url("productos.productos")),
                ("Proveedores", _setup_url("proveedores.proveedores")),
            ],
        },
        {
            "bloque": "Calidad",
            "titulo": "APPCC, ISO y trazabilidad",
            "detalle": "Equipos, planes de limpieza, evidencias y registros auditables.",
            "icono": "fa-shield-alt",
            "estado": "listo" if appcc_equipos and appcc_limpieza else "pendiente",
            "progreso": min(100, (50 if appcc_equipos else 0) + (50 if appcc_limpieza else 0)),
            "metricas": [("Equipos APPCC", appcc_equipos), ("Planes limpieza", appcc_limpieza)],
            "acciones": [
                ("APPCC", _setup_url("appcc.appcc_dashboard")),
                ("Trazabilidad", _setup_url("lotes.lista_lotes")),
            ],
        },
    ]


# =========================
# RUTAS GENERALES
# =========================
@app.route("/configuracion-inicial")
@login_required
def configuracion_inicial():
    if not (usuario_tiene_permiso("gestionar_estructura") or usuario_tiene_permiso("acceso_global")):
        flash("No tienes permisos para configurar el establecimiento.", "warning")
        return redirect(url_for("home"))
    conn = get_db()
    empresa_id = session.get("user_empresa_id", 1)
    items = _configuracion_inicial_items(conn, empresa_id)
    conn.commit()
    progreso = round(sum(item["progreso"] for item in items) / len(items)) if items else 0
    pendientes = sum(1 for item in items if item["estado"] != "listo")
    conn.close()
    return render_template(
        "configuracion_inicial.html",
        items=items,
        progreso=progreso,
        pendientes=pendientes,
    )


@app.route("/configuracion-inicial/ia", methods=["GET", "POST"])
@login_required
def configuracion_inicial_ia():
    if not (usuario_tiene_permiso("gestionar_estructura") or usuario_tiene_permiso("acceso_global")):
        flash("No tienes permisos para generar establecimientos.", "warning")
        return redirect(url_for("home"))

    plan = None
    form_data = {}
    if request.method == "POST":
        form_data = request.form.to_dict()
        plan = construir_plan_centro_ia(request.form)
        if request.form.get("accion") == "aplicar":
            conn = get_db()
            empresa_id = session.get("user_empresa_id", 1) or 1
            centro_id = aplicar_plan_centro_ia(conn, empresa_id, plan)
            session["centro_activo_id"] = centro_id
            conn.close()
            flash("Centro generado con estructura, APPCC, limpieza, almacenes y puntos de venta.", "success")
            return redirect(url_for("configuracion_inicial"))

    conn = get_db()
    ensure_centro_ia_schema(conn)
    conn.commit()
    conn.close()
    return render_template("configuracion_inicial_ia.html", plan=plan, form_data=form_data)


@app.route("/establecimiento/estructura", methods=["GET", "POST"])
@login_required
def estructura_establecimiento():
    if not (usuario_tiene_permiso("gestionar_estructura") or usuario_tiene_permiso("acceso_global")):
        flash("No tienes permisos para modificar la estructura del establecimiento.", "warning")
        return redirect(url_for("home"))

    conn = get_db()
    empresa_id = session.get("user_empresa_id", 1)
    _asegurar_estructura_schema(conn, empresa_id)
    conn.commit()
    centro_id_activo = centro_activo_id()

    if request.method == "POST":
        accion = request.form.get("accion") or ""
        if accion == "crear_bloque":
            nombre = (request.form.get("nombre") or "").strip()
            if not nombre:
                flash("El nombre del bloque es obligatorio.", "warning")
            else:
                codigo = _estructura_codigo(request.form.get("codigo") or nombre, "BLOQUE")
                conn.execute(
                    """
                    INSERT OR IGNORE INTO establecimiento_bloques
                    (codigo, nombre, descripcion, centro_id, activo, empresa_id)
                    VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (
                        codigo,
                        nombre,
                        request.form.get("descripcion") or "",
                        centro_id_activo or request.form.get("centro_id") or None,
                        empresa_id,
                    ),
                )
                conn.commit()
                flash("Bloque creado o revisado.", "success")
        elif accion == "crear_planta":
            bloque_id = request.form.get("bloque_id") or None
            numero = (request.form.get("numero") or "").strip()
            if not bloque_id or not numero:
                flash("Selecciona bloque y numero de planta.", "warning")
            else:
                nombre = (request.form.get("nombre") or "").strip() or ("Planta baja" if numero == "0" else f"Planta {numero}")
                conn.execute(
                    """
                    INSERT OR IGNORE INTO establecimiento_plantas
                    (bloque_id, numero, nombre, descripcion, activo, empresa_id, centro_id)
                    VALUES (?, ?, ?, ?, 1, ?, (SELECT centro_id FROM establecimiento_bloques WHERE id = ? AND empresa_id = ?))
                    """,
                    (bloque_id, numero, nombre, request.form.get("descripcion") or "", empresa_id, bloque_id, empresa_id),
                )
                conn.commit()
                flash("Planta creada o revisada.", "success")
        elif accion == "crear_zona":
            nombre = (request.form.get("nombre") or "").strip()
            bloque_id = request.form.get("bloque_id") or None
            planta_id = request.form.get("planta_id") or None
            if not nombre:
                flash("El nombre de la zona es obligatorio.", "warning")
            else:
                bloque_row = conn.execute(
                    "SELECT nombre FROM establecimiento_bloques WHERE id = ? AND empresa_id = ?",
                    (bloque_id, empresa_id),
                ).fetchone() if bloque_id else None
                planta_row = conn.execute(
                    "SELECT numero FROM establecimiento_plantas WHERE id = ? AND empresa_id = ?",
                    (planta_id, empresa_id),
                ).fetchone() if planta_id else None
                bloque_txt = bloque_row["nombre"] if bloque_row else ""
                planta_txt = planta_row["numero"] if planta_row else ""
                tipo = request.form.get("tipo") or "zona_comun"
                frecuencia = request.form.get("frecuencia_limpieza") or ""
                appcc = 1 if request.form.get("appcc_critico") else 0
                departamento_id = request.form.get("departamento_responsable_id") or None
                conn.execute(
                    """
                    INSERT INTO establecimiento_zonas
                    (nombre, tipo, bloque_id, planta_id, bloque, planta, departamento_responsable_id,
                     frecuencia_limpieza, appcc_critico, activo, empresa_id, centro_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (nombre, tipo, bloque_id, planta_id, bloque_txt, planta_txt, departamento_id, frecuencia, appcc, empresa_id, centro_id_activo),
                )
                if _app_table_exists(conn, "pisos_zonas"):
                    existe = conn.execute(
                        """
                        SELECT id FROM pisos_zonas
                        WHERE empresa_id = ? AND nombre = ?
                          AND COALESCE(bloque, '') = COALESCE(?, '')
                          AND COALESCE(planta, '') = COALESCE(?, '')
                        LIMIT 1
                        """,
                        (empresa_id, nombre, bloque_txt, planta_txt),
                    ).fetchone()
                    if not existe:
                        conn.execute(
                            """
                            INSERT INTO pisos_zonas
                            (nombre, tipo, bloque, planta, frecuencia, prioridad, productos_previstos,
                             instrucciones, appcc_critico, activo, empresa_id, bloque_id, planta_id, departamento_responsable_id)
                            VALUES (?, ?, ?, ?, ?, 'media', '', '', ?, 1, ?, ?, ?, ?)
                            """,
                            (nombre, tipo, bloque_txt, planta_txt, frecuencia, appcc, empresa_id, bloque_id, planta_id, departamento_id),
                        )
                conn.commit()
                flash("Zona creada y enlazada con pisos.", "success")
        elif accion == "sincronizar":
            _sincronizar_estructura_desde_datos(conn, empresa_id)
            conn.commit()
            flash("Estructura sincronizada con habitaciones y zonas existentes.", "success")
        return redirect(url_for("estructura_establecimiento"))

    if _app_table_exists(conn, "centros_trabajo"):
        if centro_id_activo:
            centros = conn.execute(
                "SELECT id, nombre FROM centros_trabajo WHERE empresa_id = ? AND id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
                (empresa_id, centro_id_activo),
            ).fetchall()
        else:
            centros = conn.execute(
                "SELECT id, nombre FROM centros_trabajo WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
                (empresa_id,),
            ).fetchall()
    else:
        centros = []
    dep_centro_sql = " AND centro_id = ?" if centro_id_activo else ""
    dep_centro_params = [centro_id_activo] if centro_id_activo else []
    departamentos = conn.execute(
        "SELECT id, nombre FROM departamentos WHERE empresa_id = ? AND COALESCE(activo, 1) = 1"
        + dep_centro_sql +
        " ORDER BY nombre",
        [empresa_id] + dep_centro_params,
    ).fetchall() if _app_table_exists(conn, "departamentos") else []
    bloque_centro_sql = " AND b.centro_id = ?" if centro_id_activo else ""
    bloque_centro_params = [centro_id_activo] if centro_id_activo else []
    bloques = conn.execute(
        """
        SELECT b.*, c.nombre AS centro_nombre,
               (SELECT COUNT(*) FROM establecimiento_plantas p WHERE p.bloque_id = b.id AND p.empresa_id = b.empresa_id AND COALESCE(p.activo, 1) = 1) AS total_plantas,
               (SELECT COUNT(*) FROM habitaciones_recepcion h WHERE h.bloque_id = b.id AND h.empresa_id = b.empresa_id AND COALESCE(h.activo, 1) = 1) AS total_habitaciones
        FROM establecimiento_bloques b
        LEFT JOIN centros_trabajo c ON c.id = b.centro_id
        WHERE b.empresa_id = ?
        """ + bloque_centro_sql + """
        ORDER BY b.activo DESC, b.nombre
        """,
        [empresa_id] + bloque_centro_params,
    ).fetchall()
    planta_centro_sql = " AND p.centro_id = ?" if centro_id_activo else ""
    planta_centro_params = [centro_id_activo] if centro_id_activo else []
    plantas = conn.execute(
        """
        SELECT p.*, b.nombre AS bloque_nombre,
               (SELECT COUNT(*) FROM habitaciones_recepcion h WHERE h.planta_id = p.id AND h.empresa_id = p.empresa_id AND COALESCE(h.activo, 1) = 1) AS total_habitaciones
        FROM establecimiento_plantas p
        LEFT JOIN establecimiento_bloques b ON b.id = p.bloque_id
        WHERE p.empresa_id = ?
        """ + planta_centro_sql + """
        ORDER BY b.nombre, CAST(p.numero AS INTEGER), p.numero
        """,
        [empresa_id] + planta_centro_params,
    ).fetchall()
    zona_centro_sql = " AND z.centro_id = ?" if centro_id_activo else ""
    zona_centro_params = [centro_id_activo] if centro_id_activo else []
    zonas = conn.execute(
        """
        SELECT z.*, b.nombre AS bloque_nombre, p.nombre AS planta_nombre, d.nombre AS departamento_nombre
        FROM establecimiento_zonas z
        LEFT JOIN establecimiento_bloques b ON b.id = z.bloque_id
        LEFT JOIN establecimiento_plantas p ON p.id = z.planta_id
        LEFT JOIN departamentos d ON d.id = z.departamento_responsable_id
        WHERE z.empresa_id = ?
        """ + zona_centro_sql + """
        ORDER BY z.activo DESC, b.nombre, p.numero, z.nombre
        """,
        [empresa_id] + zona_centro_params,
    ).fetchall()
    conn.close()
    return render_template(
        "establecimiento_estructura.html",
        centros=centros,
        departamentos=departamentos,
        bloques=bloques,
        plantas=plantas,
        zonas=zonas,
    )


@app.route("/")
@login_required
def home():
    if (session.get("user_rol") or "").lower() in {"kiosco", "tablet", "fichaje"}:
        return redirect(url_for("rrhh.kiosco_fichaje"))
    if (session.get("user_rol") or "").lower() == "empleado":
        return redirect(url_for("rrhh.portal_empleado"))

    if not usuario_tiene_permiso("ver_inicio_ejecutivo"):
        return render_template(
            "home_operativo.html",
            modulos=_modulos_operativos_usuario(),
            rol=session.get("user_rol") or "",
            nombre=session.get("user_nombre") or "",
        )

    conn = get_db()
    empresa_id = session.get("user_empresa_id", 1)

    # ============================================
    # 1. ALERTAS GLOBALES
    # ============================================
    stock_bajo = conn.execute("""
        SELECT COUNT(*) as total
        FROM productos p
        LEFT JOIN stock_almacen s ON p.id = s.producto_id
        WHERE p.empresa_id = ? AND p.activo = 1
        GROUP BY p.id
        HAVING COALESCE(SUM(s.cantidad), 0) <= p.stock_minimo
           AND COALESCE(SUM(s.cantidad), 0) > 0
    """, (empresa_id,)).fetchall()
    total_stock_bajo = len(stock_bajo)

    agotados = conn.execute("""
        SELECT COUNT(*) as total
        FROM productos p
        LEFT JOIN stock_almacen s ON p.id = s.producto_id
        WHERE p.empresa_id = ? AND p.activo = 1
        GROUP BY p.id
        HAVING COALESCE(SUM(s.cantidad), 0) <= 0
    """, (empresa_id,)).fetchall()
    total_agotados = len(agotados)

    incidencias_abiertas = conn.execute("""
        SELECT COUNT(*) as total
        FROM incidencias
        WHERE empresa_id = ? AND estado IN ('abierta', 'en_proceso')
    """, (empresa_id,)).fetchone()["total"]

    texto_descongelado = "LOWER(COALESCE(p.familia, '') || ' ' || COALESCE(p.nombre, ''))"
    filtro_descongelado = f"""
        LOWER(COALESCE(p.tipo_conservacion, '')) LIKE '%congel%'
        AND (
            LOWER(COALESCE(p.familia, '')) IN ('carnes', 'pescados', 'mariscos', 'moluscos', 'cefalopodos')
            OR
            {texto_descongelado} LIKE '%carne%'
            OR {texto_descongelado} LIKE '%pollo%'
            OR {texto_descongelado} LIKE '%pavo%'
            OR {texto_descongelado} LIKE '%cerdo%'
            OR {texto_descongelado} LIKE '%ternera%'
            OR {texto_descongelado} LIKE '%vacuno%'
            OR {texto_descongelado} LIKE '%cordero%'
            OR {texto_descongelado} LIKE '%pescado%'
            OR {texto_descongelado} LIKE '%merluza%'
            OR {texto_descongelado} LIKE '%atun%'
            OR {texto_descongelado} LIKE '%atún%'
            OR {texto_descongelado} LIKE '%bacalao%'
            OR {texto_descongelado} LIKE '%salmon%'
            OR {texto_descongelado} LIKE '%salmón%'
            OR {texto_descongelado} LIKE '%lubina%'
            OR {texto_descongelado} LIKE '%dorada%'
            OR {texto_descongelado} LIKE '%rape%'
            OR {texto_descongelado} LIKE '%rodaballo%'
            OR {texto_descongelado} LIKE '%lenguado%'
            OR {texto_descongelado} LIKE '%gallo%'
            OR {texto_descongelado} LIKE '%corvina%'
            OR {texto_descongelado} LIKE '%marisco%'
            OR {texto_descongelado} LIKE '%gamba%'
            OR {texto_descongelado} LIKE '%langostino%'
            OR {texto_descongelado} LIKE '%calamar%'
            OR {texto_descongelado} LIKE '%sepia%'
            OR {texto_descongelado} LIKE '%pulpo%'
        )
    """
    alertas_descongelado = conn.execute(f"""
        SELECT COUNT(*) as total
        FROM alertas_programadas a
        JOIN lotes l ON a.lote_id = l.id
        JOIN productos p ON l.producto_id = p.id
        WHERE a.empresa_id = ?
          AND COALESCE(a.enviada, 0) = 0
          AND COALESCE(a.resuelta, 0) = 0
          AND a.tipo = 'descongelado'
          AND a.fecha_alerta <= datetime('now', '+7 days')
          AND {filtro_descongelado}
    """, (empresa_id,)).fetchone()["total"] or 0

    caducidad_proxima = conn.execute("""
        SELECT COUNT(*) as total
        FROM lotes
        WHERE empresa_id = ?
          AND estado = 'activo'
          AND fecha_caducidad IS NOT NULL
          AND julianday(fecha_caducidad) - julianday('now') <= 7
          AND julianday(fecha_caducidad) - julianday('now') >= 0
    """, (empresa_id,)).fetchone()["total"] or 0

    # ============================================
    # 2. ESTADÍSTICAS RÁPIDAS
    # ============================================
    total_productos = conn.execute("""
        SELECT COUNT(*) as total
        FROM productos
        WHERE empresa_id = ? AND activo = 1
    """, (empresa_id,)).fetchone()["total"]

    total_recetas = conn.execute("""
        SELECT COUNT(*) as total
        FROM recetas
        WHERE empresa_id = ?
    """, (empresa_id,)).fetchone()["total"]

    total_proveedores = conn.execute("""
        SELECT COUNT(*) as total
        FROM proveedores
        WHERE empresa_id = ? AND activo = 1
    """, (empresa_id,)).fetchone()["total"]

    partes_hoy = conn.execute("""
        SELECT COUNT(*) as total
        FROM partes_produccion
        WHERE empresa_id = ? AND fecha = date('now')
    """, (empresa_id,)).fetchone()["total"]

    valor_stock = conn.execute("""
        SELECT SUM(p.cantidad * p.precio) as total
        FROM productos p
        WHERE p.empresa_id = ? AND p.activo = 1
    """, (empresa_id,)).fetchone()["total"] or 0

    total_habitaciones = _dashboard_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM habitaciones_recepcion
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        """,
        (empresa_id,),
    )
    habitaciones_ocupadas = _dashboard_scalar(
        conn,
        """
        SELECT COUNT(DISTINCT habitacion_id)
        FROM reservas_recepcion
        WHERE empresa_id = ?
          AND habitacion_id IS NOT NULL
          AND date(fecha_entrada) <= date('now')
          AND date(fecha_salida) > date('now')
          AND LOWER(COALESCE(estado, '')) NOT IN ('cancelada', 'cancelado', 'no_show', 'no-show')
        """,
        (empresa_id,),
    )
    ocupacion_pct = round((habitaciones_ocupadas / total_habitaciones) * 100, 1) if total_habitaciones else 0
    entradas_hoy = _dashboard_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM reservas_recepcion
        WHERE empresa_id = ? AND date(fecha_entrada) = date('now')
          AND LOWER(COALESCE(estado, '')) NOT IN ('cancelada', 'cancelado', 'no_show', 'no-show')
        """,
        (empresa_id,),
    )
    salidas_hoy = _dashboard_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM reservas_recepcion
        WHERE empresa_id = ? AND date(fecha_salida) = date('now')
          AND LOWER(COALESCE(estado, '')) NOT IN ('cancelada', 'cancelado', 'no_show', 'no-show')
        """,
        (empresa_id,),
    )
    facturacion_hoy = _dashboard_scalar(
        conn,
        """
        SELECT COALESCE(SUM(total), 0)
        FROM recepcion_facturas
        WHERE empresa_id = ? AND date(fecha) = date('now')
          AND LOWER(COALESCE(estado, '')) NOT IN ('anulada', 'anulado', 'rectificada')
        """,
        (empresa_id,),
    )
    facturacion_mes = _dashboard_scalar(
        conn,
        """
        SELECT COALESCE(SUM(total), 0)
        FROM recepcion_facturas
        WHERE empresa_id = ? AND date(fecha) >= date('now', 'start of month')
          AND LOWER(COALESCE(estado, '')) NOT IN ('anulada', 'anulado', 'rectificada')
        """,
        (empresa_id,),
    )
    tpv_hoy = _dashboard_scalar(
        conn,
        """
        SELECT COALESCE(SUM(total), 0)
        FROM tpv_cuentas
        WHERE empresa_id = ? AND date(COALESCE(fecha_cierre, fecha_apertura)) = date('now')
          AND LOWER(COALESCE(estado, '')) IN ('cerrada', 'cobrada', 'pagada')
        """,
        (empresa_id,),
    )
    trabajos_pisos_pendientes = _dashboard_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM pisos_trabajos
        WHERE empresa_id = ? AND date(fecha) = date('now')
          AND LOWER(COALESCE(estado, 'pendiente')) IN ('pendiente', 'en_curso')
        """,
        (empresa_id,),
    )
    ordenes_mantenimiento_abiertas = _dashboard_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM ordenes_trabajo
        WHERE empresa_id = ? AND LOWER(COALESCE(estado, '')) IN ('abierta', 'asignada', 'en_proceso')
        """,
        (empresa_id,),
    )
    pedidos_pendientes = _dashboard_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM pedidos
        WHERE empresa_id = ?
          AND LOWER(COALESCE(estado, '')) IN ('borrador', 'pendiente', 'solicitado', 'aprobado', 'enviado')
          AND COALESCE(cerrado, 0) = 0
        """,
        (empresa_id,),
    )
    empleados_activos = _dashboard_scalar(
        conn,
        "SELECT COUNT(*) FROM empleados WHERE empresa_id = ? AND COALESCE(activo, 1) = 1",
        (empresa_id,),
    )
    fichajes_hoy = _dashboard_scalar(
        conn,
        """
        SELECT COUNT(DISTINCT empleado_id)
        FROM fichajes
        WHERE empresa_id = ? AND date(fecha_hora) = date('now')
        """,
        (empresa_id,),
    )
    solicitudes_rrhh_pendientes = _dashboard_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM vacaciones_rrhh
        WHERE empresa_id = ? AND LOWER(COALESCE(estado, 'solicitada')) IN ('solicitada', 'pendiente')
        """,
        (empresa_id,),
    )

    # ============================================
    # 3. DATOS PARA GRÁFICOS
    # ============================================
    stock_familias = conn.execute("""
        SELECT
            p.familia,
            COUNT(*) as num_productos,
            SUM(p.cantidad) as total_stock,
            SUM(p.cantidad * p.precio) as valor_total
        FROM productos p
        WHERE p.empresa_id = ? AND p.activo = 1
        GROUP BY p.familia
        ORDER BY valor_total DESC
        LIMIT 8
    """, (empresa_id,)).fetchall()

    produccion_7dias = conn.execute("""
        SELECT
            date(pp.fecha) as dia,
            COUNT(DISTINCT pp.id) as num_partes,
            COUNT(lp.id) as num_lineas
        FROM partes_produccion pp
        LEFT JOIN lineas_produccion lp ON pp.id = lp.parte_id
        WHERE pp.empresa_id = ?
          AND pp.fecha IS NOT NULL
          AND pp.fecha >= date('now', '-6 days')
        GROUP BY date(pp.fecha)
        ORDER BY dia
    """, (empresa_id,)).fetchall()

    incidencias_tipo = conn.execute("""
        SELECT
            tipo,
            COUNT(*) as total
        FROM incidencias
        WHERE empresa_id = ?
        GROUP BY tipo
        ORDER BY total DESC
    """, (empresa_id,)).fetchall()

    # ============================================
    # 4. ACTIVIDAD RECIENTE
    # ============================================
    ultimas_incidencias = conn.execute("""
        SELECT *
        FROM incidencias
        WHERE empresa_id = ?
        ORDER BY fecha DESC
        LIMIT 5
    """, (empresa_id,)).fetchall()

    ultimos_partes = conn.execute("""
        SELECT pp.*, COUNT(lp.id) as num_lineas
        FROM partes_produccion pp
        LEFT JOIN lineas_produccion lp ON pp.id = lp.parte_id
        WHERE pp.empresa_id = ?
        GROUP BY pp.id
        ORDER BY pp.fecha DESC, pp.id DESC
        LIMIT 5
    """, (empresa_id,)).fetchall()

    ultimos_pedidos = conn.execute("""
        SELECT p.*, pr.nombre as proveedor_nombre
        FROM pedidos p
        JOIN proveedores pr ON p.proveedor_id = pr.id
        WHERE p.empresa_id = ?
        ORDER BY p.fecha_pedido DESC
        LIMIT 5
    """, (empresa_id,)).fetchall()

    conn.close()

    # ============================================
    # 5. CONVERTIR ROWS A DICTS
    # ============================================
    stock_familias = rows_to_dicts(stock_familias)
    produccion_7dias = rows_to_dicts(produccion_7dias)
    incidencias_tipo = rows_to_dicts(incidencias_tipo)

    # Estas no siempre necesitan tojson, pero las dejamos limpias
    ultimas_incidencias = rows_to_dicts(ultimas_incidencias)
    ultimos_partes = rows_to_dicts(ultimos_partes)
    ultimos_pedidos = rows_to_dicts(ultimos_pedidos)

    return render_template(
        "home.html",
        total_stock_bajo=total_stock_bajo,
        total_agotados=total_agotados,
        incidencias_abiertas=incidencias_abiertas,
        alertas_descongelado=alertas_descongelado,
        caducidad_proxima=caducidad_proxima,
        total_productos=total_productos,
        total_recetas=total_recetas,
        total_proveedores=total_proveedores,
        partes_hoy=partes_hoy,
        valor_stock=valor_stock,
        total_habitaciones=total_habitaciones,
        habitaciones_ocupadas=habitaciones_ocupadas,
        ocupacion_pct=ocupacion_pct,
        entradas_hoy=entradas_hoy,
        salidas_hoy=salidas_hoy,
        facturacion_hoy=facturacion_hoy,
        facturacion_mes=facturacion_mes,
        tpv_hoy=tpv_hoy,
        trabajos_pisos_pendientes=trabajos_pisos_pendientes,
        ordenes_mantenimiento_abiertas=ordenes_mantenimiento_abiertas,
        pedidos_pendientes=pedidos_pendientes,
        empleados_activos=empleados_activos,
        fichajes_hoy=fichajes_hoy,
        solicitudes_rrhh_pendientes=solicitudes_rrhh_pendientes,
        fecha_panel=datetime.now().strftime("%d/%m/%Y"),
        stock_familias=stock_familias,
        produccion_7dias=produccion_7dias,
        incidencias_tipo=incidencias_tipo,
        ultimas_incidencias=ultimas_incidencias,
        ultimos_partes=ultimos_partes,
        ultimos_pedidos=ultimos_pedidos
    )

# =========================
# INICIAR SERVIDOR
# =========================
if __name__ == "__main__":
    # init_db()
    app.run(
        host=os.getenv("ERP_HOST", "0.0.0.0"),
        port=int(os.getenv("ERP_PORT", os.getenv("PORT", "5001"))),
        debug=False,
        use_reloader=False,
        threaded=True
    )
