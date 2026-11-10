"""Regresion de escalado de ficha y cantidades sobrantes en raciones."""
import sys
from pathlib import Path
from unittest.mock import Mock
from werkzeug.exceptions import BadRequest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints.produccion import routes as pr


def main():
    ficha={'nombre':'Arroz','raciones_base':4}
    calculo=pr._calcular_linea_buffet(100,'Almuerzo','Cocina','Guarniciones',ficha)
    assert calculo['cantidad_planificada']==calculo['raciones_planificadas']/4
    linea={'cantidad_planificada':2.5,'cantidad_real':2.5,'raciones_planificadas':10}
    assert pr._cantidad_prevista_ingrediente(linea,0.4)==1
    pr._validar_sobrante(Mock(),linea,'desecho',8,'raciones')
    linea['cantidad_real']=1.25
    pr._validar_sobrante(Mock(),linea,'desecho',5,'raciones')
    try:
        pr._validar_sobrante(Mock(),linea,'desecho',6,'raciones')
        raise AssertionError('Sobrante superior a raciones reales aceptado')
    except BadRequest:
        pass
    cero=pr._calcular_linea_buffet(0,'Almuerzo','Cocina','Guarniciones',ficha)
    assert cero['cantidad_planificada']==0 and cero['raciones_planificadas']==0
    assert pr._cantidad_prevista_ingrediente({'cantidad_real':0,'cantidad_planificada':2.5},0.4)==0
    print('OK: ficha de 4 raciones escalada; multiplicador distinto de raciones; sobrante limitado a produccion real; cero respetado')


if __name__=='__main__':
    main()
