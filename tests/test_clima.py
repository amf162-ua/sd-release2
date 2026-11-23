import io
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "WM_Central"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "WM_W"))

from common import auditoria  # noqa: E402
from database import BaseDatos  # noqa: E402
from WM_Central import Central  # noqa: E402
import WM_W  # noqa: E402


class _FakeHTTPResponse(io.BytesIO):
    def __init__(self, payload: dict):
        super().__init__(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_consultar_openweather_helada_lluvia_y_ok():
    with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse({
        "name": "Oslo",
        "main": {"temp": -1.5},
        "weather": [{"main": "Clear", "description": "cielo claro"}],
    })):
        res = WM_W.consultar_openweather("Oslo", "fake-key")
    assert res["estado"] == "HELADA"
    assert res["temp"] == -1.5

    with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse({
        "name": "London",
        "main": {"temp": 14.0},
        "weather": [{"main": "Rain", "description": "lluvia ligera"}],
    })):
        res = WM_W.consultar_openweather("London", "fake-key")
    assert res["estado"] == "LLUVIA"

    with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse({
        "name": "Alicante",
        "main": {"temp": 21.3},
        "weather": [{"main": "Clear", "description": "cielo claro"}],
    })):
        res = WM_W.consultar_openweather("Alicante", "fake-key")
    assert res["estado"] == "OK"


def test_oficina_clima_notifica_alerta_y_cancelacion(tmp_path):
    fichero_ciudades = tmp_path / "ciudades.txt"
    fichero_ciudades.write_text("WS_01 Oslo\n", encoding="utf-8")

    # Evitamos que el hilo daemon se dispare en mitad del test
    with patch.object(WM_W.threading.Thread, "start"):
        oficina = WM_W.OficinaClima(
            api_key="fake-key",
            url_central="http://localhost:8080",
            fichero_ciudades=str(fichero_ciudades),
        )

    llamadas_http = []
    oficina._enviar_http = lambda metodo, url, payload=None: llamadas_http.append((metodo, url, payload))

    # 1. Alerta de helada -> PUT /weather/WS_01
    oficina._notificar_central("WS_01", {
        "ciudad": "Oslo",
        "temp": -3.0,
        "estado": "HELADA",
        "descripcion": "nevada ligera",
    })
    assert len(llamadas_http) == 1
    assert llamadas_http[0][0] == "PUT"
    assert llamadas_http[0][1].endswith("/weather/WS_01")
    assert llamadas_http[0][2]["motivo"] == "HELADA"

    # 2. Clima vuelve a ser favorable -> DELETE /weather/WS_01
    oficina._notificar_central("WS_01", {
        "ciudad": "Oslo",
        "temp": 19.0,
        "estado": "OK",
        "descripcion": "despejado",
    })
    assert len(llamadas_http) == 2
    assert llamadas_http[1][0] == "DELETE"
    assert llamadas_http[1][1].endswith("/weather/WS_01")


def test_riego_en_curso_termina_normal_y_luego_pasa_a_fuera_servicio(tmp_path, monkeypatch):
    monkeypatch.setattr(auditoria, "AUDITORIA_PATH", str(tmp_path / "auditoria.jsonl"))
    bd = BaseDatos(str(tmp_path / "test.db"))
    bd.guardar_estacion("WS_01", "Alicante")
    bd.guardar_operario("OP_01", "Laura")

    central = Central(puerto_sockets=0, servidor_kafka="localhost:9092", bd=bd)
    ws = central.estaciones["WS_01"]
    ws.conectada = True
    ws.riego = {
        "order_id": "ORD-1",
        "operator_id": "OP_01",
        "duracion": 15,
        "caudal": 12.5,
        "volumen": 50.0,
        "segundos": 4,
    }
    assert ws.estado == "REGANDO"

    # Llega alerta climática mientras está regando -> sigue REGANDO
    ok, _ = central.registrar_alerta_clima("WS_01", "LLUVIA", temperatura=11.0, ciudad="Alicante")
    assert ok is True
    assert ws.estado == "REGANDO"

    # Finaliza el riego en curso con normalidad -> pasa automáticamente a FUERA_SERVICIO
    central.procesar_evento({
        "ws_id": "WS_01",
        "evento": "RIEGO_FINALIZADO",
        "order_id": "ORD-1",
        "operator_id": "OP_01",
        "motivo": "TIEMPO_COMPLETADO",
        "segundos": 15,
        "volumen": 180.0,
    })
    assert ws.estado == "FUERA_SERVICIO"

    # Si cancelamos la alerta -> vuelve a DISPONIBLE
    central.cancelar_alerta_clima("WS_01")
    assert ws.estado == "DISPONIBLE"