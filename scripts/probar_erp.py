"""Ejecuta las baterias aisladas de la ERP y conserva un informe conjunto."""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PRUEBAS=('probar_modulo_reservas.py','probar_circuito_produccion.py','probar_revision_erp.py',
         'probar_aislamiento_lotes.py','probar_lotes_produccion.py','probar_prevision_servicios.py',
         'probar_raciones_produccion.py','probar_plazos_pedidos.py',
         'probar_asistente_datos_insuficientes.py','probar_asistente_presupuesto_pantalla.py',
         'probar_contexto_centros.py','probar_acceso_empresas.py',
         'probar_permisos_recepcion.py','probar_ambito_costes_produccion.py',
         'probar_ambito_compras.py','probar_envio_pedido.py')


def ejecutar(script):
    inicio=time.monotonic()
    try:
        env=dict(os.environ, PYTHONIOENCODING='utf-8')
        proceso=subprocess.run([sys.executable,str(ROOT/'scripts'/script)],cwd=ROOT,
                               capture_output=True,text=True,encoding='utf-8',env=env,timeout=180)
        return dict(prueba=script,ok=proceso.returncode==0,codigo=proceso.returncode,
                    segundos=round(time.monotonic()-inicio,2),salida=proceso.stdout,error=proceso.stderr)
    except subprocess.TimeoutExpired:
        return dict(prueba=script,ok=False,error='Tiempo limite de 180 segundos superado')


def main():
    with ThreadPoolExecutor(max_workers=3) as pool:
        resultados=list(pool.map(ejecutar,PRUEBAS))
    # El proceso de pantallas puede terminar bien aunque haya registrado fallos HTTP.
    pantallas=json.loads((ROOT/'docs'/'resultado_revision_erp.json').read_text(encoding='utf-8')) if resultados[2]['ok'] else None
    if pantallas:
        fallos=[x for x in pantallas['pantallas'] if x['resultado']!='carga']
        resultados[2]['ok']=not fallos and not pantallas['sintaxis'] and pantallas['integridad']=='ok'
    informe=dict(fecha_utc=datetime.now(timezone.utc).isoformat(),baterias=resultados,
                 limites='Pruebas aisladas con algunas dependencias sustituidas; no certifican integraciones externas ni navegador.',
                 detalles_sin_datos=pantallas.get('detalles_sin_datos_del_centro',[]) if pantallas else [])
    output=ROOT/'docs'/'resultado_pruebas_erp.json'
    output.write_text(json.dumps(informe,ensure_ascii=True,indent=2),encoding='utf-8')
    for resultado in resultados:
        print(('OK' if resultado['ok'] else 'FALLO')+' '+resultado['prueba'])
    print(f'Informe: {output}')
    return 0 if all(r['ok'] for r in resultados) else 1


if __name__=='__main__':
    sys.exit(main())
