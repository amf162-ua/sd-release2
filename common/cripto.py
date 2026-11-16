import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken


class ErrorCripto(Exception):
    """Clave inválida o mensaje manipulado. Es la única excepción que ve el resto del código."""


def generar_clave():
    clave = Fernet.generate_key()
    return clave.decode()


def cifrar(clave, datos):
    texto = json.dumps(datos)
    crudo = texto.encode()
    f = Fernet(clave.encode())
    cifrado = f.encrypt(crudo)
    return cifrado.decode()


def descifrar(clave, token):
    token_bytes = token.encode()
    try:
        f = Fernet(clave.encode())
        crudo = f.decrypt(token_bytes)
    except (InvalidToken, ValueError):
        raise ErrorCripto("Clave incorrecta o mensaje alterado")
    texto = crudo.decode()
    return json.loads(texto)


def derivar_clave(secreto):
    """Convierte un secreto cualquiera en una clave Fernet (32 bytes en base64 urlsafe)."""
    h = hashlib.sha256(secreto.encode()).digest()
    b64 = base64.urlsafe_b64encode(h)
    return b64.decode()
