"""
WSGI entry point para producción.
Usa Gunicorn o uWSGI en lugar de Flask dev server.

Uso:
  gunicorn --workers 4 --threads 2 --worker-class gthread --bind 127.0.0.1:5001 wsgi:app
  
O para desarrollo (SOLO desarrollo):
  python wsgi.py
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

# Configurar logging antes de importar app
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Importar app
from app import app

if __name__ == '__main__':
    ENV = os.getenv('ENV', 'development').lower()
    
    if ENV == 'production':
        logger.critical("❌ NO USAR 'python wsgi.py' EN PRODUCCIÓN")
        logger.critical("   Usar: gunicorn wsgi:app --bind 127.0.0.1:5001")
        exit(1)
    
    logger.warning("⚠️ Servidor de desarrollo. No usar en producción.")
    logger.info(f"Iniciando servidor en http://127.0.0.1:5001")
    
    # Solo desarrollo: unicamente localhost
    app.run(
        host='127.0.0.1',
        port=5001,
        debug=True,
        use_reloader=True
    )
