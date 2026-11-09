# -*- coding: utf-8 -*-
"""Configuracion del servidor y estado compartido en memoria.

Todo lo que se puede cambiar sin tocar codigo vive aca. Casi todos los
valores se pueden pisar con variables de entorno (las R2_*), asi que
normalmente NO hace falta editar este archivo.

--
Parte del servidor abierto de R2Beat. Ver LEEME.md en la raiz del proyecto.
"""

import datetime
import socket



import os

# ============================================================================
# DONDE ESTAN LAS COSAS
# ============================================================================
# Todo cuelga de la carpeta del proyecto (la que tiene LEEME.md), asi que el
# servidor arranca igual lo llames desde donde lo llames.
CARPETA_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARPETA_DATOS    = os.path.join(CARPETA_PROYECTO, "datos")
CARPETA_NOTAS    = os.path.join(CARPETA_DATOS, "notas")   # notas de cada cancion: <id>.bin
CARPETA_LOGS     = os.path.join(CARPETA_PROYECTO, "logs")

# La base de datos. Se crea sola la primera vez que arranca el servidor.
RUTA_DB        = os.environ.get("R2_DB") or os.path.join(CARPETA_DATOS, "cuentas.db")
# Tablas que se sacan del cliente con herramientas/extraer_datos.py:
RUTA_TIENDA    = os.path.join(CARPETA_DATOS, "shop_lt.txt")   # catalogo de la tienda
RUTA_CARAS     = os.path.join(CARPETA_DATOS, "face.txt")      # caras
RUTA_ALAS      = os.path.join(CARPETA_DATOS, "wing.txt")      # alas
RUTA_CANCIONES = os.path.join(CARPETA_DATOS, "canciones.txt") # lista del selector
RUTA_ESCENARIOS = os.path.join(CARPETA_DATOS, "escenarios.txt")  # pistas donde se corre
RUTA_LOG       = os.path.join(CARPETA_LOGS, "servidor.log")

for _carpeta in (CARPETA_DATOS, CARPETA_NOTAS, CARPETA_LOGS):
    os.makedirs(_carpeta, exist_ok=True)


HOST = "0.0.0.0"
LOGIN_PORT = 48004
LOBBY_PORT = 48005

# CERRAR EL SOCKET DE LOGIN TRAS EL RE-LOGIN DE SWITCH DE CANAL
#   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#ajustes-cerrar-el-socket-de-login-tras-el-re-login)
RELOGIN_CLOSE = os.environ.get("R2_RELOGIN_CLOSE", "1") != "0"

