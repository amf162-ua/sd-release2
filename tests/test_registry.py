from common import certificados
from WM_Registry import registry

def _registrar(cliente, ws_id, ubicacion="Parque X"):
    _, cert = certificados.generar(ws_id, ubicacion)
    return cliente.post('/api/v1/estaciones', json={
        'ws_id': ws_id, 'ubicacion': ubicacion, 'certificado_pem': cert})

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

def test_get_lista_devuelve_las_estaciones_registradas(tmp_path):
    # 1. Preparar: dos estaciones
    cliente = _cliente(tmp_path)
    _registrar(cliente, "WS_01")
    _registrar(cliente, "WS_02")

    # 2. Ejecutar
    r = cliente.get('/api/v1/estaciones')

    # 3. Comprobar
    assert r.status_code == 200
    assert len(r.get_json()) == 2

def test_get_estacion_existente_devuelve_200(tmp_path):
    # 1. Preparar estación
    cliente = _cliente(tmp_path)
    alta = _registrar(cliente, "WS_01")
    token = alta.get_json()["token"]

    # 2. Ejecutar
    r = cliente.get('/api/v1/estaciones/WS_01')

    # 3. Comprobar
    assert r.status_code == 200
    assert r.get_json()["ws_id"] == "WS_01"
    assert "clave_auth" not in r.get_json()
    assert token not in r.get_data(as_text=True)

def test_get_estacion_inexistente_devuelve_404(tmp_path):
    # 1. Preparar: BD vacía, ninguna estación registrada
    cliente = _cliente(tmp_path)

    # 2. Ejecutar
    r = cliente.get('/api/v1/estaciones/WS_99')

    # 3. Comprobar
    assert r.status_code == 404


def test_salud_devuelve_ok(tmp_path):
    cliente = _cliente(tmp_path)

    r = cliente.get('/api/v1/salud')

    assert r.status_code == 200
    assert r.get_json() == {'ok': True}


def test_put_sin_token_devuelve_401(tmp_path):
    cliente = _cliente(tmp_path)
    _registrar(cliente, "WS_01")

    r = cliente.put('/api/v1/estaciones/WS_01', json={'ubicacion': 'Jardines Y'})

    assert r.status_code == 401


def test_put_con_token_de_otra_estacion_devuelve_401(tmp_path):
    cliente = _cliente(tmp_path)
    _registrar(cliente, "WS_01")
    token_ws02 = _registrar(cliente, "WS_02").get_json()["token"]

    r = cliente.put('/api/v1/estaciones/WS_01', json={'ubicacion': 'Jardines Y'},
                    headers={'X-Token': token_ws02})

    assert r.status_code == 401


def test_put_sin_ubicacion_devuelve_400(tmp_path):
    cliente = _cliente(tmp_path)
    token = _registrar(cliente, "WS_01").get_json()["token"]

    r = cliente.put('/api/v1/estaciones/WS_01', json={}, headers={'X-Token': token})

    assert r.status_code == 400


def test_put_correcto_cambia_la_ubicacion(tmp_path):
    # 1. Preparar
    cliente = _cliente(tmp_path)
    token = _registrar(cliente, "WS_01").get_json()["token"]

    # 2. Ejecutar
    r = cliente.put('/api/v1/estaciones/WS_01', json={'ubicacion': 'Jardines Y'},
                    headers={'X-Token': token})

    # 3. Comprobar: la respuesta y el estado real en la BD
    assert r.status_code == 200
    assert cliente.get('/api/v1/estaciones/WS_01').get_json()['ubicacion'] == 'Jardines Y'


def test_delete_sin_token_devuelve_401_y_no_da_de_baja(tmp_path):
    cliente = _cliente(tmp_path)
    _registrar(cliente, "WS_01")

    r = cliente.delete('/api/v1/estaciones/WS_01')

    assert r.status_code == 401
    assert cliente.get('/api/v1/estaciones/WS_01').get_json()['activa'] == 1


def test_delete_correcto_da_de_baja_y_el_token_deja_de_valer(tmp_path):
    # 1. Preparar
    cliente = _cliente(tmp_path)
    token = _registrar(cliente, "WS_01").get_json()["token"]

    # 2. Ejecutar
    r = cliente.delete('/api/v1/estaciones/WS_01', headers={'X-Token': token})

    # 3. Comprobar
    assert r.status_code == 200
    assert cliente.get('/api/v1/estaciones/WS_01').get_json()['activa'] == 0
    r2 = cliente.put('/api/v1/estaciones/WS_01', json={'ubicacion': 'Z'},
                     headers={'X-Token': token})
    assert r2.status_code == 401
