import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request

from common import certificados, credenciales

app = Flask(__name__)
app.config['DB_PATH'] = os.environ.get('DB_PATH', 'data/water_management.db')

def bd():
    return credenciales.abrir(app.config['DB_PATH'])


@app.post('/ws')
def alta():
    datos = request.get_json(silent=True) or {}
    ws_id = datos.get('ws_id')
    ubicacion = datos.get('ubicacion')
    cert = datos.get('certificado_pem')

    # 1. Faltan campos -> 400
    if not (ws_id and ubicacion and cert):
        return jsonify({'error': 'Faltan campos: ws_id, ubicacion, certificado_pem'}), 400

    # 2. Certificado inválido o con CN distinto de ws_id -> 400
    try:
        if certificados.nombre_comun(cert) != ws_id:
            return jsonify({'error': 'El certificado no pertenece a ese ws_id'}), 400
        huella = certificados.huella(cert)
    except certificados.ErrorCertificado:
        return jsonify({'error': 'Certificado no válido'}), 400

    # 3. Crear; si ya existe -> 409
    conn = bd()
    try:
        token = credenciales.crear(conn, ws_id, ubicacion, huella)
    except credenciales.YaExiste:
        return jsonify({'error': f'La estación {ws_id} ya está registrada'}), 409
    finally:
        conn.close()

    # 4. Todo bien -> 201 con el token
    return jsonify({'ws_id': ws_id, 'token': token}), 201

@app.get('/ws')
def listar():
    conn = bd()
    try:
        estaciones = credenciales.listar(conn)
    finally:
        conn.close()
    return jsonify(estaciones)


@app.get('/ws/<ws_id>')
def consultar(ws_id):
    conn = bd()
    try:
        estacion = credenciales.obtener(conn, ws_id)
    finally:
        conn.close()
    if estacion is None:
        return jsonify({'error': f'La estación {ws_id} no existe'}), 404
    return jsonify(estacion)

def _token_valido(conn, ws_id):
    """True si la petición lleva en X-Token el token de esa estación (y está activa)."""
    token = request.headers.get('X-Token')
    return bool(token) and credenciales.verificar_token(conn, ws_id, token)


@app.put('/ws/<ws_id>')
def modificar(ws_id):
    conn = bd()
    try:
        # Primero la autenticación (401) y después los datos (400)
        if not _token_valido(conn, ws_id):
            return jsonify({'error': 'Token ausente o no válido'}), 401
        datos = request.get_json(silent=True) or {}
        ubicacion = datos.get('ubicacion')
        if not ubicacion:
            return jsonify({'error': 'Falta el campo ubicacion'}), 400
        credenciales.actualizar_ubicacion(conn, ws_id, ubicacion)
    finally:
        conn.close()
    return jsonify({'ws_id': ws_id, 'ubicacion': ubicacion})


@app.delete('/ws/<ws_id>')
def baja(ws_id):
    conn = bd()
    try:
        if not _token_valido(conn, ws_id):
            return jsonify({'error': 'Token ausente o no válido'}), 401
        credenciales.dar_de_baja(conn, ws_id)
    finally:
        conn.close()
    return jsonify({'ws_id': ws_id, 'activa': False})


@app.get('/system')
def salud():
    return jsonify({'ok': True})

if __name__ == '__main__':
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cert = os.environ.get('CERT_PATH', os.path.join(raiz, 'certs', 'registry.pem'))
    clave = os.environ.get('KEY_PATH', os.path.join(raiz, 'certs', 'registry.key'))
    app.run(host='0.0.0.0', port=8443, ssl_context=(cert, clave))
