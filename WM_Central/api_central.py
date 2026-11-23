"""
API REST de CENTRAL (API_Central - Release 2, diseño orientado a recursos/entidades).

Entidades expuestas:
  GET    /ws                      -> listado de estaciones de riego
  GET    /ws/{id}                 -> detalle de una estación concreta
  POST   /ws                      -> alta de una estación
  PUT    /ws/{id}                 -> modificar estación (BLOQUEAR, ACTIVAR, RESET_CLAVES)
  GET    /operators               -> listado de operarios
  GET    /operators/{id}          -> detalle de un operario
  POST   /operators               -> alta de un operario
  GET    /irrigations             -> riegos en curso e histórico reciente
  POST   /irrigations             -> iniciar un riego desde Central (punto 11)
  GET    /weather                 -> estado climático y alertas de todas las estaciones
  GET    /weather/{ws_id}         -> clima/alerta de una estación concreta
  PUT    /weather/{ws_id}         -> reportar clima / activar alerta (LLUVIA o HELADA)
  DELETE /weather/{ws_id}         -> cancelar alerta climática y restaurar estación
  GET    /audit                   -> eventos de auditoría (?limite=100)
  GET    /audit/{fecha}           -> eventos de auditoría de una fecha (YYYY-MM-DD)
  GET    /system                  -> estado técnico de Central y últimos logs de consola
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from urllib.parse import parse_qs, unquote, urlparse

from common import auditoria


def iniciar_api(central, puerto: int):
    api_key_esperada = os.getenv("API_KEY", "").strip()

    class Manejador(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _cabeceras_cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")

        def _json(self, codigo, datos):
            cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
            self.send_response(codigo)
            self._cabeceras_cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def _leer_body(self):
            tam = int(self.headers.get("Content-Length", 0))
            if tam <= 0:
                return {}
            return json.loads(self.rfile.read(tam).decode("utf-8"))

        def _entero(self, qs, nombre, defecto):
            """Lee un parámetro entero de la query; si no lo es, responde 400 y devuelve None."""
            try:
                return int(qs.get(nombre, [str(defecto)])[0])
            except ValueError:
                self._json(400, {"ok": False, "mensaje": f"'{nombre}' debe ser un entero"})
                return None

        def _autorizado(self):
            if not api_key_esperada:
                return True
            recibida = self.headers.get("X-API-Key", "").strip()
            if hmac.compare_digest(recibida.encode(), api_key_esperada.encode()):
                return True
            ip = self.client_address[0]
            auditoria.registrar("API_CENTRAL", ip, "AUTH_KO",
                                f"Petición rechazada sin X-API-Key válida en {self.path}")
            self._json(401, {"ok": False, "mensaje": "API Key inválida o ausente"})
            return False

        def do_OPTIONS(self):
            self.send_response(204)
            self._cabeceras_cors()
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            partes = [unquote(p) for p in parsed.path.strip("/").split("/") if p]
            qs = parse_qs(parsed.query)

            # --- /ws y /ws/{id} ---
            if partes == ["ws"]:
                return self._json(200, central.foto_estaciones())
            if len(partes) == 2 and partes[0] == "ws":
                ws_id = partes[1]
                for ws in central.foto_estaciones():
                    if ws["id"] == ws_id:
                        return self._json(200, ws)
                return self._json(404, {"ok": False, "mensaje": f"Estación {ws_id} no encontrada"})

            # --- /operators y /operators/{id} ---
            if partes == ["operators"]:
                return self._json(200, central.bd.operarios())
            if len(partes) == 2 and partes[0] == "operators":
                op_id = partes[1]
                for op in central.bd.operarios():
                    if op["id"] == op_id:
                        return self._json(200, op)
                return self._json(404, {"ok": False, "mensaje": f"Operario {op_id} no encontrado"})

            # --- /irrigations ---
            if partes == ["irrigations"]:
                limite = self._entero(qs, "limite", 20)
                if limite is None:
                    return
                en_curso = [ws for ws in central.foto_estaciones() if ws["estado"] == "REGANDO"]
                return self._json(200, {
                    "en_curso": en_curso,
                    "historico": central.bd.ultimos_riegos(limite),
                })

            # --- /weather y /weather/{ws_id} ---
            if partes == ["weather"]:
                clima = [
                    {
                        "ws_id": ws["id"],
                        "ciudad": ws.get("ciudad"),
                        "temperatura": ws.get("temperatura"),
                        "alerta_clima": ws.get("alerta_clima"),
                        "estado_ws": ws["estado"],
                    }
                    for ws in central.foto_estaciones()
                ]
                return self._json(200, clima)
            if len(partes) == 2 and partes[0] == "weather":
                ws_id = partes[1]
                for ws in central.foto_estaciones():
                    if ws["id"] == ws_id:
                        return self._json(200, {
                            "ws_id": ws["id"],
                            "ciudad": ws.get("ciudad"),
                            "temperatura": ws.get("temperatura"),
                            "alerta_clima": ws.get("alerta_clima"),
                            "estado_ws": ws["estado"],
                        })
                return self._json(404, {"ok": False, "mensaje": f"Estación {ws_id} no encontrada"})

            # --- /audit y /audit/{fecha} ---
            if partes == ["audit"]:
                limite = self._entero(qs, "limite", 100)
                if limite is None:
                    return
                return self._json(200, auditoria.leer_ultimos(limite))
            if len(partes) == 2 and partes[0] == "audit":
                fecha = partes[1]
                return self._json(200, auditoria.leer_por_fecha(fecha))

            # --- /system ---
            if partes == ["system"]:
                return self._json(200, {
                    "puerto_sockets": central.puerto_sockets,
                    "kafka": central.servidor_kafka,
                    "logs": list(central.registro)[-80:],
                })

            self._json(404, {"ok": False, "mensaje": "Recurso no encontrado"})

        def do_POST(self):
            if not self._autorizado():
                return
            partes = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]
            ip = self.client_address[0]
            try:
                datos = self._leer_body()

                # POST /ws -> alta de estación
                if partes == ["ws"]:
                    ws_id = datos["id"].strip()
                    ubicacion = datos["ubicacion"].strip()
                    central.alta_estacion(ws_id, ubicacion)
                    auditoria.registrar("API_CENTRAL", ip, "REGISTRO", f"Alta de estación {ws_id} ({ubicacion})", ws_id=ws_id)
                    return self._json(201, {"ok": True, "mensaje": f"Estación {ws_id} registrada"})

                # POST /operators -> alta de operario
                if partes == ["operators"]:
                    op_id = datos["id"].strip()
                    nombre = datos["nombre"].strip()
                    central.alta_operario(op_id, nombre)
                    auditoria.registrar("API_CENTRAL", ip, "REGISTRO", f"Alta de operario {op_id} ({nombre})")
                    return self._json(201, {"ok": True, "mensaje": f"Operario {op_id} registrado"})

                # POST /irrigations -> iniciar riego desde Central (punto 11)
                if partes == ["irrigations"]:
                    ws_id = datos["ws_id"].strip()
                    duracion = int(datos.get("duracion", 10))
                    mensaje = central.orden_central("REGAR", ws_id, duracion)
                    auditoria.registrar("API_CENTRAL", ip, "RIEGO_INICIADO", mensaje, ws_id=ws_id)
                    return self._json(201, {"ok": True, "mensaje": mensaje})

                self._json(404, {"ok": False, "mensaje": "Recurso no encontrado"})
            except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
                self._json(400, {"ok": False, "mensaje": f"Petición no válida: {e}"})

        def do_PUT(self):
            if not self._autorizado():
                return
            partes = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]
            ip = self.client_address[0]
            try:
                datos = self._leer_body()

                # PUT /ws/{id} -> acciones sobre estación (BLOQUEAR, ACTIVAR, RESET_CLAVES)
                if len(partes) == 2 and partes[0] == "ws":
                    ws_id = partes[1]
                    accion = datos.get("accion", "").upper()
                    if accion in ("BLOQUEAR", "ACTIVAR"):
                        mensaje = central.orden_central(accion, ws_id)
                        auditoria.registrar("API_CENTRAL", ip, "ESTADO_WS", mensaje, ws_id=ws_id)
                        return self._json(200, {"ok": True, "mensaje": mensaje})
                    if accion == "RESET_CLAVES":
                        if hasattr(central, "revocar_claves"):
                            ok, mensaje = central.revocar_claves(ws_id, ip)
                            return self._json(200 if ok else 404, {"ok": ok, "mensaje": mensaje})
                        mensaje = central.orden_central("BLOQUEAR", ws_id)
                        auditoria.registrar("API_CENTRAL", ip, "CLAVES_RESET",
                                            f"Claves revocadas en {ws_id}", ws_id=ws_id)
                        return self._json(200, {"ok": True, "mensaje": mensaje})
                    return self._json(400, {"ok": False, "mensaje": "Acción debe ser BLOQUEAR, ACTIVAR o RESET_CLAVES"})

                # PUT /weather/{ws_id} -> WM_W actualiza clima / activa alerta
                if len(partes) == 2 and partes[0] == "weather":
                    ws_id = partes[1]
                    motivo = datos.get("motivo", "").upper()
                    if motivo not in ("LLUVIA", "HELADA"):
                        return self._json(400, {"ok": False, "mensaje": "Motivo debe ser LLUVIA o HELADA"})
                    ok, mensaje = central.registrar_alerta_clima(
                        ws_id,
                        motivo,
                        temperatura=datos.get("temperatura"),
                        ciudad=datos.get("ciudad"),
                        ip_origen=ip,
                    )
                    return self._json(200 if ok else 404, {"ok": ok, "mensaje": mensaje})

                self._json(404, {"ok": False, "mensaje": "Recurso no encontrado"})
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                self._json(400, {"ok": False, "mensaje": f"JSON no válido: {e}"})

        def do_DELETE(self):
            if not self._autorizado():
                return
            partes = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]
            ip = self.client_address[0]

            # DELETE /weather/{ws_id} -> WM_W cancela alerta climática
            if len(partes) == 2 and partes[0] == "weather":
                ws_id = partes[1]
                ok, mensaje = central.cancelar_alerta_clima(ws_id, ip_origen=ip)
                return self._json(200 if ok else 404, {"ok": ok, "mensaje": mensaje})

            self._json(404, {"ok": False, "mensaje": "Recurso no encontrado"})

    servidor = ThreadingHTTPServer(("0.0.0.0", puerto), Manejador)
    central.log(f"[API_CENTRAL] API REST por entidades en http://0.0.0.0:{puerto} (/ws, /operators, /irrigations, /weather, /audit)")
    servidor.serve_forever()
