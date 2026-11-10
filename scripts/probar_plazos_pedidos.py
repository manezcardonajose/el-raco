"""Los pedidos tardios, sin fecha o no enviados no cubren produccion."""
import sqlite3
import sys
from pathlib import Path
from flask import Flask,session
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blueprints.produccion import routes as pr


def main():
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
    c.executescript('''
        CREATE TABLE pedidos(id,empresa_id,centro_id,estado,fecha_entrega);
        CREATE TABLE lineas_pedido(pedido_id,producto_id,cantidad_solicitada,cantidad_recibida);
        INSERT INTO pedidos VALUES
        (1,1,3,'enviado','2026-11-02'),(2,1,3,'enviado','2026-11-05'),
        (3,1,3,'borrador','2026-11-01'),(4,1,3,'enviado',NULL),
        (5,1,1,'enviado','2026-11-01'),(6,2,3,'enviado','2026-11-01'),
        (7,1,3,'recibido_parcial','2026-11-02');
        INSERT INTO lineas_pedido VALUES
        (1,1,10,0),(2,1,20,0),(3,1,90,0),(4,1,80,0),(5,1,70,0),(6,1,60,0),(7,1,8,3);
    ''')
    app=Flask(__name__);app.secret_key='prueba'
    with app.test_request_context('/'):
        session['centro_activo_id']=3
        assert pr._pendiente_compra_producto(c,1,1,'2026-11-01')==0
        assert pr._pendiente_compra_producto(c,1,1,'2026-11-02')==15
        assert pr._pendiente_compra_producto(c,1,1,'2026-11-05')==35
        limite=pr._inicio_preparacion_produccion('2026-11-05',72)
        assert limite.isoformat()=='2026-11-02T08:00:00'
        assert pr._pendiente_compra_producto(c,1,1,limite.date().isoformat())==15
        assert pr._inicio_preparacion_produccion('2026-11-05',0).isoformat()=='2026-11-05T08:00:00'
        assert pr._inicio_preparacion_produccion('2026-11-05',24,'18:30').isoformat()=='2026-11-04T18:30:00'
        assert pr._inicio_preparacion_produccion('2026-11-05',12,'06:00').isoformat()=='2026-11-04T18:00:00'
    c.close()
    print('OK: entrega tardia/sin fecha, borradores y otros centros excluidos; recepcion parcial resta lo recibido')


if __name__=='__main__':
    main()
