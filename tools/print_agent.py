"""
Agente local de impresion para El Raco ERP.

Ejemplo:
python tools/print_agent.py --base-url http://127.0.0.1:5001 --device UID --token TOKEN

El agente consulta la cola de impresion de su dispositivo y manda el HTML al sistema
de impresion local. En Windows intenta usar el verbo "printto" cuando hay impresora
asignada; si no, usa la impresora predeterminada.
"""

import argparse
import ctypes
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes


AGENT_VERSION = "0.2.0"


def _url(base_url, path, params=None):
    base = base_url.rstrip("/")
    query = urllib.parse.urlencode(params or {})
    return f"{base}{path}" + (f"?{query}" if query else "")


def _json_get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _text_get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _json_post(url, payload, timeout=10):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def marcar_estado(base_url, empresa_id, device_uid, token, job_id, estado, error=""):
    payload = {
        "empresa_id": empresa_id,
        "device_uid": device_uid,
        "token": token,
        "estado": estado,
        "error": error,
    }
    return _json_post(_url(base_url, f"/empresas/impresion/api/trabajos/{job_id}/estado"), payload)


def enviar_ping(base_url, empresa_id, device_uid, token):
    payload = {
        "empresa_id": empresa_id,
        "device_uid": device_uid,
        "token": token,
        "version": AGENT_VERSION,
    }
    return _json_post(_url(base_url, "/empresas/impresion/api/ping"), payload)


def imprimir_html(contenido_html, printer_name=""):
    fd, path = tempfile.mkstemp(prefix="el_raco_label_", suffix=".html")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(contenido_html)

    if os.name == "nt":
        if printer_name:
            # printto depende de la aplicacion asociada a .html. En Windows suele
            # funcionar con navegadores/IE heredado; si falla, dejamos error claro.
            os.startfile(path, "printto", f'"{printer_name}"')  # type: ignore[attr-defined]
        else:
            os.startfile(path, "print")  # type: ignore[attr-defined]
        return path

    cmd = ["lp"]
    if printer_name:
        cmd.extend(["-d", printer_name])
    cmd.append(path)
    subprocess.run(cmd, check=True)
    return path


def _windows_default_printer():
    winspool = ctypes.WinDLL("winspool.drv")
    needed = wintypes.DWORD(0)
    winspool.GetDefaultPrinterW(None, ctypes.byref(needed))
    if needed.value <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(needed.value)
    if not winspool.GetDefaultPrinterW(buffer, ctypes.byref(needed)):
        return ""
    return buffer.value


def _windows_raw_print(printer_name, raw_data):
    class DOC_INFO_1(ctypes.Structure):
        _fields_ = [
            ("pDocName", wintypes.LPWSTR),
            ("pOutputFile", wintypes.LPWSTR),
            ("pDatatype", wintypes.LPWSTR),
        ]

    printer_name = printer_name or _windows_default_printer()
    if not printer_name:
        raise RuntimeError("No hay impresora asignada ni impresora predeterminada")

    winspool = ctypes.WinDLL("winspool.drv")
    handle = wintypes.HANDLE()
    if not winspool.OpenPrinterW(printer_name, ctypes.byref(handle), None):
        raise ctypes.WinError()
    try:
        doc_info = DOC_INFO_1("El Raco etiqueta", None, "RAW")
        job_id = winspool.StartDocPrinterW(handle, 1, ctypes.byref(doc_info))
        if job_id == 0:
            raise ctypes.WinError()
        try:
            if not winspool.StartPagePrinter(handle):
                raise ctypes.WinError()
            written = wintypes.DWORD(0)
            data = raw_data.encode("utf-8") if isinstance(raw_data, str) else raw_data
            buffer = ctypes.create_string_buffer(data)
            if not winspool.WritePrinter(handle, buffer, len(data), ctypes.byref(written)):
                raise ctypes.WinError()
            winspool.EndPagePrinter(handle)
        finally:
            winspool.EndDocPrinter(handle)
    finally:
        winspool.ClosePrinter(handle)


def imprimir_raw(contenido_raw, printer_name=""):
    if os.name == "nt":
        _windows_raw_print(printer_name, contenido_raw)
        return "RAW Windows"
    cmd = ["lp", "-o", "raw"]
    if printer_name:
        cmd.extend(["-d", printer_name])
    process = subprocess.run(cmd, input=contenido_raw.encode("utf-8"), check=True)
    return f"RAW lp {process.returncode}"


def procesar_trabajo(base_url, empresa_id, device_uid, token, job):
    job_id = job["id"]
    marcar_estado(base_url, empresa_id, device_uid, token, job_id, "procesando")
    lenguaje = (job.get("lenguaje_impresion") or "html").lower()
    if lenguaje in {"zpl", "escpos", "raw"}:
        contenido = _text_get(
            _url(
                base_url,
                f"/empresas/impresion/api/trabajos/{job_id}/raw",
                {"empresa_id": empresa_id, "device_uid": device_uid, "token": token},
            )
        )
        imprimir_raw(contenido, job.get("impresora_nombre") or "")
    else:
        contenido = _text_get(
            _url(
                base_url,
                f"/empresas/impresion/api/trabajos/{job_id}/contenido",
                {"empresa_id": empresa_id, "device_uid": device_uid, "token": token},
            )
        )
        imprimir_html(contenido, job.get("impresora_nombre") or "")
    marcar_estado(base_url, empresa_id, device_uid, token, job_id, "impreso")


def main():
    parser = argparse.ArgumentParser(description="Agente local de impresion de El Raco ERP")
    parser.add_argument("--base-url", default="http://127.0.0.1:5001")
    parser.add_argument("--empresa-id", type=int, default=1)
    parser.add_argument("--device", required=True, help="device_uid mostrado en la ERP")
    parser.add_argument("--token", required=True, help="token del dispositivo mostrado en la ERP")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true", help="procesa una vez y termina")
    args = parser.parse_args()

    print("Agente de impresion El Raco iniciado")
    print(f"ERP: {args.base_url} | empresa {args.empresa_id} | equipo {args.device[-10:]}")

    while True:
        try:
            enviar_ping(args.base_url, args.empresa_id, args.device, args.token)
            data = _json_get(
                _url(
                    args.base_url,
                    "/empresas/impresion/api/trabajos",
                    {
                        "empresa_id": args.empresa_id,
                        "device_uid": args.device,
                        "token": args.token,
                        "version": AGENT_VERSION,
                    },
                )
            )
            for job in data.get("jobs", []):
                try:
                    print(f"Imprimiendo trabajo #{job['id']} -> {job.get('impresora_nombre') or 'predeterminada'}")
                    procesar_trabajo(args.base_url, args.empresa_id, args.device, args.token, job)
                except Exception as exc:
                    print(f"Error en trabajo #{job.get('id')}: {exc}")
                    marcar_estado(args.base_url, args.empresa_id, args.device, args.token, job["id"], "error", str(exc))
        except urllib.error.HTTPError as exc:
            print(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}")
        except Exception as exc:
            print(f"Esperando ERP/cola: {exc}")

        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
