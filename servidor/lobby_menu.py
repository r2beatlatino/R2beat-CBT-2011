# -*- coding: utf-8 -*-
"""Entrar al juego: personaje, menu principal y cambio de canal.

Es lo primero que ve el jugador. Si no tiene personaje, se lo manda a
crear uno; si ya tiene, se le arma su ficha (MyRecord) con nivel, ropa,
monedas e inventario.

--
Una pieza de LobbyProtocol. Se junta con las demas en lobby.py.
"""

import struct

from .ajustes import CLIENT_CODEPAGE, FF49_INVENTARIO, LICENSE_BYTE, WELCOME_TEXT
from .basedatos import (
    db,
    db_create_char,
    db_get_char,
    db_get_user,
    has_character,
    licencia_ffc9_de,
)
from .canciones import SONGS
from .catalogo import db_inv_list, db_worn_items
from .cifrado import enc_text
from .paquetes import (
    build_ff13_libre,
    build_ff45_couple_for,
    build_myrecord_honesto,
    inventario_de,
    pack_item_record,
    parse_create_char,
)
from .progresion import (
    LIBRE_FF13,
    LIBRE_LIC,
    LIBRE_PORT,
    LICENCIA_HONESTA,
    NOMBRE_LICENCIA,
    tier_from_exp,
)
from .registro import log


