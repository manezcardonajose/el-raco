# -*- coding: utf-8 -*-
"""Herramientas locales de seguridad: salud de base de datos y backups.

No cifra ni altera datos operativos. Crea diagnosticos y copias consistentes
para poder trabajar antes de reparar o simular roturas.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
DEFAULT_BACKUP_DIR = ROOT / "backups" / "erp"
DEFAULT_REPORT_DIR = ROOT / "publicaciones" / "el_raco"


CRITICAL_TABLES = [
    "empresas",
    "usuarios",
    "empleados",
    "centros_trabajo",
    "reservas_recepcion",
    "habitaciones_recepcion",
    "productos",
    "recetas",
    "pedidos",
    "appcc_controles_plan",
    "appcc_registros_plan",
    "prl_riesgos",
    "prl_formaciones",
    "prl_formacion_empleados",
    "documentos_rrhh",
    "erp_seguridad_auditoria",
]

SENSITIVE_TABLES = [
    "usuarios",
    "empleados",
    "documentos_rrhh",
    "nominas",
    "embargos",
    "reservas_recepcion",
    "registro_viajeros",
    "viajeros",
    "clientes",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Herramientas de seguridad local para El Raco ERP.")
    parser.add_argument(
        "accion",
        choices=["health", "backup", "snapshot", "list-backups", "verify-backup", "restore-test", "network"],
        help="health diagnostica, backup copia, snapshot hace ambas, network revisa exposicion local.",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--backup", default="")
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--restore-dir", default=str(ROOT / "tmp" / "restore_tests"))
    parser.add_argument("--motivo", default="manual")
    parser.add_argument("--empresa-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=3)
    return parser.parse_args()


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def scalar(conn: sqlite3.Connection, sql: str, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return default
        return row[0] if row[0] is not None else default
    except Exception:
        return default


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_tool_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS erp_herramientas_backups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP,
            db_origen TEXT,
            archivo TEXT,
            manifest TEXT,
            sha256 TEXT,
            tamano_bytes INTEGER,
            motivo TEXT,
            estado TEXT,
            detalle TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS erp_herramientas_salud (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP,
            db_path TEXT,
            score REAL,
            estado TEXT,
            incidencias INTEGER DEFAULT 0,
            criticas INTEGER DEFAULT 0,
            altas INTEGER DEFAULT 0,
            reporte_json TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS erp_seguridad_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            usuario_id INTEGER,
            usuario_email TEXT,
            empresa_id INTEGER,
            centro_id INTEGER,
            modulo TEXT,
            accion TEXT NOT NULL,
            resultado TEXT,
            ip TEXT,
            user_agent TEXT,
            detalle TEXT,
            origen TEXT DEFAULT 'humano'
        )
        """
    )
    conn.commit()


def add_issue(issues: list[dict], severidad: str, codigo: str, titulo: str, detalle: str) -> None:
    issues.append({
        "severidad": severidad,
        "codigo": codigo,
        "titulo": titulo,
        "detalle": detalle,
    })


def count_orphans(
    conn: sqlite3.Connection,
    child: str,
    parent: str,
    child_col: str,
    parent_col: str = "id",
    where: str = "",
) -> int:
    if not (table_exists(conn, child) and table_exists(conn, parent)):
        return 0
    if child_col not in columns(conn, child) or parent_col not in columns(conn, parent):
        return 0
    sql = f"""
        SELECT COUNT(*)
        FROM {child} c
        LEFT JOIN {parent} p ON p.{parent_col}=c.{child_col}
        WHERE c.{child_col} IS NOT NULL AND p.{parent_col} IS NULL
    """
    if where:
        sql += f" AND ({where})"
    return int(scalar(conn, sql, default=0))


def _db_inventory(root: Path) -> list[dict]:
    ignored_parts = {"__pycache__", ".git", "node_modules", ".venv", "venv"}
    rows = []
    for path in root.rglob("*.db"):
        if any(part in ignored_parts for part in path.parts):
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path
        rows.append({
            "ruta": str(rel),
            "tamano_bytes": path.stat().st_size,
            "modificado": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            "es_backup": "backups" in path.parts or "restore_tests" in path.parts,
        })
    return sorted(rows, key=lambda item: item["tamano_bytes"], reverse=True)[:30]


