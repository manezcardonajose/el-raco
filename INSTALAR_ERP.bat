@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Necesitas instalar Python 3.11 o superior.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" python -m venv .venv
call ".venv\Scripts\python.exe" -m pip install --upgrade pip
call ".venv\Scripts\python.exe" -m pip install -r requirements-prod.txt
if errorlevel 1 (
  echo No se pudieron instalar las dependencias.
  pause
  exit /b 1
)
call ".venv\Scripts\python.exe" scripts\preparar_instalacion.py
if errorlevel 1 (
  echo La instalacion no ha terminado correctamente.
  pause
  exit /b 1
)
echo Instalacion terminada. Ya puedes usar INICIAR_ERP.bat.
pause
