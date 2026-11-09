# CONTRATOS · Release 2

Acuerdos entre las dos personas para poder trabajar en paralelo. **Si algo de aquí cambia, se avisa y se edita este fichero en el mismo commit.**

Decisiones tomadas: cifrado **Fernet** · credenciales en **tabla de la misma BD SQLite** · identificación de la WS con **certificado autofirmado** · auditoría en **JSON Lines**.

---

## 1. Cifrado (`common/cripto.py`) — se escribe entre los dos

Fernet (AES-128-CBC + HMAC-SHA256 + marca de tiempo). Se justifica en la memoria: cifra y autentica a la vez (un mensaje manipulado no se descifra), gestiona el IV y evita errores típicos como reutilizar un nonce.

| Función | Qué hace |
|---|---|
| `generar_clave() -> str` | Clave Fernet nueva (texto base64) |
| `cifrar(clave: str, datos: dict \| str) -> str` | Devuelve el token Fernet como texto |
| `descifrar(clave: str, token: str) -> dict \| str` | Lanza `ErrorCripto` si la clave o el mensaje no son válidos |
| `derivar_clave(secreto: str) -> str` | SHA-256 del secreto convertido en clave Fernet (para proteger la entrega de la clave de sesión) |

**Clave de sesión:** única por WS, la genera Central al autenticar. Se guarda en memoria y en la BD (columna `clave_sesion`). Revocar = ponerla a NULL.

### Qué va cifrado y cómo

- **Sockets Monitor → Central** (después del AUTH): `ESTADO#<ws_id>#<token_fernet de "OK"|"KO">`. El `ws_id` va en claro para que Central sepa qué clave usar. La respuesta de Central también va cifrada.
- **Kafka**: sobre `{"ws_id": "WS_01", "cifrado": "<token_fernet del JSON original>"}`.
  - Cifrados: `wm-eventos-ws`, `wm-telemetria` (WS → Central) y los mensajes `tipo=ORDEN` de `wm-central` (Central → Engine).
  - En claro (los leen los FO): `NOTIFICACION` y `ESTADO_RED`.
  - Regla para el consumidor: si el mensaje tiene la clave `cifrado`, se descifra; si no, se trata como antes.
- **Engine**: recibe la clave del Monitor en la respuesta al `HOLA`: `ID#<ws_id>#<clave_sesion>`. Es una conexión local entre contenedores del mismo PC.

## 2. Registry (WM_Registry) — API REST sobre HTTPS, puerto 8443

Certificado del servidor: autofirmado, lo genera el Registry. El Monitor lo verifica **fijándolo** (`ca_registry.pem` en su imagen), con `verify_mode=CERT_REQUIRED` y sin comprobar el nombre del host, porque la IP de AWS cambia en cada sesión.

Certificado de la WS: el Monitor genera un par RSA y un certificado autofirmado (`CN=<ws_id>`, `O=<ubicación>`). La clave privada nunca sale de la estación.

| Método | Ruta | Cuerpo / cabecera | Respuesta |
|---|---|---|---|
| POST | `/api/v1/estaciones` | `{ws_id, ubicacion, certificado_pem}` | `201 {ws_id, token}` (el token solo se muestra ahora) · `409` si ya existe |
| GET | `/api/v1/estaciones` | — | Lista (sin secretos) |
| GET | `/api/v1/estaciones/{ws_id}` | — | `{ws_id, ubicacion, huella_cert, activa}` o `404` |
| PUT | `/api/v1/estaciones/{ws_id}` | `X-Token`, `{ubicacion}` | `200` |
| DELETE | `/api/v1/estaciones/{ws_id}` | `X-Token` | `200` (baja: `activa=0`) |

Tabla en la BD compartida:
`credenciales(ws_id PK, ubicacion, huella_cert, clave_auth, clave_sesion, activa, alta_ts)`
donde `clave_auth = SHA-256(token)`. El token en claro no se guarda.

Central crea la `Estacion` en memoria la primera vez que una WS registrada se autentica (no hace falta reiniciarla).

## 3. Autenticación Monitor ↔ Central (sockets)

El token no viaja nunca en claro: reto-respuesta con HMAC.

1. Monitor → Central: `AUTH#<ws_id>`
2. Central → Monitor: `RETO#<nonce aleatorio>` (o `KO#WS_NO_REGISTRADA`)
3. Monitor → Central: `PRUEBA#<ws_id>#<hex(HMAC-SHA256(clave_auth, nonce))>#<salud>`
4. Central → Monitor: `OK#<ws_id>#<clave_sesion cifrada con derivar_clave(clave_auth)>` o `KO#CREDENCIAL_INVALIDA` / `KO#ID_YA_CONECTADO`

Reset de claves (opción en Central): `clave_sesion = NULL`. En el siguiente `ESTADO`, Central responde `KO#REAUTENTICAR`; la WS queda fuera de servicio hasta que el operador use la opción **Reautenticar** del menú del Monitor, que repite los pasos 1-4.

