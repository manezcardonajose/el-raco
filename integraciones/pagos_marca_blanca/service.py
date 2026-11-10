"""Servicio reusable de pagos marca blanca."""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from .adapters import build_gateway
from .schema import ensure_payment_schema
from .security import masked_secrets, pack_secrets, safe_json, unpack_secrets


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def _format_money(value: Any, currency: str = "EUR") -> str:
    try:
        amount = float(value or 0)
    except Exception:
        amount = 0.0
    text = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} {currency or 'EUR'}"


class PaymentService:
    def __init__(self, conn: sqlite3.Connection, empresa_id: int = 1, centro_id: int | None = None, usuario: str = "sistema"):
        self.conn = conn
        self.empresa_id = empresa_id or 1
        self.centro_id = centro_id
        self.usuario = usuario or "sistema"
        ensure_payment_schema(conn)

    def audit(self, accion: str, resultado: str = "ok", entidad: str = "", entidad_id: int | None = None, detalle: dict | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO pagos_auditoria
            (empresa_id, centro_id, usuario, accion, resultado, entidad, entidad_id, detalle_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (self.empresa_id, self.centro_id, self.usuario, accion, resultado, entidad, entidad_id, safe_json(detalle)),
        )

    def list_configs(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM pagos_gateway_config
            WHERE empresa_id=? AND (? IS NULL OR centro_id=? OR centro_id IS NULL)
            ORDER BY activo DESC, nombre
            """,
            (self.empresa_id, self.centro_id, self.centro_id),
        ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["metodos"] = json.loads(data.get("metodos_json") or "{}")
            data["config_publica"] = json.loads(data.get("config_publica_json") or "{}")
            data["secretos_mascara"] = masked_secrets(data.get("secretos_json"))
            data["diagnostico"] = self.diagnose_config(data)
            result.append(data)
        return result

    def get_config(self, config_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM pagos_gateway_config WHERE id=? AND empresa_id=?",
            (config_id, self.empresa_id),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["metodos"] = json.loads(data.get("metodos_json") or "{}")
        data["config_publica"] = json.loads(data.get("config_publica_json") or "{}")
        data["secretos"] = unpack_secrets(data.get("secretos_json"))
        data["secretos_mascara"] = masked_secrets(data.get("secretos_json"))
        data["diagnostico"] = self.diagnose_config(data)
        return data

    def table_exists(self, table: str) -> bool:
        return self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

    def diagnose_config(self, config: dict[str, Any]) -> dict[str, Any]:
        provider = (config.get("proveedor") or "").lower()
        mode = (config.get("modo") or "test").lower()
        metodos = config.get("metodos") or {}
        public_config = config.get("config_publica") or {}
        masked = config.get("secretos_mascara") or masked_secrets(config.get("secretos_json"))
        issues: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []

        if not config.get("activo"):
            warnings.append({"codigo": "inactiva", "texto": "La pasarela no esta activa."})
        if mode == "live" and provider == "simulado":
            issues.append({"codigo": "simulado_live", "texto": "No uses proveedor simulado en modo real."})
        if provider == "stripe":
            if not (masked.get("api_key") or masked.get("secret_key")):
                issues.append({"codigo": "stripe_sin_clave", "texto": "Falta API key de Stripe."})
            if not (public_config.get("success_url") and public_config.get("cancel_url")):
                warnings.append({"codigo": "stripe_urls", "texto": "Configura success_url y cancel_url."})
        elif provider == "redsys":
            for key in ("merchant_code", "terminal", "secret_key"):
                if not masked.get(key):
                    issues.append({"codigo": f"redsys_{key}", "texto": f"Falta {key} de Redsys."})
            if metodos.get("bizum") and "z" not in str(public_config.get("paymethods", "")).lower():
                warnings.append({"codigo": "redsys_bizum", "texto": "Para forzar Bizum en Redsys se usa paymethods z; si no, lo decide el TPV."})
        elif provider == "adyen":
            if not (masked.get("api_key") and (masked.get("merchant_account") or public_config.get("merchant_account"))):
                issues.append({"codigo": "adyen_claves", "texto": "Falta API key o merchant account de Adyen."})
        elif provider != "simulado":
            issues.append({"codigo": "proveedor_desconocido", "texto": "Proveedor no reconocido por el modulo."})

        if metodos.get("nfc") and provider not in {"simulado", "stripe", "adyen"}:
            warnings.append({"codigo": "nfc_limitado", "texto": "NFC necesita terminal certificado; con Redsys depende del banco/terminal."})
        if metodos.get("preautorizacion") and provider not in {"simulado", "stripe", "adyen", "redsys"}:
            warnings.append({"codigo": "preautorizacion", "texto": "Revisa soporte de preautorizacion antes de activarlo en real."})

        estado = "ok"
        if issues:
            estado = "bloqueante"
        elif warnings:
            estado = "revisar"
        return {"estado": estado, "issues": issues, "warnings": warnings}

    def save_config(
        self,
        *,
        nombre: str,
        proveedor: str,
        modo: str = "test",
        activo: int = 1,
        marca_nombre: str = "",
        marca_logo: str = "",
        marca_color: str = "",
        moneda: str = "EUR",
        metodos: dict[str, Any] | None = None,
        config_publica: dict[str, Any] | None = None,
        secretos: dict[str, str] | None = None,
        config_id: int | None = None,
    ) -> int:
        packed = None
        if secretos:
            packed = pack_secrets(secretos)
        if config_id:
            actual = self.get_config(config_id)
            if not actual:
                raise ValueError("Configuracion de pago no encontrada.")
            secretos_sql = "secretos_json=COALESCE(?, secretos_json),"
            self.conn.execute(
                f"""
                UPDATE pagos_gateway_config
                SET nombre=?, proveedor=?, modo=?, activo=?, marca_nombre=?, marca_logo=?,
                    marca_color=?, moneda=?, metodos_json=?, config_publica_json=?,
                    {secretos_sql} fecha_actualizacion=CURRENT_TIMESTAMP
                WHERE id=? AND empresa_id=?
                """,
                (
                    nombre,
                    proveedor,
                    modo,
                    int(bool(activo)),
                    marca_nombre,
                    marca_logo,
                    marca_color,
                    moneda or "EUR",
                    safe_json(metodos),
                    safe_json(config_publica),
                    packed,
                    config_id,
                    self.empresa_id,
                ),
            )
            self.audit("actualizar_config_pago", entidad="pagos_gateway_config", entidad_id=config_id, detalle={"proveedor": proveedor})
            return config_id
        cur = self.conn.execute(
            """
            INSERT INTO pagos_gateway_config
            (empresa_id, centro_id, nombre, proveedor, modo, activo, marca_nombre, marca_logo,
             marca_color, moneda, metodos_json, config_publica_json, secretos_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.empresa_id,
                self.centro_id,
                nombre,
                proveedor,
                modo,
                int(bool(activo)),
                marca_nombre,
                marca_logo,
                marca_color,
                moneda or "EUR",
                safe_json(metodos),
                safe_json(config_publica),
                packed or "{}",
            ),
        )
        config_id = int(cur.lastrowid)
        self.audit("crear_config_pago", entidad="pagos_gateway_config", entidad_id=config_id, detalle={"proveedor": proveedor})
        return config_id

    def default_config(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id
            FROM pagos_gateway_config
            WHERE empresa_id=? AND activo=1 AND (? IS NULL OR centro_id=? OR centro_id IS NULL)
            ORDER BY CASE WHEN centro_id=? THEN 0 ELSE 1 END, id
            LIMIT 1
            """,
            (self.empresa_id, self.centro_id, self.centro_id, self.centro_id),
        ).fetchone()
        return self.get_config(int(row["id"])) if row else None

    def list_terminals(self, config_id: int | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [self.empresa_id, self.centro_id, self.centro_id]
        filtro = ""
        if config_id:
            filtro = "AND t.config_id=?"
            params.append(config_id)
        rows = self.conn.execute(
            f"""
            SELECT t.*, cfg.nombre AS config_nombre, cfg.proveedor
            FROM pagos_terminales t
            JOIN pagos_gateway_config cfg ON cfg.id=t.config_id
            WHERE t.empresa_id=? AND (? IS NULL OR t.centro_id=? OR t.centro_id IS NULL)
              {filtro}
            ORDER BY t.activo DESC, t.nombre
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def save_terminal(
        self,
        *,
        config_id: int,
        nombre: str,
        proveedor_terminal_id: str = "",
        ubicacion: str = "",
        nfc: int = 1,
        tap_to_pay: int = 0,
        activo: int = 1,
        terminal_id: int | None = None,
    ) -> int:
        config = self.get_config(config_id)
        if not config:
            raise ValueError("Configuracion de pago no encontrada.")
        if terminal_id:
            self.conn.execute(
                """
                UPDATE pagos_terminales
                SET config_id=?, nombre=?, proveedor_terminal_id=?, ubicacion=?,
                    nfc=?, tap_to_pay=?, activo=?
                WHERE id=? AND empresa_id=?
                """,
                (config_id, nombre, proveedor_terminal_id, ubicacion, int(bool(nfc)), int(bool(tap_to_pay)), int(bool(activo)), terminal_id, self.empresa_id),
            )
            self.audit("actualizar_terminal_pago", entidad="pagos_terminales", entidad_id=terminal_id, detalle={"config_id": config_id})
            return terminal_id
        cur = self.conn.execute(
            """
            INSERT INTO pagos_terminales
            (config_id, empresa_id, centro_id, nombre, proveedor_terminal_id, ubicacion, nfc, tap_to_pay, activo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (config_id, self.empresa_id, self.centro_id, nombre, proveedor_terminal_id, ubicacion, int(bool(nfc)), int(bool(tap_to_pay)), int(bool(activo))),
        )
        terminal_id = int(cur.lastrowid)
        self.audit("crear_terminal_pago", entidad="pagos_terminales", entidad_id=terminal_id, detalle={"config_id": config_id})
        return terminal_id

    def create_operation(
        self,
        *,
        importe: float,
        descripcion: str,
        config_id: int | None = None,
        moneda: str = "EUR",
        metodo: str = "",
        origen_modulo: str = "",
        origen_tipo: str = "",
        origen_id: int | None = None,
        tipo_operacion: str = "payment",
        cliente_nombre: str = "",
        cliente_email: str = "",
        terminal_id: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = self.get_config(config_id) if config_id else self.default_config()
        if not config:
            raise ValueError("No hay pasarela activa configurada.")
        ref = f"PAY-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(12).upper()}"
        request_payload = {
            "importe": importe,
            "descripcion": descripcion,
            "moneda": moneda,
            "metodo": metodo,
            "origen": {"modulo": origen_modulo, "tipo": origen_tipo, "id": origen_id},
            **(extra or {}),
        }
        cur = self.conn.execute(
            """
            INSERT INTO pagos_operaciones
            (empresa_id, centro_id, config_id, referencia_interna, origen_modulo, origen_tipo,
             origen_id, tipo_operacion, importe, moneda, descripcion, cliente_nombre,
             cliente_email, metodo, estado, proveedor, terminal_id, raw_request_json,
             fecha_expiracion, usuario)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'creado', ?, ?, ?, ?, ?)
            """,
            (
                self.empresa_id,
                self.centro_id,
                config["id"],
                ref,
                origen_modulo,
                origen_tipo,
                origen_id,
                tipo_operacion,
                float(importe),
                moneda or config.get("moneda") or "EUR",
                descripcion,
                cliente_nombre,
                cliente_email,
                metodo,
                config["proveedor"],
                terminal_id,
                safe_json(request_payload),
                (datetime.now() + timedelta(hours=24)).isoformat(timespec="seconds"),
                self.usuario,
            ),
        )
        operacion_id = int(cur.lastrowid)
        operation = self.get_operation(operacion_id)
        gateway = build_gateway(config["proveedor"], config["config_publica"], config["secretos"], config["modo"])
        result = gateway.create_payment(operation)
        self.conn.execute(
            """
            UPDATE pagos_operaciones
            SET estado=?, proveedor_payment_id=?, proveedor_session_id=?, checkout_url=?,
                raw_response_json=?, error=?, fecha_actualizacion=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                result.estado,
                result.proveedor_payment_id,
                result.proveedor_session_id,
                result.checkout_url,
                safe_json(result.raw_response),
                result.error,
                operacion_id,
            ),
        )
        self.audit("crear_operacion_pago", resultado="ok" if result.ok else "error", entidad="pagos_operaciones", entidad_id=operacion_id, detalle={"ref": ref, "estado": result.estado, "error": result.error})
        self.conn.commit()
        return self.get_operation(operacion_id) or {}

    def get_operation(self, operation_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM pagos_operaciones WHERE id=? AND empresa_id=?",
            (operation_id, self.empresa_id),
        ).fetchone()
        return _row_to_dict(row)

    def get_operation_by_ref(self, ref: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM pagos_operaciones WHERE referencia_interna=? AND empresa_id=?",
            (ref, self.empresa_id),
        ).fetchone()
        return _row_to_dict(row)

    def get_public_operation_by_ref(self, ref: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM pagos_operaciones WHERE referencia_interna=?",
            (ref,),
        ).fetchone()
        return _row_to_dict(row)

    def list_operations(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT op.*, cfg.nombre AS config_nombre
            FROM pagos_operaciones op
            LEFT JOIN pagos_gateway_config cfg ON cfg.id=op.config_id
            WHERE op.empresa_id=? AND (? IS NULL OR op.centro_id=? OR op.centro_id IS NULL)
            ORDER BY op.id DESC
            LIMIT ?
            """,
            (self.empresa_id, self.centro_id, self.centro_id, int(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def seed_default_templates(self) -> int:
        existing = self.conn.execute(
            """
            SELECT id FROM pagos_plantillas_comunicacion
            WHERE empresa_id=? AND canal='email' AND tipo_operacion='payment' AND idioma='es'
            LIMIT 1
            """,
            (self.empresa_id,),
        ).fetchone()
        if existing:
            return 0
        subject = "Link de pago {{referencia}}"
        body_html = """
        <p>Hola {{cliente_nombre}},</p>
        <p>Te enviamos el link seguro para realizar el pago de <strong>{{importe}}</strong>.</p>
        <p><strong>Concepto:</strong> {{descripcion}}</p>
        <p><a href="{{checkout_url}}">Pagar ahora</a></p>
        <p>Referencia: {{referencia}}</p>
        <p>Este enlace pertenece a {{marca_nombre}}. No respondas con datos de tarjeta por email.</p>
        """
        body_text = (
            "Hola {{cliente_nombre}},\n\n"
            "Te enviamos el link seguro para realizar el pago de {{importe}}.\n"
            "Concepto: {{descripcion}}\n"
            "Link: {{checkout_url}}\n"
            "Referencia: {{referencia}}\n\n"
            "Este enlace pertenece a {{marca_nombre}}. No envies datos de tarjeta por email."
        )
        self.save_template(
            nombre="Link de pago estandar",
            canal="email",
            tipo_operacion="payment",
            idioma="es",
            asunto=subject,
            cuerpo_html=body_html,
            cuerpo_texto=body_text,
            activo=1,
            por_defecto=1,
        )
        return 1

    def list_templates(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        filtro = "" if include_inactive else "AND activo=1"
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM pagos_plantillas_comunicacion
            WHERE empresa_id=? AND (? IS NULL OR centro_id=? OR centro_id IS NULL) {filtro}
            ORDER BY activo DESC, por_defecto DESC, canal, tipo_operacion, nombre
            """,
            (self.empresa_id, self.centro_id, self.centro_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_template(self, template_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM pagos_plantillas_comunicacion WHERE id=? AND empresa_id=?",
            (template_id, self.empresa_id),
        ).fetchone()
        return _row_to_dict(row)

    def default_template(self, canal: str = "email", tipo_operacion: str = "payment", idioma: str = "es") -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM pagos_plantillas_comunicacion
            WHERE empresa_id=? AND activo=1 AND canal=? AND tipo_operacion=? AND idioma=?
              AND (? IS NULL OR centro_id=? OR centro_id IS NULL)
            ORDER BY por_defecto DESC, CASE WHEN centro_id=? THEN 0 ELSE 1 END, id
            LIMIT 1
            """,
            (self.empresa_id, canal, tipo_operacion, idioma, self.centro_id, self.centro_id, self.centro_id),
        ).fetchone()
        return _row_to_dict(row)

    def save_template(
        self,
        *,
        nombre: str,
        canal: str = "email",
        tipo_operacion: str = "payment",
        idioma: str = "es",
        asunto: str = "",
        cuerpo_html: str = "",
        cuerpo_texto: str = "",
        activo: int = 1,
        por_defecto: int = 0,
        template_id: int | None = None,
    ) -> int:
        if por_defecto:
            self.conn.execute(
                """
                UPDATE pagos_plantillas_comunicacion
                SET por_defecto=0
                WHERE empresa_id=? AND canal=? AND tipo_operacion=? AND idioma=?
                  AND (? IS NULL OR centro_id=? OR centro_id IS NULL)
                """,
                (self.empresa_id, canal, tipo_operacion, idioma, self.centro_id, self.centro_id),
            )
        if template_id:
            self.conn.execute(
                """
                UPDATE pagos_plantillas_comunicacion
                SET nombre=?, canal=?, tipo_operacion=?, idioma=?, asunto=?, cuerpo_html=?,
                    cuerpo_texto=?, activo=?, por_defecto=?, fecha_actualizacion=CURRENT_TIMESTAMP
                WHERE id=? AND empresa_id=?
                """,
                (nombre, canal, tipo_operacion, idioma, asunto, cuerpo_html, cuerpo_texto, int(bool(activo)), int(bool(por_defecto)), template_id, self.empresa_id),
            )
            self.audit("actualizar_plantilla_pago", entidad="pagos_plantillas_comunicacion", entidad_id=template_id)
            return template_id
        cur = self.conn.execute(
            """
            INSERT INTO pagos_plantillas_comunicacion
            (empresa_id, centro_id, nombre, canal, tipo_operacion, idioma, asunto,
             cuerpo_html, cuerpo_texto, activo, por_defecto)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (self.empresa_id, self.centro_id, nombre, canal, tipo_operacion, idioma, asunto, cuerpo_html, cuerpo_texto, int(bool(activo)), int(bool(por_defecto))),
        )
        template_id = int(cur.lastrowid)
        self.audit("crear_plantilla_pago", entidad="pagos_plantillas_comunicacion", entidad_id=template_id)
        return template_id

    def render_template(self, template: dict[str, Any], operation: dict[str, Any], checkout_url: str) -> dict[str, str]:
        config = self.get_config(int(operation["config_id"])) if operation.get("config_id") else None
        variables = {
            "cliente_nombre": operation.get("cliente_nombre") or "cliente",
            "cliente_email": operation.get("cliente_email") or "",
            "importe": _format_money(operation.get("importe"), operation.get("moneda") or "EUR"),
            "moneda": operation.get("moneda") or "EUR",
            "descripcion": operation.get("descripcion") or "Pago",
            "referencia": operation.get("referencia_interna") or "",
            "checkout_url": checkout_url or "",
            "marca_nombre": (config or {}).get("marca_nombre") or (config or {}).get("nombre") or "El Raco",
            "fecha_expiracion": operation.get("fecha_expiracion") or "",
        }

        def replace(text: str) -> str:
            def repl(match: re.Match[str]) -> str:
                key = match.group(1).strip()
                return str(variables.get(key, match.group(0)))

            return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", repl, text or "")

        return {
            "asunto": replace(template.get("asunto") or ""),
            "cuerpo_html": replace(template.get("cuerpo_html") or ""),
            "cuerpo_texto": replace(template.get("cuerpo_texto") or ""),
        }

    def record_communication(
        self,
        operation_id: int,
        *,
        canal: str,
        destinatario: str,
        asunto: str,
        cuerpo: str,
        resultado: str,
        error: str = "",
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO pagos_comunicaciones
            (empresa_id, centro_id, operacion_id, canal, destinatario, asunto, cuerpo, resultado, error, usuario)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (self.empresa_id, self.centro_id, operation_id, canal, destinatario, asunto, cuerpo, resultado, error, self.usuario),
        )
        comm_id = int(cur.lastrowid)
        self.audit("comunicar_link_pago", resultado=resultado, entidad="pagos_comunicaciones", entidad_id=comm_id, detalle={"operation_id": operation_id, "canal": canal, "destinatario": destinatario, "error": error})
        return comm_id

    def list_communications(self, operation_id: int) -> list[dict[str, Any]]:
        if not self.table_exists("pagos_comunicaciones"):
            return []
        rows = self.conn.execute(
            """
            SELECT *
            FROM pagos_comunicaciones
            WHERE empresa_id=? AND operacion_id=?
            ORDER BY id DESC
            """,
            (self.empresa_id, operation_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_simulated(self, ref: str, estado: str) -> dict[str, Any]:
        if estado not in {"confirmado", "fallido", "cancelado"}:
            raise ValueError("Estado no valido.")
        op = self.get_operation_by_ref(ref)
        if not op:
            raise ValueError("Operacion no encontrada.")
        self.conn.execute(
            """
            UPDATE pagos_operaciones
            SET estado=?, fecha_confirmacion=CASE WHEN ?='confirmado' THEN CURRENT_TIMESTAMP ELSE fecha_confirmacion END,
                fecha_actualizacion=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (estado, estado, op["id"]),
        )
        self.audit("checkout_simulado", resultado=estado, entidad="pagos_operaciones", entidad_id=op["id"], detalle={"ref": ref})
        if estado == "confirmado":
            self.sync_confirmed_operation(int(op["id"]))
        self.conn.commit()
        return self.get_operation(int(op["id"])) or {}

    def sync_confirmed_operation(self, operation_id: int) -> dict[str, Any]:
        """Cruza pagos confirmados con tablas conocidas si existen.

        El nucleo sigue siendo reusable: solo aplica hooks si detecta las tablas.
        """
        op = self.get_operation(operation_id)
        if not op or op.get("estado") != "confirmado":
            return {"synced": False, "reason": "no_confirmado"}
        if op.get("origen_modulo") == "recepcion" and op.get("origen_tipo") in {"reserva", "deposito", "checkout"} and op.get("origen_id"):
            return self._sync_reserva_recepcion(op)
        return {"synced": False, "reason": "sin_hook"}

    def _sync_reserva_recepcion(self, op: dict[str, Any]) -> dict[str, Any]:
        if not (self.table_exists("pagos_reserva_recepcion") and self.table_exists("reservas_recepcion")):
            return {"synced": False, "reason": "tablas_recepcion_no_existen"}
        reserva_id = int(op["origen_id"])
        existente = self.conn.execute(
            """
            SELECT id FROM pagos_reserva_recepcion
            WHERE empresa_id=? AND reserva_id=? AND referencia=?
            LIMIT 1
            """,
            (self.empresa_id, reserva_id, op["referencia_interna"]),
        ).fetchone()
        if not existente:
            self.conn.execute(
                """
                INSERT INTO pagos_reserva_recepcion
                (reserva_id, fecha, concepto, importe, forma_pago, referencia, estado,
                 observaciones, empresa_id, usuario_id)
                VALUES (?, date('now'), ?, ?, ?, ?, 'confirmado', ?, ?, NULL)
                """,
                (
                    reserva_id,
                    op["descripcion"] or "Pago pasarela",
                    op["importe"],
                    op["metodo"] or op["proveedor"] or "pasarela",
                    op["referencia_interna"],
                    "Creado automaticamente desde modulo de pagos marca blanca.",
                    self.empresa_id,
                ),
            )
        pagos_info = self.conn.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN estado!='anulado' THEN importe ELSE 0 END),0) AS total
            FROM pagos_reserva_recepcion
            WHERE reserva_id=? AND empresa_id=?
            """,
            (reserva_id, self.empresa_id),
        ).fetchone()
        reserva = self.conn.execute(
            "SELECT precio_total, extras_total FROM reservas_recepcion WHERE id=? AND empresa_id=?",
            (reserva_id, self.empresa_id),
        ).fetchone()
        if reserva:
            total_reserva = float(reserva["precio_total"] or 0) + float(reserva["extras_total"] or 0)
            pagado = float(pagos_info["total"] or 0)
            saldo = max(0.0, total_reserva - pagado)
            self.conn.execute(
                """
                UPDATE reservas_recepcion
                SET deposito=?, saldo=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND empresa_id=?
                """,
                (pagado, saldo, reserva_id, self.empresa_id),
            )
        self.audit("sincronizar_pago_reserva", entidad="pagos_operaciones", entidad_id=int(op["id"]), detalle={"reserva_id": reserva_id})
        return {"synced": True, "origen": "recepcion.reserva", "reserva_id": reserva_id}

    def refund(self, operation_id: int, amount: float | None = None) -> dict[str, Any]:
        op = self.get_operation(operation_id)
        if not op:
            raise ValueError("Operacion no encontrada.")
        config = self.get_config(int(op["config_id"]))
        if not config:
            raise ValueError("Configuracion de pago no encontrada.")
        gateway = build_gateway(config["proveedor"], config["config_publica"], config["secretos"], config["modo"])
        result = gateway.refund(op, amount)
        self.conn.execute(
            """
            UPDATE pagos_operaciones
            SET estado=?, raw_response_json=?, error=?, fecha_actualizacion=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (result.estado, safe_json(result.raw_response), result.error, operation_id),
        )
        self.audit("devolver_pago", resultado="ok" if result.ok else "error", entidad="pagos_operaciones", entidad_id=operation_id, detalle={"amount": amount, "error": result.error})
        self.conn.commit()
        return self.get_operation(operation_id) or {}

    def record_webhook(self, config_id: int, proveedor: str, body: bytes, headers: dict[str, str], payload: dict[str, Any]) -> int:
        config = self.get_config(config_id)
        valid = False
        if config:
            gateway = build_gateway(config["proveedor"], config["config_publica"], config["secretos"], config["modo"])
            valid = gateway.verify_webhook(body, headers)
        cur = self.conn.execute(
            """
            INSERT INTO pagos_eventos
            (empresa_id, centro_id, config_id, proveedor, evento_tipo, evento_id, firma_valida, payload_json, estado_procesado)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'recibido')
            """,
            (
                self.empresa_id,
                self.centro_id,
                config_id,
                proveedor,
                str(payload.get("type") or payload.get("event") or ""),
                str(payload.get("id") or ""),
                1 if valid else 0,
                safe_json(payload),
            ),
        )
        self.audit("webhook_pago", resultado="ok" if valid else "firma_no_valida", entidad="pagos_eventos", entidad_id=int(cur.lastrowid), detalle={"proveedor": proveedor})
        self.conn.commit()
        return int(cur.lastrowid)

    def verify_webhook(self, config_id: int, body: bytes, headers: dict[str, str]) -> tuple[bool, str]:
        config = self.get_config(config_id)
        if not config:
            return False, "configuracion_no_encontrada"
        gateway = build_gateway(config["proveedor"], config["config_publica"], config["secretos"], config["modo"])
        if gateway.verify_webhook(body, headers):
            return True, ""
        return False, "firma_no_valida"
