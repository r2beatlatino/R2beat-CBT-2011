# -*- coding: utf-8 -*-
"""Servidor de LOBBY y JUEGO (puertos 48005/48015/48025/48035).

Es el grande: menu, salas, chat, tienda, carrera y cartel de
resultados. Un puerto por canal; la identidad del canal la fija el
cliente al entrar, no el puerto.

--
Parte del servidor abierto de R2Beat. Ver LEEME.md en la raiz del proyecto.
"""

import asyncio
import datetime
import json
import os
import random
import time
import struct
import traceback

from . import telemetry
from . import ajustes
from .ajustes import (
    ADMIN_UIDS,
    AUTHORIZED_LOGINS,
    CARTEL_MARGIN,
    CLIENT_CODEPAGE,
    CONNECTIONS,
    COUNTDOWN_EST,
    COUPLE_PENDING,
    ENABLE_DEV,
    EXP_LOCK,
    FF49_INVENTARIO,
    FF49_REPLY,
    GM_MSG_NL,
    GM_NAME_INDENT,
    GM_SENDER_NL,
    IDLE_TIMEOUT,
    KEEPALIVE_SECS,
    LAUNCHER_LOGIN_WINDOW,
    LICENSE_BYTE,
    LOAD_TIMEOUT,
    LOBBY_PORT,
    PERMANENT_ENDTIME,
    RACE_MAX_SEC,
    ROOMS,
    WELCOME_TEXT,
    alloc_room_id,
    next_room_slot,
)
from .basedatos import (
    db,
    db_add_exp_coin,
    db_add_friend,
    db_ban_account,
    db_create_char,
    db_del_couple,
    db_del_friend,
    db_get_char,
    db_get_couple,
    db_get_friends,
    db_get_mission,
    db_get_user,
    db_set_coin,
    db_set_couple,
    db_set_mission,
    db_unban_account,
    has_character,
    licencia_ffc9_de,
)
from .canciones import (
    NPC_AUTOPLAY,
    NPC_MOVE,
    NPC_TRACK,
    SIM_RACE_PROFILES,
    SONGS,
    load_notes,
    npc_autoplay_score,
    npc_profile_for_slot,
    race_reward,
    song_base_coin,
    song_note_count,
)
from .catalogo import (
    CONSUMABLE_USES,
    SLOTS_SIN_EXCLUSIVIDAD,
    db_clear_worn,
    db_expire_items,
    db_inv_add,
    db_inv_add_count,
    db_inv_clear_slot,
    db_inv_has,
    db_inv_list,
    db_inv_set_inuse,
    db_set_worn_slot,
    db_worn_items,
    decode_item_id,
    duration_to_endtime,
    item_slot,
    occ_face_index,
    occ_wing_id,
    pack_date,
    price_for_item,
    shop_lookup,
)
from .cifrado import decrypt_ff55, enc_text, encrypt_ff55
from .paquetes import (
    HANDLED_INNER,
    _activar_keepalive,
    build_ff13_libre,
    build_ff45_couple_for,
    build_ff97,
    build_myrecord_honesto,
    inventario_de,
    pack_item_record,
    parse_create_char,
    parse_ff55_commands,
)
from .progresion import (
    LEVEL_TABLE,
    LIBRE_FF13,
    LIBRE_LIC,
    LIBRE_MODO6,
    LIBRE_PORT,
    LICENCIA_HONESTA,
    NOMBRE_LICENCIA,
    level_from_exp,
    licencia_de_nivel,
    tier_from_exp,
)
from .registro import _c, log, wire
from .lobby_social import PiezaSocial
from .lobby_tienda import PiezaTienda
from .lobby_menu import PiezaMenu
from .lobby_salas import PiezaSalas
from .lobby_carrera import PiezaCarrera
from .lobby_admin import PiezaAdmin


