import datetime
import ipaddress
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARPETA = os.path.join(RAIZ, 'certs')


def main():
    os.makedirs(CARPETA, exist_ok=True)

    # 1. La pareja de claves: la privada irá al .key y la pública dentro del certificado
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # 2. El certificado, autofirmado (emisor = sujeto)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'WM_Registry')])
    ahora = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(nombre)
            .issuer_name(nombre)
            .public_key(clave.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(ahora)
            .not_valid_after(ahora + datetime.timedelta(days=825))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, key_encipherment=True,
                content_commitment=False, data_encipherment=False, key_agreement=False,
                crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(clave.public_key()), critical=False)
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName('localhost'),
                x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .sign(clave, hashes.SHA256()))

    # 3. Guardar: clave privada (secreta) y certificado (público)
    ruta_key = os.path.join(CARPETA, 'registry.key')
    with open(ruta_key, 'wb') as f:
        f.write(clave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
    os.chmod(ruta_key, 0o600)    # solo lectura/escritura para el dueño (en Windows no tiene efecto)

    ruta_pem = os.path.join(CARPETA, 'registry.pem')
    with open(ruta_pem, 'wb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    print(f'Certificado (público): {ruta_pem}')
    print(f'Clave privada:         {ruta_key}')
    print('ATENCIÓN: registry.key NO se sube a Git ni se copia dentro de ninguna imagen Docker.')


if __name__ == '__main__':
    main()