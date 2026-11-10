"""Crea una copia separada de codigo; nunca modifica la carpeta de trabajo."""
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
DESTINO = Path.home() / 'Desktop' / 'el-raco-github'
DIRECTORIOS = ('blueprints', 'helpers', 'ia', 'templates', 'scripts', 'tools', 'integraciones', 'utils')
EXTENSIONES = {'.py', '.html', '.css', '.js', '.svg'}
RAIZ = ('.gitignore', 'README.md', 'AGENTS.md', 'configuracion.env.example',
        'INICIAR_ERP.bat', 'INSTALAR_ERP.bat', 'requirements.txt', 'requirements-prod.txt')
PATRONES = (
    re.compile(rb'sk-proj-[A-Za-z0-9_-]{16,}'),
    re.compile(rb'AIza[0-9A-Za-z_-]{25,}'),
    re.compile(rb'gh[pousr]_[A-Za-z0-9]{20,}'),
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
)


def candidatos():
    archivos = [p for p in ROOT.glob('*.py') if p.is_file()]
    archivos.extend(ROOT / nombre for nombre in RAIZ)
    archivos.append(ROOT / 'docs' / 'preparacion_github.md')
    for directorio in DIRECTORIOS:
        archivos.extend(p for p in (ROOT / directorio).rglob('*')
                        if p.is_file() and p.suffix.lower() in EXTENSIONES
                        and '__pycache__' not in p.parts)
    for directorio in ('static/brand', 'static/img', 'static/js'):
        archivos.extend(p for p in (ROOT / directorio).rglob('*')
                        if p.is_file() and p.suffix.lower() in EXTENSIONES | {'.png', '.jpg', '.jpeg', '.webp'})
    return sorted(set(archivos))


def main():
    if DESTINO.exists():
        raise SystemExit('La carpeta de destino ya existe; no se sobrescribira.')
    archivos = candidatos()
    for archivo in archivos:
        if not archivo.is_file() or archivo.is_symlink() or not archivo.resolve().is_relative_to(ROOT):
            raise SystemExit(f'Archivo ausente o fuera del proyecto: {archivo}')
        if archivo.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
            if any(p.search(archivo.read_bytes()) for p in PATRONES):
                raise SystemExit(f'Posible credencial; revisar antes de copiar: {archivo.relative_to(ROOT)}')
    DESTINO.mkdir()
    for archivo in archivos:
        destino = DESTINO / archivo.relative_to(ROOT)
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archivo, destino)
    print(f'Copia preparada: {DESTINO}')
    print(f'Archivos copiados: {len(archivos)}')
    print('No incluye bases, .env, documentos importados ni datos de simulacion.')
    print('La busqueda de credenciales es limitada; revisar antes de publicar.')


if __name__ == '__main__':
    sys.exit(main())
