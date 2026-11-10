from __future__ import annotations

import json
import os
from datetime import datetime
from urllib.parse import urlparse

from flask import flash, redirect, render_template, request, session, url_for

from blueprints.auth.routes import login_required, usuario_tiene_permiso
from database import get_db
from helpers.centros_helper import centro_activo_id
from helpers.email_helper import enviar_email
from integraciones.pagos_marca_blanca.security import SecretError, crypto_available, generate_master_key
from integraciones.pagos_marca_blanca.service import PaymentService
from . import pagos_bp


PROVEEDORES = [
    ("simulado", "Simulado / laboratorio"),
    ("redsys", "Redsys / TPV banco"),
    ("stripe", "Stripe / Terminal / online"),
    ("adyen", "Adyen / cadena hotelera"),
]

METODOS = [
    ("tarjeta", "Tarjeta"),
    ("bizum", "Bizum"),
    ("apple_pay", "Apple Pay"),
    ("google_pay", "Google Pay"),
    ("samsung_pay", "Samsung Pay"),
    ("nfc", "NFC/contactless"),
    ("link", "Link de pago"),
    ("preautorizacion", "Preautorizacion"),
]


def _puede_ver():
    return any(usuario_tiene_permiso(p) for p in ("pagos_ver", "pagos_configurar", "pagos_operar", "contabilidad_ver", "recepcion_facturacion", "tpv_gestionar", "acceso_global"))


def _puede_configurar():
    return any(usuario_tiene_permiso(p) for p in ("pagos_configurar", "contabilidad_gestionar", "gestionar_empresas", "acceso_global"))


def _service(conn=None) -> PaymentService:
    own = conn is None
    if own:
        conn = get_db()
    return PaymentService(
        conn,
        empresa_id=session.get("user_empresa_id", 1),
        centro_id=centro_activo_id(),
        usuario=session.get("user_email") or session.get("user_nombre") or "usuario",
    )


@pagos_bp.before_request
def _bloquear_sin_permiso():
    if request.endpoint in {"pagos.webhook", "pagos.checkout_simulado"}:
        return None
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    if _puede_ver():
        return None
    flash("No tienes permisos para acceder a pagos.", "warning")
    return redirect(url_for("home"))


def _parse_json_text(value, default=None):
    if default is None:
        default = {}
    value = (value or "").strip()
    if not value:
        return default
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else default
    except Exception:
        return default


def _metodos_form():
    return {code: bool(request.form.get(f"metodo_{code}")) for code, _label in METODOS}


def _public_base_url() -> str:
    configured = (
        os.environ.get("PAGOS_PUBLIC_BASE_URL")
        or os.environ.get("ERP_PUBLIC_URL")
        or os.environ.get("APP_PUBLIC_URL")
        or ""
    ).strip()
    if configured:
        return configured.rstrip("/")
    return request.url_root.rstrip("/")


def _absolute_payment_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if not url.startswith("/"):
        url = "/" + url
    return _public_base_url() + url


def _public_url_diagnostics() -> dict:
    base_url = _public_base_url()
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    warnings = []
    issues = []
    if not parsed.scheme or not parsed.netloc:
        issues.append("La URL publica no parece completa. Usa formato http://ip:puerto o https://dominio.")
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local") or host == "local":
        issues.append("La URL publica apunta a local. Los clientes no podran abrir enlaces desde su movil o correo.")
    if parsed.scheme == "http" and os.environ.get("ERP_ENV", "").lower() in {"prod", "produccion", "production"}:
        warnings.append("En produccion conviene usar HTTPS para enlaces de pago.")
    if parsed.scheme == "http" and host not in {"localhost", "127.0.0.1"}:
        warnings.append("HTTP vale para laboratorio/red interna. En real, preparar HTTPS antes de cobrar dinero.")
    status = "ok"
    if issues:
        status = "bloqueante"
    elif warnings:
        status = "revisar"
    return {
        "status": status,
        "base_url": base_url,
        "sample_url": _absolute_payment_url("/pagos/checkout-simulado/EJEMPLO"),
        "issues": issues,
        "warnings": warnings,
    }


