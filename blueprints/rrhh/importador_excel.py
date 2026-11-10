# -*- coding: utf-8 -*-
"""
Importador RRHH desde Excel para El Racó ERP.

Soporta dos usos:
1) Excel OPS actual: hojas ROLES_TARIFAS, SOCIOS, OPERATIVA_HOTEL.
2) Excel genérico de cliente: hoja con columnas tipo Nombre, Apellidos, DNI/NIE, Rol/Categoría,
   Teléfono, Email, Fecha alta, Nº Seguridad Social, etc.

No borra datos. Crea categorías si no existen y evita duplicados por DNI/NIE o nombre+rol.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from openpyxl import load_workbook
except Exception as exc:  # pragma: no cover
    load_workbook = None
    OPENPYXL_ERROR = exc
else:
    OPENPYXL_ERROR = None


DIAS_SEMANA = {
    "lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2, "jueves": 3,
    "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6,
}


@dataclass
class ImportResult:
    categorias_creadas: int = 0
    categorias_actualizadas: int = 0
    empleados_creados: int = 0
    empleados_omitidos: int = 0
    centros_creados: int = 0
    errores: List[str] = None
    avisos: List[str] = None

    def __post_init__(self):
        self.errores = self.errores or []
        self.avisos = self.avisos or []

    def as_dict(self) -> Dict[str, Any]:
        return {
            "categorias_creadas": self.categorias_creadas,
            "categorias_actualizadas": self.categorias_actualizadas,
            "empleados_creados": self.empleados_creados,
            "empleados_omitidos": self.empleados_omitidos,
            "centros_creados": self.centros_creados,
            "errores": self.errores,
            "avisos": self.avisos,
        }


def _norm(value: Any) -> str:
    txt = "" if value is None else str(value).strip()
    txt = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", txt).lower().strip()


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        if isinstance(value, str):
            value = value.replace("€", "").replace("%", "").replace(".", "").replace(",", ".").strip()
        return float(value)
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def _bool_excel(value: Any) -> bool:
    return _norm(value) in {"si", "sí", "s", "yes", "y", "true", "1", "activo", "activa"}


def _table_exists(conn, table: str) -> bool:
    return conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _column_exists(conn, table: str, column: str) -> bool:
    try:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(c[1] == column for c in cols)
    except Exception:
        return False


def _add_col(conn, table: str, sql: str) -> None:
    col = sql.strip().split()[0]
    if _table_exists(conn, table) and not _column_exists(conn, table, col):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {sql}")


def ensure_import_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS categorias_laborales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            salario_base REAL DEFAULT 0,
            convenio TEXT,
            empresa_id INTEGER DEFAULT 1,
            activo INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS empleados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            apellidos TEXT,
            nif TEXT,
            email TEXT,
            telefono TEXT,
            categoria_id INTEGER,
            rol TEXT,
            empresa_id INTEGER DEFAULT 1,
            activo INTEGER DEFAULT 1
        )
        """
    )
    for col in [
        "salario_base REAL DEFAULT 0", "convenio TEXT", "activo INTEGER DEFAULT 1",
        "origen_importacion TEXT", "codigo_importacion TEXT",
    ]:
        _add_col(conn, "categorias_laborales", col)
    for col in [
        "apellidos TEXT", "nif TEXT", "email TEXT", "telefono TEXT", "numero_seguridad_social TEXT",
        "categoria_id INTEGER", "rol TEXT", "fecha_alta TEXT", "fecha_baja TEXT", "observaciones TEXT",
        "empresa_id INTEGER DEFAULT 1", "activo INTEGER DEFAULT 1", "tipo_empleado TEXT DEFAULT 'real'",
        "origen_importacion TEXT", "codigo_importacion TEXT", "departamento_id INTEGER", "subdepartamento_id INTEGER",
        "punto_venta_id INTEGER", "iban TEXT", "direccion TEXT", "localidad TEXT", "provincia TEXT", "pais TEXT DEFAULT 'España'",
    ]:
        _add_col(conn, "empleados", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS centros_trabajo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            codigo TEXT,
            descripcion TEXT,
            empresa_id INTEGER DEFAULT 1,
            activo INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def _find_header(ws, aliases: Iterable[str], max_rows: int = 15) -> Optional[int]:
    alias_norm = {_norm(a) for a in aliases}
    for row_idx in range(1, min(ws.max_row, max_rows) + 1):
        values = [_norm(c.value) for c in ws[row_idx]]
        if any(v in alias_norm for v in values):
            return row_idx
    return None


def _headers(ws, header_row: int) -> Dict[str, int]:
    out = {}
    for idx, cell in enumerate(ws[header_row], start=1):
        key = _norm(cell.value)
        if key:
            out[key] = idx
    return out


def _get(row, headers: Dict[str, int], *names: str) -> Any:
    for name in names:
        idx = headers.get(_norm(name))
        if idx:
            return row[idx - 1].value
    return None


def _ensure_categoria(conn, nombre: str, empresa_id: int, salario_base: float = 0, grupo: str = "", descripcion: str = "", result: Optional[ImportResult] = None) -> Optional[int]:
    nombre = _clean(nombre)
    if not nombre:
        return None
    row = conn.execute(
        "SELECT id FROM categorias_laborales WHERE empresa_id=? AND lower(nombre)=lower(?) LIMIT 1",
        (empresa_id, nombre),
    ).fetchone()
    if row:
        if salario_base:
            conn.execute(
                "UPDATE categorias_laborales SET salario_base=COALESCE(NULLIF(?,0), salario_base), convenio=COALESCE(NULLIF(?,''), convenio), activo=1 WHERE id=?",
                (salario_base, grupo, row["id"] if hasattr(row, "keys") else row[0]),
            )
            if result:
                result.categorias_actualizadas += 1
        return row["id"] if hasattr(row, "keys") else row[0]
    conn.execute(
        """
        INSERT INTO categorias_laborales (nombre, descripcion, salario_base, convenio, empresa_id, activo, origen_importacion)
        VALUES (?, ?, ?, ?, ?, 1, 'excel_rrhh')
        """,
        (nombre, descripcion, salario_base, grupo, empresa_id),
    )
    if result:
        result.categorias_creadas += 1
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _ensure_centro(conn, nombre: str, empresa_id: int, result: Optional[ImportResult] = None) -> Optional[int]:
    nombre = _clean(nombre)
    if not nombre:
        return None
    row = conn.execute(
        "SELECT id FROM centros_trabajo WHERE empresa_id=? AND lower(nombre)=lower(?) LIMIT 1",
        (empresa_id, nombre),
    ).fetchone()
    if row:
        return row["id"] if hasattr(row, "keys") else row[0]
    conn.execute(
        "INSERT INTO centros_trabajo (nombre, codigo, descripcion, empresa_id, activo) VALUES (?, ?, ?, ?, 1)",
        (nombre, nombre.upper().replace(" ", "_")[:20], "Importado desde Excel RRHH", empresa_id),
    )
    if result:
        result.centros_creados += 1
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _empleado_existe(conn, empresa_id: int, nombre: str, nif: str = "", codigo: str = "") -> bool:
    if nif:
        return conn.execute("SELECT id FROM empleados WHERE empresa_id=? AND nif=? LIMIT 1", (empresa_id, nif)).fetchone() is not None
    if codigo:
        return conn.execute("SELECT id FROM empleados WHERE empresa_id=? AND codigo_importacion=? LIMIT 1", (empresa_id, codigo)).fetchone() is not None
    return conn.execute(
        "SELECT id FROM empleados WHERE empresa_id=? AND lower(nombre)=lower(?) LIMIT 1",
        (empresa_id, nombre),
    ).fetchone() is not None


def _crear_empleado(conn, empresa_id: int, data: Dict[str, Any], result: ImportResult) -> None:
    nombre = _clean(data.get("nombre"))
    if not nombre:
        result.empleados_omitidos += 1
        return
    nif = _clean(data.get("nif"))
    codigo = _clean(data.get("codigo_importacion"))
    if _empleado_existe(conn, empresa_id, nombre, nif=nif, codigo=codigo):
        result.empleados_omitidos += 1
        return
    campos = {
        "nombre": nombre,
        "apellidos": _clean(data.get("apellidos")),
        "nif": nif,
        "numero_seguridad_social": _clean(data.get("numero_seguridad_social")),
        "email": _clean(data.get("email")),
        "telefono": _clean(data.get("telefono")),
        "categoria_id": data.get("categoria_id"),
        "rol": _clean(data.get("rol")),
        "fecha_alta": _clean(data.get("fecha_alta")),
        "observaciones": _clean(data.get("observaciones")),
        "empresa_id": empresa_id,
        "activo": 1,
        "tipo_empleado": _clean(data.get("tipo_empleado")) or "plantilla",
        "origen_importacion": _clean(data.get("origen_importacion")) or "excel_rrhh",
        "codigo_importacion": codigo,
        "direccion": _clean(data.get("direccion")),
        "localidad": _clean(data.get("localidad")),
        "provincia": _clean(data.get("provincia")),
        "pais": _clean(data.get("pais")) or "España",
        "iban": _clean(data.get("iban")),
    }
    usable = {k: v for k, v in campos.items() if _column_exists(conn, "empleados", k)}
    cols = list(usable.keys())
    placeholders = ",".join("?" for _ in cols)
    conn.execute(f"INSERT INTO empleados ({','.join(cols)}) VALUES ({placeholders})", [usable[c] for c in cols])
    result.empleados_creados += 1


def importar_roles_tarifas(conn, wb, empresa_id: int, result: ImportResult) -> None:
    if "ROLES_TARIFAS" not in wb.sheetnames:
        result.avisos.append("No existe hoja ROLES_TARIFAS; se crearán categorías desde los roles encontrados.")
        return
    ws = wb["ROLES_TARIFAS"]
    header = _find_header(ws, ["Rol", "Bruto mensual"])
    if not header:
        result.avisos.append("ROLES_TARIFAS existe, pero no se encontró cabecera Rol.")
        return
    h = _headers(ws, header)
    for row in ws.iter_rows(min_row=header + 1, values_only=False):
        rol = _clean(_get(row, h, "Rol"))
        if not rol:
            continue
        grupo = _clean(_get(row, h, "Grupo"))
        bruto = _to_float(_get(row, h, "Bruto mensual"), 0)
        comentario = _clean(_get(row, h, "Comentario"))
        _ensure_categoria(conn, rol, empresa_id, salario_base=bruto, grupo=grupo, descripcion=comentario, result=result)


def importar_socios(conn, wb, empresa_id: int, result: ImportResult, solo_activos: bool = False, limite: Optional[int] = None, tipo_empleado: str = "plantilla") -> None:
    if "SOCIOS" not in wb.sheetnames:
        result.avisos.append("No existe hoja SOCIOS.")
        return
    ws = wb["SOCIOS"]
    header = _find_header(ws, ["Nombre", "Rol", "DNI/NIE"])
    if not header:
        result.errores.append("No se encontró cabecera en SOCIOS.")
        return
    h = _headers(ws, header)
    contador = 0
    for row in ws.iter_rows(min_row=header + 1, values_only=False):
        nombre = _clean(_get(row, h, "Nombre"))
        rol = _clean(_get(row, h, "Rol"))
        if not nombre or not rol or nombre.lower().startswith("totales"):
            continue
        activo = _bool_excel(_get(row, h, "Activo"))
        if solo_activos and not activo:
            continue
        contador += 1
        if limite and contador > limite:
            break
        categoria_id = _ensure_categoria(conn, rol, empresa_id, result=result)
        bruto = _to_float(_get(row, h, "Bruto mensual"), 0)
        if bruto and categoria_id:
            conn.execute("UPDATE categorias_laborales SET salario_base=? WHERE id=?", (bruto, categoria_id))
        hotel = _clean(_get(row, h, "Hotel asignado", "Hotel/Centro"))
        area = _clean(_get(row, h, "Área/servicio", "Area/servicio"))
        if hotel:
            _ensure_centro(conn, hotel, empresa_id, result)
        _crear_empleado(conn, empresa_id, {
            "nombre": nombre,
            "nif": _clean(_get(row, h, "DNI/NIE", "DNI", "NIF", "NIE")),
            "rol": rol,
            "categoria_id": categoria_id,
            "tipo_empleado": tipo_empleado,
            "codigo_importacion": f"SOCIOS:{nombre}:{rol}",
            "observaciones": f"Importado desde SOCIOS. Centro: {hotel}. Área: {area}. Activo Excel: {'Sí' if activo else 'No'}",
        }, result)


def importar_operativa_como_plantilla(conn, wb, empresa_id: int, result: ImportResult, limite: Optional[int] = None, estrategia: str = "sumatorio") -> None:
    """Genera empleados plantilla desde OPERATIVA_HOTEL.

    estrategia='sumatorio': suma todas las personas por rol en la hoja.
    estrategia='max_por_rol': toma el máximo de personas simultáneas por rol.
    """
    if "OPERATIVA_HOTEL" not in wb.sheetnames:
        result.avisos.append("No existe hoja OPERATIVA_HOTEL.")
        return
    ws = wb["OPERATIVA_HOTEL"]
    header = _find_header(ws, ["Rol", "Personas", "Hotel"])
    if not header:
        result.errores.append("No se encontró cabecera en OPERATIVA_HOTEL.")
        return
    h = _headers(ws, header)
    conteo: Dict[str, int] = {}
    hoteles_por_rol: Dict[str, set] = {}
    areas_por_rol: Dict[str, set] = {}
    for row in ws.iter_rows(min_row=header + 1, values_only=False):
        rol = _clean(_get(row, h, "Rol"))
        if not rol:
            continue
        personas = _to_int(_get(row, h, "Personas"), 0)
        if personas <= 0:
            continue
        hotel = _clean(_get(row, h, "Hotel"))
        area = _clean(_get(row, h, "Servicio/Área", "Servicio/Area", "Área/servicio"))
        if estrategia == "max_por_rol":
            conteo[rol] = max(conteo.get(rol, 0), personas)
        else:
            conteo[rol] = conteo.get(rol, 0) + personas
        hoteles_por_rol.setdefault(rol, set()).add(hotel)
        areas_por_rol.setdefault(rol, set()).add(area)
        if hotel:
            _ensure_centro(conn, hotel, empresa_id, result)
    total_generados = 0
    for rol, cantidad in conteo.items():
        categoria_id = _ensure_categoria(conn, rol, empresa_id, result=result)
        for i in range(1, cantidad + 1):
            if limite and total_generados >= limite:
                return
            nombre = f"{rol} {i:02d}"
            _crear_empleado(conn, empresa_id, {
                "nombre": nombre,
                "rol": rol,
                "categoria_id": categoria_id,
                "tipo_empleado": "plantilla",
                "codigo_importacion": f"OPERATIVA:{rol}:{i:02d}",
                "observaciones": "Plantilla generada desde OPERATIVA_HOTEL. Centros: " + ", ".join(sorted(x for x in hoteles_por_rol.get(rol, set()) if x)) + ". Áreas: " + ", ".join(sorted(x for x in areas_por_rol.get(rol, set()) if x)),
            }, result)
            total_generados += 1


def importar_excel_generico(conn, wb, empresa_id: int, result: ImportResult, hoja: str, tipo_empleado: str = "real") -> None:
    if hoja not in wb.sheetnames:
        result.errores.append(f"No existe la hoja {hoja}.")
        return
    ws = wb[hoja]
    header = _find_header(ws, ["Nombre", "Empleado", "Trabajador", "Rol", "Categoría"])
    if not header:
        result.errores.append(f"No se encontró cabecera en {hoja}.")
        return
    h = _headers(ws, header)
    for row in ws.iter_rows(min_row=header + 1, values_only=False):
        nombre = _clean(_get(row, h, "Nombre", "Empleado", "Trabajador"))
        if not nombre:
            continue
        rol = _clean(_get(row, h, "Rol", "Categoría", "Categoria", "Puesto")) or "Sin categoría"
        categoria_id = _ensure_categoria(conn, rol, empresa_id, result=result)
        _crear_empleado(conn, empresa_id, {
            "nombre": nombre,
            "apellidos": _clean(_get(row, h, "Apellidos")),
            "nif": _clean(_get(row, h, "DNI/NIE", "DNI", "NIF", "NIE")),
            "numero_seguridad_social": _clean(_get(row, h, "Nº Seguridad Social", "Numero Seguridad Social", "NSS", "NAF")),
            "email": _clean(_get(row, h, "Email", "Correo")),
            "telefono": _clean(_get(row, h, "Teléfono", "Telefono", "Móvil", "Movil")),
            "fecha_alta": _clean(_get(row, h, "Fecha alta", "Alta")),
            "rol": rol,
            "categoria_id": categoria_id,
            "tipo_empleado": tipo_empleado,
            "codigo_importacion": _clean(_get(row, h, "Código", "Codigo", "ID")) or f"GEN:{nombre}:{rol}",
            "direccion": _clean(_get(row, h, "Dirección", "Direccion")),
            "localidad": _clean(_get(row, h, "Localidad")),
            "provincia": _clean(_get(row, h, "Provincia")),
            "pais": _clean(_get(row, h, "País", "Pais")),
            "iban": _clean(_get(row, h, "IBAN", "Cuenta bancaria")),
            "observaciones": _clean(_get(row, h, "Observaciones")),
        }, result)


def importar_rrhh_desde_excel(
    conn,
    ruta_excel: str,
    empresa_id: int = 1,
    modo: str = "socios",
    hoja_generica: str = "EMPLEADOS",
    limite: Optional[int] = None,
    solo_activos: bool = False,
    estrategia_operativa: str = "sumatorio",
    tipo_empleado: str = "plantilla",
) -> Dict[str, Any]:
    if load_workbook is None:
        return {"errores": [f"Falta openpyxl. Instala con: pip install openpyxl. Error: {OPENPYXL_ERROR}"]}
    result = ImportResult()
    ensure_import_schema(conn)
    ruta = Path(ruta_excel)
    if not ruta.exists():
        return {"errores": [f"No existe el Excel: {ruta_excel}"]}
    wb = load_workbook(ruta, data_only=True)

    # Siempre importamos roles/tarifas si están, porque alimentan categorías y salario base.
    importar_roles_tarifas(conn, wb, empresa_id, result)

    modo = (modo or "socios").lower().strip()
    if modo == "socios":
        importar_socios(conn, wb, empresa_id, result, solo_activos=solo_activos, limite=limite, tipo_empleado=tipo_empleado)
    elif modo == "operativa":
        importar_operativa_como_plantilla(conn, wb, empresa_id, result, limite=limite, estrategia=estrategia_operativa)
    elif modo == "generico":
        importar_excel_generico(conn, wb, empresa_id, result, hoja_generica, tipo_empleado=tipo_empleado or "real")
    elif modo == "todo_ops":
        importar_socios(conn, wb, empresa_id, result, solo_activos=solo_activos, limite=limite, tipo_empleado=tipo_empleado)
        # No generamos operativa además para evitar duplicar personas; solo categorías y socios.
        result.avisos.append("Modo todo_ops: importados ROLES_TARIFAS + SOCIOS. OPERATIVA_HOTEL se deja para necesidades/costes, no para duplicar empleados.")
    else:
        result.errores.append(f"Modo no reconocido: {modo}")
    conn.commit()
    return result.as_dict()
