"""Revision reproducible de sintaxis y pantallas en una copia SQLite temporal."""
import ast
import json
import os
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    informe = {'fecha': datetime.now().isoformat(), 'sintaxis': [], 'pantallas': []}
    for folder in ('blueprints', 'helpers', 'scripts'):
        for path in (ROOT / folder).rglob('*.py'):
            try:
                ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
            except (SyntaxError, UnicodeError) as exc:
                informe['sintaxis'].append({'archivo': str(path.relative_to(ROOT)), 'error': str(exc)})
    with tempfile.TemporaryDirectory(prefix='raco_revision_', ignore_cleanup_errors=True) as folder:
        source = ROOT / 'simulacion_el_raco.db'
        path = Path(folder) / 'revision.db'
        with sqlite3.connect(str(source)) as origen, sqlite3.connect(str(path)) as copia:
            origen.backup(copia)
        origen.close()
        copia.close()
        os.environ['ERP_DATABASE'] = str(path)
        from app import app
        from scripts.prueba_fuego_rutas_simulacion import build_routes, first_id_scoped
        app.config.update(TESTING=True)
        conn=sqlite3.connect(str(path)); conn.row_factory=sqlite3.Row
        rutas=build_routes(conn, 3)
        informe['detalles_sin_datos_del_centro'] = [
            tabla for tabla in ('reservas_recepcion', 'partes_produccion', 'pedidos',
                                'recepciones', 'almacenes', 'tpv_terminales')
            if first_id_scoped(conn, tabla, 3) is None
        ]
        informe['integridad']=conn.execute('PRAGMA integrity_check').fetchone()[0]
        conn.close()
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess.update(user_id=5, user_nombre='Prueba aislada', user_rol='admin', user_empresa_id=1, user_es_global=1, centro_activo_id=3)
            for modulo, ruta in rutas:
                item={'modulo':modulo, 'ruta':ruta}
                try:
                    response=client.get(ruta, follow_redirects=False)
                    item.update(status=response.status_code, destino=response.headers.get('Location'))
                    item['resultado']='carga' if response.status_code == 200 else 'redireccion' if 300 <= response.status_code < 400 else 'error_http'
                except Exception as exc:
                    item.update(resultado='excepcion', error=f'{type(exc).__name__}: {exc}')
                informe['pantallas'].append(item)
        informe['resumen']=dict(Counter(x['resultado'] for x in informe['pantallas']))
    output=ROOT/'docs'/'resultado_revision_erp.json'
    output.write_text(json.dumps(informe,ensure_ascii=True,indent=2),encoding='utf-8')
    print(json.dumps({'informe':str(output),'errores_sintaxis':len(informe['sintaxis']),'resumen':informe['resumen']}))


if __name__ == '__main__':
    main()
