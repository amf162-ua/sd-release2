import pytest

from common import certificados


def test_huella_son_64_caracteres_hexadecimales():
    _, cert = certificados.generar("WS_01", "Parque X")
    h = certificados.huella(cert)
    assert len(h) == 64
    int(h, 16)    # falla si no es hexadecimal


def test_nombre_comun_es_el_ws_id():
    _, cert = certificados.generar("WS_07", "Universidad")
    assert certificados.nombre_comun(cert) == "WS_07"


def test_certificado_basura_lanza_error():
    with pytest.raises(certificados.ErrorCertificado):
        certificados.huella("basura")


def test_la_clave_privada_no_va_en_el_certificado():
    clave, cert = certificados.generar("WS_01", "Parque X")
    assert "PRIVATE KEY" in clave
    assert "PRIVATE KEY" not in cert
