# -*- coding: utf-8 -*-
"""Busca fuentes externas para investigaciones creadas por el simulador.

Guarda resultados como fuentes de conocimiento pendientes de revision. No las
convierte en verdad operativa hasta que alguien las revise.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sqlite3
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1


DOMINIOS_DESCARTADOS = {
    "www.bing.com", "bing.com", "go.microsoft.com", "login.live.com",
    "www.google.com", "google.com", "webcache.googleusercontent.com",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Buscar fuentes externas para investigaciones del simulador.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--max-investigaciones", type=int, default=3)
    parser.add_argument("--resultados", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_fuentes_conocimiento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            tipo TEXT DEFAULT 'otro',
            ambito TEXT DEFAULT 'multi_hosteleria',
            area TEXT DEFAULT 'mercado',
            titulo TEXT NOT NULL,
            url TEXT,
            autor TEXT,
            resumen TEXT,
            lectura TEXT,
            uso_recomendado TEXT,
            riesgos TEXT,
            confianza_pct REAL DEFAULT 30,
            estado TEXT DEFAULT 'pendiente_revision',
            lectura_ia TEXT,
            usuario TEXT
        )
        """
    )


def limpiar_url(href: str) -> str:
    href = unescape(href or "")
    parsed_redirect = urlparse(href)
    if parsed_redirect.netloc.lower() in {"www.bing.com", "bing.com"} and parsed_redirect.path.startswith("/ck/"):
        qs = parse_qs(parsed_redirect.query)
        encoded = (qs.get("u") or [""])[0]
        if encoded.startswith("a1"):
            raw = encoded[2:]
            try:
                padding = "=" * (-len(raw) % 4)
                href = base64.urlsafe_b64decode((raw + padding).encode("ascii")).decode("utf-8", errors="replace")
            except Exception:
                href = ""
        elif encoded:
            href = unquote(encoded)
    if href.startswith("/"):
        return ""
    if not href.startswith("http"):
        return ""
    parsed = urlparse(href)
    dominio = parsed.netloc.lower()
    if not dominio or dominio in DOMINIOS_DESCARTADOS:
        return ""
    if "bing.com" in dominio or "microsoft.com" in dominio:
        return ""
    if re.search(r"\.(css|js|png|jpg|jpeg|gif|svg|ico)(\?|$)", parsed.path, re.I):
        return ""
    return href.split("#", 1)[0]


def texto_limpio(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html or "")
    html = re.sub(r"(?is)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", unescape(html)).strip()


def buscar(consulta: str, limite: int) -> list[dict]:
    url = f"https://www.bing.com/search?q={quote_plus(consulta)}"
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urlopen(req, timeout=15) as response:
            html = response.read(1_500_000).decode("utf-8", errors="replace")
    except Exception:
        return []

    tokens_consulta = tokens_relevantes(consulta)
    resultados: list[dict] = []
    for bloque in re.findall(r"(?is)<li class=\"b_algo\".*?</li>", html):
        h2_match = re.search(r"(?is)<h2.*?</h2>", bloque)
        href_match = re.search(r"href=[\"']([^\"']+)[\"']", h2_match.group(0) if h2_match else bloque)
        if not href_match:
            continue
        href = limpiar_url(href_match.group(1))
        if not href:
            continue
        titulo = texto_limpio(h2_match.group(0)) if h2_match else href
        resumen_match = re.search(r"(?is)<p>(.*?)</p>", bloque)
        resumen = texto_limpio(resumen_match.group(1)) if resumen_match else ""
        if any(r["url"] == href for r in resultados):
            continue
        candidato = {"titulo": titulo[:240], "url": href, "resumen": resumen[:700]}
        if score_relevancia(candidato, tokens_consulta) < 2:
            continue
        resultados.append(candidato)
        if len(resultados) >= limite:
            break
    if len(resultados) < limite:
        for href_raw in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
            href = limpiar_url(href_raw)
            if not href or any(r["url"] == href for r in resultados):
                continue
            dominio = urlparse(href).netloc.lower()
            if dominio.startswith("r.") or dominio.startswith("th."):
                continue
            candidato = {"titulo": dominio, "url": href, "resumen": ""}
            if score_relevancia(candidato, tokens_consulta) < 2:
                continue
            resultados.append(candidato)
            if len(resultados) >= limite:
                break
    return resultados


