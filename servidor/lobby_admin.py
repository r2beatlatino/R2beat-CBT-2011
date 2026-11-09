# -*- coding: utf-8 -*-
"""Comandos de administrador y avisos globales.

Se escriben en el chat empezando con un punto (.ban, .anuncio, .rate).
Solo funcionan para los uid que esten en R2_ADMIN_UIDS.

--
Una pieza de LobbyProtocol. Se junta con las demas en lobby.py.
"""

import struct

from . import ajustes
from .ajustes import CLIENT_CODEPAGE, CONNECTIONS, EXP_LOCK, ROOMS
from .basedatos import db_ban_account, db_get_char, db_get_user, db_unban_account
from .cifrado import enc_text
from .registro import log


class PiezaAdmin:
    """Comandos de administrador y avisos globales."""

    def apply_rate_command(self, cmd_txt):
        """★ `.rate` ADMIN (prod-safe, gateado por ADMIN_UIDS). Cambia los rates de recompensa EN VIVO (globales de
        módulo -> aplican desde la próxima carrera). `.rate` = muestra; `.rate exp|coin|base|pernote <n>` = setea."""
        def _show(): return f"rates -> exp x{ajustes.EXP_RATE} · coin x{ajustes.COIN_RATE} · base {ajustes.BASE_COIN} · pernote {ajustes.COIN_PER_NOTE}"
        parts = cmd_txt.split()
        if len(parts) == 1:                     # `.rate` -> mostrar
            self.send_system_chat(_show()); return
        key = parts[1]
        try:
            val = float(parts[2])
        except (IndexError, ValueError):
            self.send_system_chat("uso: .rate [exp|coin|base|pernote] <valor>"); return
        # ★ CANDADO: con R2_EXP_LOCK=1 el rate de exp NO se puede tocar desde el chat. Sin esto,
        #   cualquiera de los ADMIN_UIDS lo levanta sin querer y se rompe el congelado (pasó el 28/07).
        if key == "exp" and EXP_LOCK:
            self.send_system_chat("la EXP está BLOQUEADA por el server (R2_EXP_LOCK). "
                                  ".rate exp no tiene efecto hasta que se libere.")
            log(f"[Lobby] .rate exp RECHAZADO (EXP_LOCK) uid={self.user_id} intento={val}")
            return
        if   key == "exp":     ajustes.EXP_RATE = val
        elif key == "coin":    ajustes.COIN_RATE = val
        elif key == "base":    ajustes.BASE_COIN = int(val)
        elif key == "pernote": ajustes.COIN_PER_NOTE = val
        else:
            self.send_system_chat("uso: .rate [exp|coin|base|pernote] <valor>"); return
        self.send_system_chat("OK " + _show())
        log(f"[Lobby] .rate uid={self.user_id} -> exp={ajustes.EXP_RATE} coin={ajustes.COIN_RATE} base={ajustes.BASE_COIN} pernote={ajustes.COIN_PER_NOTE}")

    def build_ff40_announce(self, message, sender="Aviso", horn_type=1, extra=0):
        """★ FF40 sub 0x0D = ANUNCIO / confeti (확성기/horn). Formato S2C CONFIRMADO en el binario
        (handler cliente 0x58D001 -> display 0x45C060, this=[0x766628]):
          [0x0D][u32 horn_type][str8 sender][str8 msg][u8 extra]
        - horn_type: 1=confeti normal, 2=grande (ambos = notice abajo-derecha; 0/otros = no se muestra).
        - extra: flag bool que el handler lee y convierte a 0/2 (0 = normal). Doc docs/FF40_REVERSING_RESULTADO.md."""
        s = enc_text(sender)[:0xFF]
        m = enc_text(message)[:0xFF]
        body = bytearray([0x0D])
        body += struct.pack("<I", horn_type & 0xFFFFFFFF)
        body.append(len(s)); body += s
        body.append(len(m)); body += m
        body.append(extra & 0xFF)
        return bytes(body)

    def broadcast_announce(self, message, sender="Aviso", horn_type=1):
        """Manda el anuncio FF40/0x0D a TODOS los jugadores conectados (lobby, menú y sala). Devuelve nº de envíos."""
        body = self.build_ff40_announce(message, sender, horn_type)
        sent = 0
        for uid, conn in list(CONNECTIONS.items()):
            try:
                conn.send_packet(0x40, body); sent += 1
            except Exception as e:
                log(f"[Lobby] anuncio fallo a uid={uid}: {e}")
        log(f"[Lobby] ANUNCIO (horn={horn_type}) de uid={self.user_id} sender={sender!r}: {message!r} -> {sent} conns")
        return sent

    def apply_admin_command(self, text_bytes):
        """Comandos ADMIN (prod, gateados por ADMIN_UIDS). Devuelve True si consumio el comando.
        .host | .who <t> | .dc <t> | .kick <t> | .ban <t> | .unban <t>   (t = nick o #uid). Ban = solo cuenta."""
        raw = text_bytes.decode(CLIENT_CODEPAGE, "replace").strip()
        parts = raw.split()
        cmd = parts[0].lower() if parts else ""
        if cmd not in (".host", ".who", ".dc", ".disconnect", ".kick", ".ban", ".unban"):
            return False

        # .host - hacerme lider/anfitrion de la sala actual
        if cmd == ".host":
            rid = getattr(self, "my_room_id", None)
            room = ROOMS.get(rid) if rid else None
            if room is None:
                self.send_system_chat("[admin] no estas en una sala"); return True
            room["host_uid"] = self.user_id
            self.join_host_uid = None
            self.send_packet(0x9B, bytes([getattr(self, "host_slot", 2) & 0x7F, 0x00, 0x00]))
            self.broadcast_roominfo(rid)
            self.send_system_chat(f"[admin] ahora sos anfitrion de la sala {rid}")
            log(f"[ADMIN] .host uid={self.user_id} -> lider sala {rid}")
            return True

        # resto: requieren target (nick o #uid)
        target = " ".join(parts[1:]).strip()
        if not target:
            self.send_system_chat(f"[admin] uso: {cmd} <nick|#uid>"); return True

        if target.startswith("#") and target[1:].isdigit():
            tuid = int(target[1:]); tch = db_get_char(tuid)
            tnick = (tch["nickname"] if tch else f"#{tuid}")
        else:
            tch = self._find_char_by_nick(target)
            if not tch:
                self.send_system_chat(f"[admin] no encontre a {target!r}"); return True
            tuid = tch["uid"]; tnick = tch["nickname"]
        tuser = db_get_user(tuid)
        tconn = CONNECTIONS.get(tuid)
        live_ip = None
        if tconn is not None:
            try:
                live_ip = (tconn.transport.get_extra_info("peername") or (None,))[0]
            except Exception:
                live_ip = None
        ip = live_ip or (tuser["last_ip"] if tuser else None)

        if cmd == ".who":
            self.send_system_chat(f"[admin] {tnick} uid={tuid} ip={ip} {'ONLINE' if tconn else 'offline'}")
            return True

        if cmd in (".dc", ".disconnect"):
            if tconn is None:
                self.send_system_chat(f"[admin] {tnick} no esta conectado"); return True
            try:
                tconn.transport.close()
            except Exception:
                pass
            self.send_system_chat(f"[admin] desconectado {tnick} (uid={tuid})")
            log(f"[ADMIN] .dc uid={self.user_id} -> uid={tuid}")
            return True

        if cmd == ".kick":
            if tconn is None or getattr(tconn, "my_room_id", None) is None:
                self.send_system_chat(f"[admin] {tnick} no esta en una sala"); return True
            self._admin_kick_from_room(tconn, tuid, tnick)
            self.send_system_chat(f"[admin] expulsado {tnick} de su sala")
            log(f"[ADMIN] .kick uid={self.user_id} -> uid={tuid}")
            return True

        if cmd == ".ban":
            ok_a, msg_a = db_ban_account(tuid)
            if tconn is not None:
                try:
                    tconn.transport.close()
                except Exception:
                    pass
            self.send_system_chat(f"[admin] BAN {tnick} (uid={tuid}) cuenta: {msg_a}")
            log(f"[ADMIN] .ban uid={self.user_id} -> uid={tuid} ({msg_a})")
            return True

        if cmd == ".unban":
            ok_a, msg_a = db_unban_account(tuid)
            self.send_system_chat(f"[admin] UNBAN {tnick} (uid={tuid}) cuenta: {msg_a}")
            log(f"[ADMIN] .unban uid={self.user_id} -> uid={tuid}")
            return True

        return True

    def _admin_kick_from_room(self, tconn, tuid, tnick):
        """Expulsa a tuid de SU sala (iniciado por admin, no por el lider). Replica la mecanica del FF20."""
        rid = getattr(tconn, "my_room_id", None)
        room = ROOMS.get(rid) if rid else None
        if room is None:
            return
        room["players"] = [p for p in room.get("players", []) if p["uid"] != tuid]
        room["cur"] = len(room["players"])
        if room.get("host_uid") == tuid and room["players"]:
            room["host_uid"] = room["players"][0]["uid"]
        tconn.my_room_id = None
        try:
            tconn.send_packet(0x20, b"\x00")   # "te kickearon" -> vuelve a la lobby
        except Exception:
            pass
        kname = enc_text(tnick)[:0xFF]
        ff0e = bytes([len(kname) & 0xFF]) + kname
        for p in room["players"]:
            c = CONNECTIONS.get(p["uid"])
            if c is not None:
                try:
                    c.send_packet(0x0E, ff0e)   # remueve su avatar de los que quedan
                except Exception:
                    pass
        self.notify_room(rid)