def _local_ips() -> list[str]:
    ips = {"127.0.0.1"}
    hostname = socket.gethostname()
    try:
        for item in socket.getaddrinfo(hostname, None):
            ip = item[4][0]
            if ":" not in ip:
                ips.add(ip)
    except Exception:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ips.add(sock.getsockname()[0])
    except Exception:
        pass
    return sorted(ips)


def _port_reachable(host: str, port: int, timeout: float = 0.6) -> bool:
    target = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    try:
        with socket.create_connection((target, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _netstat_listeners(port: int | None = None) -> list[dict]:
    try:
        proc = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception:
        return []
    listeners = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local = parts[1]
        state = parts[3].upper()
        pid = parts[4]
        if state != "LISTENING":
            continue
        try:
            local_port = int(local.rsplit(":", 1)[-1])
        except ValueError:
            continue
        if port and local_port != int(port):
            continue
        listeners.append({
            "protocolo": "TCP",
            "local": local,
            "puerto": local_port,
            "estado": state,
            "pid": pid,
            "toda_red": local.startswith("0.0.0.0:") or local.startswith("[::]:") or local.startswith(":::"),
        })
    return listeners[:50]


def network_report() -> dict:
    erp_host = os.environ.get("ERP_HOST", "0.0.0.0")
    erp_port = int(os.environ.get("ERP_PORT") or os.environ.get("PORT") or "5001")
    listeners = _netstat_listeners(erp_port)
    issues = []
    if erp_host in {"0.0.0.0", "::"}:
        add_issue(
            issues,
            "media",
            "erp_host_toda_red",
            "ERP_HOST escucha toda la red",
            "Correcto solo si hay LAN controlada, firewall o VPN. Para local puro usar 127.0.0.1.",
        )
    if any(item["toda_red"] for item in listeners):
        add_issue(
            issues,
            "media",
            "puerto_erp_toda_red",
            "Puerto ERP abierto en todas las interfaces",
            "Verificar que el router/firewall no lo expone a internet.",
        )
    return {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "hostname": socket.gethostname(),
        "ips_locales": _local_ips(),
        "erp_host": erp_host,
        "erp_port": erp_port,
        "erp_cookie_secure": os.environ.get("ERP_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"},
        "erp_public_mode": os.environ.get("ERP_PUBLIC_MODE", "false").strip().lower() in {"1", "true", "yes", "on"},
        "puerto_responde": _port_reachable(erp_host, erp_port),
        "listeners_erp": listeners,
        "listeners_total_muestra": _netstat_listeners(None)[:20],
        "issues": issues,
    }


def health_report(db_path: Path, empresa_id: int, centro_id: int, persist: bool = True) -> dict:
    if not db_path.exists():
        raise FileNotFoundError(f"No existe la base: {db_path}")
    conn = connect(db_path)
    try:
        ensure_tool_schema(conn)
        issues: list[dict] = []
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            add_issue(issues, "critica", "integrity_check", "SQLite integrity_check falla", str(integrity))

        table_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        table_names = {row["name"] for row in table_rows}
        missing = [t for t in CRITICAL_TABLES if t not in table_names]
        for table in missing:
            add_issue(issues, "alta", "tabla_critica_faltante", f"Falta tabla critica {table}", "Modulo relacionado incompleto o no inicializado.")

        empty_critical = []
        for table in CRITICAL_TABLES:
            if table in table_names:
                total = int(scalar(conn, f"SELECT COUNT(*) FROM {table}", default=0))
                if total == 0 and table in {"empresas", "usuarios", "empleados"}:
                    add_issue(issues, "alta", "tabla_critica_vacia", f"Tabla critica vacia: {table}", "Sin datos minimos para operar.")
                elif total == 0:
                    empty_critical.append(table)

        orphan_checks = [
            ("empleados", "departamentos", "departamento_id"),
            ("empleados", "centros_trabajo", "centro_id"),
            ("reservas_recepcion", "habitaciones_recepcion", "habitacion_id"),
            ("productos", "proveedores", "proveedor_id"),
            ("documentos_rrhh", "empleados", "empleado_id"),
            ("prl_formacion_empleados", "empleados", "empleado_id"),
            ("prl_formacion_empleados", "prl_formaciones", "formacion_id"),
        ]
        for child, parent, col in orphan_checks:
            total = count_orphans(conn, child, parent, col)
            if total:
                add_issue(issues, "alta", "datos_huerfanos", f"{child}.{col} apunta a {parent} inexistente", f"{total} registros huerfanos.")

        docs_sin_archivo = 0
        if table_exists(conn, "documentos_rrhh") and "archivo" in columns(conn, "documentos_rrhh"):
            rows = conn.execute(
                """
                SELECT id, archivo
                FROM documentos_rrhh
                WHERE COALESCE(archivo,'')!=''
                LIMIT 500
                """
            ).fetchall()
            for row in rows:
                archivo = Path(str(row["archivo"]))
                candidates = [archivo]
                if not archivo.is_absolute():
                    candidates.append(ROOT / archivo)
                if not any(p.exists() for p in candidates):
                    docs_sin_archivo += 1
            if docs_sin_archivo:
                add_issue(issues, "media", "documentos_sin_archivo", "Documentos RRHH con archivo no localizado", f"{docs_sin_archivo} documentos revisados no tienen archivo accesible.")

        backup_count = int(scalar(conn, "SELECT COUNT(*) FROM erp_herramientas_backups", default=0))
        if backup_count == 0:
            add_issue(issues, "media", "sin_backups_registrados", "No hay backups registrados por herramienta", "La base puede tener copias manuales, pero no estan trazadas.")

        if not table_exists(conn, "erp_seguridad_auditoria"):
            add_issue(issues, "alta", "sin_auditoria_seguridad", "No existe auditoria de seguridad", "No se pueden trazar logins, salidas ni acciones sensibles.")
            audit_counts = {}
        else:
            audit_counts = {
                "total": int(scalar(conn, "SELECT COUNT(*) FROM erp_seguridad_auditoria", default=0)),
                "login_ok": int(scalar(conn, "SELECT COUNT(*) FROM erp_seguridad_auditoria WHERE accion='login' AND resultado='ok'", default=0)),
                "login_fallido": int(scalar(conn, "SELECT COUNT(*) FROM erp_seguridad_auditoria WHERE accion='login' AND resultado='fallido'", default=0)),
                "password": int(scalar(conn, "SELECT COUNT(*) FROM erp_seguridad_auditoria WHERE accion='cambio_password'", default=0)),
                "herramientas": int(scalar(conn, "SELECT COUNT(*) FROM erp_seguridad_auditoria WHERE modulo='seguridad_backups'", default=0)),
                "denegados": int(scalar(conn, "SELECT COUNT(*) FROM erp_seguridad_auditoria WHERE resultado='denegado'", default=0)),
            }
            if audit_counts["total"] == 0:
                add_issue(issues, "media", "auditoria_seguridad_vacia", "Auditoria de seguridad sin eventos", "La tabla existe, pero aun no hay logins o cambios trazados.")
            if audit_counts["login_fallido"] >= 5:
                add_issue(issues, "media", "logins_fallidos", "Varios accesos fallidos registrados", f"{audit_counts['login_fallido']} intentos fallidos. Revisar si son pruebas o posible ataque.")

        if os.environ.get("SECRET_KEY") in {None, "", "clave-secreta-cambiar-en-produccion"}:
            add_issue(issues, "media", "secret_key_pruebas", "SECRET_KEY de pruebas o no definida", "Correcto para laboratorio, bloqueante antes de publicar.")

        erp_host = os.environ.get("ERP_HOST", "0.0.0.0")
        erp_port = os.environ.get("ERP_PORT") or os.environ.get("PORT") or "5001"
        cookie_secure = os.environ.get("ERP_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
        modo_publico = os.environ.get("ERP_PUBLIC_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
        if erp_host in {"0.0.0.0", "::"}:
            add_issue(
                issues,
                "media",
                "escucha_toda_red",
                "La ERP puede escuchar en todas las interfaces",
                "Usar solo en red interna controlada. Para local puro usar ERP_HOST=127.0.0.1; para produccion, proxy/VPN/firewall.",
            )
        if modo_publico and not cookie_secure:
            add_issue(
                issues,
                "alta",
                "cookie_no_secure_publico",
                "Cookie de sesion sin marca Secure en modo publico",
                "Con HTTPS publico debe activarse ERP_COOKIE_SECURE=true.",
            )

        db_inventory = _db_inventory(ROOT)
        active_db_resolved = str(db_path.resolve())
        db_operativas_sueltas = [
            item for item in db_inventory
            if not item["es_backup"] and str((ROOT / item["ruta"]).resolve()) != active_db_resolved
        ]
        if db_operativas_sueltas:
            add_issue(
                issues,
                "media",
                "bases_db_sueltas",
                "Hay bases .db fuera del backup activo",
                f"{len(db_operativas_sueltas)} archivos .db no marcados como backup/restauracion. Revisar si son necesarios.",
            )

        sensitive_counts = {}
        for table in SENSITIVE_TABLES:
            if table_exists(conn, table):
                sensitive_counts[table] = int(scalar(conn, f"SELECT COUNT(*) FROM {table}", default=0))

        counts = {
            "criticas": sum(1 for i in issues if i["severidad"] == "critica"),
            "altas": sum(1 for i in issues if i["severidad"] == "alta"),
            "medias": sum(1 for i in issues if i["severidad"] == "media"),
        }
        penalty = counts["criticas"] * 30 + counts["altas"] * 12 + counts["medias"] * 4
        score = max(0.0, 100.0 - penalty)
        if counts["criticas"]:
            estado = "critico"
        elif counts["altas"]:
            estado = "riesgo"
        elif counts["medias"]:
            estado = "advertencia"
        else:
            estado = "ok"
        report = {
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "db_path": str(db_path),
            "tamano_bytes": db_path.stat().st_size,
            "integrity_check": integrity,
            "tablas": len(table_names),
            "tablas_criticas_faltantes": missing,
            "tablas_criticas_vacias_no_bloqueantes": empty_critical,
            "score": round(score, 1),
            "estado": estado,
            "incidencias": len(issues),
            **counts,
            "issues": issues,
            "auditoria_seguridad": audit_counts,
            "inventario_db": db_inventory,
            "tablas_sensibles": sensitive_counts,
            "entorno": {
                "secret_key_definida": bool(os.environ.get("SECRET_KEY")),
                "secret_key_pruebas": os.environ.get("SECRET_KEY") in {None, "", "clave-secreta-cambiar-en-produccion"},
                "erp_host": erp_host,
                "erp_port": erp_port,
                "erp_cookie_secure": cookie_secure,
                "erp_public_mode": modo_publico,
            },
        }
        if persist:
            conn.execute(
                """
                INSERT INTO erp_herramientas_salud
                (db_path, score, estado, incidencias, criticas, altas, reporte_json, empresa_id, centro_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(db_path),
                    report["score"],
                    estado,
                    len(issues),
                    counts["criticas"],
                    counts["altas"],
                    json.dumps(report, ensure_ascii=False),
                    empresa_id,
                    centro_id,
                ),
            )
            conn.commit()
        return report
    finally:
        conn.close()


def make_backup(db_path: Path, backup_dir: Path, motivo: str, empresa_id: int, centro_id: int) -> dict:
    if not db_path.exists():
        raise FileNotFoundError(f"No existe la base: {db_path}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = db_path.stem.replace(" ", "_")
    backup_path = backup_dir / f"{safe_name}_{stamp}.db"
    manifest_path = backup_dir / f"{safe_name}_{stamp}.manifest.json"

    src = connect(db_path)
    try:
        ensure_tool_schema(src)
        dst = sqlite3.connect(backup_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    sha = sha256_file(backup_path)
    manifest = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "origen": str(db_path),
        "backup": str(backup_path),
        "sha256": sha,
        "tamano_bytes": backup_path.stat().st_size,
        "motivo": motivo,
        "empresa_id": empresa_id,
        "centro_id": centro_id,
        "tipo": "sqlite_backup_api",
        "cifrado": False,
        "nota": "Backup local sin cifrar. Proteger carpeta y copiar externamente segun politica.",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    conn = connect(db_path)
    try:
        ensure_tool_schema(conn)
        conn.execute(
            """
            INSERT INTO erp_herramientas_backups
            (db_origen, archivo, manifest, sha256, tamano_bytes, motivo, estado, detalle, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, 'ok', ?, ?, ?)
            """,
            (
                str(db_path),
                str(backup_path),
                str(manifest_path),
                sha,
                backup_path.stat().st_size,
                motivo,
                "Backup consistente creado con API SQLite.",
                empresa_id,
                centro_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return manifest


def _manifest_for_backup(backup_path: Path) -> Path:
    if backup_path.name.endswith(".db"):
        return backup_path.with_name(backup_path.name[:-3] + ".manifest.json")
    return backup_path.with_suffix(backup_path.suffix + ".manifest.json")


def load_manifest_for_backup(backup_path: Path) -> dict:
    manifest_path = _manifest_for_backup(backup_path)
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def list_backups(backup_dir: Path) -> dict:
    backup_dir.mkdir(parents=True, exist_ok=True)
    backups = []
    for path in sorted(backup_dir.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True):
        manifest = load_manifest_for_backup(path)
        backups.append({
            "archivo": str(path),
            "manifest": str(_manifest_for_backup(path)) if _manifest_for_backup(path).exists() else "",
            "fecha_archivo": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            "tamano_bytes": path.stat().st_size,
            "sha256_manifest": manifest.get("sha256", ""),
            "motivo": manifest.get("motivo", ""),
            "origen": manifest.get("origen", ""),
        })
    return {"total": len(backups), "backups": backups[:50]}


def verify_backup(backup_path: Path) -> dict:
    if not backup_path.exists():
        raise FileNotFoundError(f"No existe el backup: {backup_path}")
    manifest = load_manifest_for_backup(backup_path)
    sha = sha256_file(backup_path)
    sha_ok = bool(manifest.get("sha256")) and manifest.get("sha256") == sha
    conn = connect(backup_path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = int(scalar(conn, "SELECT COUNT(*) FROM sqlite_master WHERE type='table'", default=0))
    finally:
        conn.close()
    estado = "ok" if integrity == "ok" and (sha_ok or not manifest.get("sha256")) else "riesgo"
    return {
        "backup": str(backup_path),
        "manifest": str(_manifest_for_backup(backup_path)) if _manifest_for_backup(backup_path).exists() else "",
        "tamano_bytes": backup_path.stat().st_size,
        "sha256_actual": sha,
        "sha256_manifest": manifest.get("sha256", ""),
        "sha256_ok": sha_ok if manifest.get("sha256") else None,
        "integrity_check": integrity,
        "tablas": tables,
        "estado": estado,
        "manifest_data": manifest,
    }


def restore_test(backup_path: Path, restore_dir: Path, empresa_id: int, centro_id: int) -> dict:
    verification = verify_backup(backup_path)
    if verification["integrity_check"] != "ok":
        return {
            "estado": "bloqueado",
            "motivo": "El backup no pasa integrity_check.",
            "verificacion": verification,
        }
    restore_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    restored = restore_dir / f"{backup_path.stem}_restore_test_{stamp}.db"
    shutil.copy2(backup_path, restored)
    health = health_report(restored, empresa_id, centro_id, persist=False)
    return {
        "estado": "ok" if health["integrity_check"] == "ok" else "riesgo",
        "backup": str(backup_path),
        "restaurado_en": str(restored),
        "verificacion": verification,
        "health_restaurado": health,
        "nota": "Restauracion de prueba. No pisa la base activa.",
    }


def write_report(report_dir: Path, name: str, payload: dict) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    args = parse_args()
    db_path = resolve_path(args.db)
    backup_path = resolve_path(args.backup) if args.backup else None
    backup_dir = resolve_path(args.backup_dir)
    report_dir = resolve_path(args.report_dir)
    restore_dir = resolve_path(args.restore_dir)

    result: dict = {"accion": args.accion, "db": str(db_path)}
    if args.accion in {"backup", "snapshot"}:
        result["backup"] = make_backup(db_path, backup_dir, args.motivo, args.empresa_id, args.centro_id)
    if args.accion in {"health", "snapshot"}:
        result["health"] = health_report(db_path, args.empresa_id, args.centro_id)
    if args.accion == "list-backups":
        result["listado"] = list_backups(backup_dir)
    if args.accion == "verify-backup":
        if not backup_path:
            raise SystemExit("Falta --backup")
        result["verificacion"] = verify_backup(backup_path)
    if args.accion == "restore-test":
        if not backup_path:
            raise SystemExit("Falta --backup")
        result["restore_test"] = restore_test(backup_path, restore_dir, args.empresa_id, args.centro_id)
    if args.accion == "network":
        result["network"] = network_report()

    report_name = f"erp_seguridad_{args.accion}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report_path = write_report(report_dir, report_name, result)
    result["reporte"] = str(report_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
