# -*- coding: utf-8 -*-
"""Servidor de LOGIN (puerto 48004).

Es el primer contacto: valida usuario y contrasena, entrega un token y
le dice al cliente que canales existen. Despues cierra la conexion a
proposito: el cliente solo deja cambiar de canal cuando este socket se
cerro.

--
Parte del servidor abierto de R2Beat. Ver LEEME.md en la raiz del proyecto.
"""

import asyncio
import datetime
import struct
import traceback

from .ajustes import (
    AUTHORIZED_LOGINS,
    CLIENT_CODEPAGE,
    LAUNCHER_QUEUE,
    LOCAL_IP,
    REGISTER_CMD,
    RELOGIN_CLOSE,
    _prune_launcher_queue,
)
from .basedatos import (
    db_get_char,
    db_issue_token,
    db_register,
    db_touch_login,
    db_verify,
    has_character,
)
from .paquetes import _activar_keepalive, parse_login, parse_register
from .progresion import build_channel_list, tier_from_exp
from .registro import log, wire




# ============================================================
# LOGIN server (puerto 48004) — HONESTO y COMPLETO
#   cmd 0x01: valida credenciales (launcher) o token (juego) -> emite/confirma token
#   cmd 0x02: le dice al cliente dónde está el lobby
# ============================================================
class LoginProtocol(asyncio.Protocol):
    def __init__(self):
        self.transport = None; self.buffer = b""; self.user_id = 0

    def connection_made(self, transport):
        self.transport = transport
        _activar_keepalive(transport, "login")
        log(f"[Login] conexión {transport.get_extra_info('peername')}")

    def data_received(self, data):
        self.buffer += data
        while len(self.buffer) >= 4:
            if self.buffer[0] != 0xFF:
                self.buffer = self.buffer[1:]; continue
            cmd = self.buffer[1]
            ln = struct.unpack("<H", self.buffer[2:4])[0]
            if len(self.buffer) < 4 + ln:
                break
            pkt = self.buffer[:4 + ln]; self.buffer = self.buffer[4 + ln:]
            wire(self.user_id, "C2S", "login", cmd, pkt)
            try:
                self.handle(cmd, pkt[4:])
            except Exception:
                import traceback; log("[Login] EXC:\n" + traceback.format_exc())

    def send(self, data, name=""):
        self.transport.write(data)
        wire(self.user_id, "S2C", "login", data[1], data, note=name)

    def handle(self, cmd, payload):
        ip = (self.transport.get_extra_info("peername") or ("0.0.0.0", 0))[0]
        if cmd == 0x01:
            info = parse_login(payload)
            is_cred = bool(info and info["username"] and len(info["pass_hash"]) == 64)
            if is_cred:
                # Login con credenciales = el LAUNCHER.
                row = db_verify(info["username"], info["pass_hash"])
                if not row:
                    log(f"[Login] credenciales inválidas para '{info['username']}'")
                    fail = bytes([0x01]) + struct.pack("<I", 0) + bytes(4) + bytes([0])
                    self.send(b"\xFF\x01" + struct.pack("<H", len(fail)) + fail, "LOGIN FAIL")
                    return
                self.user_id = row["uid"]; db_touch_login(self.user_id, ip)
                _prune_launcher_queue(ip)
                LAUNCHER_QUEUE.setdefault(ip, []).append({"uid": self.user_id, "at": datetime.datetime.now()})  # ENCOLAR handoff (FF01)
                AUTHORIZED_LOGINS[self.user_id] = datetime.datetime.now()   # ★ autoriza a ESTE uid (validado en lobby FF02, por-UID)
                log(f"[Login] OK (cred) uid={self.user_id} user={row['username']} ip={ip} (cola={len(LAUNCHER_QUEUE[ip])})")
            else:
                # Sin credenciales = el JUEGO. Dos casos:
                #  (a) RE-LOGIN de SWITCH DE CANAL: el cliente cerró por FF3D (§G.13) y re-loguea SOLO,
                #      trayendo su uid en el payload (offset 0, u32 != 0). Aceptar -> reconnect al canal.
                #  (b) handoff inicial launcher->juego: uid=0 en payload, resuelto por la COLA FIFO LAUNCHER_QUEUE[ip].
                relogin_uid = struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0
                if relogin_uid and has_character(relogin_uid):
                    # (a) reconnect de switch. (Seguridad: el original usa token de sesión; acá confiamos
                    # en el uid para el switch en LAN — endurecer con token cuando se cierre el flujo.)
                    self.user_id = relogin_uid
                    self.is_relogin = True     # ★ marca: hay que CERRAR el socket tras el LOGIN OK
                    log(f"[Login] ★ RE-LOGIN (switch de canal) aceptado uid={relogin_uid}")
                else:
                    _prune_launcher_queue(ip)
                    q = LAUNCHER_QUEUE.get(ip)
                    if not q:
                        log(f"[Login] RECHAZADO: juego sin login reciente del launcher (ip={ip}, cola vacía)")
                        fail = bytes([0x01]) + struct.pack("<I", 0) + bytes(4) + bytes([0])
                        self.send(b"\xFF\x01" + struct.pack("<H", len(fail)) + fail, "LOGIN FAIL (sin login)")
                        return
                    entry = q.pop(0)                    # consumir el MÁS VIEJO (FIFO) de esta IP
                    if not q: LAUNCHER_QUEUE.pop(ip, None)
                    self.user_id = entry["uid"]
                    log(f"[Login] juego OK -> uid={self.user_id} (handoff FIFO ip={ip}, quedan={len(q)})")
            token = db_issue_token(self.user_id, ip)
            # ★ MURO A (RE agente aae43b48, layout EXACTO): FF01 CmdLogin (cliente 0x56f990):
            #   [00][uid:4][B:1][C:2][lenD:1=0][len(nick):1][nick][EXP:4][buff:2]
            #   El EXP va JUSTO tras el nick -> setExp 0x451d20 -> player+0x2d4 ANTES del matcher del FF02.
            #   (Lo que antes mandábamos como "token" el cliente lo guarda como NICK; no necesita token del FF01.)
            _lch = db_get_char(self.user_id)
            _lexp = (_lch["exp"] if _lch else 0)   # ★ exp REAL (0 si no hay char) -> el matcher rutea por nivel real
            # FIELD9 (campo "buff" del FF01) = nibble de MISIONES-historia completadas
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#login-field9-campo-buff-del-ff01-nibble-de-misione)
            _mctr = ((_lch["mission"] if _lch else 0) or 0)
            _field9 = max(0, min(_mctr - 4, 8)) & 0x0FFF   # nibble0 = misiones grupo-1 hechas
            resp = (bytes([0x00]) + struct.pack("<I", self.user_id) + bytes(4)
                    + bytes([len(token)]) + token.encode("ascii")   # FIELD G (nick) = token (cosmético, lo pisa MyRecord)
                    + struct.pack("<I", _lexp & 0xFFFFFFFF)          # ★ FIELD H = EXP REAL -> matcher bucketea por banda
                    + struct.pack("<H", _field9))                    # FIELD I = FIELD9 nibble misiones (0x455A00)
            self.send(b"\xFF\x01" + struct.pack("<H", len(resp)) + resp, f"LOGIN OK (exp={_lexp})")
            # ★ Tras el RE-LOGIN de switch, el cliente NO va a pedir la lista (FF02): se va directo al
            #   lobby del canal elegido. Si no cerramos acá, el socket de login queda abierto y la
            #   bandera [[0x769C28]+0x17780] se queda en 1 => no se puede volver a cambiar de canal.
            if getattr(self, "is_relogin", False) and RELOGIN_CLOSE:
                log("[Login] re-login: CERRANDO el socket -> OnClose apaga la bandera 0x17780 "
                    "(vuelve a habilitarse el cambio de canal)")
                self.transport.close()

        elif cmd == REGISTER_CMD:
            # REGISTRO (lo manda launcher-clean.py, nunca el juego). Crea la cuenta en la DB.
            user, ph = parse_register(payload)
            ok, msg = db_register(user, ph) if user else (False, "datos inválidos")
            status = 0x00 if ok else (0x01 if msg == "el usuario ya existe" else 0x02)
            mb = msg.encode("utf-8")[:255]
            resp = bytes([status, len(mb)]) + mb
            self.send(bytes([0xFF, REGISTER_CMD]) + struct.pack("<H", len(resp)) + resp, f"REGISTER {msg}")

        elif cmd == 0x02:
            # LOBBYSERVER_LIST (handler cliente 0x56FD70)
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#login-lobbyserver-list-handler-cliente-0x56fd70)
            ip_b = LOCAL_IP.encode("ascii")
            # Se mandan TODOS los peldaños de canal, cada uno con su puerto. El
            # cliente elige solo a cuál auto-conectarse según el nivel del jugador,
            # y los demás quedan en la lista para cambiarse a mano.
            _ch = db_get_char(self.user_id)
            assigned_idx, _alic, _atier = tier_from_exp(_ch["exp"] if _ch else 0)
            log(f"[Login] FF02 user uid={self.user_id} exp={_ch['exp'] if _ch else 0} -> banda {_atier} (canal {assigned_idx})")
            chans = build_channel_list()
            resp = bytearray([len(chans)])                   # N canales
            for chan_id, name, status, cip, cport in chans:
                n2 = name.encode(CLIENT_CODEPAGE, "replace")[:0xFF]
                cip_b = cip.encode("ascii")
                # ESTE u32 ES LA **CLAVE DE IDENTIDAD** DEL CANAL, no un "uid"
                #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#login-este-u32-es-la-clave-de-identidad-del-canal-)
                resp += struct.pack("<I", chan_id & 0xFFFF)  # ★ clave ÚNICA por canal (antes: 0 = colapsaban)
                resp += struct.pack("<H", cport)             # PORT (wordB, por-canal)
                resp += bytes([len(cip_b)]) + cip_b          # len1 + IP (string1, por-canal)
                resp += struct.pack("<H", 0)                 # wordC
                resp += struct.pack("<H", chan_id)           # chan_id
                resp += bytes([status])                      # STATUS
                resp += bytes([len(n2)]) + n2                # len2 + nombre
            self.send(b"\xFF\x02" + struct.pack("<H", len(resp)) + resp, f"LOBBYSERVER_LIST ({len(chans)} canales: {[hex(c[2]) for c in chans]})")
            self.transport.close()

    def connection_lost(self, exc):
        log("[Login] cerrado")
