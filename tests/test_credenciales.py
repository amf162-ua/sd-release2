import pytest

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


def test_crear_dos_veces_el_mismo_id_falla(tmp_path):
    conn = credenciales.abrir(str(tmp_path / "test.db"))
    credenciales.crear(conn, "WS_01", "Parque X", "abc")
    with pytest.raises(credenciales.YaExiste):
        credenciales.crear(conn, "WS_01", "Otro sitio", "def")


def test_obtener_inexistente_devuelve_none(tmp_path):
    conn = credenciales.abrir(str(tmp_path / "test.db"))
    assert credenciales.obtener(conn, "WS_99") is None


def test_listar_devuelve_todas_sin_secretos(tmp_path):
    # 1. Preparar
    conn = credenciales.abrir(str(tmp_path / "test.db"))
    credenciales.crear(conn, "WS_02", "Universidad", "abc")
    credenciales.crear(conn, "WS_01", "Parque X", "def")

    # 2. Ejecutar
    lista = credenciales.listar(conn)

    # 3. Comprobar
    assert [e["ws_id"] for e in lista] == ["WS_01", "WS_02"]   # ordenadas
    for estacion in lista:
        assert "clave_auth" not in estacion
        assert "clave_sesion" not in estacion


def test_dar_de_baja_deja_la_estacion_inactiva(tmp_path):
    # 1. Preparar
    conn = credenciales.abrir(str(tmp_path / "test.db"))
    credenciales.crear(conn, "WS_01", "Parque X", "abc")

    # 2. Ejecutar
    resultado = credenciales.dar_de_baja(conn, "WS_01")

    # 3. Comprobar
    assert resultado is True
    estacion = credenciales.obtener(conn, "WS_01")
    assert estacion is not None
    assert estacion["activa"] == 0


def test_dar_de_baja_inexistente_devuelve_false(tmp_path):
    conn = credenciales.abrir(str(tmp_path / "test.db"))
    assert credenciales.dar_de_baja(conn, "WS_99") is False
