"""Prueba el mensaje de fallo del envio sin mandar correos reales."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import Flask, get_flashed_messages, session
from blueprints.compras import routes as compras


class EnvioPedidoTest(unittest.TestCase):
    def test_correo_aceptado_sin_guardar_estado_no_invita_a_reenviar(self):
        app = Flask(__name__)
        app.secret_key = 'solo-prueba'
        with app.test_request_context('/', method='POST'):
            session.update(user_id=1, user_nombre='Prueba', user_empresa_id=1)
            conn = MagicMock()
            pedido = {'estado': 'autorizado', 'departamento_id': 1, 'proveedor_id': 1,
                      'proveedor_nombre': 'Prueba', 'email': 'proveedor@example.invalid',
                      'numero_pedido': 'TEST', 'departamento_nombre': 'Cocina'}
            conn.execute.return_value.fetchone.return_value = pedido
            conn.execute.return_value.fetchall.return_value = []
            mail = MagicMock()
            fake_weasyprint = types.SimpleNamespace(HTML=lambda **_: MagicMock(write_pdf=lambda: b'pdf'))
            with patch.object(compras, 'get_db', side_effect=[conn, RuntimeError('fallo simulado de BD')]), \
                 patch.object(compras, '_scope_departamento', return_value=('p.empresa_id = ?', [1])), \
                 patch.object(compras, '_requiere_gestion_compras', return_value=None), \
                 patch.object(compras, '_pedido_preparado_para_autorizacion', return_value=(True, '')), \
                 patch.object(compras, '_obtener_nombre_empresa', return_value='Empresa prueba'), \
                 patch.object(compras, '_obtener_mail_actual', return_value=mail), \
                 patch.object(compras, '_asegurar_config_mail_desde_env', return_value='prueba@example.invalid'), \
                 patch.object(compras, 'render_template', return_value='<p>prueba</p>'), \
                 patch.object(compras, 'url_for', return_value='/compras'), \
                 patch.dict(sys.modules, {'weasyprint': fake_weasyprint}):
                respuesta = compras.enviar_pedido_por_email(1)
            self.assertEqual(respuesta.status_code, 302)
            mail.send.assert_called_once()
            mensajes = ' '.join(get_flashed_messages())
            self.assertIn('no se pudo registrar el estado', mensajes)
            self.assertIn('No lo reenvies', mensajes)


if __name__ == '__main__':
    unittest.main()
