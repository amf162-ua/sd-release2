import json
import threading
from common import auditoria


def test_registrar_y_leer_basico(tmp_path, monkeypatch):
    # 1. Preparar
    fichero = tmp_path / "auditoria.jsonl"
    monkeypatch.setattr(auditoria, "AUDITORIA_PATH", str(fichero))

    # 2. Ejecutar
    ev = auditoria.registrar("WS_01", "10.0.0.5", "AUTH_OK", "Autenticación correcta")
    ultimos = auditoria.leer_ultimos(10)

    # 3. Comprobar
    assert ev["actor"] == "WS_01"
    assert ev["ip"] == "10.0.0.5"
    assert ev["accion"] == "AUTH_OK"
    assert "ts" in ev
    assert len(ultimos) == 1
    assert ultimos[0]["descripcion"] == "Autenticación correcta"


def test_campos_extra_y_limite(tmp_path, monkeypatch):
    # 1. Preparar
    fichero = tmp_path / "auditoria.jsonl"
    monkeypatch.setattr(auditoria, "AUDITORIA_PATH", str(fichero))

    # 2. Ejecutar
    for i in range(5):
        auditoria.registrar(
            f"WS_0{i}",
            "127.0.0.1",
            "ALERTA_CLIMA",
            "Lluvia detectada",
            ciudad="Madrid",
            temperatura=12.3,
        )
    ultimos = auditoria.leer_ultimos(2)

    # 3. Comprobar
    assert len(ultimos) == 2
    assert ultimos[0]["actor"] == "WS_03"
    assert ultimos[1]["actor"] == "WS_04"
    assert ultimos[1]["ciudad"] == "Madrid"
    assert ultimos[1]["temperatura"] == 12.3


def test_leer_cuando_no_existe_fichero(tmp_path, monkeypatch):
    # 1. Preparar
    fichero = tmp_path / "no_existe.jsonl"
    monkeypatch.setattr(auditoria, "AUDITORIA_PATH", str(fichero))

    # 2. Ejecutar y Comprobar
    assert auditoria.leer_ultimos(10) == []


def test_concurrencia_hilos(tmp_path, monkeypatch):
    # 1. Preparar
    fichero = tmp_path / "auditoria.jsonl"
    monkeypatch.setattr(auditoria, "AUDITORIA_PATH", str(fichero))

    # 2. Ejecutar
    def escribir(idx):
        auditoria.registrar("CENTRAL", "127.0.0.1", "ESTADO_WS", f"Evento {idx}")

    hilos = [threading.Thread(target=escribir, args=(i,)) for i in range(20)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    # 3. Comprobar
    lineas = fichero.read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 20
    for linea in lineas:
        parsed = json.loads(linea)
        assert parsed["actor"] == "CENTRAL"