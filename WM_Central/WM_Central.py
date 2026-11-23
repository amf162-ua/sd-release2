"""
WM_Central - Central de control de la red de riego.

Uso:  python WM_Central.py <puerto_sockets> <ip_broker> [<puerto_broker>] [--panel 8080] [--db ruta]

Hilos que se ejecutan en paralelo:
  - Servidor de sockets: un hilo por cada Monitor (WM_WS_M) conectado.
  - Consumidor Kafka: solicitudes de riego, eventos y telemetría de los Engines.
  - Publicador del estado de la red (cada 2 s) para las apps de operario.
  - Panel web (HTTP) con el cuadro de mandos y las órdenes del punto 11.
"""

import argparse
import os
import socket
import sys
import threading
import time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import auditoria                                        # noqa: E402
from common import estados as E                                     # noqa: E402
from common import kafka_utils as K                                 # noqa: E402
from common.protocolo import CanalTramas, ErrorProtocolo, STX, EOT, construir, separar  # noqa: E402
from database import BaseDatos                                      # noqa: E402
from panel_web import iniciar_panel                                 # noqa: E402

TIMEOUT_MONITOR = 6          # s sin noticias del Monitor -> WS DESCONECTADA
TIMEOUT_AUTORIZACION = 5     # s que esperamos a que el Engine abra la electroválvula
DURACION_MAXIMA = 3600
GRUPO_KAFKA = 'wm-central'   # fijo: Kafka recuerda por dónde iba CENTRAL entre reinicios


class Estacion:
    """Estado en memoria de una WS. El estado visible se deduce de estos campos."""

    def __init__(self, ws_id, ubicacion, bloqueada=False):
        self.id = ws_id
        self.ubicacion = ubicacion
        self.bloqueada = bloqueada
        self.conectada = False
        self.salud = E.SALUD_OK
        self.riego = None       # dict con order_id, operator_id, caudal, volumen, segundos
        self.pendiente = None   # solicitud autorizada por CENTRAL esperando al Engine
        self.ciudad = ubicacion
        self.temperatura = None
        self.alerta_clima = None  # None | 'LLUVIA' | 'HELADA'
        self.bloqueo_previo = False   # ¿estaba bloqueada a mano al llegar la alerta?

    @property
    def estado(self):
        if not self.conectada:
            return E.DESCONECTADA
        if self.salud == E.SALUD_KO:
            return E.FUGA
        if self.bloqueada and not (self.alerta_clima and self.riego):
            return E.FUERA_SERVICIO
        if self.riego:
            return E.REGANDO
        return E.DISPONIBLE

    def a_dict(self):
        riego = self.riego or {}
        return {
            'id': self.id,
            'ubicacion': self.ubicacion,
            'estado': self.estado,
            'registrada': True,
            'token_activo': bool(getattr(self, 'clave_sesion', None)),
            'caudal': riego.get('caudal', 0.0),
            'volumen': riego.get('volumen', 0.0),
            'segundos': riego.get('segundos', 0),
            'duracion': riego.get('duracion', 0),
            'operario': riego.get('operator_id', ''),
            'ciudad': self.ciudad,
            'temperatura': self.temperatura,
            'alerta_clima': self.alerta_clima,
        }


