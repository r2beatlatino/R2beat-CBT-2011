# -*- coding: utf-8 -*-
"""Las salas: crearlas, listarlas, entrar, salir y quien esta adentro.

Una sala tiene lugares numerados (slots) y un anfitrion. Casi todos los
errores raros de esta parte salen de mandar un mensaje mas corto de lo
que el cliente espera: no lo rellena con ceros y aparecen jugadores
fantasma con nombres de basura.

--
Una pieza de LobbyProtocol. Se junta con las demas en lobby.py.
"""

import asyncio
import random
import struct

from .ajustes import CLIENT_CODEPAGE, CONNECTIONS, ENABLE_DEV, LICENSE_BYTE, LOBBY_PORT, ROOMS
from .basedatos import db_get_char, db_get_couple
from .canciones import SONGS, npc_profile_for_slot
from .catalogo import db_worn_items, occ_face_index, occ_wing_id
from .cifrado import enc_text
from .pktlog import pkt
from .registro import log


class PiezaSalas:
    """Las salas: crearlas, listarlas, entrar, salir y quien esta adentro."""

    # ── EXPERIMENTO: inyectar 1 sala falsa para confirmar que el cliente la muestra ──
    #    (y que el "no hay salas" era solo por estar vacío). Es TEMPORAL; quitar luego.
    def _fake_rooms(self):
        # song_id DEBE ser una canción que el cliente TENGA en su DB [0x766638]: el append de sala 0...
        #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-song-id-debe-ser-una-canci-n-que-el-cliente-)
        if not ENABLE_DEV:
            return []
        return [{"id": 0x7FF, "mode": 0, "name": "SALA TEST", "map_id": 8, "song_id": 518, "cur": 1, "max": 6, "flags": 0}]

    def build_roomlist_body(self, rooms, page=1):
        """FF6E ROOMLIST2 — formato de ENTRADA reversado (handler 0x5823C0, rama NON-SOL tipo!=6).
        CORREGIDO 2026-06-21: el formato viejo (10b+nombre, sin cola) NO renderizaba — desincronizaba
        por faltarle el byte B2, los B4/B5 tras el nombre y la COLA obligatoria de 5 bytes [4 dword][1].
        Layout por entrada (lecturas 0x5824ED..0x5826A5): 18 + len(nombre) bytes:
          [2 id|mode][1 flags][2 W1=map][2 W2=song][1 cur][1 B2][1 max][1 namelen][name]
          [1 B4 flags2(bit2=lock)][1 B5][4 D1 cola][1 B6 cola].
        OJO max: el cliente le SUMA 0xFA (=-6) -> para mostrar 6 hay que enviar 12 (calibrar en vivo).
        Rama SOL (tipo==6) inserta 3 strings extra antes de la cola; este builder es NON-SOL."""
        body = bytearray()
        body.append(page & 0xFF)                    # header 'page': el handler 0x5823C0 hace cmp page,1;jbe -> con page>1 habilita las PESTAÑAS de página
        body += struct.pack("<H", len(rooms))       # count (u16) = nº de salas
        for r in rooms:
            # ★ v11 = id | (tipo<<11). El TIPO (v11>>11) NO es solo/equipo (el render no lo usa p/ iconos). Dejarlo 0.
            v11 = (r["id"] & 0x7FF)
            # 2º byte = FLAGS de solo/equipo×velocidad/ítem (decode 0x43b200): bit4(0x10)=EQUIPO, bit2(0...
            #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-2-byte-flags-de-solo-equipo-velocidad-tem-de)
            m = r.get("mode", 0)
            lflags = (0x10 if (m & 0x02) else 0) | (0x00 if (m & 0x01) else 0x04)   # byMode (bits 2,4)
            # ★ El 2º byte lleva byMode (bits 2,4) + FLAGS de ESTADO (bits 0,1) que el handler 0x5823C0 testea
            #   (`test al,1`/`test al,2` @0x5826b2): bit1(0x02)="JUGANDO" (partida en curso). Sin esto la sala figura
            #   siempre "esperando" para los que miran la lista aunque el host ya arrancó. (bit0=0x01=contraseña, pendiente.)
            if r.get("game_started"):
                lflags |= 0x02
            name = r["name"].encode(CLIENT_CODEPAGE, "ignore")[:32]
            body += struct.pack("<H", v11)          # id (tipo=0)
            body.append(lflags)                     # FLAGS equipo/velocidad (0x10 = equipo+velocidad)
            body += struct.pack("<H", r.get("map_id", 8))    # W1 = mapa
            body += struct.pack("<H", r.get("song_id", 102)) # W2 = canción
            body.append(r.get("cur", 1) & 0xFF)     # B1 = jugadores actuales
            body.append(0)                          # B2 (probar 0)
            body.append((r.get("max", 6) + 6) & 0xFF)  # B3 = max (cliente resta 6 -> +6 aquí)
            body.append(len(name)); body += name    # namelen + nombre (EUC-KR/cp1252)
            body.append(0)                          # B4 flags2 (bit2 = lock)
            body.append(0)                          # B5 (probar 0)
            body += struct.pack("<I", 0)            # D1 cola (obligatoria)
            body.append(0)                          # B6 cola (obligatoria)
        return bytes(body)

    # Salas por página = capacidad de display del cliente. VERIFICADO EN VIVO (2026-07-01): el cliente muestra 12 slots por
    #   página (con 8 quedaban 4 vacíos). 60 salas -> 5 páginas de 12.
    ROOMS_PER_PAGE = 12

    def send_roomlist(self, page=0):
        # ★ Salas REALES del MISMO canal (mismo puerto) + la SALA TEST. El cliente lista SALA TEST + todas las reales sin
        #   problema (el "crash con 2+ salas" era artefacto de Frida). PAGINACIÓN SERVER-SIDE (2026-07-01): el cliente pide
        #   la página en el request FF6E (bytes[1:3]); devolvemos SOLO las salas de esa página + page_hdr=TOTAL de páginas
        #   (>1 habilita las pestañas en el handler 0x5823C0). El cliente re-pide al cambiar de pestaña.
        my_port = getattr(self, "port", LOBBY_PORT)
        real = [r for r in ROOMS.values() if r.get("port") == my_port]
        all_rooms = self._fake_rooms() + real
        per = self.ROOMS_PER_PAGE
        total_pages = max(1, (len(all_rooms) + per - 1) // per)   # nº de páginas -> va en el header 'page'
        page = max(0, min(page, total_pages - 1))                 # clamp (por si piden una página inexistente)
        page_rooms = all_rooms[page * per: page * per + per]      # SOLO las salas de esta página
        self.send_packet(0x6E, self.build_roomlist_body(page_rooms, page=total_pages))
        log(f"[Lobby] ROOMLIST -> pág {page+1}/{total_pages}: {len(page_rooms)} sala(s) de {len(all_rooms)} totales (canal {my_port})")

    def _host_slot_of(self, rid):
        """Slot del LÍDER actual de la sala (host_uid) -> va en host_pos del FF13 (byte#2). El cliente compara host_pos
        con su slot [me+0x40] para mostrar 'anfitrión' (RE a9e1f0ec: no hay flag sticky, es slot-derivado server-delivered).
        Antes hardcodeado=2 -> el líder transferido (FF9B) o auto-transferido (al salir) no se reflejaba client-side."""
        room = ROOMS.get(rid)
        if room:
            huid = room.get("host_uid")
            for p in room.get("players", []):
                if p["uid"] == huid:
                    return p["slot"] & 0x7F
        return getattr(self, "host_slot", 2) & 0x7F

    def send_roominfo(self):
        """Re-arma y envía el FF13 desde el ESTADO de sala server-side (stage/canción/slots/modo).
        Se llama al pedir ROOMINFO y tras cada cambio (stage FF1B / slot FF87) para que el cliente redibuje."""
        rid = getattr(self, "my_room_id", None) or getattr(self, "room_id", 1) or 1
        self.send_packet(0x13, self.build_ff13(
            roomid=rid,   # ★ id REAL de la sala (antes: default 1 -> TODAS mostraban "001")
            name=getattr(self, "room_name", "sala"),
            mode=getattr(self, "room_mode", 0),
            map_id=getattr(self, "room_map", 8),
            song_id=getattr(self, "room_song", 102),
            host_pos=self._host_slot_of(rid),   # ★ slot del LÍDER real (antes hardcodeado 2 -> el líder transferido/movido no se reflejaba)
            slot_mask=getattr(self, "room_slot_mask", 0),
            license_byte=self.licencia_actual()))   # ★ off 40+N -> SetLicense. En 0 vaciaba el picker
        # ★ Red de seguridad: re-fijar la licencia con FFC9 después del FF13. Es idempotente y el
        #   handler `0x581A30` ignora el 0, así que no puede empeorar nada. Si algún día un FF13 se
        #   escapa sin `license_byte`, esto lo tapa igual.
        self.send_packet(0xC9, bytes([self.licencia_actual()]))

        # ★ 2026-07-01: YA NO se manda FF4B. El FF13 ahora lleva el byMode en el byte #4 y su handler aplica el modo por
        #   la VÍA SILENCIOSA (0x53ACF0/0x529EB0) SIN popup. El FF4B (applier 0x532FC0) era el ÚNICO que abría el popup
        #   "el modo cambió". Como el FF13 se reenvía en cada cambio (stage/slot/config), el modo queda siempre correcto.

    def broadcast_roominfo(self, rid):
        """★ SYNC de INFO DE SALA (fase sync): reenvía el FF13 (stage/canción/modo/título) a TODOS los ocupantes ->
        cuando el host cambia el STAGE (FF1B) o la CANCIÓN (FF1C), el JOINER también lo ve. Sincroniza los campos
        per-conn desde ROOMS[rid] (fuente de verdad de la sala) antes de que cada uno arme su FF13. Antes: send_roominfo
        solo a self -> el joiner quedaba con el stage/canción viejos."""
        room = ROOMS.get(rid)
        if not room:
            self.send_roominfo(); return
        for p in room.get("players", []):
            conn = CONNECTIONS.get(p["uid"])
            if conn is None:
                continue
            conn.room_name = room.get("name", getattr(conn, "room_name", "sala"))
            conn.room_mode = room.get("mode", getattr(conn, "room_mode", 0))
            conn.room_map  = room.get("map_id", getattr(conn, "room_map", 8))
            conn.room_song = room.get("song_id", getattr(conn, "room_song", 518))
            conn.room_slot_mask = room.get("slot_mask", getattr(conn, "room_slot_mask", 0x3F))
            conn.send_roominfo()

    def build_ff4b(self):
        """FF4B CmdChangeRoomMode (recv 0x57A170 -> applier 0x532FC0): fija el layout solo/equipo de la sala
        escribiendo cRoom+0x6D84 (==1 solo, ==0 equipo 3v3) y +0x6D80 (velocidad/item), reasignando los 6 slots.
        Formato: [result:u8=1][byMode:u8][len1:u8][str1][len2:u8][str2][coupons:u8]. byMode (decoder 0x43B1E0/
        0x43B1B0): bit4(0x10)=equipo, bit2(0x04)=velocidad, bit7(0x80)=variante couple. Lo construimos del modo del
        CREATEROOM (bit1=equipo, bit0=velocidad). (Agente RE 2026-06-30 a4cb003d.)"""
        # CREATEROOM mode: bit1=equipo, bit0=velocidad(1)/item(0). byMode: bit4=equipo (OK), bit2=ITEM (VERIFICADO en
        # vivo: 0x6D80=1 => muestra "item", así que bit2 se prende para ITEM, no velocidad -> invertido vs lo del agente).
        m = getattr(self, "room_mode", 0)
        bymode = (0x10 if (m & 0x02) else 0) | (0x00 if (m & 0x01) else 0x04)   # bit2=item (m&1==0)
        return bytes([0x01, bymode & 0xFF, 0x00, 0x00, 0x00])   # result=1, byMode, len1=0, len2=0, coupons=0

    def send_room_mode(self):
        self.send_packet(0x4B, self.build_ff4b())

    def send_team_colors(self):
        """★ COLOR DE EQUIPO SIN POPUP (Etapa B 2026-07-01). El FF13 silencioso fija el MODO de sala (cRoom+0x6D84) pero
        NO pinta el color de equipo de cada jugador (occupant+0x157) — sin esto los avatares salen con el color 'solo'
        (mostaza/verde) aunque el título diga 'equipo'. El FF4B sí pinta (0x532FC0->0x52FB70) pero abre el popup. FF37
        (recv 0x5793A0 -> 0x52FB70(team,to) -> occupant+0x157) pinta el color por slot SIN popup: con from==to no mueve.
        Solo tiene sentido en EQUIPO (bit1 de room_mode); en SOLO los colores son otro palette (no tocar)."""
        if not (getattr(self, "room_mode", 0) & 0x02):   # bit1 = equipo; en solo no hay color de equipo que pintar
            return
        for occ in self.room_occupants():
            slot = occ["slot"] & 0x7F
            team = 1 if slot < 3 else 2                    # 0,1,2 = rojo/team1 · 3,4,5 = azul/team2
            self.send_packet(0x37, bytes([team & 0xFF, slot, slot]))   # [team][from][to], from==to -> pinta sin mover

    def build_ff13(self, roomid=1, mode=0, name="sala", map_id=8, song_id=102, host_pos=2, speed=3,
                   slot_mask=0, license_byte=None):
        """FF13 ROOMINFO — RE-VERIFICADO 2026-06-30 byte-a-byte contra handler 0x5780E0 (reads 0x5d4fe0):
        [u16 roomid][8 bytes: host_pos,0,mode,0,0,speed,0,0][u16 map][u16 song][u8 namelen][name]
        [u32 A][u8 b][u32 B][u32 C][u32 D][u32 E = SLOT BITMASK (test en 0x578249)][u32 F][u8 b].
        BUG ANTERIOR (corregido): se mandaban 7 bytes de cabecera (el handler lee 8) y 5 dwords de cola
        (lee 6) -> map/song/NAMELEN corridos 1 byte -> TÍTULO ILEGIBLE; y el bitmask caía en offset basura
        -> SLOTS CERRADOS. slot_mask: bits de slots (0 = probar abiertos)."""
        nb = enc_text(name)[:0xFF]
        b = bytearray()
        b += struct.pack("<H", roomid)
        # VÍA SILENCIOSA DEL MODO (Frida ): el byte #4 (mode) es el que el handler de FF13 pasa a la...
        #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-v-a-silenciosa-del-modo-frida-el-byte-4-mode)
        bym = (0x10 if (mode & 0x02) else 0) | (0x00 if (mode & 0x01) else 0x04)   # room_mode (bit1=equipo,bit0=vel) -> byMode
        b += bytes([host_pos & 0xFF, 0, bym & 0xFF, 0, 0, speed & 0xFF, 0, 0])  # 8 bytes (#2..#9): host_pos/byMode/speed + relleno
        b += struct.pack("<H", map_id)              # w1 = mapa
        b += struct.pack("<H", song_id)             # w2 = canción
        b.append(len(nb)); b += nb                  # nombre de la sala (ahora ALINEADO)
        # ★ BITMASK DE SLOTS = PRIMER u32 tras el nombre (read14, cuerpo+0x30; test en 0x578249; loop 0x578240-0x578276
        #   llama 0x529cc0 que escribe [sala+slot+0x6ae0]=1/0). POLARIDAD: bit=1 -> ABIERTO, bit=0 -> CERRADO. (Agente RE
        #   2026-06-30 ab83a98e: antes lo poníamos en dw_E con polaridad invertida -> slots siempre cerrados.)
        b += struct.pack("<I", slot_mask)           # dw_A = SLOT BITMASK (bit=1 abierto)
        b.append(0)                                 # b_A
        b += struct.pack("<I", 0)                   # dw_B
        b += struct.pack("<I", 0)                   # dw_C
        b += struct.pack("<I", 0)                   # dw_D
        b += struct.pack("<I", 0)                   # dw_E
        b += struct.pack("<I", 0)                   # dw_F
        # `b_B` (off 40+N) NO ES RELLENO: **ES EL BYTE DE LICENCIA**
        #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-b-b-off-40-n-no-es-relleno-es-el-byte-de-lic)
        b.append((LICENSE_BYTE if license_byte is None else license_byte) & 0xFF)   # b_B = LICENCIA
        return bytes(b)

    # ──────────────────────────────────────────────────────────────────────
    #  FF0C ROOM_USERLIST — MULTI-OCUPANTE (host + fantasmas/NPCs server-side)
    #  Refactor 2026-06-30 (plan docs/MULTIPLAYER_FASE2_GHOST_USER.md): build_ff0c
    #  pasó de 1 solo usuario (host) a una LISTA de ocupantes. El fantasma se
    #  inyecta 100% server-side (sin 2ª PC/VM) y es la base de un futuro NPC.
    #  Se activa en sala con el comando de chat `.npc` (toggle).
    # ──────────────────────────────────────────────────────────────────────
    def room_occupants(self):
        """Ocupantes actuales de la sala = host + un NPC por cada slot en self.ghost_slots.
        Cada ocupante: dict {char_id, items[], name(bytes), exp, gender, slot, identity}.
        identity = r08 del FF0C: ==2 -> me-detect (0x586A05) matchea [jugador_local+0x40] => "soy yo".
        Cada NPC usa identity = su slot (≠2) => me-detect FALLA => avatar REMOTO (0x52b2e0)."""
        # ★ MODELO POR-SALA (multiplayer real): los ocupantes salen de ROOMS[my_room_id]["players"] (lista {uid,slot}
        #   compartida por todos los de la sala). CADA jugador arma SU FF0C: su propio record con identity=SU slot
        #   (que DEBE == su [me+0x40]: creador=2, joiner=0 -> por eso el creador va a slot 2 y el joiner a slot 0),
        #   y los OTROS con identity=su slot (≠ mi [me+0x40] -> avatar remoto). Sin registro (o solo yo) -> solo yo.
        rid = getattr(self, "my_room_id", None)
        room = ROOMS.get(rid) if rid else None
        players = (room or {}).get("players")
        occ = []
        if players:
            for p in players:
                occ.append(self._occupant_for(p["uid"], p["slot"]))
        else:
            occ.append(self._occupant_for(self.user_id, getattr(self, "host_slot", 2)))
        # ★ NPCs (.npc): leer los ghost_slots del estado COMPARTIDO de la sala (ROOMS[rid]["ghost_slots"]), NO de
        #   self.ghost_slots -> así el JOINER (que nunca corrió `.npc`, tiene self.ghost_slots vacío) TAMBIÉN los ve.
        #   El char del NPC = char del HOST de la sala (host_uid) para que todos rendericen el mismo NPC. Se saltan
        #   los slots ya ocupados por un player real (evita duplicar slot).
        ghost_slots = (room.get("ghost_slots") if room else None) or getattr(self, "ghost_slots", []) or []
        if ghost_slots:
            host_uid = (room or {}).get("host_uid", self.user_id)
            base = self._occupant_for(host_uid, getattr(self, "host_slot", 2))   # slot del base es irrelevante (build_ghost lo sobrescribe)
            player_slots = {o["slot"] & 0x7F for o in occ}
            for gs in sorted(set(ghost_slots)):
                if (gs & 0x7F) not in player_slots:
                    occ.append(self.build_ghost(base, gs))
        return occ

    def _occupant_for(self, uid, slot):
        """Record de ocupante para `uid` en `slot`. identity=slot: si slot==[me+0x40] del que RECIBE -> me-detect
        matchea (=yo, con control); si no -> avatar remoto. (char real de la DB.)"""
        ch = db_get_char(uid); slot &= 0x7F
        return {
            "char_id": (ch["char_id"] if ch else 0) or 0x0001FF54,
            "items": db_worn_items(uid, ch),   # pelo/ropa/patines + MANO/CARA equipados (persisten)
            "name": enc_text(ch["nickname"] if ch else f"P{uid}")[:0xFF],
            "exp": (ch["exp"] if ch else 0) or 0,
            "gender": (ch["gender"] if ch else 0) or 0,
            "slot": slot,
            "identity": slot,
        }

    def leave_room(self):
        """Saca a este jugador de su sala. Modelo guía coreana (leave_room + FF0E):
          1. quitarme de players; si la sala queda VACÍA -> cerrarla (sale de ROOMS y de la lista);
          2. si YO era el LÍDER (host_uid) y quedan otros -> TRANSFERIR el liderazgo a otro jugador (aleatorio si hay
             más de 1, para no favorecer; determinista=el único si queda 1). Antes: si el host salía se BORRABA la sala
             y los joiners quedaban colgados (veían el slot del creador para siempre). Ahora la sala sobrevive con nuevo líder;
          3. FF0E REMOVEUSER `[namelen][name]` a los que quedan -> REMUEVE mi avatar de sus salas (el FF0C hace INSERT al
             mapa cRoom+0x2c, no clear+rebuild -> un FF0C con menos ocupantes NO me quita; hace falta el FF0E, recv 0x577F80);
          4. notify_room -> refresca su FF0C/colores. Idempotente."""
        rid = getattr(self, "my_room_id", None)
        self.my_room_id = None
        if rid is None:
            return
        room = ROOMS.get(rid)
        if not room:
            return
        # nombre ANTES de salir (el FF0E identifica al que se va por nombre, como la guía)
        ch = db_get_char(self.user_id)
        my_name = enc_text(ch["nickname"] if ch else f"P{self.user_id}")[:0xFF]
        _leaving = next((p["slot"] & 0x7F for p in room.get("players", []) if p["uid"] == self.user_id), None)
        room["players"] = [p for p in room.get("players", []) if p["uid"] != self.user_id]
        if _leaving is not None:
            room.get("face_state", {}).pop(_leaving, None)   # limpiar cache de cara del slot que se va (para re-mandar si se reusa)
        room["cur"] = len(room["players"])
        if not room["players"]:
            ROOMS.pop(rid, None)
            log(f"[Lobby] SALA {rid} cerrada (uid={self.user_id} salió; sala vacía)")
            return
        # ★★ TAREA 3 — MID-RACE (capturado en vivo): el caso que FUNCIONA (joiner abandona) manda FF0E + notify_room a
        #   media carrera y la carrera termina bien; lo que TUMBABA a la VM cuando el HOST se iba era el FF9B (transferir
        #   líder) + FF13. Además, si NO se manda FF0E, el avatar del que se fue queda COLGADO -> al llegar a la meta el
        #   teardown lo crashea. FIX: mid-race mandar FF0E (vaciar slot) + notify_room (igual que el caso que anda), y
        #   DIFERIR sólo la notificación de liderazgo (FF9B/FF13) al post-cartel.
        if room.get("game_started"):
            (room.get("reported") or set()).discard(self.user_id)
            (room.get("finished_users") or set()).discard(self.user_id)
            (room.get("scores") or {}).pop(self.user_id, None)
            if room.get("result_players"):
                room["result_players"] = [p for p in room["result_players"] if p["uid"] != self.user_id]
            # FF0E REMOVEUSER -> vacía mi slot/avatar en los que siguen (evita el avatar fantasma en el teardown)
            ff0e_body = bytes([len(my_name) & 0xFF]) + my_name
            for p in room["players"]:
                conn = CONNECTIONS.get(p["uid"])
                if conn is not None:
                    try: conn.send_packet(0x0E, ff0e_body)
                    except Exception as e: log(f"[Lobby] FF0E(mid-race) fallo a uid={p['uid']}: {e}")
            was_host = room.get("host_uid") == self.user_id
            if was_host:   # transferir liderazgo server-side SILENCIOSO; el FF9B/FF13 al nuevo líder se DIFIERE al fin
                room["host_uid"] = (random.choice(room["players"]) if len(room["players"]) > 1 else room["players"][0])["uid"]
                room["_defer_host_notify"] = True
            self.notify_room(rid)   # FF0C/FF37 -> refresca la lista sin el que se fue (SIN FF13/FF9B mid-race)
            log(f"[Lobby] SALA {rid}: uid={self.user_id} SALIÓ EN CARRERA -> FF0E+notify (slot vaciado), líder difiere "
                f"(quedan {room['cur']}, nuevo líder={room.get('host_uid')})")
            self._maybe_settle_after_leave(rid)   # re-evaluar el cartel con el que se fue ya descontado
            return
        # transferir liderazgo si yo era el líder (RE a9e1f0ec: el server es autoridad; hay que NOTIFICAR al nuevo)
        host_changed = False
        if room.get("host_uid") == self.user_id:
            room["host_uid"] = (random.choice(room["players"]) if len(room["players"]) > 1 else room["players"][0])["uid"]
            host_changed = True
            nh = CONNECTIONS.get(room["host_uid"])
            if nh is not None:
                nh.join_host_uid = None   # el nuevo líder es dueño de la sala (habilita START/cambiar modo)
                nh_slot = next((p["slot"] & 0x7F for p in room["players"] if p["uid"] == room["host_uid"]), 2)
                nh.send_packet(0x9B, bytes([nh_slot, 0x00, 0x00]))   # ★ FF9B -> habilita la UI de anfitrión en el nuevo líder
            log(f"[Lobby] SALA {rid}: LÍDER transferido de uid={self.user_id} -> uid={room['host_uid']} (quedan {room['cur']})")
        # FF0E REMOVEUSER a los que quedan -> quita mi avatar/slot de su vista
        ff0e_body = bytes([len(my_name) & 0xFF]) + my_name
        for p in room["players"]:
            conn = CONNECTIONS.get(p["uid"])
            if conn is not None:
                try:
                    conn.send_packet(0x0E, ff0e_body)
                except Exception as e:
                    log(f"[Lobby] FF0E remove fallo a uid={p['uid']}: {e}")
        self.notify_room(rid)   # refresca FF0C/colores en los que quedan
        if host_changed:
            self.broadcast_roominfo(rid)   # ★ FF13 host_pos=nuevo a todos -> refleja el nuevo anfitrión client-side
        log(f"[Lobby] uid={self.user_id} salió de sala {rid} (quedan {room['cur']}, líder uid={room.get('host_uid')})")

    def notify_room(self, rid):
        """Empuja a CADA jugador de la sala su FF0C (personalizado) + FF13. Se llama al entrar/salir alguien (relay).
        Cada conn arma su propio build_ff0c() (con él mismo en su slot=me). Como la guía coreana notify_room_update."""
        room = ROOMS.get(rid)
        if not room:
            return
        players = room.get("players", [])
        for p in players:
            conn = CONNECTIONS.get(p["uid"])
            if conn is None:
                continue
            try:
                # FF0C = lista de ocupantes actualizada (cada uno arma la suya con él=me). NO FF13/FF4B: FF13 resetea el
                # modo a solo y FF4B dispara el popup "el modo cambió".
                conn.send_packet(0x0C, conn.build_ff0c())
                conn.send_team_colors()   # ★ pinta rojo/azul por slot (FF37, sin popup) tras actualizar la lista de ocupantes
            except Exception as e:
                log(f"[Lobby] notify_room: fallo push a uid={p['uid']}: {e}")
        # ★ FF15 caras/ropa: DIFF a todos (ocupantes cuyo worn/faceIndex cambió: equipar/cambiar/entrar/salir).
        #   Fuerza el reload del ocupante para que se vea el cambio de ropa/cara (el FF0C solo NO re-renderiza).
        self.broadcast_face_updates(rid)
        # ★ FIX SLOTS 2026-07-13: el reload del FF15 RESETEA los colores de asiento equipo/solo (reconstruye los
        #   widgets a su default) -> re-pintar los colores de equipo (FF37, escribe directo occ+0x157, sin depender
        #   del observer de modo) DESPUÉS del FF15. INMEDIATO arregla ropa/cara/join. DIFERIDO (~0.4s) arregla el
        #   reset del PET, que se instancia ASÍNCRONO (un frame después del FF15) -> el FF37 inmediato no lo alcanza.
        def _repaint_teams():
            room2 = ROOMS.get(rid)
            if not room2:
                return
            plist = room2.get("players", [])
            uids_here = {p["uid"] for p in plist}
            for p in plist:
                conn = CONNECTIONS.get(p["uid"])
                if conn is not None:
                    try:
                        conn.send_team_colors()
                    except Exception:
                        pass
                    # ★ PAREJA EN SALA: si el partner de este jugador está también en la sala, mandarle
                    #   el ENTER (cmd 0x47 sub=6) con el nombre del partner -> el cliente adorna al
                    #   ocupante-partner (occ+0x1D4=0xFE). Va tras el FF15 (que resetea widgets de asiento),
                    #   inmediato + diferido, igual que la pet. Ver COUPLE_SALA_MAPEO.md HALLAZGOS v2.
                    try:
                        partner = db_get_couple(p["uid"])
                        if partner and partner["uid"] in uids_here:
                            conn.send_packet(0x48, conn._ff48_couple_room(6, partner["nickname"]))
                    except Exception as _e:
                        log(f"[COUPLE] notify sala: fallo ENTER a uid={p['uid']}: {_e}")
        _repaint_teams()                                   # inmediato: ropa/cara/join
        try:
            asyncio.get_event_loop().call_later(0.4, _repaint_teams)   # diferido: reset async del pet
        except Exception:
            pass

    def _player_slots(self):
        """★ 6-SLOT: slots ocupados por JUGADORES REALES de la sala (ROOMS[rid]["players"]), MENOS el mío. Para que
        NPCs y cambios de equipo no pisen a otro jugador cuando la sala tiene más de 2 (hasta 6). Vale en cualquier
        modo (solo/equipo/item/velocidad): un slot ocupado es ocupado sea cual sea el modo."""
        rid = getattr(self, "my_room_id", None)
        room = ROOMS.get(rid) if rid else None
        if not room:
            return set()
        return {p["slot"] & 0x7F for p in room.get("players", []) if p["uid"] != self.user_id}

    def all_free_slots(self):
        """Todos los slots ABIERTOS (bitmask FF13) que NO sean del host NI de otro jugador real. Orden: 0,1 (rojo)
        y 3,4,5 (azul) -> llena ambos equipos. (host suele estar en 2=rojo.)"""
        host = getattr(self, "host_slot", 2) & 0x7F
        mask = getattr(self, "room_slot_mask", 0x3F)
        others = self._player_slots()
        return [s for s in (0, 1, 3, 4, 5, 2) if s != host and s not in others and (mask >> s) & 1]

    def next_free_slot(self):
        """Próximo slot libre para un NPC (no host, no otro jugador real, no ya ocupado por otro NPC)."""
        taken = set(getattr(self, "ghost_slots", []) or [])
        for s in self.all_free_slots():
            if s not in taken:
                return s
        return None

    def _open_slot_on_side(self, side):
        """Un slot ABIERTO del LADO dado (side=(0,1,2) rojo / (3,4,5) azul) que NO sea el del host, NI de otro
        jugador real, NI de un NPC. Destino del MOVE de cambio de equipo (FF37). El slot debe estar abierto en la
        máscara (proxy de cRoom+0x6ae0). Devuelve None si el lado está lleno."""
        host = getattr(self, "host_slot", 2) & 0x7F
        mask = getattr(self, "room_slot_mask", 0x3F)
        taken = set(getattr(self, "ghost_slots", []) or []) | {host} | self._player_slots()
        for s in side:
            if s not in taken and (mask >> s) & 1:
                return s
        return None

    def build_ghost(self, host, slot):
        """Record de UN NPC en `slot`. Reusa char/ropa del host (para que renderice con cuerpo+ropa) pero con
        nombre y exp PROPIOS, slot propio y identity=slot (≠2 -> rama remota). El equipo lo da el slot (FF4B:
        0,1,2=rojo / 3,4,5=azul)."""
        slot &= 0x7F
        # exp por slot: nivel distinto al host para que no salgan "como vos" (tabla 0x6E90B8; valores de banda).
        exp_by_slot = {0: 900000, 1: 2000000, 3: 6000000, 4: 12000000, 5: 19610219}
        return {
            "char_id": host["char_id"],
            "items": list(host["items"]),
            "name": enc_text(npc_profile_for_slot(slot)["name"])[:0xFF],   # nombre del roster (coherente con su estilo de juego)
            "exp": exp_by_slot.get(slot, host["exp"]),
            "gender": host["gender"],
            "slot": slot,                            # r32 slot visible
            "identity": slot,                        # r08 ≠2 -> NO soy yo -> avatar remoto
        }

    def _ff0c_record(self, occ, channel_mode=0):
        """Emite UN registro de usuario del FF0C (todo lo que va DESPUÉS de la cabecera COUNT).
        Layout EXACTO del recv handler 0x5863F0 (program-order de los read=0x5d4fe0):
        (r04 u32 SOLO si channel_mode==6 "sol") r05 EXP u32, r06 mission u16, r07 gender u8,
        r08 IDENTIDAD u8, r09/r10 u8x2=0, r11 u32, r12 u32, r13 ITEMCOUNT u8, r14 item_ids u32xN
        (clasificados a slots por 0x4C43C0), r15 char_id u32, r16 u32, r17 NAMELEN u8 + r18 name,
        r19 u32, r20 STR2LEN u8 + r21 str2, r22 u32, r23-27 u8x5, r28 u16, r29 u8, r30 u8 (flag,
        OBLIGATORIO=0 evita read extra), r31 u32, r32 slot u32.

        r04 CONDICIONAL: el handler lo lee SOLO si el modo de canal global ([0x766608+0x13e5])==6
        (0x586488 cmp al,6 -> sete flag -> 0x5864B8 je salta el read de 4 bytes). En canales no-sol
        mandar r04 corre TODO +4 -> NAMELEN cae sobre basura -> nombre roto "1/2". Por eso solo si ==6."""
        b = bytearray()
        if channel_mode == 6:                        # r04 (u32) SOLO en canal "sol"
            b += struct.pack("<I", 0)
        b += struct.pack("<I", occ["exp"] & 0xFFFFFFFF)       # r05 EXP (clamp 348681 en 0x5867F0)
        b += struct.pack("<H", 0)                             # r06 mission
        b += bytes([occ["gender"] & 0xFF, occ["identity"] & 0xFF, 0, 0])  # r07 gender, r08 identidad, r09/r10
        # CUERPO F EN SALA — CAUSA REAL (, pista amigo coreano "RoleID=Gender" + su server
        #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-cuerpo-f-en-sala-causa-real-pista-amigo-core)
        b += struct.pack("<I", occ["char_id"] & 0xFFFFFFFF)   # r11 = char_id (slots[0] = CUERPO base) ★FIX género/cuerpo
        b += struct.pack("<I", 0)                             # r12
        # CARA+MANO EN SALA — el char_id va DOS veces a propósito
        #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-cara-mano-en-sala-el-char-id-va-dos-veces-a-)
        worn = [occ["char_id"]] + [p for p in occ["items"] if p]  # r14[0]=char_id (cuerpo/cara/mano), resto=ropa
        b.append(len(worn) & 0xFF)                            # r13 ITEMCOUNT
        for p in worn:
            b += struct.pack("<I", p & 0xFFFFFFFF)            # r14 item_ids (r14[0]=char_id, resto=ropa)
        b += struct.pack("<I", occ["char_id"] & 0xFFFFFFFF)   # r15 char_id (cuerpo; se mantiene: avatar/voz)
        b += struct.pack("<I", 0)                             # r16
        name = occ["name"]
        b.append(len(name) & 0xFF); b += name                 # r17/r18 nombre
        b += struct.pack("<I", 0)                             # r19
        b.append(0)                                           # r20 STR2LEN=0
        # ★★ ALA EN SALA — r22 ES el Wing.Id (ver occ_wing_id). read 0x586640 -> desc+0x34 -> 0x56645F
        #   -> 0x473710 SetWingById(occ+0x1B4, wingId), ruta PER-OCUPANTE que no mira el inventario local.
        #   0 = sin ala (detach). Antes iba fijo en 0 -> ningún Wing.Id matcheaba -> nunca se veía un ala
        #   en sala multi (ni propia ni ajena), aunque sí en práctica (otra rama del gate 0x566449).
        b += struct.pack("<I", occ_wing_id(occ["items"]) & 0xFFFFFFFF)   # r22 = Wing.Id
        # CARA NATIVA EN SALA (, RE del handler 0x5863F0) — r25 ES el faceByte
        #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-cara-nativa-en-sala-re-del-handler-0x5863f0-)
        b += bytes([0, 0, occ_face_index(occ["items"]) & 0xFF, 0, 0])   # r23, r24, ★r25=cara, r26, r27
        b += struct.pack("<H", 0)                             # r28
        b.append(0)                                           # r29
        # r30: el cliente SOLO lo lee en canal "sol" (read 0x5866F8 gateado por canal==6, mismo gate que r04).
        # ★ BUG RESUELTO (agente RE ae965ff2): emitirlo SIEMPRE dejaba 1 byte sobrante en canal normal -> con
        # COUNT≥2 desalineaba el 2º registro -1 byte (su r08 leía el gender=0 -> slot 0/rojo; namelen basura ->
        # sin nombre). Con COUNT=1 (host) el sobrante quedaba al final, inofensivo -> por eso nunca se notó.
        if channel_mode == 6:
            b.append(0)                                       # r30 (solo canal sol)
        b += struct.pack("<I", 0)                             # r31
        b += struct.pack("<I", occ["slot"] & 0x7F)            # r32 slot
        return bytes(b)

    def build_ff0c(self, occupants=None, channel_mode=0):    # channel_mode = MODO del canal (campoB del status),
        # NO la licencia. Canal 0x0F -> modo 0 -> NO se manda r04. (Antes =LICENSE_BYTE: con licencia sol=6
        # creía modo-sol y mandaba r04 que el cliente no lee -> desync -> nombre roto "1/2". [workflow RE])
        """FF0C ROOM_USERLIST completo: cabecera [H0][COUNT][H2][H3] + 1 registro por ocupante.
        `occupants` = lista de dicts (ver room_occupants); None -> usa room_occupants() (host + fantasma)."""
        if occupants is None:
            occupants = self.room_occupants()
        b = bytearray()
        b += bytes([0, len(occupants) & 0xFF, 0, 0])          # H0, COUNT, H2, H3
        for occ in occupants:
            b += self._ff0c_record(occ, channel_mode)
        return bytes(b)

    @staticmethod
    def build_ff15(slot, char_id, worn, face_index, wing_id=0):
        """FF15 = APARIENCIA per-ocupante (handler cliente 0x58dc60). PISA la apariencia COMPLETA del
        ocupante (identificado por SLOT) y al final dispara el reload (0x53ca60) que reconstruye el mesh
        leyendo [occ+0x1a1]=faceByte -> por eso la CARA/piel se ve en sala (el FF0C solo no la aplica).
        ⚠ Como pisa TODO, hay que mandar el `worn` COMPLETO (=[char_id]+worn, igual que FF0C r14) o se
        borra ropa/pet/mano. Layout de 17 campos reversado de 0x58dc60 (4 load-bearing: slot, char_id,
        worn, faceByte; el resto en 0). char_id DEBE ser 0x1ff54/55 o la validación 0x4c5670 tira el
        faceByte a 0. Ver memoria r2beat-cara-sala."""
        b  = bytes([slot & 0xFF])                         # #1  slot (key del ocupante)
        b += bytes([0])                                   # #2  f1 flag
        b += struct.pack("<I", char_id & 0xFFFFFFFF)      # #3  char_id (valida la cara + record[0])
        b += struct.pack("<I", 0)                         # #4  uA
        b += bytes([len(worn) & 0xFF])                    # #5  worn_count
        for it in worn:
            b += struct.pack("<I", it & 0xFFFFFFFF)       # #6  worn[i] (clasificado por 0x4c43c0)
        b += bytes([0])                                   # #7  bB
        b += struct.pack("<I", 0)                         # #8  uC
        # ★ #9 = Wing.Id para CAMBIOS en vivo: 0x58DD9D llama 0x52F180(slot, wingId, u8) que termina en el
        #   mismo 0x473710 (vía 0x52F208) que usa el FF0C r22. Sin esto, equipar/quitar un ala estando ya
        #   en la sala no se vería hasta re-entrar (el FF0C solo re-dibuja a ocupantes nuevos).
        b += struct.pack("<I", wing_id & 0xFFFFFFFF)      # #9  uD = Wing.Id
        b += bytes([0])                                   # #10 bE
        b += bytes([0])                                   # #11 bF
        b += bytes([face_index & 0xFF])                   # #12 ★ faceByte -> [occ+0x1a1]
        b += bytes([0])                                   # #13 bG
        b += bytes([0])                                   # #14 bH
        b += struct.pack("<H", 0)                         # #15 uI
        b += struct.pack("<I", 0)                         # #16 uJ
        b += struct.pack("<I", 0)                         # #17 uK
        return bytes(b)

    def _occupants_of_room(self, rid):
        """Ocupantes de `rid` (char/items/slot) SIN depender de self.my_room_id -> usable desde notify_room
        aunque self ya haya salido. Espejo de room_occupants pero por rid explícito (identity no importa
        para FF15, que usa el slot)."""
        room = ROOMS.get(rid)
        if not room:
            return []
        occ = [self._occupant_for(p["uid"], p["slot"]) for p in room.get("players", [])]
        ghost_slots = room.get("ghost_slots") or []
        players = room.get("players") or []
        if ghost_slots and players:
            host_uid = room.get("host_uid", players[0]["uid"])
            base = self._occupant_for(host_uid, 2)
            pslots = {o["slot"] & 0x7F for o in occ}
            for gs in sorted(set(ghost_slots)):
                if (gs & 0x7F) not in pslots:
                    occ.append(self.build_ghost(base, gs))
        return occ

    def seed_face_state(self, rid):
        """Registra en room['face_state'] la apariencia ACTUAL de cada ocupante SIN mandar nada.

        ★ 2026-07-25 — va JUSTO DESPUÉS de un FF0C inicial: ahí el cliente ya dibujó a los ocupantes
        con su cara nativa (r25), así que ese es el estado de referencia contra el que diffea después
        broadcast_face_updates. Sin esta siembra el caché arranca vacío y el PRIMER cambio en sala
        parece una "primera aparición" -> no se manda FF15 -> se oía el equipar (sonido local del
        cliente) pero la cara NO cambiaba. Antes esto no pasaba porque el FF15 de "caras presentes"
        llenaba el estado de rebote."""
        room = ROOMS.get(rid)
        if not room:
            return
        state = room.setdefault("face_state", {})
        for occ in self._occupants_of_room(rid):
            worn = [occ["char_id"]] + [p for p in occ["items"] if p]
            state[occ["slot"] & 0x7F] = (tuple(worn), occ_face_index(occ["items"]),
                                         occ_wing_id(occ["items"]))

    def broadcast_face_updates(self, rid):
        """FF15 = re-render de la apariencia de un ocupante YA DIBUJADO. Solo para CAMBIOS en vivo
        (equipar/cambiar/des-equipar ropa, mano, patines o cara mientras se está en la sala): el FF0C
        no re-renderiza a alguien que ya está en pantalla, y el FF15 fuerza el reload con el worn nuevo.

        ★ 2026-07-25 — NO se usa más para el ESTADO INICIAL. La cara de un ocupante que aparece viaja
        nativamente en el FF0C (r25, ver _ff0c_record), que la aplica al construir el ocupante sin pasar
        por el reload 0x53CA60 -> sin el sonido de "cambió la apariencia" que se oía en cada ingreso.
        Por eso desapareció el parámetro `target` (empujaba las caras presentes a un conn que entraba) y
        la 1ª aparición de un slot NUNCA manda FF15, tenga cara o no.

        DIFF contra room['face_state'] por apariencia COMPLETA (worn + cara) -> no se re-manda de gusto."""
        room = ROOMS.get(rid)
        if not room:
            return
        state = room.setdefault("face_state", {})
        conns = [c for c in (CONNECTIONS.get(p["uid"]) for p in room.get("players", [])) if c]
        for occ in self._occupants_of_room(rid):
            slot = occ["slot"] & 0x7F
            fi = occ_face_index(occ["items"])            # 0 = sin cara -> default
            wi = occ_wing_id(occ["items"])               # 0 = sin ala -> detach
            worn = [occ["char_id"]] + [p for p in occ["items"] if p]   # = FF0C r14 (worn completo, no borra ropa)
            cur = (tuple(worn), fi, wi)
            prev = state.get(slot)
            if prev == cur:
                continue                                 # sin cambio -> no re-mandar (evita sonido/flicker espurio)
            state[slot] = cur
            if prev is None:
                continue                                 # 1ª aparición -> ya la dibujó el FF0C (cara incluida)
            dests = conns                                # a todos (incl. el dueño: se ve desde su registro de ocupante)
            pkt = self.build_ff15(occ["slot"], occ["char_id"], worn, fi, wi)
            for c in dests:
                try:
                    c.send_packet(0x15, pkt)
                except Exception:
                    pass
            log(f"[Lobby] FF15 apariencia occ slot={slot} faceIdx={fi} wingId={wi} "
                f"worn={len(worn)-1} pzs -> {len(dests)} conns")

    def send_music_list(self):
        """Empuja todas las canciones del picker (un FF78 por canción) al entrar/crear sala."""
        for sid, sname in SONGS:
            self.send_packet(0x78, self.build_ff78(sid, sname))
        log(f"[Lobby] MUSIC LIST -> {len(SONGS)} canciones (FF78) al picker de sala")