## 4. API_Central (HTTP, puerto 8080, sustituye a `panel_web.py`)

Prefijo `/api/v1`. CORS abierto para el Front. WM_W envía la cabecera `X-API-Key` (variable de entorno `API_KEY`).

| Método | Ruta | Uso |
|---|---|---|
| GET | `/estado` | Todo lo que pinta el Front (estaciones, operarios, riegos, registro) |
| GET | `/estaciones`, `/operarios`, `/riegos` | Consultas sueltas |
| GET | `/alertas` | Alertas climáticas activas |
| GET | `/auditoria?limite=100` | Últimas líneas del log de auditoría |
| PUT | `/alertas/{ws_id}` | WM_W: activa o actualiza alerta `{motivo: "LLUVIA"\|"HELADA", temperatura, ciudad}` |
| DELETE | `/alertas/{ws_id}` | WM_W: cancela la alerta |
| DELETE | `/estaciones/{ws_id}/clave` | Reset de claves (la WS queda fuera de servicio) |
| POST | `/ordenes` | Órdenes del punto 11 (las de la R1) |

### Campos nuevos en `Estacion.a_dict()`

| Campo | Tipo | Lo rellena |
|---|---|---|
| `registrada` | bool | Persona 1 |
| `token_activo` | bool (hay `clave_sesion`) | Persona 1 |
| `ciudad` | str \| null | Persona 2 |
| `temperatura` | float \| null | Persona 2 |
| `alerta_clima` | null \| `{motivo, temperatura, ciudad}` | Persona 2 |

### Regla del estado con alerta climática

Banderas independientes: `bloqueada` (orden manual) y `alerta_clima` (WM_W). Al activar una alerta no se corta el riego en curso. Estado visible = FUERA DE SERVICIO si `bloqueada` o (`alerta_clima` y no está regando). Al cancelar la alerta se restaura sin tocar `bloqueada`.

## 5. Auditoría (`common/auditoria.py`)

`registrar(actor, ip, accion, descripcion, **extra)` añade una línea JSON a `/data/auditoria.jsonl`:

```json
{"ts": "2026-11-09T09:40:12+01:00", "actor": "WS_01", "ip": "10.0.0.5", "accion": "AUTH_OK", "descripcion": "Autenticación correcta"}
```

Acciones: `AUTH_OK`, `AUTH_KO`, `REGISTRO`, `BAJA`, `CLAVES_RESET`, `FUGA`, `FUGA_RESUELTA`, `ESTADO_WS`, `BLOQUEO`, `RIEGO_INICIADO`, `RIEGO_FINALIZADO`, `ALERTA_CLIMA`, `ALERTA_CANCELADA`, `ERROR`.
La IP de una WS sale del socket (`addr`). Los FO añaden su IP al campo `ip` de la solicitud en Kafka (la declaran ellos).

## 6. Puertos y variables

| Servicio | Puerto | Dónde |
|---|---|---|
| Central (sockets) | 5000 | cloud |
| API_Central | 8080 | cloud |
| Registry (HTTPS) | 8443 | cloud |
| Front (Node) | 3000 | cloud |
| Kafka | 9092 | cloud |

Variables: `API_KEY`, `OPENWEATHER_KEY`, `REGISTRY_URL`, `CA_REGISTRY` (ruta del `.pem`), `KAFKA_BROKER`.

## 7. Reparto de tareas

Dos funcionalidades de punta a punta. Cada una toca Registry/Central/Monitor/Front/despliegue, así los dos trabajáis en todo el sistema.

| Iker · registro y acceso seguro | Àlex · clima y alertas |
|---|---|
| Registry, certificados y HTTPS | WM_W y OpenWeather |
| Menú del Monitor (registro, baja, reautenticar) | API_Central (`api_central.py`) y endpoints de alertas |
| `AUTH` con reto-respuesta y claves de sesión | Parada diferida por clima en Central |
| Sockets Monitor ↔ Central cifrados | Kafka Engine ↔ Central cifrado |
| Reset de claves | Auditoría (`auditoria.py` y endpoint) |
| Front: sección de WS (registro, activación, token, operarios) | Front: sección de clima, alertas, mensajes de WM_W y errores |
| Despliegue: Registry (cloud) y composes de WS (lab 2) | Despliegue: Front y API (cloud) y WM_W (lab 1) |

### Reglas de trabajo

- Rama por persona (`iker-registro`, `alex-clima`) y PR pequeños a `main` al menos una vez por semana.
- **Cada PR lo revisa el otro** antes de mezclarse.
- **Cada viernes**, 20 minutos: cada uno explica al otro lo que ha hecho esa semana. Se defiende todo el código, no solo el propio.
- Cada tarea se da por hecha cuando funciona **en Docker**, no solo en local.
- Los prompts de IA que uséis se guardan en `prompts/` según vayáis trabajando, no al final.

### Plan semanal

Entrega: semana del 13/12. Quedan cinco semanas de trabajo.

#### Semana 1 · 9 – 15 nov · Base común