@pagos_bp.route("/")
@login_required
def dashboard():
    conn = get_db()
    try:
        service = _service(conn)
        service.seed_default_templates()
        conn.commit()
        configs = service.list_configs()
        operaciones = service.list_operations(limit=30)
        terminales = service.list_terminals()
        return render_template(
            "pagos/dashboard.html",
            configs=configs,
            operaciones=operaciones,
            terminales=terminales,
            crypto_ok=crypto_available(),
            master_key_sugerida=generate_master_key() if not crypto_available() else "",
            puede_configurar=_puede_configurar(),
            public_url_diagnostics=_public_url_diagnostics(),
        )
    finally:
        conn.close()


@pagos_bp.route("/plantillas", methods=["GET", "POST"])
@login_required
def plantillas():
    if not _puede_configurar():
        flash("No tienes permisos para configurar plantillas de pago.", "warning")
        return redirect(url_for("pagos.dashboard"))
    conn = get_db()
    try:
        service = _service(conn)
        service.seed_default_templates()
        edit_id = int(request.args.get("edit") or 0)
        plantilla = service.get_template(edit_id) if edit_id else None
        if request.method == "POST":
            try:
                service.save_template(
                    template_id=int(request.form.get("template_id") or 0) or None,
                    nombre=request.form.get("nombre", "").strip() or "Plantilla",
                    canal=request.form.get("canal", "email").strip() or "email",
                    tipo_operacion=request.form.get("tipo_operacion", "payment").strip() or "payment",
                    idioma=request.form.get("idioma", "es").strip() or "es",
                    asunto=request.form.get("asunto", "").strip(),
                    cuerpo_html=request.form.get("cuerpo_html", "").strip(),
                    cuerpo_texto=request.form.get("cuerpo_texto", "").strip(),
                    activo=1 if request.form.get("activo") else 0,
                    por_defecto=1 if request.form.get("por_defecto") else 0,
                )
                conn.commit()
                flash("Plantilla guardada.", "success")
                return redirect(url_for("pagos.plantillas"))
            except Exception as exc:
                conn.rollback()
                flash(f"No se pudo guardar la plantilla: {exc}", "danger")
        conn.commit()
        return render_template(
            "pagos/plantillas.html",
            plantillas=service.list_templates(include_inactive=True),
            plantilla=plantilla,
            variables=["cliente_nombre", "cliente_email", "importe", "moneda", "descripcion", "referencia", "checkout_url", "marca_nombre", "fecha_expiracion"],
        )
    finally:
        conn.close()


@pagos_bp.route("/config/nueva", methods=["GET", "POST"])
@pagos_bp.route("/config/<int:config_id>", methods=["GET", "POST"])
@login_required
def config_form(config_id=None):
    if not _puede_configurar():
        flash("No tienes permisos para configurar pasarelas.", "warning")
        return redirect(url_for("pagos.dashboard"))
    conn = get_db()
    try:
        service = _service(conn)
        config = service.get_config(config_id) if config_id else None
        if request.method == "POST":
            secretos = {}
            for key in ("api_key", "secret_key", "webhook_secret", "merchant_code", "terminal", "merchant_account"):
                value = request.form.get(f"secret_{key}", "").strip()
                if value:
                    secretos[key] = value
            try:
                saved_id = service.save_config(
                    config_id=config_id,
                    nombre=request.form.get("nombre", "").strip() or "Pasarela",
                    proveedor=request.form.get("proveedor", "simulado"),
                    modo=request.form.get("modo", "test"),
                    activo=1 if request.form.get("activo") else 0,
                    marca_nombre=request.form.get("marca_nombre", "").strip(),
                    marca_logo=request.form.get("marca_logo", "").strip(),
                    marca_color=request.form.get("marca_color", "").strip() or "#ff6b00",
                    moneda=request.form.get("moneda", "EUR").strip() or "EUR",
                    metodos=_metodos_form(),
                    config_publica=_parse_json_text(request.form.get("config_publica_json")),
                    secretos=secretos,
                )
                conn.commit()
                flash("Pasarela guardada. Las claves no se muestran de nuevo.", "success")
                return redirect(url_for("pagos.config_form", config_id=saved_id))
            except SecretError as exc:
                conn.rollback()
                flash(str(exc), "danger")
            except Exception as exc:
                conn.rollback()
                flash(f"No se pudo guardar la pasarela: {exc}", "danger")
        return render_template(
            "pagos/config_form.html",
            config=config,
            proveedores=PROVEEDORES,
            metodos=METODOS,
            crypto_ok=crypto_available(),
            master_key_sugerida=generate_master_key() if not crypto_available() else "",
        )
    finally:
        conn.close()


