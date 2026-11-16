from common import credenciales

def test_abrir_crea_la_tabla(tmp_path):
    conn = credenciales.abrir(str(tmp_path / "test.db"))
    tablas = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert ("credenciales",) in tablas

def test_crear_devuelve_token_y_se_puede_obtener(tmp_path):
    conn = credenciales.abrir(str(tmp_path / "test.db"))
    token = credenciales.crear(conn, "WS_01", "Parque X", "abc123")
    assert token                                   # no vacío
    assert credenciales.obtener(conn, "WS_01") is not None

def test_el_token_no_se_guarda_en_claro(tmp_path):
    # 1. Preparar
    conn = credenciales.abrir(str(tmp_path / "test.db"))
    token = credenciales.crear(conn, "WS_02", "Universidad", "abc123")

    # 2. Leer la columna clave_auth directamente
    fila = conn.execute(
        "SELECT clave_auth FROM credenciales WHERE ws_id = ?", ("WS_02",)
    ).fetchone()

    # 3. Comprobar
    assert fila[0] != token