import hashlib
import hmac
import os
import secrets
import sqlite3
import time

# Columnas que se pueden mostrar: nunca clave_auth ni clave_sesion
COLUMNAS_PUBLICAS = ('ws_id', 'ubicacion', 'huella_cert', 'activa', 'alta_ts')


class YaExiste(Exception):
    pass

def abrir(ruta):
    carpeta = os.path.dirname(ruta)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    conn = sqlite3.connect(ruta)
    # 1. activar WAL y busy_timeout
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    # 2. crear la tabla credenciales si no existe
    conn.execute("""
    CREATE TABLE IF NOT EXISTS credenciales (
        ws_id        TEXT PRIMARY KEY,
        ubicacion    TEXT NOT NULL,
        huella_cert  TEXT NOT NULL,
        clave_auth   TEXT NOT NULL,
        clave_sesion TEXT,
        activa       INTEGER NOT NULL DEFAULT 1,
        alta_ts      TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn

def crear(conn, ws_id, ubicacion, huella_cert):
    """Da de alta una estación y devuelve el token. El token solo existe aquí: en la BD se guarda su hash."""
    token = secrets.token_urlsafe(32)
    clave_auth = hashlib.sha256(token.encode()).hexdigest()
    alta_ts = time.strftime('%Y-%m-%d %H:%M:%S')
    try:
        conn.execute(
            "INSERT INTO credenciales (ws_id, ubicacion, huella_cert, clave_auth, alta_ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (ws_id, ubicacion, huella_cert, clave_auth, alta_ts),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise YaExiste(ws_id)
    return token


def obtener(conn, ws_id):
    """Datos públicos de una estación (nunca los secretos) o None si no existe."""
    fila = conn.execute(
        f"SELECT {', '.join(COLUMNAS_PUBLICAS)} FROM credenciales WHERE ws_id = ?",
        (ws_id,),
    ).fetchone()
    if fila is None:
        return None
    return dict(zip(COLUMNAS_PUBLICAS, fila))


def listar(conn):
    """Todas las estaciones, solo con datos públicos, ordenadas por ws_id."""
    filas = conn.execute(
        f"SELECT {', '.join(COLUMNAS_PUBLICAS)} FROM credenciales ORDER BY ws_id"
    ).fetchall()
    return [dict(zip(COLUMNAS_PUBLICAS, f)) for f in filas]


def dar_de_baja(conn, ws_id):
    """Marca la estación como no activa. True si existía, False si no."""
    cursor = conn.execute("UPDATE credenciales SET activa = 0 WHERE ws_id = ?", (ws_id,))
    conn.commit()
    return cursor.rowcount > 0


def verificar_token(conn, ws_id, token):
    """True si el token corresponde a esa estación y la estación está activa."""
    hash_recibido = hashlib.sha256(token.encode()).hexdigest()
    fila = conn.execute(
        "SELECT clave_auth, activa FROM credenciales WHERE ws_id = ?", (ws_id,)
    ).fetchone()
    if fila is None:
        return False
    clave_auth, activa = fila
    # compare_digest tarda lo mismo acierte o falle: evita adivinar el secreto midiendo tiempos
    return hmac.compare_digest(hash_recibido, clave_auth) and activa == 1
