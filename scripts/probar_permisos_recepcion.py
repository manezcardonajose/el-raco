"""Prueba HTTP de permisos sobre una copia temporal de la base de simulacion."""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory(prefix='raco_permisos_', ignore_cleanup_errors=True) as folder:
        destino = Path(folder) / 'permisos.db'
        with sqlite3.connect(f"file:{(ROOT / 'simulacion_el_raco.db').as_posix()}?mode=ro", uri=True) as origen:
            with sqlite3.connect(destino) as copia:
                origen.backup(copia)
        os.environ['ERP_DATABASE'] = str(destino)
        from app import app
        from blueprints.auth.routes import _ensure_auth_schema
        conn = sqlite3.connect(destino)
        conn.row_factory = sqlite3.Row
        _ensure_auth_schema(conn)
        conn.execute("INSERT OR IGNORE INTO roles (nombre) VALUES ('cocinero')")
        rol_id = conn.execute("SELECT id FROM roles WHERE nombre='cocinero'").fetchone()['id']
        cursor = conn.execute("INSERT INTO usuarios (nombre,email,password_hash,rol_id,empresa_id,activo) VALUES ('Prueba cocina','permiso-temporal@example.invalid','no-login',?,1,1)", (rol_id,))
        usuario_id = cursor.lastrowid
        conn.commit()
        conn.close()
        app.config.update(TESTING=True)
        with app.test_client() as client:
            with client.session_transaction() as sesion:
                sesion.update(user_id=usuario_id, user_nombre='Prueba', user_rol='cocinero', user_empresa_id=1, centro_activo_id=3, _csrf_token='token-prueba')
            for ruta in ('/recepcion/', '/recepcion/reservas', '/recepcion/habitaciones'):
                respuesta = client.get(ruta, follow_redirects=False)
                assert respuesta.status_code in (302, 303), f'{ruta}: HTTP {respuesta.status_code}'
                assert respuesta.headers.get('Location', '').endswith('/'), ruta
            respuesta = client.post('/recepcion/reservas/nueva', data={'csrf_token': 'token-prueba'}, follow_redirects=False)
            assert respuesta.status_code in (302, 303), f'POST reservas: HTTP {respuesta.status_code}'
            assert respuesta.headers.get('Location', '').endswith('/')
            with client.session_transaction() as sesion:
                sesion['puede_validar_recepcion'] = 1
            respuesta = client.get('/recepcion/', follow_redirects=False)
            assert respuesta.status_code == 200, f'Permiso concedido: HTTP {respuesta.status_code}'
    print('OK: cocina sin permiso no entra en recepcion; permiso explicito permite acceso')


if __name__ == '__main__':
    main()
