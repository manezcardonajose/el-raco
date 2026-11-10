@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Primero debes ejecutar INSTALAR_ERP.bat.
  pause
  exit /b 1
)
call ".venv\Scripts\python.exe" scripts\iniciar_erp.py
if errorlevel 1 pause
