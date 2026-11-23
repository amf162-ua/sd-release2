from common import certificados
from WM_Registry import registry


def _cliente(tmp_path):
    registry.app.config['DB_PATH'] = str(tmp_path / "test.db")
    return registry.app.test_client()


def test_post_estacion_devuelve_201_y_token(tmp_path):
    cliente = _cliente(tmp_path)
    _, cert = certificados.generar("WS_01", "Parque X")

    r = cliente.post('/api/v1/estaciones', json={
        'ws_id': 'WS_01', 'ubicacion': 'Parque X', 'certificado_pem': cert})

    assert r.status_code == 201
    assert r.get_json()['token']

def test_post_sin_ubicacion_devuelve_400(tmp_path):
    cliente = _cliente(tmp_path)
    _, cert = certificados.generar("WS_01", "Parque X")

    r = cliente.post('/api/v1/estaciones', json={
        'ws_id': 'WS_01', 'certificado_pem': cert})        # falta 'ubicacion'

    assert r.status_code == 400


def test_post_con_certificado_de_otra_estacion_devuelve_400(tmp_path):
    cliente = _cliente(tmp_path)
    _, cert_de_ws02 = certificados.generar("WS_02", "Parque X")

    r = cliente.post('/api/v1/estaciones', json={
        'ws_id': 'WS_01', 'ubicacion': 'Parque X', 'certificado_pem': cert_de_ws02})

    assert r.status_code == 400


def test_post_duplicado_devuelve_409(tmp_path):
    # 1. Preparar: una primera alta correcta
    cliente = _cliente(tmp_path)
    _, cert = certificados.generar("WS_01", "Parque X")
    datos = {'ws_id': 'WS_01', 'ubicacion': 'Parque X', 'certificado_pem': cert}
    assert cliente.post('/api/v1/estaciones', json=datos).status_code == 201

    # 2. Ejecutar: la misma alta otra vez
    r = cliente.post('/api/v1/estaciones', json=datos)

    # 3. Comprobar
    assert r.status_code == 409