# FF49 — subsistema de NOTIFICACIÓN del cliente (handler 0x577640)
#   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#ajustes-ff49-subsistema-de-notificaci-n-del-client)
FF49_REPLY = os.environ.get("R2_FF49", "1") != "0"
# (bis) — EL FF49 TAMBIÉN VACIABA EL INVENTARIO
#   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#ajustes-bis-el-ff49-tambi-n-vaciaba-el-inventario)
FF49_INVENTARIO = os.environ.get("R2_FF49_INV", "1") != "0"
KEEPALIVE_SECS = 3        # TEST watchdog: cada cuánto el server manda keepalive en el lobby
DB_PATH = RUTA_DB  # VPS: R2_DB=<carpeta-del-servidor>/datos/cuentas.db (compartida con el patch server)
# ★ MODO DEV/TEST (default OFF = producción). Local: `R2_DEV=1`. Gatea la SALA DEMO ("SALA TEST" de _fake_rooms) y los
#   comandos de chat de test `.npc`/`.err`. En el VPS quedan APAGADOS -> la lista solo muestra salas reales y el chat no
#   procesa comandos de debug.
ENABLE_DEV = os.environ.get("R2_DEV", "1") != "0"   # ★ default ON (local/test): habilita `.npc`/`.err`/SALA TEST. En el VPS lanzar con R2_DEV=0
CLIENT_CODEPAGE = "cp1252"           # el cliente Latino decodifica texto con cp1252
TOKEN_TTL_HOURS = 6
# Handoff launcher->juego: el launcher hace login real (cred) y guarda aquí el uid+hora.
# El JUEGO (que conecta sin credenciales) SOLO se acepta si hubo un login del launcher
# hace < LAUNCHER_LOGIN_WINDOW seg. Sin eso -> RECHAZO (no más "entra como cualquiera").
LAUNCHER_LOGIN_WINDOW = 300  # segundos
# ★ handoff launcher->juego = COLA FIFO POR IP (bug fix 2026-06-30). Antes era un ÚNICO slot global -> con 2 logins
#   simultáneos el 2º launcher pisaba al 1º y un juego quedaba sin handoff ("id/contraseña no corresponde"). Peor:
#   VM con NAT aparece con la MISMA IP que el host (192.168.0.1), así que ni siquiera per-IP alcanzaba. El juego
#   NO manda identidad en su FF01 (uid=0), así que no se puede distinguir por contenido -> se encolan por IP y cada
#   juego consume el MÁS VIEJO (FIFO): con el launcher lanzando el juego justo después de loguear, el orden se
#   preserva. (Robustez total contra IP-compartida + reordenamiento = TOKEN en el FF01, requiere tocar launcher+exe.)
LAUNCHER_QUEUE = {}   # ip -> [ {"uid": int, "at": datetime}, ... ]  (FIFO de logins de launcher pendientes de handoff)
# ★ AUTORIZACIÓN POR-UID (fortificación 2026-06-30): el mecanismo REAL para diferenciar usuarios bajo la MISMA IP
#   NO es la IP ni el handoff FF01 (donde el juego manda uid=0) -> es el UID que el juego lee de su REGISTRY
#   (HKCU\Software\Softnyx\R2Beat, escrito por el launcher) y manda en el LOBBY FF02. El launcher marca acá "uid X
#   autorizado" al loguear con credenciales; el lobby FF02 valida que el uid presentado tenga login reciente.
#   Keyeado por UID => 2 usuarios de la misma IP se diferencian sin colisión, aunque logueen al mismo tiempo.
AUTHORIZED_LOGINS = {}   # uid -> datetime (login de launcher reciente para ese uid)
def _prune_launcher_queue(ip):
    """Descarta handoffs vencidos (> ventana) de la cola de esa IP."""
    now = datetime.datetime.now(); q = LAUNCHER_QUEUE.get(ip)
    if not q: return
    LAUNCHER_QUEUE[ip] = [e for e in q if (now - e["at"]).total_seconds() <= LAUNCHER_LOGIN_WINDOW]
    if not LAUNCHER_QUEUE[ip]: LAUNCHER_QUEUE.pop(ip, None)
PERMANENT = 0xEEFFCBB8
LICENSE_BYTE = 7   # SOL MAX (FFC9 -> player+0x6d36): check per-canción 0x451c60 permite tier <=20 (era 6=<=17). Sube el cap para habilitar las nivel 19 (2 Pianos 2573, Nano Breaker 2283, 444! Hell Survival 1271). El cap es un máximo: incluye todo lo inferior. (02=est 04=luna 06=sol-novato<=17 07=SOL<=20)
DEFAULT_EXP = 1000       # ★ 2026-07-06: exp INICIAL de chars NUEVOS (nivel 1 -> canal Estrella). YA NO se fuerza en
                         # los reads (progresión por nivel real). Antes era 25M (nivel 72=Sol) que TAPABA el muro de canal.
DEFAULT_COIN = 200000    # rcoin por defecto p/ usuarios NUEVOS (regla pedida): arrancan con 200000 金화.

# ★ RATES de recompensa por carrera (editables sin tocar código: variables de entorno; o cambiá el default acá).
#   El premio se calcula SERVER-SIDE (el cliente manda coin=0) ligado a judgment(pd)/combo/estrellas/puesto y se
#   PERSISTE en la DB (exp += , coin += ). x1 = normal; 1.5/2/3 = evento doble/triple.
EXP_RATE     = float(os.environ.get("R2_EXP_RATE",  "1.0"))    # multiplicador de EXP ganado por carrera
# ★★ CANDADO DE EXP (2026-07-29, pedido del dueño). Con R2_EXP_LOCK=1 la exp queda CLAVADA EN 0:
#    · db_add_exp_coin ESCRIBE exp=0 (no "suma 0") -> aunque algo la haya subido, la baja de nuevo.
#    · race_reward devuelve exp_total=0 -> el cartel de fin de carrera también muestra 0.
#    · `.rate exp N` queda RECHAZADO -> ningún admin puede levantarlo desde el chat.
#    Poner EXP_RATE=0 NO alcanzaba: `.rate exp N` lo pisa en memoria al instante y cualquiera de los
#    4 ADMIN_UIDS podía descongelarlo sin querer (fue exactamente lo que pasó el 28/07).
#    El RCOIN no se toca. Para liberar: sacar R2_EXP_LOCK del systemd + reiniciar.
EXP_LOCK     = os.environ.get("R2_EXP_LOCK", "0").strip().lower() not in ("", "0", "false", "no")
COIN_RATE    = float(os.environ.get("R2_COIN_RATE", "1.0"))    # multiplicador de RCOIN ganado por carrera
BASE_COIN    = int(os.environ.get("R2_BASE_COIN", "1000"))     # rcoin BASE fallback (canción SIN notas). El original traía
                                                               # el premio POR CANCIÓN; el 2013 Latino lo tiene en 0, así que
                                                               # derivamos el base de la CANTIDAD DE NOTAS (obstáculos).
