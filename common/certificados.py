import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


class ErrorCertificado(Exception):
    """El texto recibido no es un certificado PEM válido."""


def generar(ws_id, ubicacion):
    """Crea un par de claves RSA y un certificado autofirmado. Devuelve (clave_privada_pem, certificado_pem)."""
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, ws_id),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, ubicacion),
    ])
    ahora = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(nombre)
            .issuer_name(nombre)                      # autofirmado: el emisor es el propio sujeto
            .public_key(clave.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(ahora)
            .not_valid_after(ahora + datetime.timedelta(days=365))
            .sign(clave, hashes.SHA256()))
    clave_pem = clave.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    return clave_pem, cert_pem


def _cargar(certificado_pem):
    try:
        return x509.load_pem_x509_certificate(certificado_pem.encode())
    except (ValueError, AttributeError):
        raise ErrorCertificado("Certificado no válido")


def huella(certificado_pem):
    """SHA-256 del certificado en hexadecimal (64 caracteres)."""
    return _cargar(certificado_pem).fingerprint(hashes.SHA256()).hex()


def nombre_comun(certificado_pem):
    """El CN del certificado, que es el ws_id de la estación."""
    atributos = _cargar(certificado_pem).subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not atributos:
        raise ErrorCertificado("El certificado no tiene nombre común")
    return atributos[0].value
