"""
WM_W - Oficina Meteorológica (Weather Control Office).

Uso:  python WM_W.py [<url_api_central>] [<fichero_ciudades>]

  - Consulta OpenWeather cada 4 s para cada estación configurada.
  - Detecta condiciones adversas: HELADA (temp < 0 °C) o LLUVIA.
  - Permite cambiar las ciudades en caliente (editando el fichero o desde el menú).
  - Notifica alertas y cancelaciones a API_Central (cuando esté disponible).
"""

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

INTERVALO_CONSULTA = 4  # segundos (requisito del enunciado)
URL_OPENWEATHER = "https://api.openweathermap.org/data/2.5/weather"
FICHERO_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ciudades.txt")

MENU = """
=== WEATHER CONTROL OFFICE (WM_W) ===
  1  Ver estado climático actual de las estaciones
  2  Asignar / cambiar ciudad de una estación
  3  Quitar estación de la vigilancia
  0  Salir"""


def log(texto):
    print(f"{time.strftime('%H:%M:%S')} {texto}", flush=True)


def consultar_openweather(ciudad, api_key):
    """
    Consulta Current Weather Data de OpenWeather para una ciudad.
    Devuelve un dict: {'ciudad', 'temp', 'estado', 'descripcion'}
    donde 'estado' es 'HELADA', 'LLUVIA' u 'OK'.
    """
    params = urllib.parse.urlencode({
        "q": ciudad,
        "appid": api_key,
        "units": "metric",
        "lang": "es",
    })
    url = f"{URL_OPENWEATHER}?{params}"
    req = urllib.request.Request(url, method="GET")

    with urllib.request.urlopen(req, timeout=5) as resp:
        datos = json.loads(resp.read().decode("utf-8"))

    temp = float(datos["main"]["temp"])
    weather_list = datos.get("weather", [])
    principales = [w.get("main", "") for w in weather_list]
    descripcion = weather_list[0].get("description", "") if weather_list else ""

    llueve = ("rain" in datos) or any(
        m in ("Rain", "Drizzle", "Thunderstorm") for m in principales
    )

    if temp < 0:
        estado = "HELADA"
    elif llueve:
        estado = "LLUVIA"
    else:
        estado = "OK"

    return {
        "ciudad": datos.get("name", ciudad),
        "temp": round(temp, 1),
        "estado": estado,
        "descripcion": descripcion,
    }


