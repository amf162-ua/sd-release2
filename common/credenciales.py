import sqlite3
import hashlib, secrets, time
class YaExiste(Exception):
    pass

def abrir(ruta):
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
        "SELECT ws_id, ubicacion, huella_cert, activa, alta_ts FROM credenciales WHERE ws_id = ?",
        (ws_id,),
    ).fetchone()
    if fila is None:
        return None
    claves = ('ws_id', 'ubicacion', 'huella_cert', 'activa', 'alta_ts')
    return dict(zip(claves, fila))