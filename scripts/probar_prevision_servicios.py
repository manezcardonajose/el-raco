"""Verifica comensales por regimen, fechas y ambito del centro."""
import sqlite3
import sys
from pathlib import Path
from flask import Flask,session
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints.produccion import routes as pr


def main():
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
    c.executescript('''
        CREATE TABLE reservas_recepcion(empresa_id,centro_id,fecha_entrada,fecha_salida,regimen,adultos,menores,estado);
        INSERT INTO reservas_recepcion VALUES
        (1,3,'2026-11-01','2026-11-03','pension_completa',2,1,'confirmada'),
        (1,3,'2026-11-01','2026-11-03','media_pension',2,0,'confirmada'),
        (1,3,'2026-11-01','2026-11-03','alojamiento_desayuno',1,0,'confirmada'),
        (1,1,'2026-11-01','2026-11-03','pension_completa',40,0,'confirmada'),
        (2,3,'2026-11-01','2026-11-03','pension_completa',50,0,'confirmada'),
        (1,3,'2026-11-01','2026-11-03','pension_completa',90,0,'cancelada'),
        (1,3,'2026-11-01','2026-11-03','pension_completa',80,0,'no_show'),
        (1,3,'2026-11-01','2026-11-03','pension_completa',0,0,'confirmada');
    ''')
    app=Flask(__name__);app.secret_key='prueba'
    with app.test_request_context('/'):
        session['centro_activo_id']=3
        for fecha,esperados in [('2026-11-01',(0,3,5)),('2026-11-02',(6,3,5)),('2026-11-03',(6,0,0))]:
            reales=tuple(pr._pax_previstos_servicio(c,1,fecha,s) for s in ('Desayuno','Almuerzo','Cena'))
            assert reales==esperados,(fecha,reales)
        c.execute('ALTER TABLE reservas_recepcion RENAME COLUMN centro_id TO sin_centro')
        assert pr._pax_previstos_servicio(c,1,'2026-11-02','Desayuno')==0
    c.close()
    print('OK: regimen, llegada, salida, cancelaciones, cero ocupantes y aislamiento de empresa/centro')


if __name__=='__main__':
    main()
