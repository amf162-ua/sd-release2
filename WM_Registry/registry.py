import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request

from common import certificados, credenciales

app = Flask(__name__)
app.config['DB_PATH'] = os.environ.get('DB_PATH', 'data/water_management.db')

def bd():
    return credenciales.abrir(app.config['DB_PATH'])


@app.post('/api/v1/estaciones')
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

@app.get('/api/v1/estaciones')
def listar():
    conn = bd()
    try:
        estaciones = credenciales.listar(conn)
    finally:
        conn.close()
    return jsonify(estaciones)


@app.get('/api/v1/estaciones/<ws_id>')
def consultar(ws_id):
    conn = bd()
    try:
        estacion = credenciales.obtener(conn, ws_id)
    finally:
        conn.close()
    if estacion is None:
        return jsonify({'error': f'La estación {ws_id} no existe'}), 404
    return jsonify(estacion)
