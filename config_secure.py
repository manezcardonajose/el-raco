# -*- coding: utf-8 -*-
"""
Configuración segura de la aplicación Flask.
Valida variables de entorno requeridas en producción.
"""

import os
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

# ======================================
# VALIDAR CONFIGURACIÓN EN PRODUCCIÓN
# ======================================

ENV = os.getenv('ENV', 'development').lower()
IS_PRODUCTION = ENV in {'prod', 'production'}
IS_LOCAL = ENV in {'local', 'desktop'}

def validate_production_config():
    """Valida que la configuración sea segura en producción."""
    if not IS_PRODUCTION:
        return
    
    required_vars = {
        'SECRET_KEY': 'Clave secreta para sesiones',
    }
    
    missing = []
    for var, desc in required_vars.items():
        if not os.getenv(var):
            missing.append(f"{var} ({desc})")
    if os.getenv('MAIL_USERNAME') and not os.getenv('MAIL_PASSWORD'):
        missing.append('MAIL_PASSWORD (obligatoria solo cuando se configura MAIL_USERNAME)')
    
    if missing:
        msg = f"CONFIGURACIÓN INCOMPLETA EN PRODUCCIÓN:\n" + "\n".join(f"  - {m}" for m in missing)
        logger.critical(msg)
        raise RuntimeError(msg)

# ======================================
# SEGURIDAD DE SESIÓN
# ======================================

def get_secret_key():
    """Obtiene SECRET_KEY validando que sea segura."""
    secret = os.getenv('SECRET_KEY')
    
    if not secret:
        if IS_PRODUCTION:
            raise RuntimeError("SECRET_KEY es requerida en producción")
        logger.warning("⚠️ Usando SECRET_KEY insegura en desarrollo. No usar en producción.")
        secret = "dev-insecure-key-cambiar-en-produccion"
    
    if len(secret) < 32 and IS_PRODUCTION:
        raise RuntimeError("SECRET_KEY debe tener mínimo 32 caracteres en producción")
    
    return secret

# ======================================
# CONFIGURACIÓN DE APLICACIÓN
# ======================================

class Config:
    """Configuración base."""
    
    # Seguridad
    SECRET_KEY = get_secret_key()
    SESSION_COOKIE_SECURE = os.getenv('ERP_COOKIE_SECURE', 'false').strip().lower() in {'1', 'true', 'yes', 'on'}
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)
    
    # Servidor
    ENV = ENV
    DEBUG = ENV == 'development'
    TESTING = False
    
    # Base de datos
    DATABASE = os.getenv('ERP_DATABASE', 'pepe4.db')
    DB_TIMEOUT = int(os.getenv('DB_TIMEOUT', 10))
    
    # Uploads
    MAX_CONTENT_LENGTH = int(os.getenv('MAX_CONTENT_LENGTH', 25 * 1024 * 1024))
    UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', 'uploads')
    
    # Email
    MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.getenv('MAIL_PORT', 587))
    MAIL_USE_TLS = os.getenv('MAIL_USE_TLS', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
    MAIL_USERNAME = os.getenv('MAIL_USERNAME')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER') or MAIL_USERNAME
    
    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'WARNING' if IS_PRODUCTION else 'INFO')
    LOG_FILE = os.getenv('LOG_FILE', 'logs/pepe4.log')
    
    # IA
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    
    # Herramientas externas
    TESSERACT_CMD_PATH = os.getenv('TESSERACT_CMD_PATH', r'C:\Program Files\Tesseract-OCR\tesseract.exe')

class DevelopmentConfig(Config):
    """Configuración de desarrollo."""
    DEBUG = True
    SESSION_COOKIE_SECURE = False

class ProductionConfig(Config):
    """Configuración de producción."""
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    TESTING = False


class LocalConfig(Config):
    """Ejecucion comercial en un unico equipo o red interna sin TLS."""
    DEBUG = False
    SESSION_COOKIE_SECURE = False
    TESTING = False

# Validar al importar
try:
    validate_production_config()
except RuntimeError as e:
    if IS_PRODUCTION:
        raise
    logger.warning(f"Advertencia de configuración: {e}")

def get_config():
    """Retorna la configuración según el ambiente."""
    if IS_PRODUCTION:
        return ProductionConfig()
    if IS_LOCAL:
        return LocalConfig()
    return DevelopmentConfig()
