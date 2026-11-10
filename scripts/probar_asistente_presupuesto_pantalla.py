"""Comprueba la pantalla del copiloto sobre una copia temporal de simulacion."""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory(prefix='raco_copiloto_', ignore_cleanup_errors=True) as folder:
        path = Path(folder) / 'pantalla.db'
        with sqlite3.connect(f"file:{(ROOT / 'simulacion_el_raco.db').as_posix()}?mode=ro", uri=True) as original:
            with sqlite3.connect(path) as copia:
                original.backup(copia)
        os.environ['ERP_DATABASE'] = str(path)
        from app import app
        from blueprints.contabilidad.routes import _ensure_schema
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        with app.test_request_context('/'):
            _ensure_schema(conn)
        cur = conn.execute("INSERT INTO contabilidad_presupuestos (empresa_id, centro_id, anio, nombre) VALUES (1, 3, 2098, 'Prueba copiloto aislada')")
        presupuesto = cur.lastrowid
        ajeno = conn.execute("INSERT INTO contabilidad_presupuestos (empresa_id, centro_id, anio, nombre) VALUES (2, 4, 2098, 'Prueba privada otra empresa')").lastrowid
        conn.executemany(
            "INSERT INTO contabilidad_presupuesto_lineas (presupuesto_id, empresa_id, centro_id, anio, mes, tipo, bloque, concepto, importe, fiabilidad) VALUES (?, 1, 3, 2098, 1, ?, 'prueba', 'prueba', ?, ?)",
            [(presupuesto, 'ingreso', 100, 'simulado'), (presupuesto, 'gasto', 40, 'pendiente')],
        )
        conn.commit()
        conn.close()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(user_id=5, user_nombre='Prueba aislada', user_rol='admin', user_empresa_id=1, user_es_global=1, centro_activo_id=3, _csrf_token='token-prueba')
            response = client.post('/asistente/operativo', data={'presupuesto_id': presupuesto, 'peticion': 'Revisa los costes', 'csrf_token': 'token-prueba'})
            assert response.status_code == 200, f'HTTP {response.status_code}'
            html = response.get_data(as_text=True)
            assert 'Prueba copiloto aislada' in html
            assert 'Es una prevision, no un resultado real' in html
            assert '1 lineas pendientes' in html
            assert 'hay margen positivo' not in html
            response = client.post('/asistente/operativo', data={'presupuesto_id': 999999999, 'peticion': 'Revisa los costes', 'csrf_token': 'token-prueba'})
            assert response.status_code == 200
            assert 'No se ha podido consultar ese presupuesto' in response.get_data(as_text=True)
            response = client.post('/asistente/operativo', data={'presupuesto_id': ajeno, 'peticion': 'Revisa los costes', 'csrf_token': 'token-prueba'})
            assert response.status_code == 200
            html = response.get_data(as_text=True)
            assert 'No se ha podido consultar ese presupuesto' in html
            assert 'Prueba privada otra empresa' not in html
    print('OK: pantalla del copiloto con presupuesto seleccionado y seleccion invalida; copia temporal')


if __name__ == '__main__':
    main()