@pagos_bp.route("/terminales", methods=["GET", "POST"])
@login_required
def terminales():
    if not _puede_configurar():
        flash("No tienes permisos para configurar terminales.", "warning")
        return redirect(url_for("pagos.dashboard"))
    conn = get_db()
    try:
        service = _service(conn)
        if request.method == "POST":
            try:
                service.save_terminal(
                    config_id=int(request.form.get("config_id") or 0),
                    nombre=request.form.get("nombre", "").strip() or "Terminal",
                    proveedor_terminal_id=request.form.get("proveedor_terminal_id", "").strip(),
                    ubicacion=request.form.get("ubicacion", "").strip(),
                    nfc=1 if request.form.get("nfc") else 0,
                    tap_to_pay=1 if request.form.get("tap_to_pay") else 0,
                    activo=1 if request.form.get("activo") else 0,
                )
                conn.commit()
                flash("Terminal guardado.", "success")
                return redirect(url_for("pagos.terminales"))
            except Exception as exc:
                conn.rollback()
                flash(f"No se pudo guardar el terminal: {exc}", "danger")
        return render_template("pagos/terminales.html", configs=service.list_configs(), terminales=service.list_terminals())
    finally:
        conn.close()


@pagos_bp.route("/operaciones")
@login_required
def operaciones():
    conn = get_db()
    try:
        service = _service(conn)
        return render_template("pagos/operaciones.html", operaciones=service.list_operations(limit=200))
    finally:
        conn.close()


@pagos_bp.route("/operaciones/<int:operation_id>")
@login_required
def operacion_detalle(operation_id):
    conn = get_db()
    try:
        service = _service(conn)
        operacion = service.get_operation(operation_id)
        if not operacion:
            flash("Operacion no encontrada.", "warning")
            return redirect(url_for("pagos.operaciones"))
        service.seed_default_templates()
        conn.commit()
        sync = None
        if request.args.get("sync") == "1":
            sync = service.sync_confirmed_operation(operation_id)
            conn.commit()
        return render_template(
            "pagos/operacion_detalle.html",
            operacion=service.get_operation(operation_id),
            comunicaciones=service.list_communications(operation_id),
            plantillas=service.list_templates(),
            checkout_url_publica=_absolute_payment_url((service.get_operation(operation_id) or {}).get("checkout_url") or ""),
            sync=sync,
        )
    finally:
        conn.close()


@pagos_bp.route("/operaciones/<int:operation_id>/enviar-link", methods=["POST"])
@login_required
def enviar_link(operation_id):
    conn = get_db()
    try:
        service = _service(conn)
        operacion = service.get_operation(operation_id)
        if not operacion:
            flash("Operacion no encontrada.", "warning")
            return redirect(url_for("pagos.operaciones"))
        destinatario = (request.form.get("destinatario") or operacion.get("cliente_email") or "").strip()
        if not destinatario:
            flash("Indica un email de destino.", "warning")
            return redirect(url_for("pagos.operacion_detalle", operation_id=operation_id))
        checkout_url = _absolute_payment_url(operacion.get("checkout_url") or "")
        plantilla_id = int(request.form.get("plantilla_id") or 0)
        plantilla = service.get_template(plantilla_id) if plantilla_id else None
        if not plantilla:
            service.seed_default_templates()
            tipo_operacion = operacion.get("tipo_operacion") or "payment"
            plantilla = service.default_template(canal="email", tipo_operacion=tipo_operacion, idioma="es")
            if not plantilla and tipo_operacion != "payment":
                plantilla = service.default_template(canal="email", tipo_operacion="payment", idioma="es")
            if not plantilla:
                disponibles = service.list_templates()
                plantilla = disponibles[0] if disponibles else None
        if not plantilla:
            flash("No hay plantilla activa para enviar el link.", "warning")
            return redirect(url_for("pagos.operacion_detalle", operation_id=operation_id))
        rendered = service.render_template(plantilla, operacion, checkout_url)
        asunto = request.form.get("asunto") or rendered["asunto"] or f"Link de pago {operacion.get('referencia_interna')}"
        cuerpo = request.form.get("cuerpo") or rendered["cuerpo_html"] or rendered["cuerpo_texto"]
        try:
            from app import mail

            ok, resultado = enviar_email(destinatario, asunto, cuerpo, html=True, mail_instance=mail)
        except Exception as exc:
            ok, resultado = False, str(exc)
        service.record_communication(
            operation_id,
            canal="email",
            destinatario=destinatario,
            asunto=asunto,
            cuerpo=cuerpo,
            resultado="enviado" if ok else "error",
            error="" if ok else resultado,
        )
        conn.commit()
        if ok:
            flash("Link de pago enviado por email.", "success")
        else:
            flash(f"No se pudo enviar email: {resultado}", "danger")
        return redirect(url_for("pagos.operacion_detalle", operation_id=operation_id))
    finally:
        conn.close()