COIN_PER_NOTE = float(os.environ.get("R2_COIN_PER_NOTE", "2.0"))  # rcoin base POR NOTA (base_cancion = n_notas * esto)
ADMIN_UIDS   = {int(x) for x in os.environ.get("R2_ADMIN_UIDS", "").split(",") if x.strip().isdigit()}  # uids con `.rate`
# ★ Mensaje de BIENVENIDA (banner notice_common, horn=7777). Editable a gusto. Se manda al entrar al menú.
WELCOME_TEXT = "Bienvenido a R2beat Latino\nClassic! Unite al Discord\noficial - tu-sitio.example\nDiviertete!!"
# ★ Saltos de línea para bajar el bloque del banner GM al área libre (debajo del logo). Sender y mensaje son
#   posiciones INDEPENDIENTES: el sender va un poco más arriba que el mensaje (para que quede arriba, no invertido).
GM_SENDER_NL = 2   # \n antes del NICK (sender)
GM_MSG_NL    = 2   # \n antes del MENSAJE  (mayor/= que el sender -> mensaje debajo del sender)
GM_NAME_INDENT = 4  # espacios al PRINCIPIO del NICK -> lo separa del muro izquierdo (se alinea a la izquierda)
LOAD_TIMEOUT = float(os.environ.get("R2_LOAD_TIMEOUT", "12"))  # seg. a esperar la carga de todos antes de FORZAR el arranque
RACE_MAX_SEC = float(os.environ.get("R2_RACE_MAX_SEC", "240")) # seg. tope de carrera -> fallback de settlement (anti-atasco)
# ★ CARTEL — PACIENCIA + KICK: la cuenta atrás del cliente (~5s, POR FRAMES) corre normal. El server espera hasta que TODOS
#   reporten (llegada/fallo) y ahí manda el cartel a todos; si al cierre (cuenta + margen) falta un rezagado, se lo DESCONECTA
#   (fuera del cartel) y el cartel se arma con los que SÍ entregaron. La tolerancia real a lentos va por resync EN GAMEPLAY.
COUNTDOWN_EST = float(os.environ.get("R2_COUNTDOWN_EST", "5.0"))  # ~duración de la cuenta atrás del cliente (300 frames @60fps)
CARTEL_MARGIN = float(os.environ.get("R2_CARTEL_MARGIN", "3.0"))  # margen (seg) TRAS la cuenta p/ recibir al rezagado; si no reporta -> se lo desconecta y el cartel se arma sin él
IDLE_TIMEOUT = float(os.environ.get("R2_IDLE_TIMEOUT", "15"))  # seg. sin recibir NADA EN CARRERA -> muerto (char fantasma).
                                                               # Un cliente CONGELADO no cierra su TCP; connection_lost no
                                                               # dispara. En carrera el cliente vivo manda FF4E cada <1s.
                                                               # SOLO aplica en gameplay: fuera de juego NO se patea (AFK ok).

# ★ 3 USUARIOS VÁLIDOS (cuentas reales en la DB, cada una con su exp/nivel/licencia). El tier sale del exp REAL
#   del usuario (NO de un toggle) -> el matcher rutea por banda. Para probar otro tier: logueá con otra cuenta
#   (sin reiniciar el server). Bandas de exp (matcher 0x452610 / tabla cliente 0x6E90B8):
LUNA_MIN_EXP = 847894      # >= esto y < SOL_MIN = banda LUNA (bucket 5, nivel 41-60)
SOL_MIN_EXP  = 19610219    # >= esto = banda SOL (bucket 7, nivel 71+)   (< LUNA_MIN = ESTRELLA, bucket 3)

