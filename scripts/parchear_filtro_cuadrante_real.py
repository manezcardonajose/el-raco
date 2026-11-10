from pathlib import Path
import shutil

BASE = Path.cwd()
src = Path(__file__).resolve().parents[1] / "blueprints" / "rrhh" / "routes.py"
dst = BASE / "blueprints" / "rrhh" / "routes.py"

if not dst.exists():
    raise SystemExit(f"No encuentro {dst}. Ejecuta este script desde la raíz del proyecto pepe4.")

backup = dst.with_suffix(".py.bak_filtro_cuadrante_real")
shutil.copy2(dst, backup)
shutil.copy2(src, dst)

print("OK: routes.py actualizado.")
print(f"Copia de seguridad: {backup}")
