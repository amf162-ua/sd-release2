from common import cripto
import pytest

def test_ida_y_vuelta_con_texto():
    # 1. Preparar
    clave = cripto.generar_clave()
    original = "OK"

    # 2. Ejecutar
    token = cripto.cifrar(clave, original)
    resultado = cripto.descifrar(clave, token)

    # 3. Comprobar
    assert token != original
    assert resultado == original

def test_ida_y_vuelta_con_diccionario():
    # 1. Preparar
    clave = cripto.generar_clave()
    original = {"ws_id": "WS_01", "salud": "OK"}

    # 2. Ejecutar
    token = cripto.cifrar(clave, original)
    resultado = cripto.descifrar(clave, token)

    # 3. Comprobar
    assert "WS_01" not in token
    assert resultado == original

def test_clave_equivocada():
    # 1. Preparar
    clave = cripto.generar_clave()
    original = "OK"
    clave_equiv = cripto.generar_clave()

    # 2. Ejecutar
    token = cripto.cifrar(clave, original)

    # 3. Comprobar
    with pytest.raises(cripto.ErrorCripto):
        cripto.descifrar(clave_equiv, token)

def test_token_manipulado():
    # 1. Preparar
    clave = cripto.generar_clave()
    original = "OK"

    # 2. Ejecutar
    token = cripto.cifrar(clave, original)
    nuevo = "A" if token[8] != "A" else "B"
    token_alterado = token[:8] + nuevo + token[9:]

    # 3. Comprobar
    with pytest.raises(cripto.ErrorCripto):
        cripto.descifrar(clave, token_alterado)


def test_clave_mal_formada():
    # 1. Preparar
    clave = cripto.generar_clave()
    token = cripto.cifrar(clave, "OK")

    # 2. Comprobar: una clave que no es una clave Fernet también es ErrorCripto
    with pytest.raises(cripto.ErrorCripto):
        cripto.descifrar("hola", token)


def test_derivar_clave_es_determinista():
    assert cripto.derivar_clave("abc") == cripto.derivar_clave("abc")
    assert cripto.derivar_clave("abc") != cripto.derivar_clave("abd")


def test_derivar_clave_sirve_para_cifrar():
    # 1. Preparar
    clave = cripto.derivar_clave("secreto-compartido")
    original = {"ws_id": "WS_01"}

    # 2. Ejecutar
    token = cripto.cifrar(clave, original)
    resultado = cripto.descifrar(clave, token)

    # 3. Comprobar
    assert resultado == original