@pagos_bp.route("/prueba", methods=["GET", "POST"])
@login_required
def prueba():
    conn = get_db()
    try:
        service = _service(conn)
        configs = service.list_configs()
        if request.method == "POST":
            try:
                op = service.create_operation(
                    config_id=int(request.form.get("config_id") or 0) or None,
                    importe=float((request.form.get("importe") or "0").replace(",", ".")),
                    descripcion=request.form.get("descripcion") or "Pago de prueba",
                    metodo=request.form.get("metodo") or "tarjeta",
                    origen_modulo="pagos",
                    origen_tipo="prueba",
                    cliente_nombre=request.form.get("cliente_nombre") or "",
                    cliente_email=request.form.get("cliente_email") or "",
                )
                flash("Operacion de pago creada.", "success")
                return redirect(url_for("pagos.operacion_detalle", operation_id=op["id"]))
            except Exception as exc:
                flash(f"No se pudo crear la operacion: {exc}", "danger")
        return render_template("pagos/prueba.html", configs=configs, metodos=METODOS)
    finally:
        conn.close()


@pagos_bp.route("/checkout-simulado/<ref>", methods=["GET", "POST"])
def checkout_simulado(ref):
    conn = get_db()
    try:
        public_service = PaymentService(conn, empresa_id=1, centro_id=None, usuario="checkout_publico")
        op = public_service.get_public_operation_by_ref(ref)
        if not op:
            return "Operacion no encontrada", 404
        if op.get("fecha_expiracion"):
            try:
                if datetime.fromisoformat(str(op["fecha_expiracion"])[:19]) < datetime.now():
                    return "Link de pago caducado", 410
            except ValueError:
                pass
        service = PaymentService(
            conn,
            empresa_id=int(op.get("empresa_id") or 1),
            centro_id=op.get("centro_id"),
            usuario="checkout_simulado",
        )
        if request.method == "POST":
            estado = request.form.get("estado") or "confirmado"
            op = service.mark_simulated(ref, estado)
            return render_template("pagos/checkout_resultado.html", operacion=op)
        return render_template("pagos/checkout_simulado.html", operacion=op)
    finally:
        conn.close()


@pagos_bp.route("/webhook/<int:config_id>/<proveedor>", methods=["POST"])
def webhook(config_id, proveedor):
    conn = get_db()
    try:
        config_row = conn.execute(
            "SELECT empresa_id, centro_id FROM pagos_gateway_config WHERE id=? AND proveedor=?",
            (config_id, proveedor),
        ).fetchone()
        if not config_row:
            return {"ok": False, "error": "pasarela_no_encontrada"}, 404
        payload = request.get_json(silent=True) or {}
        body = request.get_data() or b""
        service = PaymentService(
            conn,
            empresa_id=int(config_row["empresa_id"] or 1),
            centro_id=config_row["centro_id"],
            usuario="webhook",
        )
        valid, reason = service.verify_webhook(config_id, body, dict(request.headers))
        event_id = service.record_webhook(config_id, proveedor, body, dict(request.headers), payload)
        if not valid:
            return {"ok": False, "event_id": event_id, "error": reason}, 401
        return {"ok": True, "event_id": event_id}
    finally:
        conn.close()