class Central:
    def __init__(self, puerto_sockets: int, servidor_kafka: str, bd: BaseDatos):
        self.puerto_sockets = puerto_sockets
        self.servidor_kafka = servidor_kafka
        self.bd = bd
        self.lock = threading.RLock()
        self.registro = deque(maxlen=300)
        self.productor = None

        # Punto 1 de la mecánica: al arrancar se cargan las WS de la BD como DESCONECTADAS
        self.estaciones = {e['id']: Estacion(e['id'], e['ubicacion'], e['bloqueada'])
                           for e in bd.estaciones()}

    def log(self, texto: str):
        linea = f"{time.strftime('%H:%M:%S')} {texto}"
        print(linea, flush=True)
        self.registro.append(linea)

    # =========================================================================
    # Envío de mensajes por Kafka
    # =========================================================================
    def publicar(self, topic, ws_id, mensaje):
        K.publicar(self.productor, topic, ws_id, mensaje, self.log)

    def notificar(self, solicitud, subtipo, texto, **extra):
        """Informa al operario (y a la WS) de cada paso de su solicitud.
        subtipo: INFO, AUTORIZADA, DENEGADA o RESUMEN."""
        self.log(f"[{solicitud['operator_id']} -> {solicitud['ws_id']}] {subtipo}: {texto}")
        self.publicar(K.TOPIC_CENTRAL, solicitud['ws_id'], {
            'tipo': K.TIPO_NOTIFICACION, 'subtipo': subtipo, 'texto': texto,
            'order_id': solicitud['order_id'], 'operator_id': solicitud['operator_id'],
            'ws_id': solicitud['ws_id'], **extra})

    def ordenar(self, ws_id, orden, **datos):
        self.publicar(K.TOPIC_CENTRAL, ws_id, {'tipo': K.TIPO_ORDEN, 'ws_id': ws_id, 'orden': orden, **datos})

    # =========================================================================
    # Solicitudes de riego (mecánica, puntos 3-6)
    # =========================================================================
    def procesar_solicitud(self, sol):
        sol.setdefault('order_id', K.nuevo_id('ORD'))
        sol.setdefault('origen', 'FO')
        ws_id = sol.get('ws_id')
        self.notificar(sol, 'INFO', 'Solicitud recibida por CENTRAL. Comprobando estación...')

        with self.lock:
            ws = self.estaciones.get(ws_id)
            motivo = None
            if sol['origen'] == 'FO' and not self.bd.existe_operario(sol['operator_id']):
                motivo = f"El operario {sol['operator_id']} no está dado de alta en CENTRAL"
            elif ws is None:
                motivo = f"La estación {ws_id} no existe"
            elif ws.estado != E.DISPONIBLE:
                motivo = f"La estación no está disponible (estado: {ws.estado})"
            elif ws.pendiente:
                motivo = 'La estación ya tiene otra activación en curso'
            elif not 0 < int(sol.get('duracion', 0)) <= DURACION_MAXIMA:
                motivo = f'Duración no válida (1-{DURACION_MAXIMA} s)'

            if motivo:
                self.notificar(sol, 'DENEGADA', motivo)
                return

            # La WS está disponible: la reservamos y pedimos al Engine que abra la válvula
            sol['duracion'] = int(sol['duracion'])
            ws.pendiente = sol
            self.notificar(sol, 'INFO', f'Estación disponible. Solicitando apertura a {ws_id}...')
            self.ordenar(ws_id, 'REGAR', order_id=sol['order_id'],
                         operator_id=sol['operator_id'], duracion=sol['duracion'])
            threading.Timer(TIMEOUT_AUTORIZACION, self._caducar_pendiente,
                            args=(ws_id, sol['order_id'])).start()

    def _caducar_pendiente(self, ws_id, order_id):
        with self.lock:
            ws = self.estaciones.get(ws_id)
            if ws and ws.pendiente and ws.pendiente['order_id'] == order_id:
                sol, ws.pendiente = ws.pendiente, None
                self.notificar(sol, 'DENEGADA', 'La estación no ha respondido a tiempo')

    # =========================================================================
    # Eventos y telemetría que publican los Engines
    # =========================================================================
    def procesar_evento(self, ev):
        ws_id, tipo = ev.get('ws_id'), ev.get('evento')
        with self.lock:
            ws = self.estaciones.get(ws_id)
            if ws is None:
                return

            if tipo == 'ENGINE_LISTO':
                # El Engine acaba de (re)arrancar: le recordamos si está bloqueado
                self.log(f"[{ws_id}] Engine conectado a Kafka")
                if ws.riego:
                    self._finalizar_riego(ws, E.FIN_DESCONEXION, ws.riego['segundos'], ws.riego['volumen'])
                self.ordenar(ws_id, 'BLOQUEAR' if ws.bloqueada else 'ACTIVAR')

            elif tipo == 'RIEGO_INICIADO':
                sol = ws.pendiente
                if not sol or sol['order_id'] != ev.get('order_id'):
                    return
                ws.pendiente = None
                ws.riego = {'order_id': sol['order_id'], 'operator_id': sol['operator_id'],
                            'duracion': sol['duracion'], 'caudal': 0.0, 'volumen': 0.0, 'segundos': 0}
                self.notificar(sol, 'AUTORIZADA', f'Electroválvula de {ws_id} abierta. Riego en curso.')

            elif tipo == 'RIEGO_RECHAZADO':
                sol = ws.pendiente
                if sol and sol['order_id'] == ev.get('order_id'):
                    ws.pendiente = None
                    self.notificar(sol, 'DENEGADA', f"La estación rechazó el riego: {ev.get('motivo')}")

            elif tipo == 'RIEGO_FINALIZADO':
                segundos, volumen = ev.get('segundos', 0), ev.get('volumen', 0.0)
                if ev.get('recuperado'):
                    # El Engine se cayó en mitad del riego y, al volver, envía su estado final
                    self._riego_recuperado(ws.id, ev, segundos, volumen)
                elif ws.riego and ws.riego['order_id'] == ev.get('order_id'):
                    self._finalizar_riego(ws, ev.get('motivo'), segundos, volumen)
                elif ev.get('operator_id'):
                    # Riego que CENTRAL no tiene en memoria: terminó mientras estaba caída o se
                    # reinició en medio. Se registra igual (si ya estaba en la BD, no se duplica)
                    self._registrar_riego(ws.id, ev['order_id'], ev['operator_id'],
                                          ev.get('motivo'), segundos, volumen)

    def procesar_telemetria(self, t, atrasado=False):
        with self.lock:
            ws = self.estaciones.get(t.get('ws_id'))
            if ws is None:
                return
            if (ws.riego is None and not ws.pendiente and not atrasado and t.get('operator_id')
                    and not self.bd.existe_riego(t['order_id'])):
                # CENTRAL se ha reiniciado en mitad de este riego: lo "adopta" para mostrarlo en
                # el panel y para no autorizar otro riego en esta estación mientras tanto
                ws.riego = {'order_id': t['order_id'], 'operator_id': t['operator_id'],
                            'duracion': t.get('duracion', 0)}
                self.log(f"[{ws.id}] Riego en curso recuperado tras el reinicio de CENTRAL "
                         f"(operario {t['operator_id']})")
            if ws.riego and ws.riego['order_id'] == t.get('order_id'):
                ws.riego.update(caudal=t['caudal'], volumen=t['volumen'], segundos=t['segundos'])

    def _finalizar_riego(self, ws, motivo, segundos, volumen):
        """Cierra el riego en curso de la WS y lo registra."""
        riego, ws.riego = ws.riego, None
        self._registrar_riego(ws.id, riego['order_id'], riego['operator_id'], motivo, segundos, volumen)

    def _riego_recuperado(self, ws_id, ev, segundos, volumen):
        """
        Estado final que envía un Engine al recuperarse de una caída a mitad de riego.
        Normalmente CENTRAL ya cerró ese riego con la última telemetría (al detectar la avería):
        se corrige con los datos reales de la estación. Si no lo tenía, se registra sin más.
        """
        order_id, operator_id = ev['order_id'], ev['operator_id']
        if not self.bd.actualizar_riego(order_id, segundos, volumen, E.FIN_AVERIA):
            self._registrar_riego(ws_id, order_id, operator_id, E.FIN_AVERIA, segundos, volumen)
            return
        sol = {'order_id': order_id, 'operator_id': operator_id, 'ws_id': ws_id}
        self.notificar(sol, 'RESUMEN', f'Estado final enviado por la estación tras recuperarse de la '
                                       f'avería: {volumen} L en {segundos} s',
                       motivo=E.FIN_AVERIA, segundos=segundos, volumen=volumen)

    def _registrar_riego(self, ws_id, order_id, operator_id, motivo, segundos, volumen):
        """Guarda el riego en la BD y envía el resumen al operario (solo la primera vez)."""
        if not self.bd.guardar_riego(order_id, ws_id, operator_id, segundos, volumen, motivo):
            self.log(f"[{ws_id}] Fin de riego {order_id} repetido: ya estaba registrado")
            return
        sol = {'order_id': order_id, 'operator_id': operator_id, 'ws_id': ws_id}
        self.notificar(sol, 'RESUMEN', f'Riego finalizado ({motivo}): {volumen} L en {segundos} s',
                       motivo=motivo, segundos=segundos, volumen=volumen)

    def bucle_kafka(self):
        self.productor = K.crear_productor(self.servidor_kafka, self.log)
        topics = [K.TOPIC_SOLICITUDES, K.TOPIC_EVENTOS_WS, K.TOPIC_TELEMETRIA]
        # Grupo FIJO: al reiniciarse, Kafka le entrega lo que se publicó mientras estaba caída
        for topic, msg, atrasado in K.escuchar(self.servidor_kafka, topics, GRUPO_KAFKA, self.log):
            try:
                if topic == K.TOPIC_SOLICITUDES:
                    if atrasado:
                        # Se pidió con CENTRAL caída: quien la pidió ya dejó de esperar
                        self.log(f"[KAFKA] Solicitud descartada ({msg.get('operator_id')} -> "
                                 f"{msg.get('ws_id')}): se hizo mientras CENTRAL estaba caída")
                    else:
                        self.procesar_solicitud(msg)
                elif topic == K.TOPIC_EVENTOS_WS:
                    self.procesar_evento(msg)
                elif topic == K.TOPIC_TELEMETRIA:
                    self.procesar_telemetria(msg, atrasado)
            except Exception as e:  # un mensaje mal formado no debe tumbar CENTRAL
                self.log(f"[KAFKA] Mensaje ignorado en {topic} ({e}): {msg}")

    def bucle_estado_red(self):
        """Publica cada 2 s el estado de todas las WS y los últimos riegos para los operarios."""
        while True:
            time.sleep(2)
            self.publicar(K.TOPIC_CENTRAL, 'CENTRAL', {
                'tipo': K.TIPO_ESTADO_RED,
                'estaciones': self.foto_estaciones(),
                'ultimos_riegos': self.bd.ultimos_riegos(5)
            })

    # =========================================================================
    # Sockets con los Monitores (WM_WS_M)
    # =========================================================================
    def bucle_sockets(self):
        servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        servidor.bind(('0.0.0.0', self.puerto_sockets))
        servidor.listen()
        self.log(f"[SOCKETS] Escuchando Monitores en el puerto {self.puerto_sockets}")
        while True:
            conn, addr = servidor.accept()
            threading.Thread(target=self.atender_monitor, args=(conn, addr), daemon=True).start()

    def atender_monitor(self, conn, addr):
        canal = CanalTramas(conn, timeout=TIMEOUT_MONITOR)
        ws = None
        try:
            canal.esperar_saludo()
            while True:
                byte = canal.leer_control()
                if byte == EOT:
                    break
                if byte != STX:
                    continue
                campos = separar(canal.recibir(byte))
                if campos[0] == 'AUTH':
                    respuesta, ws = self._autenticar(campos, addr, ws)
                elif campos[0] == 'REGISTRO' and len(campos) >= 3:
                    respuesta = self._registrar_estacion(campos[1], campos[2], addr)
                elif campos[0] == 'ESTADO' and ws and campos[1] == ws.id:
                    respuesta = self._actualizar_salud(ws, campos[2])
                else:
                    respuesta = construir('KO', 'MENSAJE_NO_VALIDO')
                canal.enviar(respuesta)
        except (ErrorProtocolo, OSError) as e:
            if ws:
                self.log(f"[SOCKETS] {ws.id}: conexión perdida ({e})")
        finally:
            canal.cerrar()
            if ws:
                self._desconectar(ws)

    def _autenticar(self, campos, addr, ws_actual):
        if ws_actual or len(campos) < 3:
            return construir('KO', 'AUTH_NO_VALIDO'), ws_actual
        ws_id, ubicacion = campos[1], campos[2]
        with self.lock:
            ws = self.estaciones.get(ws_id)
            if ws and ws.conectada:
                self.log(f"[AUTH] Rechazada {ws_id} desde {addr[0]}: ya hay una estación conectada con ese ID")
                return construir('KO', 'ID_YA_CONECTADO'), None
            if ws is None:
                self.log(f"[AUTH] Rechazada {ws_id} desde {addr[0]}: estación NO registrada en CENTRAL")
                return construir('KO', 'WS_NO_REGISTRADA'), None
            ws.ubicacion = ubicacion
            ws.conectada = True
            ws.salud = campos[3] if len(campos) > 3 else E.SALUD_OK
            self.bd.guardar_estacion(ws_id, ubicacion)
            self.log(f"[AUTH] {ws_id} autenticada desde {addr[0]} -> {ws.estado}")
        return construir('OK', ws_id), ws

    def _registrar_estacion(self, ws_id, ubicacion, addr):
        with self.lock:
            if ws_id in self.estaciones:
                self.log(f"[REGISTRO] {ws_id} ya estaba registrada en CENTRAL")
                return construir('OK', 'YA_REGISTRADA')
            self.estaciones[ws_id] = Estacion(ws_id, ubicacion)
            self.bd.guardar_estacion(ws_id, ubicacion)
            self.log(f"[REGISTRO] Nueva estación {ws_id} ({ubicacion}) dada de alta desde {addr[0]}")
            return construir('OK', ws_id)

    def _actualizar_salud(self, ws, salud):
        with self.lock:
            if salud != ws.salud:
                ws.salud = salud
                if salud == E.SALUD_KO:
                    self.log(f"[FUGA] {ws.id}: avería detectada por el Monitor")
                    self._abortar(ws, E.FIN_FUGA)
                else:
                    self.log(f"[FUGA] {ws.id}: avería resuelta -> {ws.estado}")
        return construir('OK')

    def _desconectar(self, ws):
        with self.lock:
            ws.conectada = False
            self.log(f"[SOCKETS] {ws.id} DESCONECTADA")
            self._abortar(ws, E.FIN_DESCONEXION)

    def _abortar(self, ws, motivo):
        """Detiene cualquier riego o activación pendiente de la WS (fuga, bloqueo, desconexión)."""
        if ws.pendiente:
            sol, ws.pendiente = ws.pendiente, None
            self.notificar(sol, 'DENEGADA', f'Activación cancelada: {motivo}')
        if ws.riego:
            self.ordenar(ws.id, 'PARAR', motivo=motivo)
            # Si el Engine no puede contestar (fuga/caída), cerramos el riego nosotros
            if motivo in (E.FIN_FUGA, E.FIN_DESCONEXION):
                self._finalizar_riego(ws, motivo, ws.riego['segundos'], ws.riego['volumen'])

    # =========================================================================
    # Órdenes arbitrarias de CENTRAL (mecánica, punto 11). ws_id='*' = todas
    # =========================================================================
    def _destinos(self, ws_id):
        return list(self.estaciones.values()) if ws_id == '*' else [self.estaciones[ws_id]]

    def orden_central(self, orden, ws_id, duracion=10):
        with self.lock:
            if ws_id != '*' and ws_id not in self.estaciones:
                return f"La estación {ws_id} no existe"
            for ws in self._destinos(ws_id):
                if orden == 'REGAR':
                    self.procesar_solicitud({'operator_id': 'CENTRAL', 'ws_id': ws.id,
                                             'duracion': duracion, 'origen': 'CENTRAL'})
                elif orden == 'BLOQUEAR':
                    ws.bloqueada = True
                    self.bd.marcar_bloqueada(ws.id, True)
                    self._abortar(ws, E.FIN_BLOQUEO)
                    self.ordenar(ws.id, 'BLOQUEAR')
                    self.log(f"[CENTRAL] {ws.id} -> FUERA DE SERVICIO")
                elif orden == 'ACTIVAR':
                    ws.bloqueada = False
                    self.bd.marcar_bloqueada(ws.id, False)
                    self.ordenar(ws.id, 'ACTIVAR')
                    self.log(f"[CENTRAL] {ws.id} -> ACTIVADA ({ws.estado})")
                else:
                    return f"Orden desconocida: {orden}"
        return f"Orden {orden} enviada a {'todas las estaciones' if ws_id == '*' else ws_id}"

    def alta_estacion(self, ws_id, ubicacion):
        with self.lock:
            self.bd.guardar_estacion(ws_id, ubicacion)
            if ws_id in self.estaciones:
                self.estaciones[ws_id].ubicacion = ubicacion
            else:
                self.estaciones[ws_id] = Estacion(ws_id, ubicacion)
        self.log(f"[ALTA] Estación {ws_id} ({ubicacion}) registrada desde el panel")

    def alta_operario(self, operator_id, nombre):
        self.bd.guardar_operario(operator_id, nombre)
        self.log(f"[ALTA] Operario {operator_id} ({nombre}) registrado")

    def foto_estaciones(self):
        with self.lock:
            return [ws.a_dict() for ws in sorted(self.estaciones.values(), key=lambda w: w.id)]

    # =========================================================================
    def iniciar(self, puerto_panel):
        self.log(f"[CENTRAL] {len(self.estaciones)} estaciones cargadas de la BD (DESCONECTADAS)")
        threading.Thread(target=self.bucle_sockets, daemon=True).start()
        threading.Thread(target=self.bucle_kafka, daemon=True).start()
        threading.Thread(target=self.bucle_estado_red, daemon=True).start()
        iniciar_panel(self, puerto_panel)   # bloquea el hilo principal

    def registrar_alerta_clima(self, ws_id, motivo, temperatura=None, ciudad=None, ip_origen="WM_W"):
        with self.lock:
            ws = self.estaciones.get(ws_id)
            if ws is None:
                return False, f"La estación {ws_id} no existe"
            if ciudad:
                ws.ciudad = ciudad
            if temperatura is not None:
                ws.temperatura = temperatura
            if ws.alerta_clima is None:          # primera alerta: recuerda si ya estaba bloqueada
                ws.bloqueo_previo = ws.bloqueada
            ws.alerta_clima = motivo
            ws.bloqueada = True
            self.bd.marcar_bloqueada(ws.id, True)
            if ws.pendiente:
                sol, ws.pendiente = ws.pendiente, None
                self.notificar(sol, 'DENEGADA', f'Activación cancelada por alerta climática ({motivo})')
            if not ws.riego:
                self.ordenar(ws.id, 'BLOQUEAR')

        desc = f"Alerta {motivo} en {ws_id} ({ws.ciudad}, {temperatura} °C)"
        self.log(f"[CLIMA] {desc} -> FUERA DE SERVICIO")
        auditoria.registrar("WM_W", ip_origen, "ALERTA_CLIMA", desc,
                            ws_id=ws_id, motivo=motivo, temperatura=temperatura, ciudad=ws.ciudad)
        return True, desc

    def cancelar_alerta_clima(self, ws_id, ip_origen="WM_W"):
        with self.lock:
            ws = self.estaciones.get(ws_id)
            if ws is None:
                return False, f"La estación {ws_id} no existe"
            if ws.alerta_clima is None:          # nada que cancelar (DELETE idempotente)
                return True, f"{ws_id} no tenía alerta climática activa"
            ws.alerta_clima = None
            if not ws.bloqueo_previo:            # solo se desbloquea si el bloqueo era del clima
                ws.bloqueada = False
                self.bd.marcar_bloqueada(ws.id, False)
                self.ordenar(ws.id, 'ACTIVAR')

        desc = f"Alerta climática cancelada en {ws_id} ({ws.ciudad}) -> {ws.estado}"
        self.log(f"[CLIMA] {desc}")
        auditoria.registrar("WM_W", ip_origen, "ALERTA_CANCELADA", desc,
                            ws_id=ws_id, ciudad=ws.ciudad)
        return True, desc


def main():
    p = argparse.ArgumentParser(prog='WM_Central', description='Central de la red de riego')
    p.add_argument('puerto_sockets', type=int, help='Puerto que se ofrece a los WM_WS_M')
    p.add_argument('ip_broker', help='IP del broker Kafka (o ip:puerto)')
    p.add_argument('puerto_broker', nargs='?', default='9092')
    p.add_argument('--panel', type=int, default=8080, help='Puerto del panel web (por defecto 8080)')
    p.add_argument('--db', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'water_management.db'))
    args = p.parse_args()

    central = Central(args.puerto_sockets, K.bootstrap(args.ip_broker, args.puerto_broker), BaseDatos(args.db))
    try:
        central.iniciar(args.panel)
    except KeyboardInterrupt:
        print('\n[CENTRAL] Apagado.')


if __name__ == '__main__':
    main()