def tokens_relevantes(texto: str) -> set[str]:
    stop = {
        "gestion", "mejores", "practicas", "control", "hotel", "hosteleria",
        "cocina", "proveedor", "proveedores", "para", "como", "sobre",
        "buenas", "restaurante", "restaurantes",
    }
    return {t for t in re.findall(r"[a-z0-9áéíóúñü]{4,}", (texto or "").lower()) if t not in stop}


def score_relevancia(fuente: dict, tokens_consulta: set[str]) -> int:
    texto = " ".join(str(fuente.get(k) or "").lower() for k in ("titulo", "url", "resumen"))
    return sum(1 for token in tokens_consulta if token in texto)


def inferir_tipo(consulta: str) -> str:
    t = (consulta or "").lower()
    if any(x in t for x in ("normativa", "obligaciones", "legal")):
        return "informe"
    if any(x in t for x in ("practicas", "control", "gestion", "reducir")):
        return "manual_operativo"
    return "otro"


def guardar_fuente(conn: sqlite3.Connection, investigacion: sqlite3.Row, fuente: dict) -> bool:
    existente = conn.execute(
        "SELECT id FROM asistente_fuentes_conocimiento WHERE empresa_id=? AND url=? LIMIT 1",
        (EMPRESA_ID, fuente["url"]),
    ).fetchone()
    if existente:
        return False
    conn.execute(
        """
        INSERT INTO asistente_fuentes_conocimiento
        (empresa_id, tipo, ambito, area, titulo, url, resumen, lectura,
         uso_recomendado, riesgos, confianza_pct, estado, lectura_ia, usuario)
        VALUES (?, ?, 'hotel', ?, ?, ?, ?, ?, ?, ?, 35, 'pendiente_revision', ?, 'simulador')
        """,
        (
            EMPRESA_ID,
            inferir_tipo(investigacion["consulta"]),
            investigacion["modulo"] or "mercado",
            fuente["titulo"] or investigacion["consulta"],
            fuente["url"],
            fuente["resumen"],
            f"Fuente encontrada para contrastar: {investigacion['objetivo'] or investigacion['consulta']}",
            "Revisar fecha, autoridad de la fuente y encaje con Hotel 1 antes de usar en una recomendacion.",
            "Resultado automatico de buscador; pendiente de lectura humana/IA.",
            json.dumps({"investigacion_id": investigacion["id"], "consulta": investigacion["consulta"]}, ensure_ascii=False),
        ),
    )
    return True


def main() -> None:
    args = parse_args()
    db = Path(args.db)
    if not db.is_absolute():
        db = ROOT / db
    conn = connect(db)
    try:
        ensure_schema(conn)
        if not table_exists(conn, "asistente_investigaciones"):
            resultado = {"ok": False, "motivo": "No existe asistente_investigaciones"}
        else:
            investigaciones = conn.execute(
                """
                SELECT *
                FROM asistente_investigaciones
                WHERE empresa_id=? AND tipo='investigacion_externa_simulador'
                  AND estado IN ('pendiente_revision','pendiente')
                ORDER BY id DESC
                LIMIT ?
                """,
                (EMPRESA_ID, max(1, args.max_investigaciones)),
            ).fetchall()
            creadas = 0
            leidas = 0
            for inv in investigaciones:
                leidas += 1
                fuentes = buscar(inv["consulta"], max(1, args.resultados))
                for fuente in fuentes:
                    if guardar_fuente(conn, inv, fuente):
                        creadas += 1
            conn.commit()
            resultado = {"ok": True, "investigaciones_leidas": leidas, "fuentes_creadas": creadas}
    finally:
        conn.close()
    print(json.dumps(resultado, ensure_ascii=False, indent=2) if args.json else resultado)


if __name__ == "__main__":
    main()
