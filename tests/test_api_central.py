import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "WM_Central"))

from common import auditoria  # noqa: E402
from database import BaseDatos  # noqa: E402
from WM_Central import Central  # noqa: E402
from api_central import iniciar_api  # noqa: E402


def _peticion(url, metodo="GET", payload=None, api_key=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {}
    if api_key is not None:
        headers["X-API-Key"] = api_key
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8")), dict(e.headers)


def test_api_central_entidades_y_seguridad(tmp_path, monkeypatch):
    # 1. Preparar BD temporal, fichero de auditoría aislado y API_KEY
    ruta_db = str(tmp_path / "test.db")
    ruta_audit = str(tmp_path / "auditoria.jsonl")
    monkeypatch.setattr(auditoria, "AUDITORIA_PATH", ruta_audit)
    monkeypatch.setenv("API_KEY", "clave-secreta-test")

    bd = BaseDatos(ruta_db)
    bd.guardar_estacion("WS_01", "Alicante")
    central = Central(puerto_sockets=0, servidor_kafka="localhost:9092", bd=bd)
    central.estaciones["WS_01"].conectada = True
    central.productor = None

    puerto = 18080
    hilo = threading.Thread(target=iniciar_api, args=(central, puerto), daemon=True)
    hilo.start()
    time.sleep(0.15)
    base = f"http://127.0.0.1:{puerto}"

    # 2. GET /ws y GET /ws/WS_01 (lectura pública + cabeceras CORS)
    code, estaciones, headers = _peticion(f"{base}/ws")
    assert code == 200
    assert headers.get("Access-Control-Allow-Origin") == "*"
    assert estaciones[0]["id"] == "WS_01"
    assert estaciones[0]["estado"] == "DISPONIBLE"

    code, ws1, _ = _peticion(f"{base}/ws/WS_01")
    assert code == 200
    assert ws1["ubicacion"] == "Alicante"

    code, _, _ = _peticion(f"{base}/ws/WS_99")
    assert code == 404

    # 3. Seguridad: PUT /weather/WS_01 sin X-API-Key debe dar 401
    code, _, _ = _peticion(f"{base}/weather/WS_01", metodo="PUT", payload={"motivo": "HELADA"})
    assert code == 401

    # 4. PUT /weather/WS_01 con X-API-Key -> activa alerta y pasa WS_01 a FUERA_SERVICIO
    code, res, _ = _peticion(
        f"{base}/weather/WS_01",
        metodo="PUT",
        payload={"motivo": "HELADA", "temperatura": -2.5, "ciudad": "Oslo"},
        api_key="clave-secreta-test",
    )
    assert code == 200
    assert res["ok"] is True

    _, ws1_alerta, _ = _peticion(f"{base}/ws/WS_01")
    assert ws1_alerta["estado"] == "FUERA_SERVICIO"
    assert ws1_alerta["alerta_clima"] == "HELADA"
    assert ws1_alerta["temperatura"] == -2.5

    # 5. DELETE /weather/WS_01 -> cancela alerta y vuelve a DISPONIBLE
    code, res, _ = _peticion(
        f"{base}/weather/WS_01", metodo="DELETE", api_key="clave-secreta-test"
    )
    assert code == 200
    _, ws1_ok, _ = _peticion(f"{base}/ws/WS_01")
    assert ws1_ok["estado"] == "DISPONIBLE"
    assert ws1_ok["alerta_clima"] is None

    # 6. GET /audit y GET /audit/{fecha}
    code, eventos, _ = _peticion(f"{base}/audit")
    assert code == 200
    assert len(eventos) >= 3  # AUTH_KO, ALERTA_CLIMA, ALERTA_CANCELADA

    hoy = time.strftime("%Y-%m-%d")
    code, eventos_hoy, _ = _peticion(f"{base}/audit/{hoy}")
    assert code == 200
    assert len(eventos_hoy) == len(eventos)


def test_api_parametros_invalidos_dan_400_y_clave_incorrecta_401(tmp_path, monkeypatch):
    monkeypatch.setattr(auditoria, "AUDITORIA_PATH", str(tmp_path / "auditoria.jsonl"))
    monkeypatch.setenv("API_KEY", "clave-secreta-test")
    bd = BaseDatos(str(tmp_path / "test.db"))
    central = Central(puerto_sockets=0, servidor_kafka="localhost:9092", bd=bd)
    central.productor = None
    puerto = 18081
    threading.Thread(target=iniciar_api, args=(central, puerto), daemon=True).start()
    time.sleep(0.15)
    base = f"http://127.0.0.1:{puerto}"

    code, _, _ = _peticion(f"{base}/audit?limite=abc", api_key="clave-secreta-test")
    assert code == 400
    code, _, _ = _peticion(f"{base}/weather/WS_01", metodo="PUT",
                           payload={"motivo": "HELADA"}, api_key="otra-clave")
    assert code == 401
