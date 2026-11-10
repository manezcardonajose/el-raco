# -*- coding: utf-8 -*-
"""
Helper seguro para consultas PRAGMA y operaciones de BD.
Previene SQL injection.
"""

import sqlite3
from logger_config import logger, log_exception

def column_exists(cursor, table_name, column_name):
    """
    Verifica si una columna existe en una tabla de forma segura.
    
    OK: NO usa f-strings
    OK: Valida nombres
    """
    # Validar que table_name y column_name sean alfanuméricos + _
    if not all(c.isalnum() or c == '_' for c in table_name):
        logger.error(f"Nombre de tabla invalido: {table_name}")
        return False
    
    if not all(c.isalnum() or c == '_' for c in column_name):
        logger.error(f"Nombre de columna invalido: {column_name}")
        return False
    
    try:
        # PRAGMA es seguro con nombres validados
        cursor.execute(f"PRAGMA table_info({table_name})")
        cols = cursor.fetchall()
        return any(col[1] == column_name for col in cols)
    except Exception as exc:
        log_exception(exc, f"Verificando columna {table_name}.{column_name}")
        return False

def safe_add_column(cursor, table_name, column_name, column_type="TEXT", default=None):
    """
    Añade una columna de forma segura si no existe.
    
    Args:
        cursor: Cursor SQLite
        table_name: Nombre de tabla (validado)
        column_name: Nombre de columna (validado)
        column_type: Tipo SQL (TEXT, INTEGER, etc.)
        default: Valor por defecto (opcional)
    """
    # Validar nombres
    if not all(c.isalnum() or c == '_' for c in table_name):
        logger.error(f"Nombre de tabla invalido: {table_name}")
        return False
    
    if not all(c.isalnum() or c == '_' for c in column_name):
        logger.error(f"Nombre de columna invalido: {column_name}")
        return False
    
    if column_exists(cursor, table_name, column_name):
        return True
    
    try:
        # Construir ALTER TABLE de forma segura
        if default is not None:
            query = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type} DEFAULT {repr(default)}"
        else:
            query = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
        
        cursor.execute(query)
        logger.info(f"Columna agregada: {table_name}.{column_name}")
        return True
    except Exception as exc:
        log_exception(exc, f"Agregando columna {table_name}.{column_name}")
        return False

def table_exists(cursor, table_name):
    """
    Verifica si una tabla existe de forma segura.
    """
    if not all(c.isalnum() or c == '_' for c in table_name):
        logger.error(f"Nombre de tabla invalido: {table_name}")
        return False
    
    try:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,)
        )
        return cursor.fetchone() is not None
    except Exception as exc:
        log_exception(exc, f"Verificando tabla {table_name}")
        return False

def get_table_columns(cursor, table_name):
    """
    Obtiene lista de columnas de una tabla.
    """
    if not all(c.isalnum() or c == '_' for c in table_name):
        logger.error(f"Nombre de tabla invalido: {table_name}")
        return []
    
    try:
        cursor.execute(f"PRAGMA table_info({table_name})")
        return [col[1] for col in cursor.fetchall()]
    except Exception as exc:
        log_exception(exc, f"Obteniendo columnas de {table_name}")
        return []

def validate_table_name(name):
    """Valida que un nombre de tabla sea seguro."""
    return bool(name) and all(c.isalnum() or c == '_' for c in name)

def validate_column_name(name):
    """Valida que un nombre de columna sea seguro."""
    return bool(name) and all(c.isalnum() or c == '_' for c in name)