# ============================================================
# LOBBY server (puerto 48005)
#   PLUMBING honesto: intercambio de clave (FF02) + crypto FF55.
#   LÓGICA: la compuerta ¿tiene personaje? -> entrar : crear.
#   Los DATOS de los paquetes (MyRecord, prompt de creación) = TODO (investigación cliente).
# ============================================================
class LobbyProtocol(PiezaSocial, PiezaTienda, PiezaMenu, PiezaSalas, PiezaCarrera, PiezaAdmin, asyncio.Protocol):
    def __init__(self):
        self.transport = None; self.buffer = b""
        self.user_id = 0
        self.role_id = 0; self.mmd = 0; self.pwd = bytes(256)
        self.counter = 0; self.send_counter = 0
        self.nic_resp = 0     # se setea en el key exchange; usado por el nic-check FF03/FF00
        self._key_ready = False        # True tras el key-exchange FF02 (ya se puede cifrar FF55)
        self._keepalive_task = None    # TEST watchdog: keepalive periódico

    def connection_made(self, transport):
        self.transport = transport
        _activar_keepalive(transport, "lobby")
        self._conn_ts = time.monotonic()   # ★ p/ contexto FF9E crashdump (Δ desde connect: separa lobby/loading/post-carrera)
        self.last_recv = time.monotonic()  # ★ heartbeat: última vez que recibimos algo (watchdog de inactividad)
        self._keepalive_task = asyncio.get_event_loop().create_task(self._idle_watchdog())
        _lport = (transport.get_extra_info('sockname') or ('', 0))[1]
        self.port = _lport          # ★ puerto del canal de ESTA conexión (para tratar Libre distinto)
        _lname = {48005: "Estrella", 48015: "Luna", 48025: "Sol", LIBRE_PORT: "Libre"}.get(_lport, "?")
        self.channel_name = _lname
        log(f"[Lobby] conexión {transport.get_extra_info('peername')} -> PUERTO {_lport} ({_lname})")
        # (keepalive FF97 REVERTIDO 2026-06-27: el 2013 CONSUME la telemetría FF8C/97/107 sin responder
        #  y el keepalive no salvó la conexión. La cura real es push de FF49 ROSTER tras MyRecord.)

    async def _keepalive(self):
        try:
            while True:
                await asyncio.sleep(KEEPALIVE_SECS)
                if self._key_ready and self.transport and not self.transport.is_closing():
                    self.send_packet(0x97, build_ff97())
                    log(f"[Lobby] keepalive FF97 (watchdog test, cada {KEEPALIVE_SECS}s)")
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log(f"[Lobby] keepalive err: {exc}")

    async def _idle_watchdog(self):
        """★ TAREA 2 — HEARTBEAT/IDLE. Un cliente CONGELADO (proceso suspendido) NO cierra su TCP -> connection_lost
        nunca dispara y su char queda de FANTASMA en la sala/carrera. Un cliente VIVO manda algo cada 1-2s (FF4E en
        carrera, FF0C/FF13 en sala, keepalives). Si no llega NADA en IDLE_TIMEOUT seg. -> lo damos por muerto y cerramos
        su transport, lo que dispara connection_lost -> leave_room (FF0E + settle, igual que una salida real)."""
        try:
            while True:
                await asyncio.sleep(3)
                if self.transport is None or self.transport.is_closing():
                    return
                # ★ SOLO en GAMEPLAY (carrera en curso). Fuera de juego (AFK/menú/sala) NO se patea a nadie: un jugador
                #   puede quedarse quieto sin mandar tráfico y es legítimo. El char fantasma sólo molesta EN la carrera.
                _rid = getattr(self, "my_room_id", None)
                _room = ROOMS.get(_rid) if _rid else None
                if not (_room and _room.get("game_started")):
                    continue
                idle = time.monotonic() - getattr(self, "last_recv", 0.0)
                if idle > IDLE_TIMEOUT:
                    log(f"[Lobby] ★ IDLE {idle:.1f}s > {IDLE_TIMEOUT}s uid={self.user_id} (en carrera) -> congelado/muerto, forzar desconexión")
                    try: self.transport.close()   # -> connection_lost -> leave_room (mid-race: FF0E + settle)
                    except Exception: pass
                    return
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log(f"[Lobby] idle watchdog err: {exc}")

    def data_received(self, data):
        self.last_recv = time.monotonic()   # ★ heartbeat: recibimos algo -> el cliente está vivo
        self.buffer += data
        while len(self.buffer) >= 4:
            if self.buffer[0] != 0xFF:
                self.buffer = self.buffer[1:]; continue
            cmd = self.buffer[1]
            ln = struct.unpack("<H", self.buffer[2:4])[0]
            if len(self.buffer) < 4 + ln:
                break
            pkt = self.buffer[:4 + ln]; self.buffer = self.buffer[4 + ln:]
            wire(self.user_id, "C2S", "lobby", cmd, pkt)
            try:
                self.handle(cmd, pkt[4:])
            except Exception:
                import traceback; log(f"[Lobby] EXC cmd=0x{cmd:02X}:\n" + traceback.format_exc())

    # — envío plano (pre-cifrado, solo login del lobby) —
    def send(self, data, name=""):
        self.transport.write(data)
        wire(self.user_id, "S2C", "lobby", data[1], data, note=name)

    # — envío cifrado FF55 (post-login: el cliente SOLO procesa FF55) —
    def send_packet(self, cmd, data):
        pad = bytes(random.randint(0, 255) for _ in range(4))
        prefix = b"\xFE" if cmd >= 0x100 else b"\xFF"
        inner = (b"\xFF\x56" + struct.pack("<H", len(pad)) + pad
                 + prefix + bytes([cmd & 0xFF]) + struct.pack("<H", len(data)) + data)
        edx = (self.role_id + self.mmd) ^ self.send_counter
        wire(self.user_id, "S2C", "inner", cmd, data, note=f"send_counter={self.send_counter}")
        enc = encrypt_ff55(inner, self.pwd, edx)
        self.send_counter += 1
        self.transport.write(b"\xFF\x55" + struct.pack("<H", len(enc)) + enc)

    def handle(self, cmd, payload):
        if cmd == 0x01:  # CHECK_VER
            self.send(bytes.fromhex("FF01010000"), "CHECK_VER OK")

        elif cmd == 0x02:  # LOGIN del lobby = intercambio de clave (PLUMBING honesto)
            nic = struct.unpack("<I", payload[-4:])[0] ^ 0xCCCCCCCC
            nic_resp = nic ^ 0xCACB1112
            self.nic_resp = nic_resp     # se reusa en el nic-check de FF03/FF00 ([mgr+0x90]=nic)
            # ★ IDENTIDAD AUTORITATIVA = el uid que el juego lee de SU registry (escrito por el launcher) y manda acá.
            #   Es lo que diferencia usuarios bajo la MISMA IP (cada juego manda SU uid). Validamos por-UID: debe tener
            #   un login de launcher reciente (AUTHORIZED_LOGINS[uid]). Soft por ahora (no cierra la conexión de test),
            #   pero loguea claramente si un uid NO está autorizado (spoof / cliente sin launcher / registry stale).
            if len(payload) >= 4:
                self.user_id = struct.unpack("<I", payload[0:4])[0]
            _auth = AUTHORIZED_LOGINS.get(self.user_id)
            _authorized = (_auth is not None and (datetime.datetime.now() - _auth).total_seconds() <= LAUNCHER_LOGIN_WINDOW)
            if not _authorized:
                log(f"[Lobby] ⚠ FF02 uid={self.user_id} SIN login de launcher reciente (¿spoof / sin launcher / registry vieja?)")
            CONNECTIONS[self.user_id] = self
            self.notify_friends_status(True, "conectó")   # mis amigos me mueven a "conectados" (location≠0xFFFF)
            self.role_id = self.user_id; self.mmd = 0
            self.counter = 0; self.send_counter = 0
            self.pwd = bytes(random.randint(0, 255) for _ in range(256))
            self._key_ready = True     # ya se puede cifrar FF55 -> habilita el keepalive (watchdog test)
            body = bytearray([0]) + struct.pack("<I", nic_resp) + struct.pack("<I", self.mmd) + self.pwd
            self.send(b"\xFF\x02" + struct.pack("<H", len(body)) + body, "LOBBY LOGIN OK (key)")
            log(f"[Lobby] key exchange uid={self.user_id}")

        elif cmd == 0x55:  # contenedor cifrado: descifrar e ir a los comandos internos
            dec = None
            for try_c in range(self.counter, self.counter + 10):
                edx = (self.role_id + self.mmd) ^ try_c
                t = decrypt_ff55(payload, self.pwd, edx)
                if t[0:2] == b"\xFF\x56":
                    dec = t; self.counter = try_c + 1; break
            if dec is None:
                log("[Lobby] FF55 no descifró"); return
            for inner_cmd, data in parse_ff55_commands(dec):
                wire(self.user_id, "C2S", "inner", inner_cmd, data,
                     warn=inner_cmd not in HANDLED_INNER)
                self.handle_inner(inner_cmd, data)

        elif cmd == 0x4E:  # ★ GAMEDATA EN JUEGO (coords/posición de carrera) — RELAY MULTI (fase sync 2026-07-01).
            # Modelo de la guía coreana (secuencia RE, NO bytes; el crypto EXTERIOR coincide con el nuestro):
            #  1) DESCIFRAR el FF4E del emisor: key = try_counter ^ (role_id+mmd), XOR dwords[0,4,8]+u16[12], hasta que
            #     el inner sea FF26 con len 0x0A. Avanza self.counter (contador C→S compartido con FF55; si no se procesa
            #     el server se desincroniza >10 y deja de descifrar el fin de carrera).
            #  2) DECODIFICAR case/addr1/addr2 con el MAGIC de la sala (el emisor los cifra con magic; el receptor los lee
            #     RAW -> el server debe quitar el magic). Constantes 0xCA/0xEF/0xAB, field592=0 (del RE del cliente/guía).
            #  3) VALIDAR el case (set de casos que el cliente entiende, incl. 180=sync de coords). Si es inválido ->
            #     DROPEAR (anti-crash): si nuestras constantes difieren, sale inválido y no relayamos (sin sync) en vez de
            #     mandar basura que tumbe al receptor. + guard case0 (índice de slot fuera de rango -> crash).
            #  4) RE-ENSAMBLAR raw [case:1][addr1:4][addr2:4] como FF26 len=9 (+1 byte pad=14) y RE-CIFRAR por RECEPTOR con
            #     SU send_counter (mismo stream S→C que send_packet: usar-luego-incrementar). Enviar FF4E top-level (NO FF55).
            #  5) GUARDS: no relayar si la sala no está "jugando"; saltar receptores que YA terminaron (finished_users) ->
            #     mandarle coords a un finalista (estado estatua) lo crashea.
            if len(payload) >= 14:
                found = None; t = None
                for try_c in range(max(0, self.counter - 10), self.counter + 40):
                    key = (try_c ^ (self.role_id + self.mmd)) & 0xFFFFFFFF
                    tt = bytearray(payload)
                    for i in (0, 4, 8):
                        struct.pack_into("<I", tt, i, (struct.unpack_from("<I", tt, i)[0] ^ key) & 0xFFFFFFFF)
                    struct.pack_into("<H", tt, 12, (struct.unpack_from("<H", tt, 12)[0] ^ (key & 0xFFFF)) & 0xFFFF)
                    if tt[0:2] == b"\xFF\x26" and struct.unpack_from("<H", tt, 2)[0] == 0x0A:
                        found = try_c; self.counter = try_c + 1; t = tt; break
                if found is None:
                    log(f"[Lobby] FF4E no descifró (counter={self.counter}) -> posible desync de stream")
                else:
                    _rid = getattr(self, "my_room_id", None)
                    room = ROOMS.get(_rid) if _rid else None
                    if room is not None and room.get("game_started"):
                        magic = room.get("game_magic", getattr(self, "game_magic", 0)) & 0xFFFFFFFF
                        # inner FF26 payload (10B) = t[4:14]: [00][enc_case:1][enc_addr1:4][enc_addr2:4]
                        case_val = (t[5] ^ ((magic ^ 0xCA) & 0xFF)) & 0xFF
                        addr1 = (struct.unpack_from("<I", t, 6)[0] ^ ((magic ^ 0xEF) & 0xFFFFFFFF)) & 0xFFFFFFFF
                        addr2 = (struct.unpack_from("<I", t, 10)[0] ^ ((magic ^ 0xAB) & 0xFFFFFFFF)) & 0xFFFFFFFF
                        # ★ CAPTURA de TRACK para el REPLAY de NPCs (fase 2): loguea el triple RAW ya sin magic
                        #   ([case][addr1][addr2] + t + slot) a un JSONL. Se activa con R2_FF4E_CAPTURE=1. El track
                        #   así grabado es directamente reproducible (no necesita magic). Correr UNA carrera y listo.
                        if os.environ.get("R2_FF4E_CAPTURE", "0") == "1":
                            try:
                                with open(os.path.join("packets", "ff4e_track.jsonl"), "a", encoding="utf-8") as _tf:
                                    _tf.write(json.dumps({"t": time.strftime("%H:%M:%S") + f".{int((time.time()%1)*1000):03d}",
                                                          "uid": self.user_id, "slot": getattr(self, "host_slot", None),
                                                          "case": case_val, "a1": addr1, "a2": addr2}) + "\n")
                            except Exception:
                                pass
                        valid_cases = {0, 1, 5, 6, 50, 100, 101, 102, 103, 104, 105, 150, 180}
                        drop = case_val not in valid_cases
                        if not drop and case_val == 0:
                            if ((addr1 >> 24) & 0xFF) == 100 and ((addr1 >> 8) & 0xFF) != 0 and ((addr1 >> 16) & 0xFFFF) >= 6:
                                drop = True   # case0: índice de slot peligroso -> crash
                        if drop:
                            log(f"[Lobby] FF4E case={case_val} inválido/peligroso -> relay DROPEADO (no sync, no crash)")
                        else:
                            # re-ensamblar raw [case][addr1][addr2] (9B) como FF26 len=9 + 1 byte pad -> 14B para el XOR
                            p2 = bytes([case_val]) + struct.pack("<I", addr1) + struct.pack("<I", addr2)
                            inner14 = bytearray(b"\xFF\x26" + struct.pack("<H", len(p2)) + p2 + b"\x00")   # 14B
                            # ★ NO relayar coords a un jugador en estado ESTATUA (llegó a meta O falló en countdown-0):
                            #   mandarle FF4E a un estatua lo CRASHEA (RE). `reported` = finalistas + fallidos (ambos estatuas).
                            done = room.get("reported") or room.get("finished_users") or set()
                            relayed = 0
                            for p in room.get("players", []):
                                uid = p["uid"]
                                if uid == self.user_id or uid in done:
                                    continue
                                conn = CONNECTIONS.get(uid)
                                if conn is None:
                                    continue
                                p2_key = (conn.send_counter ^ (conn.role_id + conn.mmd)) & 0xFFFFFFFF
                                enc = bytearray(inner14)
                                for i in (0, 4, 8):
                                    struct.pack_into("<I", enc, i, (struct.unpack_from("<I", enc, i)[0] ^ p2_key) & 0xFFFFFFFF)
                                struct.pack_into("<H", enc, 12, (struct.unpack_from("<H", enc, 12)[0] ^ (p2_key & 0xFFFF)) & 0xFFFF)
                                conn.send_counter = (conn.send_counter + 1) & 0xFFFFFFFF
                                try:
                                    conn.transport.write(b"\xFF\x4E" + struct.pack("<H", 13) + bytes(enc[:13]))
                                    relayed += 1
                                except Exception as e:
                                    log(f"[Lobby] FF4E relay fallo a uid={uid}: {e}")
                            if relayed:
                                log(f"[Lobby] FF4E RELAY case={case_val} de uid={self.user_id} -> {relayed} receptor(es)")

    # ────────────────────────────────────────────────────────────────
    #  Comandos internos del lobby. AQUÍ vive la compuerta de creación.
    # ────────────────────────────────────────────────────────────────
    def handle_inner(self, cmd, data):
        telemetry.observe(self, cmd, data)   # panel admin: deriva online/estado (read-only)
        if cmd == 0x03:
            # FF03 = MyRecord
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-ff03-myrecord)
            if has_character(self.user_id):
                _expired = db_expire_items(self.user_id)   # quita de la DB los items vencidos ANTES del MyRecord
                ch = db_get_char(self.user_id)
                parts = db_worn_items(self.user_id, ch)   # pelo/ropa/patines + MANO/MASCOTA/CARA equipados (mismo set que la sala)
                # MAPEO REAL (RE 0x582B30): letra licencia -> player+0x348 (ícono mi-info/multiplayer):
                #   Y=1(novato)  N=2(ESTRELLA)  X=3(LUNA)  Z=4(SOL)   [el comentario viejo "N=luna" ERA FALSO]
                _uexp = ch["exp"] or 0   # ★ exp REAL (progresión por nivel; ya no se fuerza 25M)
                _uci, _ulic, _utier = tier_from_exp(_uexp)   # ← licencia/banda del EXP REAL del usuario
                mr = build_myrecord_honesto(ch["nickname"], char_id=(ch["char_id"] or 0x0001FF54),
                                            parts=parts, has_char=True, level=30, license=_ulic,
                                            coin=(ch["coin"] or 0), mission=(ch["mission"] or 0),   # ★ #64 -> restaura progreso de misión
                                            inventory=[(r["item_id"], r["end_time"], r["in_use"], r["count"]) for r in db_inv_list(self.user_id)])
                self.send_packet(0x03, mr)
                self._mr_sent = True   # ★ MyRecord enviado en esta conexión (puebla el picker) -> no re-mandar en JOINCHANNEL
                self._menu_seq = True  # ★ modo (1): el cliente PIDIÓ FF03 -> el JOINCHANNEL no debe repetir la secuencia de menú
                log(f"[Lobby] MyRecord honesto ({len(mr)}b) char={ch['nickname']!r} exp={_uexp} -> banda {_utier} (lic {_ulic}) -> MENÚ")
                # ★ BIENVENIDA con diseño notice_common (FF40/0x0D horn=7777 = tipo custom en notice.xml -> imgindex 0).
                #   Reemplaza el cartel fantasma built-in (silenciado con NOP en el exe). Texto = WELCOME_TEXT.
                self.send_packet(0x40, self.build_ff40_announce(WELCOME_TEXT, sender="Sistema", horn_type=7777))
                log(f"[Lobby] BIENVENIDA notice_common enviada (horn 7777) a uid={self.user_id}")
                # AVISO de items EXPIRADOS (FF43 EXPIREDITEM, handler cliente 0x5798B0 = [item_id:u32][mins:u32]):
                # el 2º campo = MINUTOS restantes (el cliente lo muestra como "expira en N minutos"), NO un timestamp.
                # Para un item ya vencido = 0. El server ya lo quitó de la DB; esto le avisa al usuario.
                _now = int(time.time())
                for (_iid, _et) in _expired:
                    _mins = max(0, (_et - _now) // 60)
                    self.send_packet(0x43, struct.pack("<I", _iid & 0xFFFFFFFF) + struct.pack("<I", _mins & 0xFFFFFFFF))
                if _expired:
                    log(f"[Lobby] {len(_expired)} item(s) EXPIRADO(s) -> FF43 aviso + quitados de DB: {[i for i,_ in _expired]}")
                # NIVEL: FF92 = set absoluto de exp (player+0x2D4); el nivel se computa de la tabla 0x6E90B8.
                exp = _uexp   # exp REAL del char (decide el bucket del matcher). luna/sol requieren UNCAP (ya parchado)
                self.send_packet(0x92, bytes([0]) + struct.pack("<I", exp & 0xFFFFFFFF))
                log(f"[Lobby] FF92 exp={exp} -> nivel computado por el cliente (tabla 0x6E90B8)")
                # FF3E = poblar el dropdown "Servidor" (combo UI [0x766640]+0xc8). Handler 0x579420->0x459550.
                # Payload = [channelId:u32]; 1 paquete por canal. (RE: MULTIPLAYER.md G.4). El id es un decimal
                # de 8 dígitos que codifica categoría/banda/status (TEST: usamos chan_id crudo para ver si puebla).
                # OBLIGATORIO: sin este FF49 el cliente se cae al entrar al lobby.
                # Además de llenar el popup "Servidores", deja apuntando bien un
                # índice interno del cliente. La entrada del personaje lleva su ropa
                # puesta como campos; filtrar los items nulos NO es opcional (un 0
                # pisaría el campo del cuerpo y el personaje se dibuja roto).
                _sel_cid = (ch["char_id"] if ch else 0) or 0x0001FF54
                body, _worn = self.build_ff49_roster(ch)
                self.send_packet(0x49, body)
                log(f"[Lobby] FF49 roster (tras MyRecord): select=char {_sel_cid} (worn={_worn})")
                # FF35 ANNOUNCE: el handler cliente 0x570910 PARSEA [b0][b1][b2][b3][len:1][texto] a un
                #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-ff35-announce-el-handler-cliente-0x570910-pa)
            else:
                # Sin personaje -> count=0 -> el cliente abre la pantalla de CREACIÓN (honesto).
                self.send_packet(0x03, build_myrecord_honesto("", has_char=False))
                log(f"[Lobby] uid={self.user_id} SIN personaje -> CREACIÓN")

        elif cmd == 0x00:
            # FF00 = keepalive con NIC challenge (handler 0x577550). Responder con el nic.
            self.send_packet(0x00, struct.pack("<I", self.nic_resp & 0xFFFFFFFF))

        elif cmd == 0x04:
            # CREATE_CHAR: el cliente confirma la creación y MANDA los datos elegidos.
            self.handle_create_char(data)

        elif cmd == 0xE2:
            # FFE2 = popup REFERIDO ("¿quién te invitó?") del flujo de CREACIÓN
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-ffe2-popup-referido-qui-n-te-invit-del-flujo)
            nlen = data[0] if data else 0
            nick = data[1:1 + nlen]
            self.send_packet(0xE2, bytes([0x00, nlen]) + nick)
            log(f"[Lobby] FFE2 referido nick={nick.decode(CLIENT_CODEPAGE,'replace')!r} "
                f"-> aceptado (result=0) -> popup avanza al lobby")

        elif cmd == 0x51:
            # El cliente pide su saldo cash. Responder o el popup de cash queda a medio init.
            self.send_cash()

        elif cmd == 0x06:
            # JOINCHANNEL: el cliente ENTRA a un canal (multiplayer). AQUÍ va la lista de salas.
            self.handle_joinchannel(data)

        elif cmd == 0x10:
            # CREATEROOM (handler cliente 0x584EB0). req=[mode:1][namelen:1][name][pwdlen?]. Resp
            # [roomid:u16 (>0 y !=0xFFFE)][1] = crea+entra. Luego el cliente necesita FF13 ROOMINFO
            # + FF0C USERLIST para DIBUJAR la sala (formato exacto a confirmar con el diag).
            mode = data[0] if data else 0
            nlen = data[1] if len(data) > 1 else 0
            rname = data[2:2 + nlen].decode(CLIENT_CODEPAGE, "replace") if nlen else ""
            self.room_name = rname or "sala"; self.room_mode = mode
            # Estado de sala server-side (Fase 2 Hito 1): stage(map)/canción/bitmask de slots. El cliente
            # redibuja desde el FF13 -> al cambiar algo, actualizamos el estado y RE-ENVIAMOS FF13.
            # ★ canción por defecto VÁLIDA (518 Zealot): si fuera inválida (102) la sala NO aparece en la lista de
            #   OTROS jugadores (el append 0x463a90 dropea salas con canción que el cliente no tiene). El host la
            #   cambia con el picker (FF1C) y actualizamos ROOMS.
            self.room_map = 8; self.room_song = 518; self.room_slot_mask = 0x3F   # bits=1 -> slots 0..5 ABIERTOS
            self.host_slot = 2   # slot VISIBLE del host (r32 del FF0C). team1=0,1,2 / team2=3,4,5. Cambia con FF18.
            self.ghost_slots = []   # NPCs `.npc` vacíos en sala nueva
            self.join_host_uid = None   # yo soy el HOST de esta sala (no entré a la de otro)
            # ★ REGISTRO GLOBAL: publicar la sala para que OTROS jugadores del MISMO canal la vean en su FF6E.
            self.room_id = alloc_room_id()
            self.my_room_id = self.room_id       # sala en la que estoy (para room_occupants)
            ROOMS[self.room_id] = {"id": self.room_id, "mode": self.room_mode, "name": self.room_name,
                                   "map_id": self.room_map, "song_id": self.room_song, "cur": 1, "max": 6,
                                   "flags": 0, "port": getattr(self, "port", LOBBY_PORT), "host_uid": self.user_id,
                                   "players": [{"uid": self.user_id, "slot": 2}]}   # el creador = "me" en slot 2
            log(f"[Lobby] SALA REGISTRADA id={self.room_id} '{self.room_name}' canal/puerto {getattr(self,'port','?')} (visible en FF6E)")
            # NOTA RE el modo solo/equipo NO se setea desde el server
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-nota-re-el-modo-solo-equipo-no-se-setea-desd)
            self.send_packet(0x10, struct.pack("<H", self.room_id) + b"\x00")   # roomid real, ack (modo es client-side)
            log(f"[Lobby] CREATEROOM mode={mode} name={rname!r} -> sala id={self.room_id} (ack; cliente pedirá FF13)")

        elif cmd == 0x11:
            # JOINROOM (recv 0x577FF0): el cliente ENTRA a una sala de la lista FF6E. Req: [v11:u16][...] con
            # v11 = roomid | (mode<<11) (LE; ej '01 10'=0x1001 -> roomid=1). Resp [result:u8<6][1]=entra; luego el
            # cliente pide FF13 + FF0C. ★ BUG ARREGLADO: antes SIEMPRE cargaba _fake_rooms() (SALA TEST) ignorando el
            # roomid -> caías en SALA TEST. Ahora se lee el roomid y se carga la sala REAL de ROOMS[roomid].
            v11 = struct.unpack_from("<H", data, 0)[0] if len(data) >= 2 else 0
            roomid = v11 & 0x7FF
            self.room_slot_mask = 0x3F
            self.ghost_slots = []; self.countdown_started = False; self.result_done = False
            room = ROOMS.get(roomid)
            if room:
                # ★ BLOQUEAR JOIN si la PARTIDA ESTÁ EN CURSO -> popup (2026-07-01). Códigos de error del FF11 (recv 0x577FF0,
                #   byte0, popup vía 0x41C4C0), MAPEADOS EN VIVO: 6="no se puede entrar, revise el estado de la sala" (estado
                #   cambió/partida en curso), 8="contraseña incorrecta", 9="no hay lugares suficientes" (sala llena),
                #   0xFE="limitación de nivel", 7=espectador (sin popup). Partida en curso -> 6.
                if room.get("game_started"):
                    self.send_packet(0x11, bytes([6, 0]))
                    log(f"[Lobby] JOINROOM id={roomid} RECHAZADO: partida en curso (byte0=6)")
                    return
                # ★ sala REAL de otro jugador: cargar SU estado y AGREGARME a su lista de players como "me" en slot 0
                #   (el joiner tiene [me+0x40]=0, por eso su slot/identity DEBE ser 0 -> si no, no se puede mover).
                self.room_name = room["name"]; self.room_mode = room["mode"]
                self.room_map = room["map_id"]; self.room_song = room["song_id"]
                self.my_room_id = roomid
                players = room.setdefault("players", [{"uid": room["host_uid"], "slot": 2}])
                # ★ asignar el SLOT del joiner (server-side, balanceo de equipos) y mandarlo en el BYTE0 del FF11
                existing = next((pp for pp in players if pp["uid"] == self.user_id), None)
                if existing:
                    slot = existing["slot"]
                else:
                    slot = next_room_slot(room)
                    if slot is None:
                        self.send_packet(0x11, bytes([9, 0]))   # byte0=9 -> popup "no hay lugares suficientes en la sala" (mapeado en vivo)
                        log(f"[Lobby] JOINROOM id={roomid} LLENA -> rechazado (byte0=9)")
                        return
                    players.append({"uid": self.user_id, "slot": slot})
                self.host_slot = slot                 # mi slot = "me" (r08 del FF0C == [me+0x40])
                room["cur"] = len(players)
                self.send_packet(0x11, bytes([slot & 0xFF, 0]))   # ★ byte0 = SLOT asignado -> cliente lo pone en [me+0x40]
                self.notify_room(roomid)              # push FF0C a AMBOS -> el host ve al joiner y viceversa
                log(f"[Lobby] JOINROOM -> sala REAL id={roomid} '{room['name']}' uid={self.user_id} SLOT={slot} players={[(p['uid'],p['slot']) for p in players]}")
            else:
                fakes = self._fake_rooms()
                if fakes:
                    # fallback DEV (SALA TEST id 0x7FF): la de test con NPC. Solo con R2_DEV=1.
                    r = fakes[0]
                    self.room_name = r["name"]; self.room_mode = r.get("mode", 0)
                    self.room_map = r.get("map_id", 8); self.room_song = r.get("song_id", 518)
                    self.my_room_id = None; self.host_slot = 2; self.ghost_slots = [3]
                    self.send_packet(0x11, b"\x00\x00")
                    log(f"[Lobby] JOINROOM -> SALA TEST (roomid {roomid} no registrada) con NPC [DEV]")
                else:
                    # PROD: la sala no existe (cerrada / stale) -> rechazar (byte0=6 "revise el estado de la sala")
                    self.send_packet(0x11, bytes([6, 0]))
                    log(f"[Lobby] JOINROOM id={roomid} inexistente -> rechazado (byte0=6)")

        elif cmd == 0x12:
            # EXITROOM: el handler recv 0x5780D0 = `mov ecx,[0x766618](cRoom); call 0x535390; ret` y 0x535390 ES la
            # rutina salir→lobby (SceneSwitch a cLobby = cManager+0x6a50 vía 0x43b9b0). NO lee cuerpo. El cliente
            # manda FF12 y ESPERA que el server se lo ECHE de vuelta para disparar la transición cRoom→cLobby; sin
            # eco reintenta y se queda. FIX (agente RE 2026-06-30 a98d48b7): echar FF12 al que salió. (Cuerpo irrelevante.)
            self.send_packet(0x12, b"")
            self.leave_room()   # ★ me saco de la sala (o la cierro si era host) + notifico a los que quedan
            log("[Lobby] EXITROOM -> eco FF12 (dispara cRoom->cLobby)")

        elif cmd == 0x13:
            # ROOMINFO: el cliente PIDE la info de la sala (FF13 vacío) tras crear/entrar. Sin esto
            # se queda en el telón de carga. Responder con build_ff13. (handler cliente 0x5780E0.)
            self.send_roominfo()   # manda FF13 + FF4B (modo) -> layout solo/equipo seteado ANTES del FF0C de poblado
            log("[Lobby] ROOMINFO (FF13) + FF4B (modo) enviados")
            # ★ TAREA 3 — FF9B DIFERIDO: si soy el nuevo líder tras una salida en carrera, ahora que volví a la SALA
            #   (pedí FF13) es SEGURO habilitar mi UI de anfitrión (mandarlo tras el cartel crasheaba).
            _rid13 = getattr(self, "my_room_id", None)
            _room13 = ROOMS.get(_rid13) if _rid13 else None
            if _room13 is not None and _room13.get("_pending_host_ff9b") == self.user_id:
                _room13.pop("_pending_host_ff9b", None)
                self.join_host_uid = None
                _slot = next((p["slot"] & 0x7F for p in _room13.get("players", []) if p["uid"] == self.user_id), 2)
                self.send_packet(0x9B, bytes([_slot, 0x00, 0x00]))
                log(f"[Lobby] FF9B diferido -> uid={self.user_id} habilitado como líder (de vuelta en sala {_rid13})")

        elif cmd == 0x0C:
            # ROOM_USERLIST: el cliente PIDE la lista de ocupantes (FF0C [01]). Responder con el
            # host en su slot -> dibuja los slots y PARA el loop. (handler cliente 0x5863F0.)
            self.send_packet(0x0C, self.build_ff0c())   # host + fantasma(s) si `.npc` activo
            # ★ 2026-07-25: ya NO se empuja FF15 con las caras presentes. El FF0C las trae en r25
            #   (ver _ff0c_record) -> el ocupante nace con la cara puesta, sin reload y SIN SONIDO.
            #   Sí hay que SEMBRAR el estado: es la referencia del diff para los cambios posteriores.
            self.seed_face_state(getattr(self, "my_room_id", None))
            # ★ 2026-07-01: NO se manda FF4B (abría el POPUP). El MODO lo fija el FF13 silencioso; el COLOR de equipo por
            #   jugador lo pinta send_team_colors() vía FF37 (sin popup) — sin esto los avatares salían color 'solo'.
            self.send_team_colors()
            log("[Lobby] ROOM_USERLIST (FF0C) + colores de equipo (FF37) enviado (sin FF4B/popup)")
            # MÚSICA — SIN RESOLVER
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-m-sica-sin-resolver)

        elif cmd == 0x79:
            # FF79 = flag de "seleccionando música/stage" (recv 0x57C7D0: lee 1 byte a +0x79b3).
            # El cliente lo manda en cada cambio de pestaña del picker; eco de 1 byte calla el spam.
            self.send_packet(0x79, bytes([data[0] if data else 0]))

        elif cmd == 0x1C:
            # EDIT_MUSIC: selección de canción del host. El picker de música ya funciona client-side.
            # Guardamos el song-id (formato [flag:1][songid:u16]) para mantenerlo en el FF13. Consumir.
            if len(data) >= 3 and data[0] == 0:
                self.room_song = struct.unpack("<H", data[1:3])[0]
                self.last_song = self.room_song   # ★ p/ contexto FF9E: en multiplayer la canción va por aquí (no por FF3A)
                # ★ reflejar la canción elegida en la sala global (para la lista de otros). El picker solo ofrece
                #   canciones que el cliente tiene -> sigue siendo válida (listable).
                _rid = getattr(self, "my_room_id", None)
                if _rid is not None and _rid in ROOMS:
                    ROOMS[_rid]["song_id"] = self.room_song
                    self.broadcast_roominfo(_rid)   # ★ el JOINER también ve la canción nueva (FF13 a toda la sala)
                    log(f"[Lobby] EDIT_MUSIC song={self.room_song} -> FF13 a la sala {_rid}")

        elif cmd == 0x1B:
            # EDIT_STAGE (handler recv cliente 0x5709B0 = casi vacío -> el stage NO se aplica por FF1B;
            # se DIBUJA del map_id del FF13). req=[stageid:u16]. FIX: actualizar el stage y RE-ENVIAR FF13.
            if len(data) >= 2:
                self.room_map = struct.unpack("<H", data[0:2])[0]
                _rid = getattr(self, "my_room_id", None)
                if _rid is not None and _rid in ROOMS:
                    ROOMS[_rid]["map_id"] = self.room_map
                    self.broadcast_roominfo(_rid)   # ★ el JOINER también ve el stage nuevo (FF13 a toda la sala)
                    log(f"[Lobby] EDIT_STAGE -> stage={self.room_map} (FF13 a la sala {_rid})")
                else:
                    self.send_roominfo()
                    log(f"[Lobby] EDIT_STAGE -> stage={self.room_map} (FF13 re-enviado a self)")

        elif cmd == 0x87:
            # SLOT open/close (req C→S [01][slot]). El redibujo de slots va por el BITMASK del FF13
            # (handler 0x5780E0, test en 0x578249). FIX: togglear el bit del slot y RE-ENVIAR FF13.
            # (experimento de polaridad: bit set=abierto vs cerrado -> verificar en vivo).
            if len(data) >= 2:
                slot = data[1] & 0x1F
                self.room_slot_mask ^= (1 << slot)
                _rid = getattr(self, "my_room_id", None)
                if _rid is not None and _rid in ROOMS:
                    ROOMS[_rid]["slot_mask"] = self.room_slot_mask
                    self.broadcast_roominfo(_rid)   # ★ el JOINER también ve el slot abrir/cerrar (FF13 con el bitmask a toda la sala)
                    log(f"[Lobby] SLOT toggle slot={slot} -> mask=0x{self.room_slot_mask:08X} (FF13 a la sala {_rid})")
                else:
                    self.send_roominfo()
                    log(f"[Lobby] SLOT toggle slot={slot} -> mask=0x{self.room_slot_mask:08X} (FF13 a self)")

        elif cmd == 0x17:
            # ★ TOGGLE READY (botón "listo" del invitado): C→S FF17 [ready:u8] (1 byte). El server debe BROADCASTEAR
            #   FF17 [slot][ready] a la sala para que TODOS vean el estado (recv 0x5786F0 -> occupant+0x163 -> cartel
            #   "LISTO"). El cliente IGNORA el FF17 de SU propio slot (pos==[me+0x40]); el que clickeó lo ve local.
            ready = (data[0] & 1) if data else 1
            slot = getattr(self, "host_slot", 2) & 0x7F
            rid = getattr(self, "my_room_id", None)
            room = ROOMS.get(rid) if rid else None
            targets = [CONNECTIONS.get(p["uid"]) for p in room["players"]] if room else [self]
            for conn in targets:
                if conn is not None:
                    conn.send_packet(0x17, bytes([slot, ready]))   # [slot del que cambió][ready]
            log(f"[Lobby] FF17 READY de uid={self.user_id} slot={slot} ready={ready} -> broadcast sala {rid}")

        elif cmd == 0x18:
            # CAMBIAR DE EQUIPO: el botón HUD manda C→S FF18 [team:u8] (02=azul/team2, 01=rojo/team1)
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-cambiar-de-equipo-el-bot-n-hud-manda-c-s-ff1)
            team = data[0] & 0xFF if data else 1
            cur = getattr(self, "host_slot", 2) & 0x7F
            side = (0, 1, 2) if team == 1 else (3, 4, 5)
            if cur in side:
                self.host_team = team
                log(f"[Lobby] FF18 team={team}: el host ya está en ese lado (slot {cur}) -> nada que mover")
            else:
                to = self._open_slot_on_side(side)
                if to is None:
                    self.send_system_chat("Ese equipo está lleno.")
                    log(f"[Lobby] FF18 team={team}: lado destino LLENO -> no se puede cambiar")
                else:
                    # ★ BROADCAST del FF37 [team][from][to] a TODA la sala (no solo al host): en cada receptor 0x53c4c0
                    #   MUEVE+recrea el widget del que se movió (from!=to) -> el JOINER ve al host cambiar de lado; en el
                    #   host es el move local. Antes: self.send_packet -> solo el host veía el cambio.
                    _rid = getattr(self, "my_room_id", None)
                    _room = ROOMS.get(_rid) if _rid else None
                    body = bytes([team & 0xFF, cur & 0x7F, to & 0x7F])
                    _targets = [CONNECTIONS.get(p["uid"]) for p in _room["players"]] if _room else [self]
                    for _c in _targets:
                        if _c is not None:
                            _c.send_packet(0x37, body)
                    self.host_slot = to
                    self.host_team = team
                    # ★ actualizar MI slot en el estado compartido -> FF0C/notify_room siguientes muestran al host en `to`
                    #   (me-detect coherente); si no, el host quedaría con r08 viejo. (Vale para cualquiera de los 6 slots.)
                    if _room is not None:
                        for p in _room.get("players", []):
                            if p["uid"] == self.user_id:
                                p["slot"] = to & 0x7F
                    log(f"[Lobby] FF18 team={team} ({'rojo' if team==1 else 'azul'}) -> FF37 MOVE slot {cur}->{to} a toda la sala {_rid}")

        elif cmd == 0x37:
            # FF37 directo del cliente (raro: normalmente pide por FF18). Lo re-broadcasteamos tal cual (move/aplica).
            # Formato [team][from][to]; recv cliente 0x5793A0 -> 0x53c4c0 (move si from!=to) + 0x52fb70 (pinta team).
            if len(data) >= 3:
                self.send_packet(0x37, bytes([data[0] & 0xFF, data[1] & 0x7F, data[2] & 0x7F]))
                log(f"[Lobby] FF37 directo eco team={data[0]} from={data[1]} to={data[2]}")

        elif cmd == 0x9B:
            # TRANSFERIR PROPIEDAD DE SALA (poder del anfitrión: clic en un joiner -> "hacer líder")
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-transferir-propiedad-de-sala-poder-del-anfit)
            _rid = getattr(self, "my_room_id", None)
            _room = ROOMS.get(_rid) if _rid else None
            if _room is None or not data:
                return
            if _room.get("host_uid") != self.user_id:
                log(f"[Lobby] FF9B transfer RECHAZADO: uid={self.user_id} no es el líder (sala {_rid})")
                return
            tslot = data[0] & 0x7F
            target = next((p for p in _room.get("players", []) if (p["slot"] & 0x7F) == tslot), None)
            if target is None or target["uid"] == self.user_id:
                log(f"[Lobby] FF9B transfer: slot {tslot} sin jugador / soy yo (sala {_rid})")
                return
            _room["host_uid"] = target["uid"]
            tconn = CONNECTIONS.get(target["uid"])
            if tconn is not None:
                tconn.join_host_uid = None
                tconn.send_packet(0x9B, bytes([tslot, 0x00, 0x00]))   # habilita UI de anfitrión en el nuevo líder
            self.broadcast_roominfo(_rid)   # FF13 host_pos=nuevo a todos -> el viejo cae de líder + todos ven al nuevo
            log(f"[Lobby] FF9B TRANSFERENCIA de líder uid={self.user_id} -> uid={target['uid']} (slot {tslot}) sala {_rid}")

        elif cmd == 0x20:
            # ★ KICK / EXPULSAR (poder del anfitrión: clic en un joiner -> "expulsar"). C→S FF20 [slot]. RE a9e1f0ec:
            #   recv 0x578ad0 IGNORA el cuerpo = es la auto-notif "te kickearon" (setea flag leave [0x76661c]+0x10c81 ->
            #   vuelve a la lobby). Receta: al KICKEADO -> FF20 -> se va; a los que quedan -> FF0E [nombre] + notify;
            #   liberar su slot server-side. Solo el LÍDER puede; no puede kickearse a sí mismo.
            _rid = getattr(self, "my_room_id", None)
            _room = ROOMS.get(_rid) if _rid else None
            if _room is None or not data:
                return
            if _room.get("host_uid") != self.user_id:
                log(f"[Lobby] FF20 kick RECHAZADO: uid={self.user_id} no es el líder (sala {_rid})")
                return
            tslot = data[0] & 0x7F
            target = next((p for p in _room.get("players", []) if (p["slot"] & 0x7F) == tslot), None)
            if target is None or target["uid"] == self.user_id:
                log(f"[Lobby] FF20 kick: slot {tslot} sin jugador / soy yo (sala {_rid})")
                return
            kicked_uid = target["uid"]
            kch = db_get_char(kicked_uid)
            kname = enc_text(kch["nickname"] if kch else f"P{kicked_uid}")[:0xFF]
            _room["players"] = [p for p in _room.get("players", []) if p["uid"] != kicked_uid]
            _room["cur"] = len(_room["players"])
            kconn = CONNECTIONS.get(kicked_uid)
            if kconn is not None:
                kconn.my_room_id = None
                kconn.send_packet(0x20, b"\x00")   # "te kickearon" -> el kickeado vuelve a la lobby
            ff0e_body = bytes([len(kname) & 0xFF]) + kname
            for p in _room["players"]:
                c = CONNECTIONS.get(p["uid"])
                if c is not None:
                    c.send_packet(0x0E, ff0e_body)   # remueve su avatar de los que quedan
            self.notify_room(_rid)
            log(f"[Lobby] FF20 KICK uid={self.user_id} expulsó a uid={kicked_uid} (slot {tslot}) sala {_rid} (quedan {_room['cur']})")

        elif cmd == 0x23:
            # SYNC DE MOVIMIENTO (FASE SYNC )
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-sync-de-movimiento-fase-sync)
            rid = getattr(self, "my_room_id", None)
            room = ROOMS.get(rid) if rid else None
            if room and len(data) >= 10:
                slot = getattr(self, "host_slot", 2) & 0x7F
                out = bytes([slot]) + data   # prepend slot -> [slot][kind][x][y][dir][b]
                sent = 0
                for p in room.get("players", []):
                    if p["uid"] == self.user_id:
                        continue   # excluir al emisor (su avatar ya se movió local)
                    conn = CONNECTIONS.get(p["uid"])
                    if conn is not None:
                        try:
                            conn.send_packet(0x23, out); sent += 1
                        except Exception as e:
                            log(f"[Lobby] FF23 relay fallo a uid={p['uid']}: {e}")
                if sent:
                    log(f"[Lobby] FF23 MOVE uid={self.user_id} slot={slot} -> relay a {sent} ocupante(s) ({out.hex()})")

        elif cmd == 0x4B:
            # CAMBIAR CONFIG/TIPO DE SALA: al dar OK en el popup, el cliente manda FF4B con el modo ELEGIDO en el
            # MISMO formato que CREATEROOM: [mode:u8][namelen:u8][name][pwdlen:u8][pass][trailer:u8]. Parsearlo y
            # aplicar (no ciclar). (Capturado en vivo 2026-06-30: `03 07 "testest" 00 00`.)
            if data:
                self.room_mode = data[0] & 0x03
                nlen = data[1] if len(data) > 1 else 0
                rname = data[2:2 + nlen].decode(CLIENT_CODEPAGE, "replace") if nlen else ""
                if rname:
                    self.room_name = rname
            # sincronizar el modo/nombre en el registro global de la sala (para la lista y los que se unan después)
            _rid = getattr(self, "my_room_id", None)
            if _rid is not None and _rid in ROOMS:
                ROOMS[_rid]["mode"] = self.room_mode
                ROOMS[_rid]["name"] = self.room_name
            # ★ AL HOST QUE CAMBIÓ: FF13 silencioso -> ve el modo nuevo SIN popup (el host NUNCA veía el cartel).
            self.send_roominfo()
            # RECOLOR DE SLOTS EN EL HOST (fix ): el FF13 silencioso setea el modo pero NO recolorea los...
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-recolor-de-slots-en-el-host-fix-el-ff13-sile)
            if self.room_mode & 0x02:
                self.send_team_colors()
            else:
                self.send_packet(0x4B, self.build_ff4b())
            # ★ A LOS OTROS OCUPANTES: FF4B -> además de aplicar el modo, QUITA el estado "listo" a los que estaban READY
            #   y les muestra el cartel de aviso (comportamiento del juego original, confirmado por el usuario 2026-07-01;
            #   solo el que estaba listo ve el cartel). + FF13 para refrescar su layout/modo. (Validar en 2 PCs / Etapa B.)
            room = ROOMS.get(_rid) if _rid else None
            if room:
                for p in room.get("players", []):
                    conn = CONNECTIONS.get(p["uid"])
                    if conn is not None and conn is not self:
                        conn.room_mode = self.room_mode
                        conn.send_packet(0x4B, conn.build_ff4b())   # aviso + un-ready a los READY
                        conn.send_roominfo()                        # refresca su modo/slots (FF13 silencioso)
            # NO mandar FFBD[00] acá: dispara 0x539F60 que RE-ABRE el popup tras el OK (bug observado).
            log(f"[Lobby] FF4B cambio config EN VIVO -> room_mode={self.room_mode} name={getattr(self,'room_name','')!r} (host silencioso; otros con aviso/un-ready)")

        elif cmd == 0xBD:
            # FFBD vacío = el cliente PIDE abrir el popup de config. Responder FFBD[00] (0x539F60) -> ABRE el popup.
            # (El re-abrir tras OK era porque ANTES mandábamos este FFBD[00] también en el handler de FF4B; ya no.)
            self.send_packet(0xBD, b"\x00")
            log("[Lobby] FFBD -> abre popup de config")

        elif cmd == 0x45:
            # COUPLE (lista de pareja). El cliente POLETEA esto cada pocos segundos. Respondemos según
            # la pareja real en DB: soltero -> 00 00 ; casado -> 1 entry + trailing (Mi pareja se actualiza solo).
            self.send_packet(0x45, build_ff45_couple_for(self.user_id))

        elif cmd == 0x49:
            # FF49 — el cliente lo PIDE (len=0) al entrar al canal y NUNCA se lo contestábamos
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-ff49-el-cliente-lo-pide-len-0-al-entrar-al-c)
            if FF49_REPLY:
                body, _worn = self.build_ff49_roster()
                self.send_packet(0x49, body)
                log(f"[Lobby] FF49 (pedido por el cliente) -> roster, select=char (worn={_worn})")

        elif cmd == 0x3D:
            # FF3D = el cliente AVISA que deja el canal para SWITCHEAR (data=01), y MANTIENE el socket
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-ff3d-el-cliente-avisa-que-deja-el-canal-para)
            self.handle_switch_request(data)

        # ─────────── GAMEPLAY SINGLE-PLAY ("Jugar Solo") ───────────
        # Flujo REAL (server coreano server1_.py, verificado): el singleplay son SOLO 2 pasos:
        #   1) FF3A SINGLEPLAY -> eco [type][01][magic=100000][id]
        #   2) FF57 GET_NOTE_DATA -> sirve las notas. El cliente arranca la carrera solo.
        # NO hay respuesta a FFC8 ni FF24 en singleplay (eso es flujo de SALA).
        elif cmd == 0x3A:
            # SINGLEPLAY: payload [type:1][songid:2][...]. type==2 -> transición a la escena de carga
            # (0x583420->0x43B9B0). type==3 -> VUELTA a la sala tras jugar (el cliente reenvía el song-id
            # que jugó). HIPÓTESIS (cache de selección): eco-devolver ese song-id en el campo id ->
            # el picker re-selecciona la última canción al volver (en vez de resetear). Probar en vivo.
            stype = data[0] if data else 2
            song = struct.unpack("<H", data[1:3])[0] if len(data) >= 3 else 0
            if song:
                self.last_song = song   # cache server-side de la última canción del jugador
            echo_id = song if stype == 3 else 0
            resp = bytes([stype, 0x01]) + struct.pack("<I", 100000) + struct.pack("<H", echo_id)
            self.send_packet(0x3A, resp)
            log(f"[Lobby] SINGLEPLAY type={stype} song={song} -> eco FF3A (echo_id={echo_id})")

        elif cmd == 0x24:
            # START de partida MULTI (botón "empezar")
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-start-de-partida-multi-bot-n-empezar)
            _rid = getattr(self, "my_room_id", None)
            _room = ROOMS.get(_rid) if _rid else None
            magic = random.getrandbits(32)
            mp = getattr(self, "room_map", 8); sg = getattr(self, "room_song", 102)
            if _room is not None:
                _room["game_magic"] = magic
                _room["loaded"] = set()            # uids que ya cargaron (ghosts .npc auto-cuentan: no están en players)
                _room["finished_users"] = set()    # ★ reset: quién LLEGÓ a la meta (rank 1..6)
                _room["reported"] = set()          # ★ reset: quién mandó FF28 (llegada o fallo -> estado final; para el relay FF4E y el cartel)
                _room["scores"] = {}               # ★ reset scores del cartel
                _room["countdown_started"] = False; _room["result_done"] = False   # ★ reset del flujo de fin (FF27/cartel)
                _room["game_started"] = False
                _room["result_players"] = None
                _room["_late_loaders"] = set()          # ★ tarea 2: los que no cargaron (los llena el timeout)
                _room["_defer_host_notify"] = False     # ★ tarea 3: liderazgo diferido si el líder se va en carrera
                _room.pop("_pending_host_ff9b", None)
                mp = _room.get("map_id", mp); sg = _room.get("song_id", sg)
                targets = [CONNECTIONS.get(p["uid"]) for p in _room.get("players", [])]
            else:
                targets = [self]
            body = struct.pack("<IHH", magic, mp & 0xFFFF, sg & 0xFFFF)
            sent = 0
            for c in targets:
                if c is not None:
                    c.game_magic = magic; c.game_started = False
                    c.countdown_started = False; c.result_done = False   # estado fin-de-carrera (cartel) por-conn
                    c.send_packet(0x25, body); sent += 1
            log(f"[Lobby] FF24 START -> FF25 LOADGAME magic=0x{magic:08X} map={mp} song={sg} -> {sent} ocupante(s) sala {_rid}")
            # ★★ TAREA 2 — armar el TIMEOUT DE CARGA: si a los LOAD_TIMEOUT seg. no cargaron todos, forzar el arranque.
            if _room is not None:
                asyncio.get_event_loop().call_later(
                    LOAD_TIMEOUT, lambda r=_rid, m=magic: self._force_start_if_stuck(r, m))

        elif cmd == 0x25:
            # ★ DO_LOADGAME C→S: el cliente avisa "cargué la canción". Cuando TODOS los ocupantes REALES cargaron,
            #   el server manda S→C FF24 (GO). El FANTASMA nunca manda FF25 -> se cuenta como ya-cargado (igual que la
            #   guía: players sin conexión = loaded). Con solo el host real, basta su FF25 para arrancar.
            #   FF24 recv 0x578CC0 lee [count:u8]+count×[idx][16f]; count=0 -> salta directo al arranque de carrera.
            # ★ MULTI: acumular quién cargó a nivel de SALA. Cuando TODOS los ocupantes REALES (room["players"], los
            #   ghosts .npc no cuentan porque no tienen conexión) cargaron -> FF24 GO a TODOS a la vez. Antes: GO a self
            #   apenas el host cargaba -> el host arrancaba sin esperar al joiner.
            _rid = getattr(self, "my_room_id", None)
            _room = ROOMS.get(_rid) if _rid else None
            if _room is not None:
                loaded = _room.setdefault("loaded", set())
                loaded.add(self.user_id)
                real_uids = {p["uid"] for p in _room.get("players", [])}
                if _room.get("game_started"):
                    # ★ TAREA 2: la carrera YA arrancó (probablemente forzada por el timeout de carga). Este cargó TARDE
                    #   -> darle SU GO individual y sacarlo de late-loaders (ya reportará FF28 normalmente).
                    (_room.get("_late_loaders") or set()).discard(self.user_id)
                    self.game_started = True; self._game_start_ts = time.monotonic()
                    self.send_packet(0x24, b"\x00")
                    log(f"[Lobby] FF25 late-load uid={self.user_id} tras arranque forzado -> GO individual (sala {_rid})")
                elif real_uids and real_uids <= loaded:
                    _room["game_started"] = True   # ★ sala "jugando" -> los que miran la lista (FF6E) lo ven (bit1)
                    go = 0
                    for p in _room.get("players", []):
                        c = CONNECTIONS.get(p["uid"])
                        if c is not None:
                            c.game_started = True
                            c._game_start_ts = time.monotonic()   # ★ p/ contexto FF9E crashdump (t+ en juego)
                            c.send_packet(0x24, b"\x00")   # GO (count=0: sin init de boost por-jugador; defaults)
                            go += 1
                    log(f"[Lobby] FF25 TODOS cargaron {sorted(loaded)} -> FF24 GO a {go} en sala {_rid} (¡arranca la carrera!)")
                    self._spawn_npc_replays(_rid)   # ★ FASE 2: los NPC empiezan a correr (replay FF4E)
                    # fallback de settlement al tope de tiempo (por si la carrera queda colgada)
                    asyncio.get_event_loop().call_later(
                        RACE_MAX_SEC, lambda r=_rid, m=_room.get("game_magic"): self._room_settle_fallback(r, m))
                else:
                    log(f"[Lobby] FF25 DO_LOADGAME uid={self.user_id} cargó ({len(loaded)}/{len(real_uids)} en sala {_rid}); esperando al resto")
            elif not getattr(self, "game_started", False):
                # sin sala registrada (SALA TEST con fantasma / single): GO a self, el fantasma auto-cuenta
                self.send_packet(0x24, b"\x00")
                self.game_started = True
                self._game_start_ts = time.monotonic()   # ★ p/ contexto FF9E crashdump (t+ en juego)
                log("[Lobby] FF25 DO_LOADGAME (sin sala; fantasma auto) -> FF24 GO")

        elif cmd == 0x57:
            # GET_NOTE_DATA: el cliente PIDE las notas de la canción [songid:2] (recv 0x57A9D0).
            # Responder [result:1][songid:2][ziplen:2][zlib] (notas de 6 bytes). result=0 si no hay.
            song_id = struct.unpack("<H", data[0:2])[0] if len(data) >= 2 else 0
            comp = load_notes(song_id)
            if comp is None:
                self.send_packet(0x57, bytes([0x00]))
                log(f"[Lobby] GET_NOTE_DATA song={song_id} -> SIN NOTAS (result=0)")
            else:
                resp = bytes([0x01]) + struct.pack("<H", song_id) + struct.pack("<H", len(comp)) + comp
                self.send_packet(0x57, resp)
                log(f"[Lobby] GET_NOTE_DATA song={song_id} -> {len(comp)}b de notas servidas ✓")
                # ⚠ NO mandar FF24 en singleplay. El server coreano de referencia (server1_.py,
                # handler 0x57 línea 2432) sirve las notas y NO manda nada más: el cliente arranca
                # la carrera por su cuenta. El FF24 solo existe en el flujo de SALA (0x24->FF25,
                # 0x25->FF24 cuando todos cargaron). Mandarlo en single = crash.

        elif cmd == 0xC8:
            # FFC8: en NUESTRO cliente, responder el FFC8 (16 bytes = pos de char, 4 floats) es lo que
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-ffc8-en-nuestro-cliente-responder-el-ffc8-16)
            BOOST_DUR_FRAMES = 380   # 403→~65-66 combos; bajo a 380 (~16 frames/combo) p/ terminar en ~64 como el original
            # === FIX FINAL (verificado en vivo): f2(bonus precisión / +0x2dc)=0 = PRECISIÓN REAL del su...
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-fix-final-verificado-en-vivo-f2-bonus-precis)
            self.send_packet(0xC8, struct.pack("<f I f f", 1.08, BOOST_DUR_FRAMES, 0.0, 1.08))
            self.last_boost_ts = time.monotonic()   # ★ p/ contexto FF9E crashdump (Δ desde el último boost)
            log(f"[Lobby] FFC8 -> boost: current=1.08 dur={BOOST_DUR_FRAMES} steps=0(FIX precisión) target=1.08")

        elif cmd == 0x63:
            # FF63 = resultado/estado de carrera [status:1][int×3] (recv 0x5850D0). Responder solo en
            # status==1 (fin): [00][flags:4][exp:4][coin:4]. Otros status = en curso, no responder.
            status = data[0] if data else 0
            if status == 1:
                # ★ PRÁCTICA / SINGLEPLAYER = 0 exp, 0 rcoin (la práctica NO premia; pedido del usuario). El premio
                #   va EXCLUSIVAMENTE por el cartel multijugador (FF28 en sala). Acá solo ack con ceros.
                self.send_packet(0x63, bytes([0x00]) + struct.pack("<III", 0, 0, 0))
                log("[Lobby] FF63 fin (status=1) -> ack SIN premio (práctica/single = 0; el premio va por FF28 multi)")

        elif cmd == 0x28:
            # GAMEOVER / FIN DE CARRERA — MODELO EVENT-DRIVEN (, RE aa70a7fe verificado + captura viva 2...
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-gameover-fin-de-carrera-modelo-event-driven-)
            score = ({"frame": struct.unpack("<I", data[0:4])[0], "combo": struct.unpack("<H", data[4:6])[0],
                      "pd": data[6], "star": data[7], "coin": struct.unpack("<H", data[8:10])[0]}
                     if len(data) >= 10 else {"frame": 0, "combo": 0, "pd": 0, "star": 0, "coin": 0})
            self.last_score = score
            # ★ DIAGNÓSTICO: log del BODY CRUDO del FF28 entrante (para verificar el formato/slots reales vs nuestro parse
            #   [frame:u32][combo:u16][pd:u8][star:u8][coin:u16]+hash). Cuando aparezca un valor basura (combo 42600/65500)
            #   se decodifica el raw para ver si el server lee el SLOT equivocado (hipótesis "traducción saltada").
            log(f"[FF28-raw] uid={self.user_id} len={len(data)} raw={data.hex()} | parse frame={score['frame']} combo={score['combo']} pd={score['pd']} star={score['star']} coin={score['coin']}")
            _rid = getattr(self, "my_room_id", None)
            _room = ROOMS.get(_rid) if _rid else None
            # ★ PLAUSIBILIDAD (anti-basura / anti-cheat Capa 1): el cliente puede reportar un COMBO INFLADO (visto
            #   combo=42600, p.ej. con impulsor) -> cartel + exp basura (exp=pd*10+combo+star). El combo MÁXIMO real =
            #   nº de notas de la canción; clampeamos a eso, y precisión/estrellas a ≤100 (son %). Un jugador legítimo
            #   nunca supera esas cotas -> no rompe nada. Se aplica ANTES de guardar -> cartel y recompensa quedan sanos.
            _song_id = (_room.get("song_id") if _room else None) or getattr(self, "room_song", None)
            _maxcombo = song_note_count(_song_id) or 2000     # notas de la canción (tope real de combo); fallback generoso
            if score["combo"] > _maxcombo:
                log(f"[Lobby] FF28 combo IMPLAUSIBLE uid={self.user_id} combo={score['combo']} -> clamp {_maxcombo} (song {_song_id})")
                score["combo"] = _maxcombo
            if score["pd"] > 100:
                score["pd"] = 100
            if score["star"] > 100:
                score["star"] = 100
            if _room is not None:
                if _room.get("result_done") or not _room.get("game_started"):
                    log(f"[Lobby] FF28 ignorado (settleado/no jugando) uid={self.user_id}")
                else:
                    # FALLO = judgment EN CERO (combo=pd=star=0 -> se congeló en countdown-0). Llegada real = judgment > 0.
                    is_fail = (score["combo"] == 0 and score["pd"] == 0 and score["star"] == 0)
                    _room.setdefault("reported", set()).add(self.user_id)   # ya reportó (llegó o falló -> estado final)
                    _room.setdefault("scores", {})[self.user_id] = score
                    if not is_fail:
                        _room.setdefault("finished_users", set()).add(self.user_id)   # solo los que LLEGARON -> rank 1..6
                    # ★ expected = los que DEBEN reportar (snapshot/players MENOS los que nunca cargaron). Así un
                    #   late-loader o un jugador que se fue en carrera NO bloquean el cartel.
                    expected = self._race_expected_uids(_room)
                    log(f"[Lobby] FF28 {'FALLO' if is_fail else 'META'} uid={self.user_id} frame={score['frame']} combo={score['combo']} "
                        f"(reportaron {len(_room['reported'])}/{len(expected) or len(_room.get('players', []))})")
                    # PRIMER finalista REAL -> dispara el contador 5s (FF27) para todos + fallback de settlement
                    if not is_fail and not _room.get("countdown_started"):
                        _room["countdown_started"] = True
                        _room["result_done"] = False
                        _room["result_players"] = list(_room.get("players", []))   # snapshot p/ el cartel
                        for p in _room["result_players"]:
                            c = CONNECTIONS.get(p["uid"])
                            if c is not None:
                                c.send_packet(0x27, b"")   # FF27 TIMEOVER -> contador 5→1 en cada cliente no-finalizado
                        # fallback = fin-de-cuenta estimado + margen chico (NO 10s; ese cae a mitad de cuenta de los lentos).
                        _fb = COUNTDOWN_EST + CARTEL_MARGIN
                        asyncio.get_event_loop().call_later(_fb, lambda rid=_rid: self._room_game_result(rid))
                        log(f"[Lobby] FF28 PRIMER finalista uid={self.user_id} -> FF27 TIMEOVER a la sala {_rid}; fallback {_fb:.1f}s")
                        expected = self._race_expected_uids(_room)   # recomputar con el snapshot result_players
                    # ★ CARTEL A TODOS cuando TODOS los esperados reportaron (llegaron o fallaron = ya congelados en su
                    #   estado final). Como TODOS están frozen, es seguro mandarlo a la vez (nadie a mitad de cuenta).
                    #   El margen/paciencia = el fallback de arriba (cuenta+margen): si un rezagado NO reportó al cierre,
                    #   el fallback manda el cartel a los que sí, y al rezagado se le da DIFERIDO cuando reporte.
                    if not _room.get("result_done") and expected and expected <= _room["reported"]:
                        if not _room.get("countdown_started"):
                            # caso "todos fallaron": nadie cruzó -> nadie armó el contador; ya están congelados local
                            _room["countdown_started"] = True
                            _room["result_players"] = list(_room.get("players", []))
                            for p in _room["result_players"]:
                                c = CONNECTIONS.get(p["uid"])
                                if c is not None:
                                    c.send_packet(0x27, b"")
                        asyncio.get_event_loop().call_later(0.3, lambda rid=_rid: self._room_game_result(rid))
                        log(f"[Lobby] FF28 TODOS reportaron {sorted(_room['reported'])} -> cartel a TODOS en 0.3s")
            elif not getattr(self, "countdown_started", False):
                # sin sala registrada (SALA TEST con fantasma / single): flujo viejo a self
                self.countdown_started = True
                self.result_done = False
                self.send_packet(0x27, b"")               # FF27 TIMEOVER -> countdown del cartel
                asyncio.get_event_loop().call_later(5.0, self.send_game_result)
                log("[Lobby] FF28 (sin sala) -> FF27 TIMEOVER; resultado (FF28) en 5s")

        elif cmd == 0x08:
            # CHAT: req C→S [chatType:1][texto]. Echo S→C (handler cliente 0x5778B0)
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-chat-req-c-s-chattype-1-texto-echo-s-c-handl)
            msglen = data[0] if data else 0
            text = data[1:1 + msglen][:0xFF]
            # ── COMANDOS DE SALA por chat (server-side). `.npc` = inyecta/quita un FANTASMA (futuro NPC)
            #    sin 2ª PC/VM: activa el flag y rebroadcast FF0C COUNT=2 (host + fantasma). Ver
            #    docs/MULTIPLAYER_FASE2_GHOST_USER.md. NO se hace echo del comando como chat normal.
            cmd_txt = text.decode(CLIENT_CODEPAGE, "replace").strip().lower()
            # ★ COMANDO ADMIN `.rate` — cambia los rates de recompensa EN VIVO (sin reiniciar). Gateado por ADMIN_UIDS
            #   (NO por ENABLE_DEV) -> funciona también en el VPS, pero solo para el/los uid admin.
            if self.user_id in ADMIN_UIDS and cmd_txt.startswith(".rate"):
                self.apply_rate_command(cmd_txt)
                return
            # ★ COMANDO DE TEST `.nivel N` / `.exp N` — cambia la EXP **EN VIVO**, sin relogin.
            #   El FF92 fija la exp de forma ABSOLUTA (player+0x2D4) y el cliente recalcula el nivel con
            #   su tabla `0x6E90B8`. También se guarda en la DB para que el PRÓXIMO login (o el re-login
            #   de cambio de canal) traiga la misma banda -> el matcher te manda al canal correcto.
            #   ⚠ La BANDA del canal se decide en el LOGIN: para que te mueva de canal hay que cambiar
            #     de canal después (eso re-loguea solo). El nivel en pantalla sí cambia al instante.
            #   Existe porque el muro de licencia impide probar canales de otra banda sin subir de nivel.
            if (ENABLE_DEV or self.user_id in ADMIN_UIDS) and (cmd_txt.startswith(".nivel")
                                                              or cmd_txt.startswith(".exp")):
                bits = cmd_txt.split()
                if len(bits) < 2 or not bits[1].lstrip("-").isdigit():
                    self.send_system_chat("uso: .nivel 25   |   .exp 235220")
                    return
                n = int(bits[1])
                if cmd_txt.startswith(".nivel"):
                    if not 1 <= n <= len(LEVEL_TABLE):
                        self.send_system_chat(f"nivel fuera de rango (1..{len(LEVEL_TABLE)})")
                        return
                    nuevo = LEVEL_TABLE[n - 1] if n >= 2 else 0   # exp mínima de ese nivel
                else:
                    nuevo = max(0, n)
                c = db(); c.execute("UPDATE chars SET exp=? WHERE uid=?", (nuevo, self.user_id))
                c.commit(); c.close()
                self.send_packet(0x92, bytes([0]) + struct.pack("<I", nuevo & 0xFFFFFFFF))
                _lvl = level_from_exp(nuevo)
                _banda = NOMBRE_LICENCIA.get(licencia_de_nivel(_lvl), "?")
                self.send_system_chat(f"exp={nuevo} -> nivel {_lvl} (banda {_banda}). "
                                      f"Cambiá de canal para que el matcher te reubique.")
                log(f"[Lobby] .nivel uid={self.user_id} -> exp={nuevo} nivel={_lvl} banda={_banda}")
                return
            # ★ COMANDO ADMIN `.anuncio`/`.confeti` — ANUNCIO GLOBAL (FF40 sub 0x0D = 확성기/horn). Prod-safe (ADMIN_UIDS).
            #   `.anuncio <texto>` = confeti normal (horn=1); `.confeti <texto>` = grande (horn=2). Va a TODOS los conectados.
            if self.user_id in ADMIN_UIDS and (cmd_txt.startswith(".anuncio") or cmd_txt.startswith(".confeti")):
                raw = text.decode(CLIENT_CODEPAGE, "replace").strip()   # texto ORIGINAL (sin lower)
                bits = raw.split(None, 1)
                msg = bits[1].strip() if len(bits) > 1 else ""
                if not msg:
                    self.send_system_chat("uso: .anuncio <texto>   (.confeti <texto> = horn grande)")
                    return
                htype = 2 if cmd_txt.startswith(".confeti") else 1
                ch = db_get_char(self.user_id)
                sender = (ch["nickname"] if ch else None) or "Aviso"
                n = self.broadcast_announce(msg, sender=sender, horn_type=htype)
                self.send_system_chat(f"anuncio enviado a {n} jugador(es) (horn={htype})")
                return
            # ★ COMANDO ADMIN `.horn <valor> <texto>` — prueba CUALQUIER horn_type para elegir estilo (popup vs megáfono).
            #   horns conocidos: 1=celeste(usuario) 2=rosa(usuario) 0xb5=megáfono sistema 0xb7=megáfono grande.
            if self.user_id in ADMIN_UIDS and cmd_txt.startswith(".horn"):
                raw = text.decode(CLIENT_CODEPAGE, "replace").strip()
                bits = raw.split(None, 2)
                if len(bits) < 3:
                    self.send_system_chat("uso: .horn <valor> <texto>   (ej: .horn 0xb5 hola)")
                    return
                try:
                    hv = int(bits[1], 0)
                except ValueError:
                    self.send_system_chat("valor de horn inválido (ej: 1, 2, 0xb5, 0xb7)")
                    return
                ch = db_get_char(self.user_id)
                sender = (ch["nickname"] if ch else None) or "Aviso"
                n = self.broadcast_announce(bits[2], sender=sender, horn_type=hv)
                self.send_system_chat(f".horn 0x{hv:X} ({hv}) enviado a {n} jugador(es)")
                return
            # ★ COMANDO ADMIN `.gm1/.gm2/.gm3 <texto>` — ANUNCIO DE GM con el banner gm_megaphone (verde/amarillo/rojo).
            #   itemcode 7001/7002/7003 = tipos custom en notice.xml -> imgindex 22/23/24 (los 3 recortes de gm_megaphone.tga).
            if self.user_id in ADMIN_UIDS and (cmd_txt.startswith(".gm1") or cmd_txt.startswith(".gm2") or cmd_txt.startswith(".gm3")):
                gm_map = {".gm1": 7001, ".gm2": 7002, ".gm3": 7003}
                key = cmd_txt.split()[0]
                item = gm_map.get(key)
                if item is None:
                    self.send_system_chat("uso: .gm1/.gm2/.gm3 <texto>  (verde/amarillo/rojo)")
                    return
                raw = text.decode(CLIENT_CODEPAGE, "replace").strip()
                bits = raw.split(None, 1)
                body = bits[1].strip() if len(bits) > 1 else ""
                if not body:
                    self.send_system_chat(f"uso: {key} <texto>")
                    return
                _gch = db_get_char(self.user_id)
                _gm_nick = (_gch["nickname"] if _gch else None) or "GM"
                # ★ bajar SENDER + mensaje al área libre del banner GM (debajo del logo): los \n van en el
                #   SENDER; el mensaje se dibuja pegado abajo, así baja TODO el bloque. Ajustar GM_TEXT_LEADING.
                _gm_sender = ("\n" * GM_SENDER_NL) + (" " * GM_NAME_INDENT) + _gm_nick
                _gm_body   = ("\n" * GM_MSG_NL) + body
                n = self.broadcast_announce(_gm_body, sender=_gm_sender, horn_type=item)
                self.send_system_chat(f"{key} (banner GM, de {_gm_nick}) enviado a {n} jugador(es)")
                return
            # ★ TEST TEMPORAL `.ferr <n>` — dispara FF40 0x01 action=n al que lo escribe, para descubrir
            #   qué código de error muestra "usuario incorrecto" (los str# son del cliente). Probar 3/4/8/9/otro.
            if self.user_id in ADMIN_UIDS and cmd_txt.startswith(".ferr"):
                parts = cmd_txt.split()
                try:
                    n = int(parts[1], 0) if len(parts) > 1 else 4
                except ValueError:
                    n = 4
                if n == 1:
                    self.send_system_chat(".ferr 1 = alta (no es error), probá 3/4/8/9/88")
                    return
                self.send_packet(0x40, self._ff40_result(n, uid=self.user_id, nick="TESTNICK"))
                self.send_system_chat(f".ferr -> 0x01 action={n} — leé el popup (buscamos 'usuario incorrecto')")
                log(f"[Lobby] DEBUG .ferr -> 0x01 action={n}")
                return
            # ★ TEST TEMPORAL `.couple <n>` — dispara FF40 sub-0x0A sub2=n (familia couple) al que lo escribe,
            #   para descubrir en vivo qué muestra cada opcode (buscamos "PROPUESTA RECIBIDA" y "casados").
            if self.user_id in ADMIN_UIDS and cmd_txt.startswith(".couple"):
                parts = cmd_txt.split()
                try:
                    n = int(parts[1], 0) if len(parts) > 1 else 1
                except ValueError:
                    n = 1
                body = self._ff40_couple(n, uid=self.user_id, name="AMORTEST")
                self.send_packet(0x40, body)
                self.send_system_chat(f".couple -> FF40 sub0x0A sub2={n} — mirá popup/panel (Mi pareja)")
                log(f"[Lobby] DEBUG .couple -> sub2={n} body={body.hex()}")
                return
            # ★ COMANDOS ADMIN prod (.host/.who/.dc/.kick/.ban/.unban) gateados por ADMIN_UIDS (NO por DEV).
            #   apply_admin_command devuelve False si el "." no es de su set -> cae a los checks siguientes.
            if self.user_id in ADMIN_UIDS and cmd_txt.startswith(".") and self.apply_admin_command(text):
                return
            # ★ COMANDOS DE CHAT DE TEST (`.err`, `.npc`) — SOLO en modo DEV (R2_DEV=1). En producción (VPS) NO se procesan:
            #   el texto que empiece con "." se manda como chat normal. Así no hay poderes de debug en el server público.
            if ENABLE_DEV and cmd_txt.startswith(".err"):
                parts = cmd_txt.split()
                try:
                    n = int(parts[1], 0) if len(parts) > 1 else 8
                except ValueError:
                    n = 8
                self.send_packet(0x11, bytes([n & 0xFF, 0]))
                self.send_system_chat(f".err -> FF11 byte0={n} (0x{n & 0xFF:02X}) — mirá el popup")
                log(f"[Lobby] DEBUG .err -> FF11 byte0={n}")
                return
            if ENABLE_DEV and cmd_txt.startswith(".npc"):
                if not hasattr(self, "ghost_slots") or self.ghost_slots is None:
                    self.ghost_slots = []
                if cmd_txt in (".npc off", ".npc 0", ".npc quitar"):
                    self.ghost_slots = []
                    note = "NPCs QUITADOS"
                elif cmd_txt in (".npc full", ".npc all", ".npc todos"):
                    self.ghost_slots = self.all_free_slots()        # llena rojo (0,1) y azul (3,4,5)
                    note = f"NPCs en TODOS los slots libres {self.ghost_slots}"
                else:
                    s = self.next_free_slot()
                    if s is None:
                        note = "todos los slots ya están llenos"
                    else:
                        self.ghost_slots = sorted(set(self.ghost_slots) | {s})
                        note = f"NPC agregado en slot {s} (total {len(self.ghost_slots)})"
                # ★ reflejar la ocupación de NPCs en el estado COMPARTIDO (ROOMS) -> la LISTA (FF6E `cur`) y el bloqueo de
                #   JOIN (next_room_slot) los cuentan. Sin esto la sala con NPCs figura 1/6 y deja entrar. (Versión rápida;
                #   la promoción de NPCs a ocupantes reales visibles para todos va con la fase de SYNC.)
                _rid = getattr(self, "my_room_id", None)
                if _rid is not None and _rid in ROOMS:
                    ROOMS[_rid]["ghost_slots"] = list(self.ghost_slots)
                    ROOMS[_rid]["cur"] = min(6, len(ROOMS[_rid].get("players", [])) + len(self.ghost_slots))
                # ★ Rebroadcast a TODA LA SALA (no solo al host): cada conn arma SU FF0C (que ahora incluye los ghosts
                #   del estado compartido ROOMS[rid]["ghost_slots"]) -> el JOINER también ve los NPCs. + colores de
                #   equipo (FF37, sin popup) + READY de cada ghost (FF17 [slot][1]). ORDEN: FF0C (crea el ocupante)
                #   ANTES del FF17 (0x5323E0 lo busca por slot en cRoom+0x2c). NO FF4B (era popup). Antes: solo self ->
                #   los NPC salían solo en el host. READY de cada NPC -> ocupante+0x163=1 -> cartel "LISTO".
                _room = ROOMS.get(_rid) if _rid else None
                _targets = [CONNECTIONS.get(p["uid"]) for p in _room["players"]] if _room else [self]
                for _c in _targets:
                    if _c is None:
                        continue
                    _c.send_packet(0x0C, _c.build_ff0c())
                    # ★ 2026-07-25: el FF0C ya trae la cara (r25) -> no hace falta el FF15 de "caras presentes",
                    #   pero sí sembrar el estado para que el diff detecte los cambios que vengan después.
                    _c.seed_face_state(_rid)
                    _c.send_team_colors()
                    for gs in self.ghost_slots:
                        _c.send_packet(0x17, bytes([gs & 0xFF, 0x01]))
                self.send_system_chat(f".npc -> {note}")
                log(f"[Lobby] CMD .npc -> ghost_slots={self.ghost_slots} ({note})")
                return
            chat_type = 0                        # chatType/canal NORMAL fijo (no el largo del mensaje)
            ch = db_get_char(self.user_id)
            nick = enc_text(ch["nickname"] if ch else "Player")[:0xFF]
            speaker_slot = getattr(self, "host_slot", 2) & 0x7F   # slot REAL del emisor (no 2 fijo)
            resp = bytearray()
            resp.append(len(nick)); resp += nick
            resp.append(len(text) & 0xFF); resp += text
            resp.append(speaker_slot)            # speakerId = slot del emisor
            resp.append(chat_type & 0xFF)        # canal/color de chat
            body = bytes(resp)
            # ★ SYNC DE CHAT (fase sync 2026-07-01): si estoy EN UNA SALA, BROADCAST el FF08 a TODOS los ocupantes
            #   (incluido yo -> el emisor ve su propio msg por el eco; el cliente 0x5778B0 no dibuja local, espera el eco).
            #   Fuera de sala (lobby/menú) = solo eco a mí. BUG previo: solo self.send_packet -> el chat NO llegaba al otro PC.
            _rid = getattr(self, "my_room_id", None)
            room = ROOMS.get(_rid) if _rid else None
            if room:
                sent = 0
                for p in room.get("players", []):
                    conn = CONNECTIONS.get(p["uid"])
                    if conn is not None:
                        try:
                            conn.send_packet(0x08, body); sent += 1
                        except Exception as e:
                            log(f"[Lobby] CHAT broadcast fallo a uid={p['uid']}: {e}")
                log(f"[Lobby] CHAT sala {_rid} de uid={self.user_id} slot={speaker_slot} msg={text!r} -> broadcast a {sent}")
            else:
                self.send_packet(0x08, body)
                log(f"[Lobby] CHAT echo (lobby) nick={(ch['nickname'] if ch else '?')!r} msg={text!r}")

        elif cmd == 0x6E:
            # El cliente pide la lista de salas (ROOMLIST2). ★ PAGINACIÓN (2026-07-01): el request es `[02][page:u16 LE][1F 7F]`
            #   -> la PÁGINA pedida (0-indexada) está en los bytes [1:3]. Verificado en vivo: al clickear la pestaña 2 el
            #   cliente re-pide con page=1 (`02 01 00 1F 7F`). Paginación SERVER-SIDE: devolvemos solo las salas de esa página.
            page = struct.unpack_from("<H", data, 1)[0] if data and len(data) >= 3 else 0
            self.send_roomlist(page)

        elif cmd == 0x77:
            # USERINFO ("mi info") -> responde el record del jugador (cifrado FF55).
            self.handle_userinfo(data)

        elif cmd == 0x78:
            # ★ FF78 = ver info de OTRO jugador (clic sobre un ocupante de la sala). Formato IDÉNTICO a FF77:
            #   [nameLen:u8][nick]. Capturado en vivo: ruii clickeó "SolUser" -> `07 'SolUser'`; sol clickeó
            #   "anonimo03" -> `09 'anonimo03'`. OJO: FF78 SALIENTE ya = ENABLE-SONG del picker (recv cliente
            #   0x587E90), así que la RESPUESTA del panel debe ir por FF77 (recv 0x587B90 = parser del panel).
            self.handle_userinfo_other(data)

        elif cmd in (0x8C, 0x97, 0x107):
            # BASELINE (confirmado contra server 2013, línea 891): FF8C/FF97/FF107 son TELEMETRÍA
            # que el cliente SUBE y el server CONSUME sin responder. (FF97-test y keepalive revertidos:
            # no salvaron la conexión. El crash a ~7s se investiga ahora con dbg_harness.py.)
            pass  # no-op: recibido y consumido

        elif cmd == 0x54:
            # FF54 = el cliente PIDE la lista de entrada del lobby (recv handler cliente 0x57A910)
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-ff54-el-cliente-pide-la-lista-de-entrada-del)
            self.send_packet(0x54, b"\x00")
            log("[Lobby] FF54 (lista de entrada) -> respondido VACÍO (count=0) [fix crash bienvenida]")

        elif cmd == 0x2A:
            # BUY_DRESS (compra con金화/coin): [byte0][u32 raw=itemID+dur][str extra opcional].
            # Decodificar, agregar al inventario persistente, responder OK -> el item queda comprado.
            self.handle_buy_dress(data)

        elif cmd == 0x1A:
            # EDIT_AVATAR (equipar/quitar prenda): [action:1][item_id:u32].
            # action 0=quitar 1=equipar 2=limpiar todo. Persiste el slot en el personaje y responde.
            self.handle_edit_avatar(data)

        elif cmd == 0x9E:
            # FF9E = CRASHDUMP del cliente (estilo WER, ~268B): pila con direcciones de retorno +
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-ff9e-crashdump-del-cliente-estilo-wer-268b-p)
            try:
                _addrs, _seen = [], set()
                for _i in range(0, len(data) - 3, 4):
                    _v = struct.unpack_from("<I", data, _i)[0]
                    if 0x00401000 <= _v <= 0x006D0000 and _v not in _seen:
                        _seen.add(_v); _addrs.append("0x%06X" % _v)
                _mods = []
                try:
                    for _tok in data.decode("utf-16le", "ignore").replace("\\", " ").split():
                        if _tok.lower().endswith((".dll", ".exe")) and _tok not in _mods:
                            _mods.append(_tok)
                except Exception:
                    pass
                _rid = getattr(self, "my_room_id", None)
                # ★ CONTEXTO de la conexión al crashear (el FF9E llega POR esta conexión → self tiene el estado):
                #   qué canción, si estaba en juego + segundos desde el GO, y Δ desde el último boost de ESTE uid.
                _room9e = ROOMS.get(_rid) if _rid else None
                _in_game = bool(_room9e and _room9e.get("game_started")) or getattr(self, "game_started", False)
                _song9e = getattr(self, "last_song", None)
                _now9e = time.monotonic()
                _gst = getattr(self, "_game_start_ts", None)
                _tin = ("%.1fs" % (_now9e - _gst)) if (_in_game and _gst) else "—"
                _lb = getattr(self, "last_boost_ts", None)
                _dboost = ("Δ%.1fs" % (_now9e - _lb)) if _lb else "—"
                _ct = getattr(self, "_conn_ts", None)
                _dconn = ("%.1fs" % (_now9e - _ct)) if _ct else "—"   # cuánto lleva esta conexión (lobby/loading/post-carrera)
                # firma legible (las 2 conocidas; ver r2beat-ff9e-crashdump-handler)
                _firma = "A(vtable)" if "0x5A3874" in _addrs else ("B(audio-Miles)" if "0x5C6F7B" in _addrs else "?")
                log(f"[Lobby] ★ FF9E CRASHDUMP uid={self.user_id} sala={_rid} firma={_firma} "
                    f"song={_song9e} in_game={_in_game} t+{_tin} last_boost={_dboost} conn_age={_dconn} "
                    f".text={_addrs or '—'} mod={_mods or '—'} (len={len(data)}) raw={data.hex().upper()}")
            except Exception as _e:
                log(f"[Lobby] FF9E crashdump no decodificó ({_e}) data={data.hex().upper()}")

        elif cmd == 0x40:
            # ★ FF40 = SESSION_FORWARD = subsistema de mensajería (amigos/susurro/perfil/invitación).
            #   data[0] = subtype. Ver docs/FF40_REVERSING_RESULTADO.md. Handler recv cliente 0x58A8D0.
            self.handle_ff40(data)

        elif cmd == 0x81:
            # FF81 = TUTORIAL / MISIÓN de la ESCUELA (rnr_school)
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-ff81-tutorial-misi-n-de-la-escuela-rnr-schoo)
            self.handle_ff81_mission(data)

        else:
            log(f"[Lobby] inner FF{cmd:02X} sin handler (TODO) data={data.hex().upper()}")

























































    def send_system_chat(self, msg, name="SERVER", speaker=2, chat_type=0):
        """Manda una línea de chat S→C como si la dijera 'SERVER' (mismo formato que el echo FF08:
        [namelen][name][msglen][msg][speakerId][chatType]). Para confirmar comandos como `.npc`."""
        nb = enc_text(name)[:0xFF]
        mb = enc_text(msg)[:0xFF]
        resp = bytearray()
        resp.append(len(nb)); resp += nb
        resp.append(len(mb) & 0xFF); resp += mb
        resp.append(speaker & 0xFF)
        resp.append(chat_type & 0xFF)
        self.send_packet(0x08, bytes(resp))

































    def connection_lost(self, exc):
        if getattr(self, "_keepalive_task", None):
            self._keepalive_task.cancel()   # cortar el watchdog de inactividad
        try:
            self.leave_room()   # me saca de la sala / la cierra si era host + notifica a los que quedan
        except Exception:
            pass
        # backstop: leave_room ya transfirió el liderazgo; acá SOLO cerramos salas que quedaron REALMENTE vacías o
        #   donde este uid seguía de host sin otros jugadores (no borrar una sala con gente por un host_uid stale).
        for _rid, _r in list(ROOMS.items()):
            others = [p for p in _r.get("players", []) if p["uid"] != self.user_id]
            if not others and (_r.get("host_uid") == self.user_id or not _r.get("players")):
                ROOMS.pop(_rid, None); log(f"[Lobby] SALA {_rid} des-registrada (uid={self.user_id} desconectó, sin otros jugadores)")
            elif _r.get("host_uid") == self.user_id and others:
                _r["host_uid"] = others[0]["uid"]   # transferir por las dudas (leave_room debería haberlo hecho)
                log(f"[Lobby] SALA {_rid}: líder transferido a uid={others[0]['uid']} (backstop desconexión)")
        CONNECTIONS.pop(self.user_id, None)
        try:
            self.notify_friends_status(False, "desconectó")   # location=0xFFFF -> mis amigos me mueven a "desconectados"
        except Exception:
            pass
        log(f"[Lobby] cerrado uid={self.user_id}")
