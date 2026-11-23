"""Registro estructurado de auditoría en formato JSON Lines."""

from datetime import datetime
import json
import os
import threading

_RUTA_DEFECTO = "/data/auditoria.jsonl" if os.name != "nt" else os.path.join("data", "auditoria.jsonl")
AUDITORIA_PATH = os.getenv("AUDITORIA_PATH", _RUTA_DEFECTO)

ACCIONES_VALIDAS = {
    "AUTH_OK", "AUTH_KO", "REGISTRO", "BAJA", "CLAVES_RESET",
    "FUGA", "FUGA_RESUELTA", "ESTADO_WS", "BLOQUEO",
    "RIEGO_INICIADO", "RIEGO_FINALIZADO",
    "ALERTA_CLIMA", "ALERTA_CANCELADA", "ERROR",
}

_lock = threading.Lock()


def registrar(actor, ip, accion, descripcion, **extra):
    """Añade una línea JSON al fichero de auditoría (/data/auditoria.jsonl)."""
    evento = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "actor": actor,
        "ip": ip,
        "accion": accion,
        "descripcion": descripcion,
    }
    if extra:
        evento.update(extra)

    linea = json.dumps(evento, ensure_ascii=False) + "\n"

    with _lock:
        directorio = os.path.dirname(os.path.abspath(AUDITORIA_PATH))
        if directorio:
            os.makedirs(directorio, exist_ok=True)
        with open(AUDITORIA_PATH, "a", encoding="utf-8") as f:
            f.write(linea)

    return evento


def leer_ultimos(limite=100):
    """Devuelve los últimos 'limite' eventos registrados en el fichero de auditoría."""
    if limite <= 0 or not os.path.exists(AUDITORIA_PATH):
        return []

    with _lock:
        with open(AUDITORIA_PATH, "r", encoding="utf-8") as f:
            lineas = f.readlines()

    eventos = []
    for linea in lineas[-limite:]:
        linea = linea.strip()
        if not linea:
            continue
        try:
            eventos.append(json.loads(linea))
        except json.JSONDecodeError:
            continue
    return eventos

def leer_por_fecha(fecha: str, limite: int = 500) -> list[dict]:
    """
    Devuelve los eventos cuya marca de tiempo ISO empieza por 'fecha' (ej. '2026-11-23').
    endpoint GET /audit/{fecha}.
    """
    if not os.path.exists(AUDITORIA_PATH):
        return []

    with _lock:
        with open(AUDITORIA_PATH, "r", encoding="utf-8") as f:
            lineas = f.readlines()

    eventos = []
    for linea in lineas:
        linea = linea.strip()
        if not linea:
            continue
        try:
            ev = json.loads(linea)
            if str(ev.get("ts", "")).startswith(fecha):
                eventos.append(ev)
        except json.JSONDecodeError:
            continue
    return eventos[-limite:]