class PiezaMenu:
    """Entrar al juego: personaje, menu principal y cambio de canal."""

    def route_myrecord_or_create(self):
        """LA COMPUERTA. Decisión honesta basada en la DB."""
        if has_character(self.user_id):
            ch = db_get_char(self.user_id)
            log(f"[Lobby] uid={self.user_id} TIENE personaje ({ch['nickname']!r}) -> entrar al lobby")
            self.send_myrecord(ch)
        else:
            log(f"[Lobby] uid={self.user_id} SIN personaje -> mostrar creación")
            self.send_create_prompt()

    # ────────────────────────────────────────────────────────────────
    #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby)
    def build_myrecord(self, count, name, char_id=0x0001FF54,
                       parts=(0x00033686, 0x0004BD25, 0x0006437B, 0x0007C9DE),
                       exp=0, coin=0, mission=0):
        """
        Cuerpo del MyRecord (FF03) formato LATINO 143b. Portado FIEL de TEST21
        (server-work.py ~1646-1681) pero con @54 = count parametrizable y el char-build SOLO
        presente si count>=1 (para que los campos finales queden alineados cuando count==0).
          count==0 -> array de personajes vacío -> gate cliente 0x50CB43 abre la CREACIÓN.
          count>=1 -> con char_id/parts reales -> entra al MENÚ.
        ⚠ Entrega vía override FF03 (cmd==3); el mecanismo honesto del latino aún no se reversó.
        """
        cname = enc_text(name)[:255]
        mr = bytearray()
        mr += struct.pack('<I', 0x0001FF54)
        mr += struct.pack('<I', exp & 0xFFFFFFFF)
        mr += struct.pack('<I', coin & 0xFFFFFFFF)
        mr += struct.pack('<I', mission & 0xFFFFFFFF)
        mr += struct.pack('<H', 0)
        mr += struct.pack('<I', 67890)
        mr += bytes([len(cname)]); mr += cname
        mr += struct.pack('<I', 0); mr += struct.pack('<H', 0); mr += struct.pack('<H', 0)
        mr += struct.pack('<I', 0); mr += struct.pack('<I', 0); mr += bytes([0])
        mr += struct.pack('<I', 0); mr += struct.pack('<I', 0); mr += bytes([0]); mr += bytes([0])
        mr += struct.pack('<I', 0)
        mr += struct.pack('<H', count)                       # @54 = nº de personajes (LA COMPUERTA)
        for _ in range(count):                               # char-build (solo si hay personaje)
            mr += struct.pack('<I', char_id)                 # @56 char_id
            mr += bytes([len(parts)])                        # @60 partCount
            for p in parts:
                mr += struct.pack('<I', p)
        # sección final global (de TEST21)
        mr += bytes(9) + bytes([1]) + struct.pack('<I', 0x0001FF54) + bytes([0]) + bytes(16)
        mr += bytes([0]) + bytes([1]) + struct.pack('<I', 0x00007532)
        mr += bytes([1]) + struct.pack('<I', 0x00007532) + bytes(16)
        return bytes(mr)

    def _send_myrecord_packet(self, mr):
        self.send(b'\xFF\x03' + struct.pack('<H', len(mr)) + mr, f"MYRECORD ({len(mr)}b)")

    def send_myrecord(self, ch):
        """Tiene personaje -> @54=1 con datos reales de la DB -> entra al menú."""
        parts = (ch['hair'] or 0x00033686, ch['top'] or 0x0004BD25,
                 ch['bottom'] or 0x0006437B, ch['skates'] or 0x0007C9DE)
        # OJO: el campo "coin" (offset 8 del MyRecord) es en realidad el EXP que alimenta
        # player+0x2D8 (disasm 0x589825-0x58983D). El nivel se computa de ahí. Por eso ponemos
        # el exp real en coin. (El offset 4 "exp" no controla el nivel.)
        mr = self.build_myrecord(1, ch['nickname'], char_id=ch['char_id'] or 0x0001FF54,
                                 parts=parts, exp=ch['exp'], coin=ch['exp'], mission=ch['mission'])
        self._send_myrecord_packet(mr)
        log(f"[Lobby] MyRecord @54=1 (char {ch['nickname']!r}) -> menú")
        # EXPERIMENTO: empujón para destrabar la intro -> MENÚ PRINCIPAL (sin roomlist).
        self.send_lobby_home_batch()

    def send_create_prompt(self):
        """Sin personaje -> @54=0 (array vacío) -> el cliente debería abrir la pantalla de CREACIÓN."""
        row = db_get_user(self.user_id)
        name = row["username"] if row else "Player"
        mr = self.build_myrecord(0, name)
        self._send_myrecord_packet(mr)
        log(f"[Lobby] MyRecord @54=0 (cuenta {name!r}) -> debería abrir CREACIÓN")

    def handle_create_char(self, data):
        """
        CREATE_CHAR (FF04). Layout CONFIRMADO -> parsear, PERSISTIR en DB, y responder.
        Respuesta que el cliente espera (handler 0x587730): [4]result(=char_id, !=0 y !=-1)
        [1]count [count*4]char_ids. Tras esto el cliente añade el personaje y entra.
        """
        cc = parse_create_char(data)
        if not cc:
            log(f"[Lobby] CREATE_CHAR ilegible: {data.hex().upper()}"); return
        # El género NO viene como campo aparte: va IMPLÍCITO en el char_id
        #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-el-g-nero-no-viene-como-campo-aparte-va-impl)
        gender = cc["char_id"] & 1
        db_create_char(self.user_id, cc["name"], gender, cc["char_id"],
                       cc["hair"], cc["top"], cc["bottom"], cc["skates"])
        self.send_create_char_response(cc["char_id"])
        log(f"[Lobby] personaje creado y guardado: {cc['name']!r} char_id=0x{cc['char_id']:08X} "
            f"parts={[hex(p) for p in cc['parts']]}")

    def send_create_char_response(self, char_id):
        """Respuesta OK a CREATE_CHAR: [4]result=char_id [1]count=1 [4]char_id  (handler 0x587730/0x587829)."""
        body = struct.pack("<I", char_id & 0xFFFFFFFF) + bytes([1]) + struct.pack("<I", char_id & 0xFFFFFFFF)
        self.send_packet(0x04, body)

    def send_lobby_home_batch(self):
        """
        Empujón para destrabar la intro -> MENÚ PRINCIPAL (camino con personaje, @54=1).
        ⚠ A PROPÓSITO **NO** incluye FF6E (roomlist): tu observación es que el FF6E es lo que
        hacía salir "no hay salas, ¿crear una?" como PRIMERA pantalla en server-work. Lo normal
        es menú principal limpio; el roomlist solo al entrar a multiplayer (handle_joinchannel).
        """
        # FF17 (recv cliente 0x5786F0) = "set-ready" de SALA: [slot:1][ready:1] escribe member+0x163.
        # Es de contexto-sala (raro en el menú) pero el oráculo lo manda igual en sesiones que
        # avanzan, así que se replica. [slot 0/1/2][flag=1].
        for cnl in (0, 1, 2):
            self.send_packet(0x17, bytes([cnl, 1]))
        # FF40 (recv cliente 0x58A8D0) = sub-dispatcher EVENT/RECOMMEND. sub=0x03 -> lista de
        # event/recommend con 0 entradas (03=sub, 01 00=campo, 00=count). Inofensivo / vacío.
        self.send_packet(0x40, bytes.fromhex("03010000"))
        # FF45 = lista de PAREJA (커플). Según la pareja real en DB (soltero -> 00 00; casado -> partner).
        self.send_packet(0x45, build_ff45_couple_for(self.user_id))
        # ★ BIENVENIDA con diseño notice_common: FF40/0x0D horn=7777 (tipo custom agregado a
        #   rnr_script/notice.xml -> imgindex 0 = notice_common.tga). Reemplaza el "cartel fantasma"
        #   built-in (texto basura), que queda silenciado con el NOP en el exe. Texto = WELCOME_TEXT.
        self.send_packet(0x40, self.build_ff40_announce(WELCOME_TEXT, sender="Sistema", horn_type=7777))
        log("[Lobby] batch de MENÚ PRINCIPAL enviado (FF17x3/FF40/FF45/bienvenida7777, SIN roomlist)")

    def handle_joinchannel(self, data):
        """El cliente entró a un CANAL (multiplayer) -> responde OK + la lista de salas."""
        ch_num = data[0] if data else 0
        # MyRecord EN EL JOINCHANNEL (puebla el picker #32 SONGS/#34 STAGES JUSTO al entrar al canal...
        #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-myrecord-en-el-joinchannel-puebla-el-picker-)
        if has_character(self.user_id):
            if not getattr(self, "_menu_seq", False):
                # modo (2): el cliente no pidió FF03 -> mandarle la secuencia de MENÚ primero
                self.send_menu_myrecord(motivo="reconnect de cambio de canal")
            ch = db_get_char(self.user_id)
            parts = [ch["hair"], ch["top"], ch["bottom"], ch["skates"]]   # ★ el de CANAL: SÓLO 4 (no tocar)
            _uexp = ch["exp"] or 0   # ★ exp REAL (progresión por nivel; ya no se fuerza 25M)
            _uci, _ulic, _utier = tier_from_exp(_uexp)
            mr = build_myrecord_honesto(ch["nickname"], char_id=(ch["char_id"] or 0x0001FF54),
                                        parts=parts, has_char=True, level=30, license=_ulic,
                                        coin=(ch["coin"] or 0), mission=(ch["mission"] or 0),   # ★ #64: NO pisar el progreso con 0
                                        inventory=[(r["item_id"], r["end_time"], r["in_use"], r["count"]) for r in db_inv_list(self.user_id)])
            self.send_packet(0x03, mr)
            self.send_packet(0x92, bytes([0]) + struct.pack("<I", _uexp & 0xFFFFFFFF))
            self._mr_sent = True
            log(f"[Lobby] MyRecord PROACTIVO en reconnect (puebla picker) char={ch['nickname']!r} -> canal {ch_num}")
        self.send_packet(0x06, b"\x00")                     # JoinChannel OK (result=0)
        # ★ LIBRE = canal SIN dependencia de licencia (no existe en auto-connect; se trata DISTINTO).
        # Habilitar el selector de categorías: FF13 status 0x09 -> campoA 0xC -> guard libre -> selector en
        # práctica ([[r2beat-picker-mechanism]]). El FF13 0x09 pone [0x6d36]=0x09 (inválido), PERO el FFC9 de
        # ABAJO lo re-fija a una licencia válida -> selector activo + picker funcional. SOLO en Libre (48035).
        _is_libre = (getattr(self, "port", 0) == LIBRE_PORT)   # ★ sigue al puerto configurado (ver R2_LIBRE_PORT)
        if _is_libre and LIBRE_FF13:
            self.send_packet(0x13, build_ff13_libre(b"Libre", 0x09))
            log("[Lobby]   ★ LIBRE: FF13 status 0x09 (habilita selector de categorías)")
        elif _is_libre:
            log("[Lobby]   ★ LIBRE [BISECT]: FF13 0x09 OMITIDO (R2_LIBRE_FF13=0) -> Libre se comporta como los otros canales")
        # Licencia (SetLicense, handler cliente 0x581A30 -> player+0x6d36 -> ícono 0x502578)
        #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-licencia-setlicense-handler-cliente-0x581a30)
        _lic = self.licencia_actual()
        self.send_packet(0xC9, bytes([_lic]))               # FFC9 DESPUÉS del FF13 -> re-fija [0x6d36] válido
        # ★ NO mandar send_roomlist() acá (push prematuro): el agente RE a5e7cea confirmó que llena el panel de lista
        # ClassA (this+0x11ec) que LUEGO se recrea/pisa -> queda vacío al dibujar. El draw es modo-inmediato por frame:
        # alcanza con responder al REQUEST 0x6E del cliente (cmd==0x6E -> send_roomlist), que ocurre con el panel ya vivo.
        _licname = NOMBRE_LICENCIA.get(_lic, "?")
        log(f"[Lobby] JOINCHANNEL canal={ch_num} + FFC9({_licname}){' [LIBRE+selector]' if _is_libre else ''}")

    def send_menu_myrecord(self, motivo=""):
        """La secuencia de MENÚ = lo que el server manda cuando el cliente PIDE `FF03`.

        ★★★ POR QUÉ EXISTE (2026-08-02): hay DOS modos de entrar a un canal y no son iguales.
          (1) PRIMER LOGIN     -> el cliente pide FF03, recibe ESTO, y después entra al canal.
          (2) CAMBIO DE CANAL  -> el cliente NO pide FF03 nunca; entra directo al canal.
        El picker se poblaba sólo en (1). La diferencia NO es el `parts` (unificarlos rompe (1)
        también, verificado en vivo): es que en (2) FALTABA ESTA SECUENCIA ENTERA, y el MyRecord
        de canal por sí solo no alcanza para poblar los campos #32 SONGS / #34 STAGES.
        ⇒ `handle_joinchannel` la manda cuando detecta el modo (2).

        Es EL MISMO armado que el handler de `cmd 0x03` (mismo `parts` = set completo con
        mano/mascota/cara). Si algún día cambia uno, tiene que cambiar el otro.
        """
        if not has_character(self.user_id):
            return False
        ch = db_get_char(self.user_id)
        parts = db_worn_items(self.user_id, ch)          # set COMPLETO (el de MENÚ)
        _uexp = ch["exp"] or 0
        _uci, _ulic, _utier = tier_from_exp(_uexp)
        mr = build_myrecord_honesto(ch["nickname"], char_id=(ch["char_id"] or 0x0001FF54),
                                    parts=parts, has_char=True, level=30, license=_ulic,
                                    coin=(ch["coin"] or 0), mission=(ch["mission"] or 0),
                                    inventory=[(r["item_id"], r["end_time"], r["in_use"], r["count"])
                                               for r in db_inv_list(self.user_id)])
        self.send_packet(0x03, mr)
        self.send_packet(0x92, bytes([0]) + struct.pack("<I", _uexp & 0xFFFFFFFF))
        # ★ ACÁ NO VA EL FF49. En el modo (2) el cliente lo PIDE él mismo ~525 ms después del
        #   JOINCHANNEL; mandarlo además desde acá lo duplicaba y **el sonidito sonaba DOS VECES**.
        #   (En el modo (1) sí sale, desde el handler del `cmd 0x03`, porque ahí el cliente no lo pide.)
        #   Ya no hace falta adelantarlo: desde que el FF49 lleva la lista de canciones en `N3` es
        #   IDEMPOTENTE, así que da igual si llega antes o después del MyRecord.
        self._menu_seq = True
        log(f"[Lobby] ★ secuencia de MENÚ (MyRecord completo + FF92) — {motivo or 'primer login'}")
        return True

    def build_ff49_roster(self, ch=None):
        """FF49 (handler cliente `0x577640`). Devuelve (payload, worn).

        ★★★ LA PRIMERA ENTRADA TIENE QUE SER EL PERSONAJE, Y TIENE QUE SER LA SELECCIONADA.
        La lista `[PMGR+0xc8]` MEZCLA canales con el personaje, y el render de la bienvenida y la
        voz por género leen `list[idx8c].f0` COMO SI FUERA un char. Si `idx8c` cae en una fila de
        canal -> char default; si `SelectByKey` no encuentra la clave -> `idx8c = 0xFFFF` -> CRASH.
        Por eso el char va PRIMERO (garantiza que está en la lista al momento del SelectByKey) y
        lleva sus part_ids como CAMPOS (el cliente los decodifica a slots con `0x4C43C0` = la ropa).
        ⚠ Filtrar 0/None de la ropa es OBLIGATORIO: un item nulo decodifica a slot0 y pisaría
          field0 = char_id -> cuerpo roto.

        ⚠⚠ 2026-08-02 — POR QUÉ EXISTE ESTE MÉTODO: el handler del PEDIDO (cmd 0x49) se escribió
        aparte, con las keys `[1900,2900,3901]` copiadas de la copia experimental `multiplayer/`.
        Eso NO lleva al personaje ⇒ `idx8c` apuntaba a una fila de canal vacía. Se notaba SÓLO en
        Libre, porque ahí el cliente entra por reconnect y NUNCA pide el MyRecord (`C→S 0x03`), que
        es donde vivía el FF49 bueno: pedía el FF49 y recibía el malo. Los dos caminos usan esto ahora.
        """
        if ch is None:
            ch = db_get_char(self.user_id) if has_character(self.user_id) else None
        sel_cid = (ch["char_id"] if ch else 0) or 0x0001FF54
        worn = db_worn_items(self.user_id, ch) if ch else []
        entries = [(sel_cid, [sel_cid] + worn), (1900, []), (2900, []), (3901, [])]
        body = struct.pack("<I", sel_cid) + struct.pack("<H", len(entries))
        for key, fields in entries:
            body += struct.pack("<I", key) + struct.pack("<B", len(fields))
            for it in fields:
                body += struct.pack("<I", it & 0xFFFFFFFF)
        # EL FF49 **NO** ES UN "ROSTER DE SERVIDORES": ES UN RECORD COMPLETO,
        #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-el-ff49-no-es-un-roster-de-servidores-es-un-)
        _inv = inventario_de(self.user_id) if FF49_INVENTARIO else []
        body += struct.pack("<H", len(_inv))               # N2 = inventario
        for _it in _inv:
            body += pack_item_record(*_it)
        _songs = [sid for sid, _ in SONGS]                 # el MISMO set que el #32 del MyRecord
        body += struct.pack("<H", len(_songs))             # N3 = música poseída
        for _sid in _songs:
            body += struct.pack("<H", _sid & 0xFFFF)
        return body, worn

    def licencia_actual(self) -> int:
        """La licencia que le corresponde a ESTE jugador en ESTE canal.
        ★ Se usa en DOS lugares y NO puede divergir: el FFC9 del JOINCHANNEL y el byte de licencia
          del FF13 (`build_ff13`, off 40+N). Si divergen, el segundo pisa al primero."""
        if getattr(self, "port", 0) == LIBRE_PORT and LIBRE_LIC:
            return LIBRE_LIC                       # sólo si está FORZADA por env (R2_LIBRE_LIC)
        if LICENCIA_HONESTA:
            return licencia_ffc9_de(self.user_id)  # de la banda REAL del jugador (su exp)
        return LICENSE_BYTE                        # comportamiento viejo (constante)

    def handle_switch_request(self, data):
        """El jugador pidió cambiar de canal. La respuesta correcta es CERRAR la conexión.

        Suena raro, pero es lo que hacía el servidor original, y es lo único que
        funciona: el cliente no se reconecta mientras el socket viejo siga abierto.
        Si le contestás cualquier otra cosa, se queda con el cartel de "conectando"
        para siempre. (Ésos son los "segundos de espera" que uno recuerda del juego.)
        """
        log(f"[Lobby] FF3D: piden cambiar de canal (data={data.hex()})")
        log("[Lobby]   -> cierro la conexión; el cliente se re-loguea solo en el canal nuevo")
        try:
            self.transport.close()
        except OSError as e:
            log(f"[Lobby]   error al cerrar: {e}")

    def build_ff78(self, song_id, name="", rank=0):
        """FF78 ENABLE-SONG — formato del recv handler 0x587E90 (reversado 2026-06-21).
        Puebla la lista de canciones de la sala ([0x766618]+0xc158, records 0x1B0) -> sin esto
        el picker queda VACÍO y entra en loop ("barra loca"). Una canción por paquete.
        Layout: [count1:1][count1×songid:u32][3×u32 A/B/C][flag:1][nameLen:1][name][term:1][7×u32].
        El cliente lee count1 ids -> vector 0x548500, y arma 1 record (ctor 0x587070 -> 0x531750).
        Campos del record que dibuja: +0x00=songid(word), +0x02=título, +0x10c=rank '%02d/40'."""
        nb = enc_text(name)[:0xA0]
        b = bytearray()
        b.append(1)                                  # count1 = 1 id de canción
        b += struct.pack("<I", song_id & 0xFFFFFFFF) # songid (entra al vector + al record +0x00)
        b += struct.pack("<I", rank & 0xFFFFFFFF)    # dword A (rank tentativo)
        b += struct.pack("<I", 0)                    # dword B
        b += struct.pack("<I", 0)                    # dword C
        b.append(0)                                  # flag
        b.append(len(nb)); b += nb                   # nombre (EUC-KR/cp1252)
        b.append(0)                                  # terminador (escribe [buf+nameLen]=0)
        for _ in range(7):
            b += struct.pack("<I", 0)                # 7 dwords finales
        return bytes(b)

    # ── FF77 USERINFO ("mi info") — layout EXACTO del handler 0x587B90 ──
    # result byte: ==1 => panel vacio (handler salta a abort); !=1 (0) => parsea todo.
    # Tras result vienen 5xu32, 1 byte(ignorado), 6xu32, [count]worn(u32 crudos),
    # u32 char_id, [len]nick, [len]str2, 1 byte flag, 7xu32 fijos. Faltar bytes => ceros.
    @staticmethod
    def build_userinfo(nick, char_id, parts, wins=0, losses=0, draws=0,
                       level=0, exp=0, money=0, points=0):
        b = bytearray()
        b.append(0x00)                              # result: 0 = OK (1 = vacio)
        b += struct.pack("<I", wins)               # campo0 GANADAS
        b += struct.pack("<I", losses)             # campo1 PERDIDAS
        b += struct.pack("<I", draws)              # campo2 EMPATES
        b += struct.pack("<I", level)              # campo3 (stat NIVEL visible)
        # MINI-RENDER (reversado ): el AVATAR 3D del popup NO se arma con la lista worn de
        #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-mini-render-reversado-el-avatar-3d-del-popup)
        b += struct.pack("<I", char_id & 0xFFFFFFFF)   # 5º u32 = +0x30 = CUERPO base (GATE del mini-render)
        b.append(0x00)                             # byte de padding
        piece_slots = (list(parts) + [0, 0, 0, 0, 0, 0])[:6]   # 6 piezas cosméticas (0 = slot vacío)
        for p in piece_slots:
            b += struct.pack("<I", p & 0xFFFFFFFF)  # 6º-11º u32 = piezas del avatar del preview
        # apariencia (lista worn): el cliente la lee a un vector y la DESCARTA -> inofensiva; la dejamos por compat.
        worn = [char_id] + list(parts)
        b.append(len(worn) & 0xFF)                  # nº items puestos (apariencia)
        for p in worn:
            b += struct.pack("<I", p & 0xFFFFFFFF) # item_id CRUDO -> avatar (char_id=cuerpo, resto=ropa)
        b += struct.pack("<I", char_id & 0xFFFFFFFF)
        s1 = (nick or "").encode(CLIENT_CODEPAGE, "replace")[:0xFF]
        b.append(len(s1)); b += s1                 # str1 (nick) -> el cliente lo DESCARTA (buffer muerto)
        b.append(0x00)                             # str2 -> [0x766640]+0x3E7 (no es la casilla nombre; vacío)
        b.append(0x00)                             # flag (-> record+0x488)
        for _ in range(7):
            b += struct.pack("<I", 0)              # bloque fijo final
        return bytes(b)

    # ── FF77 USERINFO ("mi info") — layout confirmado (server-work ~1854 + oráculo) ──
    def _find_char_by_nick(self, nick):
        if not nick:
            return None
        c = db(); row = c.execute("SELECT * FROM chars WHERE nickname=?", (nick,)).fetchone(); c.close()
        return row

    def handle_userinfo(self, data):
        """
        FF77 USERINFO. El cliente manda [1]nameLen [name] (su nick cacheado, o el de otro jugador).
        Respuesta CIFRADA (46 b): [1]result + 11×u32(record) + [1]itemCount.
        Buscamos el char por nick; si no aparece (nick viejo), fallback a self = "mi info".
        Los 11 campos son el record (victorias/derrotas/etc.): aún no los trackeamos -> 0 (record
        limpio honesto; se poblarán cuando llevemos esas estadísticas en DB).
        """
        nlen = data[0] if data else 0
        nick = data[1:1 + nlen].decode(CLIENT_CODEPAGE, "replace") if nlen else ""
        ch = self._find_char_by_nick(nick) or db_get_char(self.user_id)
        if ch:
            # ★ MINI-RENDER: char_id = cuerpo base (GATE del preview) + las 4 prendas base equipadas
            #   (pelo/top/bottom/patines) en los slots de piezas -> el avatar sale VESTIDO con lo que
            #   el jugador tiene puesto. NO incluimos las extra (pet/cara/mano): el resolver de avatar
            #   crashea con esas (confirmado por FF9E). Stats/record en 0 (aún no los trackeamos).
            base_worn = [ch[k] for k in ("hair", "top", "bottom", "skates") if ch[k]]
            resp = self.build_userinfo(
                nick=ch["nickname"], char_id=(ch["char_id"] or 0x0001FF54),
                parts=base_worn,
                wins=0, losses=0, draws=0,
                level=0, exp=0, money=0, points=0)
        else:
            resp = bytes([0x01])                    # result=1 => panel vacio
        self.send_packet(0x77, resp)
        log(f"[Lobby] USERINFO nick={nick!r} -> {'OK' if ch else 'FAIL'}")

    def handle_userinfo_other(self, data):
        """
        FF78 = ver info de OTRO jugador (clic sobre un ocupante). Request `[nameLen:u8][nick]` (idéntico a FF77).
        Respuesta por FF77 (el panel de info se puebla con el recv 0x587B90; responder FF78 caería en el picker).
        A DIFERENCIA de "mi info" (que devolvemos vacío/safe), acá SÍ mostramos el avatar real del otro jugador
        (char_id + prendas equipadas) para que el panel tenga sentido. Stats (wins/losses/etc.) = 0 (aún no las
        trackeamos). Si el nick no existe -> panel vacío (result=1); NO caemos a "mi info" (sería mostrar lo mío).
        """
        nlen = data[0] if data else 0
        nick = data[1:1 + nlen].decode(CLIENT_CODEPAGE, "replace") if nlen else ""
        extra = data[1 + nlen:]
        if extra:
            log(f"[Lobby] FF78 con bytes extra tras el nick: {extra.hex()}")  # por si trae 'varios offset'
        ch = self._find_char_by_nick(nick)
        if ch:
            # ★ SOLO las 4 prendas base (pelo/top/bottom/patines). NO usar db_worn_items (incluye slots EXTRA:
            #   pet/cara/mano/accesorios). El panel de info itera cada pieza por el resolver de AVATAR (loop
            #   0x5c69xx -> call esi); el PET (y cara/mano) NO son piezas de avatar (los dibuja cPet aparte) ->
            #   el resolver devuelve null -> CRASH dentro de esi. Confirmado por FF9E (crash al ver a un jugador
            #   con pet equipado; aleatorio = depende del equipo del objetivo). Las 4 base sí las dibuja el panel
            #   (son las mismas que la sala/FF0C y MyRecord renderizan sin problema).
            base_worn = [ch[k] for k in ("hair", "top", "bottom", "skates") if ch[k]]
            resp = self.build_userinfo(
                nick=ch["nickname"], char_id=(ch["char_id"] or 0x0001FF54),
                parts=base_worn,
                wins=0, losses=0, draws=0,
                level=0, exp=0, money=0, points=0)
        else:
            resp = bytes([0x01])                        # result=1 => panel vacío (nick desconocido)
        self.send_packet(0x77, resp)
        log(f"[Lobby] FF78 VER-OTRO nick={nick!r} -> {'OK' if ch else 'FAIL(vacio)'}")