class OficinaClima:
    def __init__(self, api_key, url_central=None, fichero_ciudades=FICHERO_DEFECTO):
        self.api_key = api_key
        self.url_central = url_central.rstrip("/") if url_central else None
        self.central_api_key = os.getenv("API_KEY", "")
        self.fichero_ciudades = fichero_ciudades

        self._lock = threading.Lock()
        self.ciudades = {}          # {ws_id: ciudad}
        self.ultimos_datos = {}     # {ws_id: {'ciudad', 'temp', 'estado', 'descripcion'}}
        self.alertas_activas = {}   # {ws_id: 'LLUVIA' | 'HELADA'}
        self._ultima_modificacion = 0.0

        self._asegurar_fichero_inicial()
        self.recargar_fichero_si_cambio(forzar=True)

        threading.Thread(target=self.bucle_clima, daemon=True).start()

    def _asegurar_fichero_inicial(self):
        """Crea un fichero de ciudades por defecto si aún no existe."""
        if not os.path.exists(self.fichero_ciudades):
            with open(self.fichero_ciudades, "w", encoding="utf-8") as f:
                f.write("# Formato: <WS_ID> <Ciudad>\n")
                f.write("WS_01 Alicante\n")
                f.write("WS_02 Madrid\n")

    def recargar_fichero_si_cambio(self, forzar=False):
        """Relee ciudades.txt si ha sido editado externamente en caliente."""
        if not self.fichero_ciudades or not os.path.exists(self.fichero_ciudades):
            return
        try:
            mtime = os.path.getmtime(self.fichero_ciudades)
        except OSError:
            return

        if not forzar and mtime <= self._ultima_modificacion:
            return

        nuevas = {}
        try:
            with open(self.fichero_ciudades, "r", encoding="utf-8") as f:
                for linea in f:
                    linea = linea.strip()
                    if not linea or linea.startswith("#"):
                        continue
                    partes = linea.split(maxsplit=1)
                    if len(partes) == 2:
                        nuevas[partes[0]] = partes[1]
        except OSError as e:
            log(f"[WM_W] Error leyendo {self.fichero_ciudades}: {e}")
            return

        with self._lock:
            cambio = nuevas != self.ciudades
            self.ciudades = nuevas
            self._ultima_modificacion = mtime

        if cambio:
            log(f"[WM_W] Ciudades cargadas desde fichero: {nuevas}")

    def _guardar_fichero(self):
        """Persiste en disco los cambios hechos desde el menú interactivo."""
        if not self.fichero_ciudades:
            return
        with self._lock:
            copia = dict(self.ciudades)
        try:
            with open(self.fichero_ciudades, "w", encoding="utf-8") as f:
                f.write("# Formato: <WS_ID> <Ciudad>\n")
                for ws_id, ciudad in copia.items():
                    f.write(f"{ws_id} {ciudad}\n")
            self._ultima_modificacion = os.path.getmtime(self.fichero_ciudades)
        except OSError as e:
            log(f"[WM_W] No se pudo guardar {self.fichero_ciudades}: {e}")

    def asignar_ciudad(self, ws_id, ciudad):
        with self._lock:
            self.ciudades[ws_id] = ciudad
        self._guardar_fichero()
        log(f"[WM_W] Estación {ws_id} asignada a '{ciudad}' (cambio en caliente).")

    def eliminar_estacion(self, ws_id):
        with self._lock:
            eliminada = self.ciudades.pop(ws_id, None)
            self.ultimos_datos.pop(ws_id, None)
        if eliminada:
            self._guardar_fichero()
            log(f"[WM_W] Estación {ws_id} eliminada de la vigilancia.")
        else:
            print(f"  La estación {ws_id} no estaba configurada.")

    def _notificar_central(self, ws_id, info):
        """
        Comunica cambios de alerta a API_Central (PUT o DELETE /api/v1/alertas/{ws_id}).
        Si url_central no está configurada aún (Semana 1), solo registra el evento en consola.
        """
        estado = info["estado"]
        previo = self.alertas_activas.get(ws_id)

        if estado in ("LLUVIA", "HELADA"):
            if previo != estado:
                log(f"[CLIMA] ALERTA en {ws_id} ({info['ciudad']}): {estado} "
                    f"({info['temp']} °C, {info['descripcion']})")
            self.alertas_activas[ws_id] = estado
            if self.url_central:
                self._enviar_http("PUT", f"{self.url_central}/api/v1/alertas/{ws_id}", {
                    "motivo": estado,
                    "temperatura": info["temp"],
                    "ciudad": info["ciudad"],
                })
        else:
            if previo is not None:
                log(f"[CLIMA] ALERTA CANCELADA en {ws_id} ({info['ciudad']}): "
                    f"clima favorable ({info['temp']} °C, {info['descripcion']})")
                del self.alertas_activas[ws_id]
                if self.url_central:
                    self._enviar_http("DELETE", f"{self.url_central}/api/v1/alertas/{ws_id}")

    def _enviar_http(self, metodo, url, payload=None):
        """Envía petición REST a API_Central con cabecera X-API-Key."""
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"X-API-Key": self.central_api_key}
        if data is not None:
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=4):
                pass
        except Exception as e:
            log(f"[WM_W] No se pudo notificar a API_Central ({metodo} {url}): {e}")

    def bucle_clima(self):
        """Hilo que cada 4 segundos consulta OpenWeather para todas las estaciones."""
        while True:
            self.recargar_fichero_si_cambio()
            with self._lock:
                items = list(self.ciudades.items())

            for ws_id, ciudad in items:
                try:
                    info = consultar_openweather(ciudad, self.api_key)
                    with self._lock:
                        self.ultimos_datos[ws_id] = info
                    self._notificar_central(ws_id, info)
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        log(f"[WM_W] Ciudad no encontrada en OpenWeather para {ws_id}: '{ciudad}'")
                    elif e.code == 401:
                        log("[WM_W] Error 401: OPENWEATHER_KEY inválida o aún no activada.")
                    else:
                        log(f"[WM_W] Error HTTP {e.code} consultando '{ciudad}'")
                except Exception as e:
                    log(f"[WM_W] Error de red consultando '{ciudad}': {e}")

            time.sleep(INTERVALO_CONSULTA)

    def ver_estado(self):
        with self._lock:
            ciudades = dict(self.ciudades)
            datos = dict(self.ultimos_datos)

        if not ciudades:
            print("  (no hay estaciones configuradas)")
            return

        print("\n  --- Estado Meteorológico (OpenWeather) ---")
        for ws_id, ciudad_cfg in ciudades.items():
            info = datos.get(ws_id)
            if not info:
                print(f"  {ws_id:<8} {ciudad_cfg:<16} (esperando primera consulta...)")
            else:
                marca = "ALERTA" if info["estado"] != "OK" else "OK"
                print(f"  {ws_id:<8} {info['ciudad']:<16} {info['temp']:>5} °C | "
                      f"{info['estado']:<7} ({info['descripcion']}) [{marca}]")
        print()

    def menu(self):
        while True:
            print(MENU, flush=True)
            try:
                opcion = input("> ").strip()
                if opcion == "1":
                    self.ver_estado()
                elif opcion == "2":
                    ws_id = input("  ID de la estación (ej. WS_01): ").strip()
                    ciudad = input("  Ciudad (ej. Oslo, Madrid, Tokyo): ").strip()
                    if ws_id and ciudad:
                        self.asignar_ciudad(ws_id, ciudad)
                    else:
                        print("  Datos no válidos.")
                elif opcion == "3":
                    ws_id = input("  ID de la estación a quitar: ").strip()
                    if ws_id:
                        self.eliminar_estacion(ws_id)
                elif opcion == "0":
                    return
            except EOFError:
                return

