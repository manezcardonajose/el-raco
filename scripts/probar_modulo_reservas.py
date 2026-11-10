"""Ejecuta todas las regresiones aisladas de reservas y guarda resultados."""
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PRUEBAS=(
    'probar_reservas_http.py',
    'probar_cobro_integrado.py',
    'probar_concurrencia_facturas.py',
    'probar_concurrencia_disponibilidad.py',
    'probar_disponibilidad_reservas.py', 'probar_checkin_repetido.py',
    'probar_checkout_reservas.py', 'probar_saldos_reservas.py',
    'probar_anulacion_cargos.py', 'probar_anulacion_cobros.py',
    'probar_emision_duplicada.py', 'probar_imputacion_facturas.py',
    'probar_proforma_factura.py', 'probar_estados_reservas.py',
)


def main():
    resultados=[]
    for script in PRUEBAS:
        proceso=subprocess.run([sys.executable,str(ROOT/'scripts'/script)],cwd=ROOT,
                               capture_output=True,text=True,timeout=60)
        resultados.append(dict(prueba=script,ok=proceso.returncode==0,
                               salida=proceso.stdout,error=proceso.stderr))
    output=ROOT/'docs'/'resultado_pruebas_reservas.json'
    output.write_text(json.dumps(resultados,ensure_ascii=True,indent=2),encoding='utf-8')
    correctas=sum(r['ok'] for r in resultados)
    print(f'{correctas}/{len(resultados)} pruebas correctas. Informe: {output}')
    return 0 if correctas==len(resultados) else 1


if __name__=='__main__':
    sys.exit(main())
