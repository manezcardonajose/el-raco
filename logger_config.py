# -*- coding: utf-8 -*-
"""
Sistema de logging centralizado para pepe4.
Captura excepciones, errores y avisos de forma estructurada.
"""

import os
import logging
import logging.handlers
from datetime import datetime

def setup_logging():
    """Configura logging centralizado."""
    
    log_dir = 'logs'
    os.makedirs(log_dir, exist_ok=True)
    
    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Logger root
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Handler: Archivo de errors
    error_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, 'errors.log'),
        maxBytes=10*1024*1024,  # 10 MB
        backupCount=10
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    logger.addHandler(error_handler)
    
    # Handler: Archivo de info
    info_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, 'info.log'),
        maxBytes=10*1024*1024,
        backupCount=10
    )
    info_handler.setLevel(logging.INFO)
    info_handler.setFormatter(formatter)
    logger.addHandler(info_handler)
    
    # Handler: Console (solo en desarrollo)
    if os.getenv('ENV', 'development') == 'development':
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    return logger

# Crear logger
logger = setup_logging()

def log_exception(exc, context=""):
    """Log de excepciones con contexto."""
    logger.error(
        f"Excepción {context}: {type(exc).__name__}: {str(exc)}",
        exc_info=True
    )

def log_warning(msg, context=""):
    """Log de advertencias."""
    logger.warning(f"{context}: {msg}" if context else msg)

def log_info(msg):
    """Log de información."""
    logger.info(msg)