def cargar_env_lab():
    """Carga variables de deploy/lab/.env si existen y no están ya definidas en el entorno."""
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ruta_env = os.path.join(raiz, "deploy", "lab", ".env")
    if not os.path.exists(ruta_env):
        return
    with open(ruta_env, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            clave = clave.strip()
            valor = valor.strip().strip('"').strip("'")
            if clave and clave not in os.environ:
                os.environ[clave] = valor

if __name__ == "__main__":
    cargar_env_lab()

    api_key = os.getenv("OPENWEATHER_KEY", "").strip()
    if not api_key:
        print("Error: falta OPENWEATHER_KEY en el entorno o en deploy/lab/.env.")
        sys.exit(1)

    if len(sys.argv) >= 2:
        url_central = sys.argv[1]
    else:
        host = os.getenv("CENTRAL_HOST", "").strip()
        puerto = os.getenv("API_CENTRAL_PORT", "8080").strip()
        # Si aún tiene la IP de ejemplo 1.2.3.4, no intenta conectar a API_Central todavía
        url_central = f"http://{host}:{puerto}" if host and host != "1.2.3.4" else None

    fichero = sys.argv[2] if len(sys.argv) >= 3 else FICHERO_DEFECTO

    try:
        oficina = OficinaClima(api_key, url_central=url_central, fichero_ciudades=fichero)
        oficina.menu()
    except KeyboardInterrupt:
        print("\n[WM_W] Apagado.")