PERMANENT_ENDTIME = 0xEEFFCBB8          # "永久" (año ~3000); valor del cliente para no-expira
DURATION_DAYS = {1: 7, 2: 30, 3: 100, 7: 3, 8: 365}

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close(); return ip
    except Exception:
        return "127.0.0.1"

LOCAL_IP = os.environ.get("R2_PUBLIC_IP") or get_local_ip()
CONNECTIONS = {}  # uid -> LobbyProtocol
COUPLE_PENDING = {}  # target_uid -> proposer_uid : propuestas de pareja pendientes (FF40 sub0x0A)
# ★ REGISTRO GLOBAL DE SALAS (multiplayer real): las salas creadas por jugadores viven acá para que OTROS las vean
#   en su lista (FF6E). Sin esto cada jugador solo veía la SALA TEST inyectada. key=room_id, val=dict de sala.
#   Cada sala guarda su `port` (canal): la lista se filtra por canal (jugadores del mismo puerto se ven).
ROOMS = {}
# ★ ORDEN DE FILL de slots (agente a616e7b3, confirma hipótesis del usuario): balanceo de equipos rojo(0,1,2)/
#   azul(3,4,5). Creador=2 (fijo por 0x584F80). 2º=3(azul), 3º=1(rojo), 4º=4(azul), 5º=0(rojo), 6º=5(azul).
#   El slot del JOINER lo controla el server: va en el BYTE0 de la respuesta FF11 -> el cliente lo escribe en
#   [me+0x40] (0x57805C). byte0 6-9/0xFE = ERROR (no usar como slot). Y el r08 del propio ocupante en FF0C = ese slot.
SLOT_FILL_ORDER = [2, 3, 1, 4, 0, 5]
def next_room_slot(room):
    taken = {p["slot"] for p in room.get("players", [])} | set(room.get("ghost_slots", []))   # NPCs también ocupan slot
    for s in SLOT_FILL_ORDER:
        if s not in taken:
            return s
    return None   # sala llena (6 ocupantes, incluidos NPCs)
_ROOM_SEQ = [0]   # contador de room_id (1..0x7FF; el FF6E codifica el id en 11 bits)
def alloc_room_id():
    # ★ MENOR ID LIBRE (2026-07-01): antes era monotónico ((seq%0x7FE)+1) -> el nº subía 1,2,3,4… aunque las salas se
    #   cerraran (las salas SÍ se cierran; era solo el contador). Ahora reusa el menor id 1..2046 que NO esté en ROOMS
    #   -> una sala nueva sin otras presentes vuelve a ser 001. (0x7FF=2047 = sala demo DEV, fuera del rango.)
    used = set(ROOMS.keys())
    for i in range(1, 0x7FF):
        if i not in used:
            return i
    return 1   # fallback improbable (2046 salas simultáneas)
REGISTER_CMD = 0xF0  # comando propio launcher-clean <-> server-clean para crear cuenta

# Usuario de prueba: tiene CREDENCIALES pero NO personaje, para ejercitar la compuerta de creación.
# ⚠ CUENTAS DE DEMO — BORRALAS ANTES DE ABRIR TU SERVIDOR AL PUBLICO.
#   Al arrancar por primera vez se crean solas 3 cuentas de prueba, una por cada
#   banda de nivel, para poder probar los canales sin jugar 70 niveles primero.
#   Los USUARIOS son "estrella", "luna" y "sol" (ver TIER_ACCOUNTS en
#   progresion.py). ⚠ No confundir con el APODO del personaje, que es distinto
#   ("EstrellaUser"...): para entrar va el usuario, no el apodo.
#   TODAS usan la MISMA contraseña, que es publica y esta acá abajo: "1234a".
#   Sirven para probar en tu casa. En un servidor de verdad son una puerta abierta:
#   borralas de datos/cuentas.db (o cambiales la contraseña) antes de publicarlo.
SAMPLE_USER = "ruii"
SAMPLE_PASS_HASH = "E5C6D666CB418157A097E2B7E5E89A17F4E68535AD844C41D1E3051A3F037C07"  # SHA-256("1234a")
