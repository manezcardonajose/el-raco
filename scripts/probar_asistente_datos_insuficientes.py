"""Regresion del asistente sin acceso a bases de trabajo ni servicios externos."""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blueprints.asistente.operativo_core import (
    _lectura_recetas,
    _lectura_compras,
    _count_table,
    _sum_table,
    _latest_rows,
    construir_contexto_operativo,
    presupuesto_operativo_seleccionado,
    responder_cerebro_operativo,
)


class AsistenteDatosInsuficientesTest(unittest.TestCase):
    def test_consultas_respetan_empresa_y_centro_y_fallan_cerradas(self):
        with sqlite3.connect(':memory:') as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('CREATE TABLE datos (id INTEGER, empresa_id INTEGER, centro_id INTEGER, importe REAL)')
            conn.executemany('INSERT INTO datos VALUES (?, ?, ?, ?)', [(1, 1, 1, 10), (2, 1, 2, 20), (3, 2, 1, 30)])
            self.assertEqual(_count_table(conn, 'datos', 1, 1), 1)
            self.assertEqual(_sum_table(conn, 'datos', 'importe', 1, 1), 10)
            self.assertEqual([r['id'] for r in _latest_rows(conn, 'datos', 1, 1)], [1])
            self.assertEqual(_count_table(conn, 'datos', 1, None), 2)
            conn.execute('CREATE TABLE sin_centro (id INTEGER, empresa_id INTEGER, importe REAL)')
            conn.execute('INSERT INTO sin_centro VALUES (1, 1, 100)')
            self.assertEqual(_count_table(conn, 'sin_centro', 1, 1), 0)
            self.assertEqual(_sum_table(conn, 'sin_centro', 'importe', 1, 1), 0)
            self.assertEqual(_latest_rows(conn, 'sin_centro', 1, 1), [])
            conn.execute('CREATE TABLE sin_empresa (id INTEGER, centro_id INTEGER)')
            conn.execute('INSERT INTO sin_empresa VALUES (1, 1)')
            self.assertEqual(_count_table(conn, 'sin_empresa', 1, 1), 0)

    def test_base_vacia_no_afirma_beneficios_ni_seguridad(self):
        with sqlite3.connect(':memory:') as conn:
            conn.row_factory = sqlite3.Row
            contexto = construir_contexto_operativo(conn, 1, 1)
            respuesta = responder_cerebro_operativo('Revisa los costes', contexto)['respuesta']
        self.assertIn('no hay importes suficientes', respuesta)
        self.assertNotIn('hay margen positivo', respuesta)
        self.assertIn('no se puede confirmar su seguridad', respuesta)
        self.assertIn('No se pueden comprobar todos los datos de compras', respuesta)
        self.assertNotIn('Crear proveedores base', respuesta)

    def test_compras_no_confunde_tablas_ausentes_con_proveedores_ausentes(self):
        self.assertEqual(_lectura_compras({})['estado'], 'datos_insuficientes')
        compras = {'datos_basicos_disponibles': True, 'proveedores_activos': 0}
        contexto = {'diagnosticos': {'compras': compras}}
        self.assertEqual(_lectura_compras(contexto)['estado'], 'sin_proveedores')
        compras.update(proveedores_activos=1, productos_comprables=1)
        self.assertEqual(_lectura_compras(contexto)['estado'], 'pendiente_validacion')

    def test_presupuestos_no_son_rentabilidad_confirmada(self):
        with sqlite3.connect(':memory:') as conn:
            conn.row_factory = sqlite3.Row
            contexto = construir_contexto_operativo(conn, 1, 1)
        for ingresos, gastos in ((100, 100), (100, 60), (60, 100)):
            with self.subTest(ingresos=ingresos, gastos=gastos):
                contexto['indicadores'].update(
                    presupuesto_ingresos=ingresos, presupuesto_gastos=gastos,
                    presupuesto_resultado=ingresos-gastos,
                )
                respuesta = responder_cerebro_operativo('Revisa los costes', contexto)['respuesta']
                self.assertIn('no hay importes suficientes y comparables', respuesta)
                self.assertNotIn('hay margen positivo', respuesta)

    def test_varios_presupuestos_no_producen_kpi_verde(self):
        with sqlite3.connect(':memory:') as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('CREATE TABLE contabilidad_presupuesto_lineas (empresa_id INTEGER, centro_id INTEGER, presupuesto_id INTEGER, anio INTEGER, tipo TEXT, importe REAL)')
            conn.executemany('INSERT INTO contabilidad_presupuesto_lineas VALUES (?, ?, ?, ?, ?, ?)', [
                (1, 1, 1, 2026, 'ingreso', 100), (1, 1, 1, 2026, 'gasto', 90),
                (1, 1, 2, 2027, 'ingreso', 100), (1, 1, 2, 2027, 'gasto', 20),
                (1, 2, 3, 2026, 'ingreso', 9999),
            ])
            contexto = construir_contexto_operativo(conn, 1, 1)
        kpi = next(k for k in contexto['kpis']['items'] if k['clave'] == 'finanzas_margen')
        self.assertEqual(kpi['estado'], 'sin_dato')
        self.assertIsNone(kpi['valor'])
        respuesta = responder_cerebro_operativo('Revisa los costes', contexto)['respuesta']
        self.assertIn('Presupuesto: sin dato comparable', respuesta)
        self.assertNotIn('hay margen positivo', respuesta)

    def test_recetas_requieren_datos_y_validacion(self):
        self.assertEqual(_lectura_recetas({})['estado'], 'datos_insuficientes')
        diagnostico = {'datos_basicos_disponibles': True, 'catalogo_total': 1}
        contexto = {'diagnosticos': {'recetas': diagnostico}}
        self.assertEqual(_lectura_recetas(contexto)['estado'], 'pendiente_validacion')
        diagnostico['usadas_menu_sin_alergenos'] = 1
        self.assertEqual(_lectura_recetas(contexto)['estado'], 'bloqueo_seguridad')

    def test_presupuesto_seleccionado_aislado_y_con_fiabilidad(self):
        with sqlite3.connect(':memory:') as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('CREATE TABLE contabilidad_presupuestos (id INTEGER, empresa_id INTEGER, centro_id INTEGER, anio INTEGER, nombre TEXT)')
            conn.execute('CREATE TABLE contabilidad_presupuesto_lineas (presupuesto_id INTEGER, empresa_id INTEGER, centro_id INTEGER, anio INTEGER, tipo TEXT, importe REAL, fiabilidad TEXT)')
            conn.executemany('INSERT INTO contabilidad_presupuestos VALUES (?, ?, ?, ?, ?)', [(1, 1, 1, 2027, 'Escenario A'), (2, 2, 2, 2027, 'Privado')])
            conn.executemany('INSERT INTO contabilidad_presupuesto_lineas VALUES (?, ?, ?, ?, ?, ?, ?)', [
                (1, 1, 1, 2027, 'ingreso', 100, 'simulado'),
                (1, 1, 1, 2027, 'gasto', 40, 'pendiente'),
                (1, 1, 2, 2027, 'ingreso', 9000, 'estimado'),
                (1, 1, 1, 2026, 'ingreso', 8000, 'estimado'),
            ])
            dato = presupuesto_operativo_seleccionado(conn, 1, 1, 1)
            self.assertEqual((dato['ingresos'], dato['gastos'], dato['resultado']), (100, 40, 60))
            self.assertEqual((dato['pendientes'], dato['supuestos']), (1, 1))
            self.assertFalse(dato['completo'])
            self.assertIsNone(presupuesto_operativo_seleccionado(conn, 1, 1, 2))
            self.assertIsNone(presupuesto_operativo_seleccionado(conn, 1, 2, 1))
            self.assertIsNone(presupuesto_operativo_seleccionado(conn, 1, None, 1))
            contexto = construir_contexto_operativo(conn, 1, 1, presupuesto_id=1)
            respuesta = responder_cerebro_operativo('Revisa los costes', contexto)['respuesta']
            self.assertIn('Escenario A (2027)', respuesta)
            self.assertIn('Es una prevision, no un resultado real', respuesta)


if __name__ == '__main__':
    unittest.main()
