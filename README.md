# El Raco ERP/PMS

Proyecto en desarrollo para la gestion de establecimientos de hosteleria.
Incluye modulos de operaciones, compras, produccion, recepcion y asistencia.
El estado de los modulos y sus limitaciones deben comprobarse antes de una beta
con datos reales; este repositorio no certifica su uso en produccion.

## Desarrollo local en Windows

Trabaja en una copia separada, con datos ficticios. Necesitas Python 3.11 o
superior y las dependencias indicadas en `requirements-prod.txt`.

- En una instalacion nueva, `INSTALAR_ERP.bat` crea el entorno virtual y ejecuta
  `scripts/preparar_instalacion.py`. Solicita datos de empresa y administrador,
  crea una base local y genera la configuracion. No lo ejecutes sobre datos o
  una configuracion existentes sin revisarlos y respaldarlos antes.
- `INICIAR_ERP.bat` arranca la aplicacion usando la configuracion local. El
  ejemplo `configuracion.env.example` no contiene claves y no se carga por si
  solo. No compartas `.env`, contrasenas ni bases de datos.
- El arranque y la instalacion deben validarse en una maquina limpia antes de
  distribuir una beta. Servicios externos, correo, pagos y conexiones oficiales
  no deben activarse en pruebas sin autorizacion.

## Pruebas

Hay pruebas aisladas en `scripts/probar_*.py`. Algunas requieren bibliotecas
opcionales o una base de simulacion local que no se incluye en el repositorio.
Antes de ejecutarlas, revisa sus fuentes, salidas y si escriben informes. Una
prueba aprobada no certifica por si sola el funcionamiento completo del modulo.

## Colaboracion y datos

Usa ramas y revisiones antes de integrar cambios. No publiques informacion
personal, bases, documentos de clientes, libros o archivos con derechos sin
autorizacion, ni credenciales. Consulta `docs/preparacion_github.md` antes de
la primera subida. El acceso por empresa y centro sigue en revision.
