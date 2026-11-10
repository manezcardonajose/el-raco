"""Pruebas del futuro modelo de acceso, sin tocar usuarios reales."""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpers.acceso_empresas import (autorizar_empresa, crear_esquema_acceso_empresas,
    inicializar_administrador_instalacion, usuario_puede_acceder_empresa)


class AccesoEmpresasTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.execute('CREATE TABLE usuarios (id INTEGER PRIMARY KEY, activo INTEGER)')
        self.conn.execute('CREATE TABLE empresas (id INTEGER PRIMARY KEY, activo INTEGER)')
        self.conn.executemany('INSERT INTO usuarios VALUES (?, ?)', [(1, 1), (2, 1), (3, 0)])
        self.conn.executemany('INSERT INTO empresas VALUES (?, ?)', [(1, 1), (2, 1), (3, 0)])
        crear_esquema_acceso_empresas(self.conn)
        inicializar_administrador_instalacion(self.conn, 1)

    def tearDown(self):
        self.conn.close()

    def test_administrador_y_autorizaciones_explicitas(self):
        self.assertTrue(usuario_puede_acceder_empresa(self.conn, 1, 1))
        self.assertTrue(usuario_puede_acceder_empresa(self.conn, 1, 2))
        self.assertFalse(usuario_puede_acceder_empresa(self.conn, 1, 3))
        self.assertFalse(usuario_puede_acceder_empresa(self.conn, 2, 1))
        autorizar_empresa(self.conn, 1, 2, 1)
        autorizar_empresa(self.conn, 1, 2, 1)
        self.assertTrue(usuario_puede_acceder_empresa(self.conn, 2, 1))
        self.assertFalse(usuario_puede_acceder_empresa(self.conn, 2, 2))
        self.conn.execute('UPDATE usuarios SET activo=0 WHERE id=2')
        self.assertFalse(usuario_puede_acceder_empresa(self.conn, 2, 1))

    def test_no_se_pueden_otorgar_ni_reinicializar_permisos_sin_autorizacion(self):
        with self.assertRaises(PermissionError):
            autorizar_empresa(self.conn, 2, 2, 2)
        with self.assertRaises(ValueError):
            autorizar_empresa(self.conn, 1, 3, 1)
        with self.assertRaises(ValueError):
            autorizar_empresa(self.conn, 1, 2, 3)
        with self.assertRaises(ValueError):
            inicializar_administrador_instalacion(self.conn, 2)


if __name__ == '__main__':
    unittest.main()
