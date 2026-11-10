# Preparacion de El Raco para GitHub

Estado: pendiente de revision; no publicar ni crear un repositorio publico solo
porque existan estas exclusiones.

## Antes de publicar

1. Instalar o localizar Git y comprobar el estado real del repositorio y de sus
   remotos. Un directorio `.git` sin `HEAD` ni configuracion no es suficiente.
2. Revisar tambien los archivos ya seguidos y el historial: `.gitignore` solo
   evita agregar archivos no seguidos, no elimina datos del indice ni historial.
3. Revisar el codigo y la documentacion por credenciales, datos personales,
   clientes, proveedores, informacion interna y documentos de terceros. No
   incluir bibliotecas o material sin permiso de distribucion.
4. Sustituir credenciales que hayan quedado expuestas; nunca poner claves ni
   contrasenas en incidencias, capturas, commits ni archivos de ejemplo.
5. Preparar una plantilla de configuracion sin valores reales y verificar las
   instrucciones de instalacion en un entorno temporal y aislado.
6. Revisar las exclusiones de bases de datos, copias, archivos importados,
   documentos y datos de prueba. Anadir ejemplos ficticios solo tras revisarlos.
7. Revisar licencias de dependencias y contenido antes de decidir si publicar.
   Empezar con repositorio privado y acceso restringido hasta completar auditoria.
8. Revisar la lista exacta de archivos y el tamano antes de la primera subida.

Las exclusiones actuales son preventivas y pueden dejar fuera archivos que el
proyecto necesita. Comprobar dependencias y casos de uso antes de publicarlo.