**Juntos (lunes–martes)**
- [ ] Etiquetar la R1 (`git tag release1`) y crear las dos ramas
- [ ] Escribir `common/cripto.py` con su test (ida y vuelta, clave incorrecta, mensaje manipulado)
- [ ] Pedir la API key de OpenWeather (tarda en activarse)
- [ ] Comprobar que podéis lanzar el Learner Lab de AWS y reabrir los puertos 5000, 8080, 8443, 9092, 3000

**Iker**
- [ ] Tabla `credenciales` en `WM_Central/database.py`
- [ ] Esqueleto de WM_Registry y certificado autofirmado del servidor, sirviendo por HTTPS
- [ ] `POST` y `GET /api/v1/estaciones`, probados con `curl` y con el certificado fijado

**Àlex**
- [ ] `common/auditoria.py` (Iker lo necesita pronto; que haga la versión mínima el primer día)
- [ ] WM_W: consultar OpenWeather para una ciudad y decidir LLUVIA / HELADA / OK
- [ ] Ciudades editables en caliente (fichero que se relee y/o menú), consulta cada 4 s

**Hito:** `cripto.py` mezclado en `main`, Registry responde por HTTPS, WM_W imprime el clima de varias ciudades.

#### Semana 2 · 16 – 22 nov · Registro y API

**Iker**
- [ ] `PUT` y `DELETE` del Registry; baja = `activa=0`
- [ ] Monitor: generar par RSA + certificado, menú (registrar / baja), llamada HTTPS con certificado fijado
- [ ] Dockerfile del Registry; volumen de la BD compartida con Central

**Àlex**
- [ ] `api_central.py`: migrar lo de `panel_web.py` a `/api/v1`, CORS y `X-API-Key`
- [ ] `PUT` y `DELETE /alertas/{ws_id}`, `GET /alertas`
- [ ] WM_W notifica la alerta y su cancelación a la API; Dockerfile de WM_W

**Hito:** una WS se registra desde su menú y aparece en la BD. WM_W activa y cancela alertas visibles en `/api/v1/estado`.

#### Semana 3 · 23 – 29 nov · Seguridad y lógica de clima

**Iker**
- [ ] Central: `AUTH` con reto-HMAC; crear la `Estacion` al autenticarse; generar y guardar `clave_sesion`
- [ ] Campos `registrada` y `token_activo` en `a_dict()`
- [ ] Monitor ↔ Central cifrado (`ESTADO#...`); el Monitor entrega la clave al Engine en `ID#...`

**Àlex**
- [ ] Central: banderas `alerta_clima` y `bloqueada`, regla del estado, denegar riegos nuevos con alerta
- [ ] Riego en curso termina con normalidad y luego pasa a fuera de servicio; al cancelar se restaura
- [ ] Sobre cifrado en Kafka (`wm-eventos-ws`, `wm-telemetria`, `ORDEN`); Engine cifra y descifra
- [ ] Campos `ciudad`, `temperatura`, `alerta_clima` en `a_dict()`

**Hito:** una WS registrada se autentica y todo su tráfico va cifrado. Una alerta de clima deja la WS fuera de servicio sin cortar el riego.

#### Semana 4 · 30 nov – 6 dic · Reset, Front, auditoría y despliegue

**Iker**
- [ ] Reset de claves: `DELETE /estaciones/{ws_id}/clave` y botón; opción **Reautenticar** del Monitor
- [ ] Llamadas a `auditoria.registrar` en registro, autenticación y reset
- [ ] Front (Node): estructura y sección de WS
- [ ] Despliegue: Registry en cloud, `compose.ws.yml` actualizado (lab 2)

**Àlex**
- [ ] Front (Node): sección de clima, alertas, mensajes de WM_W y errores
- [ ] Llamadas a `auditoria.registrar` en alertas, riegos y fugas; `GET /auditoria`
- [ ] Despliegue: Front y API en cloud, `compose.fo.yml` y WM_W en lab 1
- [ ] Si sobra tiempo: SSL/TLS en Kafka (opcional del enunciado)

**Hito:** todo funciona por separado desplegado en su sitio.

#### Semana 5 · 7 – 13 dic · Integración y memoria (juntos)

- [ ] Despliegue completo: cloud (AWS), lab 1 y lab 2, y ensayo del guion de la demo
- [ ] Probar los escenarios: registro, auth fallida, reset de claves, alerta de clima con riego en curso, cambio de ciudad en caliente
- [ ] Capturas del sistema integrado
- [ ] Memoria: portada, componentes, código, guía de despliegue, justificación del cifrado, capturas y prompts (cada uno redacta su funcionalidad desde la semana 4)
- [ ] Repasar entre los dos todos los módulos; cada uno debe poder explicar cualquier línea

**Plan B si se retrasa algo:** lo que se puede recortar sin perder puntos esenciales es SSL en Kafka y la prueba de posesión de la clave privada. Lo que no se puede recortar es cifrado, reset de claves, auditoría, Registry por HTTPS y el despliegue completo.
