"""Restablecimiento local autorizado del administrador de la recuperacion."""
import os
import secrets
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from werkzeug.security import generate_password_hash


def main():
    root=Path(__file__).resolve().parents[1]
    db=root/'instance'/'recuperacion_junio.db'
    if not db.is_file():
        raise SystemExit('No existe la base de recuperacion.')
    email='restelraco@gmail.com'
    conn=sqlite3.connect(db)
    usuario=conn.execute('SELECT id,activo FROM usuarios WHERE LOWER(email)=?',(email,)).fetchone()
    if not usuario or not usuario[1]:
        conn.close()
        raise SystemExit('Administrador no encontrado o inactivo.')
    backup=root/'backups'/('recuperacion_pre_reset_admin_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.db')
    copia=sqlite3.connect(backup)
    conn.backup(copia);copia.close()
    salida=root/'instance'/('acceso_admin_temporal_'+secrets.token_hex(4)+'.txt')
    salida.touch(exist_ok=False)
    if os.name=='nt':
        usuario_windows=subprocess.check_output(['whoami'],text=True).strip()
        subprocess.run(['icacls',str(salida),'/inheritance:r','/grant:r',usuario_windows+':F'],check=True,capture_output=True)
    else:
        salida.chmod(0o600)
    clave=secrets.token_urlsafe(20)
    salida.write_text('Administrador: '+email+'\nClave temporal: '+clave+'\nAcceso: http://192.168.8.5:5001/auth/login\nEl sistema pedira cambiar la clave al entrar.\n',encoding='utf-8')
    try:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('UPDATE usuarios SET password_hash=?, debe_cambiar_password=1 WHERE id=?',(generate_password_hash(clave),usuario[0]))
        conn.execute("DELETE FROM erp_login_intentos WHERE LOWER(identificador)=? AND exitoso=0",(email,))
        conn.commit()
    except Exception:
        conn.rollback()
        salida.unlink()
        raise
    finally:
        conn.close()
    print('Restablecimiento completado. Archivo local: '+str(salida))
    print('Copia previa: '+str(backup))


if __name__=='__main__':
    main()
