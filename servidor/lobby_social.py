# -*- coding: utf-8 -*-
"""Amigos, pareja, susurros y misiones del tutorial.

Todo lo que es "hablar con otro jugador" fuera de la sala. Casi todo
viaja por un solo comando (FF40) con un sub-tipo adentro.

--
Una pieza de LobbyProtocol. Se junta con las demas en lobby.py.
"""

import struct

from .ajustes import CLIENT_CODEPAGE, CONNECTIONS, COUPLE_PENDING
from .basedatos import (
    db_add_friend,
    db_del_couple,
    db_del_friend,
    db_get_char,
    db_get_couple,
    db_get_friends,
    db_get_mission,
    db_set_couple,
    db_set_mission,
)
from .cifrado import enc_text
from .paquetes import build_ff45_couple_for
from .progresion import level_from_exp, tier_from_exp
from .registro import log


class PiezaSocial:
    """Amigos, pareja, susurros y misiones del tutorial."""

    # ═══ FF81 · TUTORIAL / MISIÓN (ESCUELA) ══════════════════════════════════
    def handle_ff81_mission(self, data):
        """C→S FF81: el cliente REPORTA el paso de la misión (escuela) y BLOQUEA esperando la
        respuesta S→C para renderizar el botón continuar/lobby (mismo patrón que FFE2).
        Cable C→S (sender 0x57D060): [byteA][state][extra?]  -> state = 2º byte.
        Respuesta S→C (recv 0x583820, 14 bytes): [state:u8][selector:u8][f3:u32][f4:u32][f5:u32].
          selector=0 -> rama de AVANCE: guarda [player+0x331]=state y llama school_step(state,f4)
          = 0x4257E0 -> transición de escena (botón). state==5 -> escena LOBBY; state!=5 -> siguiente
          lección. f3=f4=f5=0 = avance limpio (setea flag en-tutorial, sin rama de error/DB).
        """
        # ★★ Layout REAL del cable (capturado en vivo, decodificado por el server):
        #     data[0] = FASE:  0 = inicio de lección   ·   1 = lección TERMINADA
        #     data[1] = Nº de LECCIÓN (1..5)
        #     data[2] = extra (solo en "terminada", =1)
        #   Ej: 00 01 = inicio lección 1 · 01 02 01 = terminó lección 2.
        fase = data[0] if len(data) > 0 else 0
        lesson = data[1] if len(data) > 1 else 0        # ★ número de lección = lo que hay que echar
        extra = data[2] if len(data) > 2 else None
        uid = getattr(self, "user_id", None)
        prev = db_get_mission(uid) if uid else 0
        if uid and fase == 1:
            db_set_mission(uid, lesson)     # persiste hasta la lección completada (monótono)
        now = db_get_mission(uid) if uid else 0
        # INICIO de lección (fase==0, len==2): fire-and-forget, NO responder.
        # LECCIÓN TERMINADA (fase==1, len>=3): el cliente BLOQUEA esperando el S→C. Responder con
        #   state = Nº de lección para que el gate del botón "siguiente" (0x425850:
        #   [player+0x331] >= lesson_idx+1) pase (lesson_idx = lección-1, 0-based). f3=1 fuerza la rama
        #   del recv que GUARDA [player+0x331]=state (con f3=f4=0 lo saltea). lección 5 -> school_step
        #   -> escena LOBBY (fin de misión).
        if fase == 1 and lesson >= 1:
            resp = bytes([lesson & 0xFF, 0x00]) + struct.pack("<I", 1) + struct.pack("<I", 0) + struct.pack("<I", 0)
            self.send_packet(0x81, resp)   # [state=lesson][sel=0][f3=1][f4=0][f5=0], 14 bytes
            sc = f"  -> S→C avance sel=0 f3=1 state=lección{lesson}" + ("  ★LOBBY(lección 5=fin)" if lesson >= 5 else "")
        else:
            sc = "  (inicio de lección -> NO se responde)"
        log(f"[Escuela] FF81 uid={uid} fase={fase} lección={lesson} extra={extra} "
            f"len={len(data)} raw={data.hex().upper()}  mission {prev}->{now}{sc}")

    # ═══ FF40 · SISTEMA DE MENSAJERÍA ════════════════════════════════════════
    def handle_ff40(self, data):
        """Dispatcher del subsistema FF40 (C2S). data[0]=subtype. Todo subtype nuevo se loguea
        para ir mapeando el C2S del cliente CBT (el reversing de docs/ es del lado S2C)."""
        if not data:
            return
        sub = data[0]
        p = data[1:]
        if sub == 0x1D:
            # ★ C2S = SOLICITUD DE AMISTAD. Formato: [u32 uid=0][str8 nick]. Capturado en vivo
            #   `1D 00000000 07 'SolUser'`. El cliente SÍ hace request->accept: esto solo DISPARA el
            #   popup en el destino (S2C 0x1D eventtype=1); el alta real ocurre cuando el destino ACEPTA
            #   (C2S 0x1E). Ver docs/FF40_CAPTURAS_C2S.md.
            nlen = p[4] if len(p) > 4 else 0
            nick = p[5:5 + nlen].decode(CLIENT_CODEPAGE, "replace") if nlen else ""
            self.friend_request(nick)
        elif sub == 0x1E:
            # ★ C2S = RESPUESTA A SOLICITUD DE AMISTAD. Formato: [u8 action][u32 requester_uid].
            #   Capturado en vivo `1E 06 05000000` = SolUser ACEPTÓ (action=6) la solicitud de uid 5.
            action = p[0] if p else 0
            ruid = struct.unpack_from("<I", p, 1)[0] if len(p) >= 5 else 0
            self.friend_respond(action, ruid)
        elif sub == 0x1F:
            # ★ C2S = ELIMINAR AMIGO. Formato: [u32 uid][u8 0]. Capturado en vivo `1F EB030000 00`.
            #   Quitamos de la DB (mutuo) y mandamos S2C 0x1E type=6 (quita de la lista) a ambos.
            tuid = struct.unpack_from("<I", p, 0)[0] if len(p) >= 4 else 0
            self.friend_delete(tuid)
        elif sub == 0x06:
            # ★ C2S = PEDIR PERFIL. Formato: [u32 uid]. Capturado en vivo `06 EB030000` (ruii pidió
            #   el perfil de uid 1003). Respondemos con S2C 0x06 (layout CONFIRMADO).
            tuid = struct.unpack_from("<I", p, 0)[0] if len(p) >= 4 else 0
            self.send_profile(tuid)
        elif sub == 0x04:
            # ★ C2S = SUSURRO / MENSAJE 1:1. Formato: [u32 uid][u16 len][msg]. Capturado en vivo
            #   `04 EB030000 0500 'dfsdf'` (ruii susurró a uid 1003). Reenviamos S2C 0x0F al destino + eco.
            self.whisper_send(p)
        elif sub == 0x21:
            # C2S bootstrap del sistema de amigos (payload vacío), al entrar al lobby. Mandamos la
            #   lista online 0x03 (layout del loop CONFIRMADO 0x58BB53-0x58BC1B). Cierra el TODO histórico.
            self.send_friend_list()
            self.sync_all_friend_states()   # fija la SECCIÓN inicial correcta (0x04 por amigo -> location u16)
            log(f"[FF40] uid={self.user_id} bootstrap amigos (0x21) -> lista completa (online+offline) enviada")
        elif sub == 0x0A:
            # ★ C2S = FAMILIA COUPLE (pareja/matrimonio con anillo). p[0]=acción.
            #   action 0x00 = PROPONER: [0x00][str8 target][u32 chargeId] (CAPTURADO EN VIVO
            #   `0A 00 06 'testff' 21790000` charge=31009=Anillo de Amor). El popup en el destino es
            #   FF40 sub-0x0A sub2=6 (aceptar/rechazar/decidir-luego), CONFIRMADO en vivo con `.couple 6`.
            #   Otras acciones (aceptar/rechazar del destino) = a capturar en vivo (se loguean).
            self.handle_couple(p)
        else:
            log(f"[FF40] uid={self.user_id} sub 0x{sub:02X} SIN handler (C2S nuevo) data={data.hex().upper()}")

    # ═══ COUPLE (pareja/matrimonio) — FF40 sub 0x0A ═══════════════════════════
    def handle_couple(self, p):
        """C2S familia couple (FF40 sub 0x0A). p[0]=acción. action=0x00=proponer (anillo)."""
        if not p:
            return
        action = p[0]
        if action == 0x00:
            self.couple_propose(p[1:])
        elif action == 0x04:
            # ★ ACEPTAR (CAPTURADO EN VIVO `0A 04 05000000`): [0x04][u32 proposer_uid].
            proposer_uid = struct.unpack_from("<I", p, 1)[0] if len(p) >= 5 else 0
            self.couple_accept(proposer_uid)
        elif action == 0x03:
            # ★ RECHAZAR (CAPTURADO EN VIVO `0A 03 05000000`): [0x03][u32 proposer_uid].
            proposer_uid = struct.unpack_from("<I", p, 1)[0] if len(p) >= 5 else 0
            self.couple_reject(proposer_uid)
        elif action == 0x07:
            # ★ ROMPER/TERMINAR (CAPTURADO EN VIVO `0A 07`, sin payload): la pareja sale de la DB.
            self.couple_break()
        else:
            log(f"[COUPLE] uid={self.user_id} acción C2S 0x{action:02X} SIN handler data={p.hex().upper()}")

    def couple_accept(self, proposer_uid):
        """El destino ACEPTÓ (C2S action 0x04). Casa a ambos + notifica:
        proponente -> sub2=4 ("X aceptó, están registrados como pareja");
        aceptante  -> sub2=8 ("ustedes aceptaron la relación"). + FF45 a ambos (Mi pareja)."""
        pending = COUPLE_PENDING.get(self.user_id)
        if pending != proposer_uid:
            log(f"[COUPLE] uid={self.user_id} aceptó a uid={proposer_uid} pero pending={pending} "
                f"(igual proceso)")
        COUPLE_PENDING.pop(self.user_id, None)
        proposer = db_get_char(proposer_uid)
        me = db_get_char(self.user_id)
        if not proposer or not me:
            log(f"[COUPLE] uid={self.user_id} aceptó pero falta char (proposer={proposer_uid})")
            return
        my_nick = me["nickname"]; pr_nick = proposer["nickname"]
        db_set_couple(self.user_id, proposer_uid)
        # ★ al casarse quedan también como AMIGOS MUTUOS PERSISTENTES: alta mutua en la DB (friends) ->
        #   al reloguear, send_friend_list los carga de la DB y siguen en la lista de ambos. + S2C 0x01
        #   action=1 para que aparezcan YA (sin esperar relogin). Esto es lo que da la persistencia.
        db_add_friend(self.user_id, proposer_uid)
        db_add_friend(proposer_uid, self.user_id)
        self.send_packet(0x40, self._ff40_couple(8, uid=proposer_uid, name=pr_nick))      # toast "pareja" (a MÍ)
        self.send_packet(0x45, build_ff45_couple_for(self.user_id))                       # Mi pareja (yo)
        self.send_packet(0x40, self._ff40_result(1, uid=proposer_uid, nick=pr_nick))      # el otro -> mi lista de amigos
        pconn = CONNECTIONS.get(proposer_uid)
        if pconn is not None:
            pconn.send_packet(0x40, pconn._ff40_couple(4, uid=self.user_id, name=my_nick))  # toast (al proponente)
            pconn.send_packet(0x45, build_ff45_couple_for(proposer_uid))                     # Mi pareja (él)
            pconn.send_packet(0x40, pconn._ff40_result(1, uid=self.user_id, nick=my_nick))   # yo -> su lista de amigos
        log(f"[COUPLE] uid={self.user_id} ({my_nick!r}) ACEPTÓ a uid={proposer_uid} ({pr_nick!r}) "
            f"-> CASADOS + AMIGOS MUTUOS PERSISTENTES (DB) + FF45 a ambos")

    def couple_reject(self, proposer_uid):
        """El destino RECHAZÓ (C2S action 0x03). Avisa al proponente sub2=3 ("X rechazó tu propuesta")."""
        COUPLE_PENDING.pop(self.user_id, None)
        me = db_get_char(self.user_id)
        my_nick = me["nickname"] if me else f"P{self.user_id}"
        pconn = CONNECTIONS.get(proposer_uid)
        if pconn is not None:
            pconn.send_packet(0x40, pconn._ff40_couple(3, uid=self.user_id, name=my_nick))
        log(f"[COUPLE] uid={self.user_id} ({my_nick!r}) RECHAZÓ a uid={proposer_uid} -> sub2=3 al proponente")

    def couple_break(self):
        """ROMPER la relación (C2S action 0x07, sin payload). Saca la pareja de la DB, avisa a la
        pareja sub2=7 ("X terminó la relación") y limpia FF45 (Mi pareja -> soltero) en AMBOS."""
        partner = db_get_couple(self.user_id)
        me = db_get_char(self.user_id)
        my_nick = me["nickname"] if me else f"P{self.user_id}"
        if not partner:
            log(f"[COUPLE] uid={self.user_id} intentó romper pero está soltero")
            return
        puid = partner["uid"]; pnick = partner["nickname"]
        db_del_couple(self.user_id)                                # borra ambas direcciones
        # a MÍ (el que rompe): aviso + FF45 soltero
        self.send_packet(0x40, self._ff40_couple(7, uid=puid, name=pnick))
        self.send_packet(0x45, build_ff45_couple_for(self.user_id))
        # a la PAREJA: aviso "X terminó la relación" + FF45 soltero
        pconn = CONNECTIONS.get(puid)
        if pconn is not None:
            pconn.send_packet(0x40, pconn._ff40_couple(7, uid=self.user_id, name=my_nick))
            pconn.send_packet(0x45, build_ff45_couple_for(puid))
        log(f"[COUPLE] uid={self.user_id} ({my_nick!r}) ROMPIÓ con uid={puid} ({pnick!r}) "
            f"-> sub2=7 + FF45 soltero a ambos")

    def couple_propose(self, body):
        """Propuesta de pareja con el anillo. body = [str8 target][u32 chargeId].
        Manda al destino ONLINE el popup sub2=6 (aceptar/rechazar/decidir-luego) con MI nombre.
        Errores -> sub2=2 (\"puedes enviar propuesta solo a usuarios conectados\")."""
        if not body:
            return
        nlen = body[0]
        nick = body[1:1 + nlen].decode(CLIENT_CODEPAGE, "replace") if nlen else ""
        charge = struct.unpack_from("<I", body, 1 + nlen)[0] if len(body) >= 1 + nlen + 4 else 0
        me = db_get_char(self.user_id)
        my_nick = me["nickname"] if me else f"P{self.user_id}"
        target = self._find_char_by_nick(nick)
        if not target or target["uid"] == self.user_id:
            self.send_packet(0x40, self._ff40_couple(2, code=0))
            log(f"[COUPLE] uid={self.user_id} propuso a {nick!r} -> inválido/self (sub2=2)")
            return
        tuid = target["uid"]
        tconn = CONNECTIONS.get(tuid)
        if tconn is None:
            self.send_packet(0x40, self._ff40_couple(2, code=0))   # offline
            log(f"[COUPLE] uid={self.user_id} propuso a {nick!r} (uid={tuid}) -> OFFLINE (sub2=2)")
            return
        COUPLE_PENDING[tuid] = self.user_id
        tconn.send_packet(0x40, self._ff40_couple(6, uid=self.user_id, name=my_nick))
        log(f"[COUPLE] uid={self.user_id} ({my_nick!r}) PROPONE a {nick!r} (uid={tuid}) "
            f"charge={charge} -> popup sub2=6 enviado")

    def friend_request(self, nick):
        """C2S 0x1D = SOLICITUD de amistad. SOLO dispara el popup en el destino (S2C 0x1D eventtype=1
        = "X quiere ser tu amigo"). El alta real ocurre cuando el destino ACEPTA (C2S 0x1E -> friend_respond).
        Confirmado en vivo: el 0x1D eventtype=1 NO agrega silencioso, muestra la solicitud con botón aceptar."""
        # Códigos de error del S2C 0x01 (str# CONFIRMADOS en vivo 2026-07-14):
        #   action=4  (str#0x48)  = "el usuario ya está en lista de amigos"
        #   action=8  (str#0x1A2) = "no puede registrar a más amigos" (lista llena)
        #   action=otro(str#0x5D) = "el usuario que busca no existe o está desconectado"  <- usamos 0x58
        #   (cualquier action ∉ {1,2,3,4,8,9} cae en str#0x5D). action=3 (str#0x57)="máx 100 amigos" -> NO usar.
        ERR_NOTFOUND = 0x58
        target = self._find_char_by_nick(nick)
        if not target:
            self.send_packet(0x40, self._ff40_result(ERR_NOTFOUND))   # "no existe o desconectado"
            log(f"[FF40] uid={self.user_id} solicitó a {nick!r} -> NO existe (str#0x5D)")
            return
        tuid = target["uid"]
        if tuid == self.user_id:
            self.send_packet(0x40, self._ff40_result(ERR_NOTFOUND))   # no a vos mismo -> genérico
            return
        # ya son amigos -> action=4 ("el usuario ya está en lista de amigos")
        if any(r["uid"] == tuid for r in db_get_friends(self.user_id)):
            self.send_packet(0x40, self._ff40_result(4))
            log(f"[FF40] uid={self.user_id} solicitó a {nick!r} (uid={tuid}) -> YA es amigo (action=4)")
            return
        # cap 100: MI lista llena -> action=3; la lista del OTRO llena -> action=8
        if len(db_get_friends(self.user_id)) >= 100:
            self.send_packet(0x40, self._ff40_result(3))
            log(f"[FF40] uid={self.user_id} solicitó a {nick!r} -> MI lista llena (action=3)")
            return
        if len(db_get_friends(tuid)) >= 100:
            self.send_packet(0x40, self._ff40_result(8))
            log(f"[FF40] uid={self.user_id} solicitó a {nick!r} (uid={tuid}) -> lista del OTRO llena (action=8)")
            return
        tconn = CONNECTIONS.get(tuid)
        if tconn is None:
            # destino offline -> str#0x5D cubre "... o está desconectado"
            self.send_packet(0x40, self._ff40_result(ERR_NOTFOUND))
            log(f"[FF40] uid={self.user_id} solicitó a {nick!r} (uid={tuid}) -> OFFLINE (str#0x5D)")
            return
        me = db_get_char(self.user_id)
        my_nick = me["nickname"] if me else f"P{self.user_id}"
        # popup de solicitud en el destino
        tconn.send_packet(0x40, self._ff40_friend_event(self.user_id, my_nick, eventtype=1))
        log(f"[FF40] uid={self.user_id} ({my_nick!r}) SOLICITÓ amistad a {nick!r} (uid={tuid}) "
            f"-> popup 0x1D eventtype=1 enviado")

    def friend_respond(self, action, requester_uid):
        """C2S 0x1E = respuesta a la solicitud. action=6 = ACEPTAR (confirmado en vivo `1E 06 <uid>`).
        Al aceptar: alta MUTUA persistida + a AMBOS un S2C 0x01 action=1 (aparece el amigo en cada lista)."""
        requester = db_get_char(requester_uid)
        if action != 6:
            # rechazo u otra acción (byte aún no confirmado). Por ahora sólo log; avisar al solicitante luego.
            log(f"[FF40] uid={self.user_id} respondió solicitud action=0x{action:02X} de uid={requester_uid} "
                f"(NO-aceptar; byte no confirmado) -> solo log")
            return
        if not requester:
            log(f"[FF40] uid={self.user_id} aceptó solicitud pero requester uid={requester_uid} no existe")
            return
        # alta MUTUA
        db_add_friend(self.user_id, requester_uid)
        db_add_friend(requester_uid, self.user_id)
        me = db_get_char(self.user_id)
        my_nick = me["nickname"] if me else f"P{self.user_id}"
        req_nick = requester["nickname"]
        # al que ACEPTA (self): aparece el solicitante en su lista
        self.send_packet(0x40, self._ff40_result(1, requester_uid, req_nick))
        # al SOLICITANTE (si online): aparece el que aceptó en su lista
        rconn = CONNECTIONS.get(requester_uid)
        if rconn is not None:
            rconn.send_packet(0x40, self._ff40_result(1, self.user_id, my_nick))
        log(f"[FF40] uid={self.user_id} ({my_nick!r}) ACEPTÓ a uid={requester_uid} ({req_nick!r}) "
            f"-> alta mutua + 0x01 action=1 a ambos ({'req online' if rconn else 'req offline'})")

    def friend_delete(self, tuid):
        """C2S 0x1F [u32 uid][u8 0] = eliminar amigo. Quita la amistad MUTUA de la DB y manda a AMBOS
        un S2C 0x1E type=6 (quita al otro de la lista, handler 0x574350). Confirmado en vivo."""
        ch = db_get_char(tuid)
        nick = ch["nickname"] if ch else str(tuid)
        db_del_friend(self.user_id, tuid)
        db_del_friend(tuid, self.user_id)
        # a mí: quitar a tuid de mi lista (S2C 0x1F flag=1)
        self.send_packet(0x40, self._ff40_friend_remove(tuid))
        # al otro (si online): quitarme a mí de su lista
        tconn = CONNECTIONS.get(tuid)
        if tconn is not None:
            tconn.send_packet(0x40, self._ff40_friend_remove(self.user_id))
        log(f"[FF40] uid={self.user_id} ELIMINÓ amigo {nick!r} (uid={tuid}) -> S2C 0x1F flag=1 a ambos")

    def send_profile(self, tuid):
        """C2S 0x06 = pedir perfil de uid. Responde S2C 0x06 (layout CONFIRMADO):
        [u8=1][u32 uid][str8 nick][u32 exp][u16 mission][u16 channel][u16 room=FFFF][u32 0][u32 0]."""
        ch = db_get_char(tuid)
        if not ch:
            log(f"[FF40] uid={self.user_id} pidió perfil de uid={tuid} -> no existe")
            return
        n = enc_text(ch["nickname"])[:0xFF]
        exp = (ch["exp"] or 0) & 0xFFFFFFFF
        level = level_from_exp(exp)                       # nivel numérico real (tabla 0x6E90B8)
        chan_idx, _lic, _tier = tier_from_exp(exp)        # 0=Estrella 1=Luna 2=Sol (servidor/canal por banda)
        # Layout S2C 0x06: [u8=1][u32 uid][str8 nick][u32 exp][u16 C][u16 D][u16 E][u32 F][u32 G]
        #  · C = NIVEL: el cliente CAPEA el nivel (exp->0x55209 ≈ nivel 30) SI C < 16 (gate 0x58C0DC).
        #    Mandando C = nivel real (≥16 para altos) se DESACTIVA el cap -> muestra el nivel completo.
        #  · D = canal/servidor (Estrella/Luna/Sol), E = ubicación (0xFFFF = vestíbulo/lobby). [EXPERIMENTAL]
        b = bytes([0x06, 1]) + struct.pack("<I", tuid & 0xFFFFFFFF) + bytes([len(n)]) + n
        b += struct.pack("<IHHHII", exp, level & 0xFFFF, chan_idx & 0xFFFF, 0xFFFF, 0, 0)
        self.send_packet(0x40, b)
        log(f"[FF40] uid={self.user_id} pidió perfil de {ch['nickname']!r} (uid={tuid}) "
            f"-> S2C 0x06 (nivel={level} banda={_tier})")

    def whisper_send(self, p):
        """C2S 0x04 = susurro 1:1. Formato: [u32 uid][u16 len][msg]. Reenvía S2C 0x0F al destino + eco al emisor."""
        if len(p) < 6:
            return
        tuid = struct.unpack_from("<I", p, 0)[0]
        mlen = struct.unpack_from("<H", p, 4)[0]
        msg = p[6:6 + mlen].decode(CLIENT_CODEPAGE, "replace")
        me = db_get_char(self.user_id)
        my_nick = me["nickname"] if me else f"P{self.user_id}"
        target = db_get_char(tuid)
        tconn = CONNECTIONS.get(tuid)
        if tconn is None:
            self.send_packet(0x40, self._ff40_whisper_line(
                f"'{target['nickname'] if target else tuid}' no está conectado.", subflag=3))
            return
        # al destino: susurro recibido (0x0F) con el nick del emisor. NO se eco al emisor: el cliente
        #   YA dibuja localmente el susurro que vos mandás -> un eco del server lo DUPLICABA (se veía
        #   la línea local + la del eco). Confirmado en vivo 2026-07-14. Solo forward al destinatario.
        tconn.send_packet(0x40, self._ff40_whisper(my_nick, msg))
        log(f"[FF40] uid={self.user_id} ({my_nick!r}) susurró a uid={tuid}: {msg!r}")

    def _ff40_whisper(self, sender, message, color=0):
        """S2C 0x0F = susurro recibido (handler 0x58D1CD -> 0x45C430). Layout REAL (disasm):
        [u8 mode][str8 sender][str8 msg][u8 color]. ★ El byte de MODO iba FALTANDO en el mapeo previo:
        mode=0 = display coloreado normal (paleta 0x6F4188[color]); mode=1/2 = notices de sistema
        (str#0x1D7/0x1FB en rojo). Sin el byte de modo, el 1er byte (len del nick) caía como modo inválido
        -> no-op -> no renderizaba. Confirmado en vivo 2026-07-14."""
        s = enc_text(sender)[:0xFF]
        m = enc_text(message)[:0xFF]
        return bytes([0x0F, 0x00, len(s)]) + s + bytes([len(m)]) + m + bytes([color & 0xFF])

    def _ff40_friend_remove(self, uid):
        """S2C 0x1F = QUITAR amigo de la lista por uid. Arm 0x58B448 (CONFIRMADO): lee [u8 flag][u32 uid]
        (★orden INVERTIDO vs el C2S 0x1F que es [u32 uid][u8 0]); flag=1 ó 3 -> 0x42E470(friend-mgr,uid)
        = remove-by-uid del vector [mgr+0x60] + reindexa UI + dec [mgr+0xA7]. Ver FF40_MAPEO_AGENTE_v2.md.
        (El 0x1E type=6 anterior NO servía: era un builder cMessenger circle, no borraba local.)"""
        return bytes([0x1F, 0x01]) + struct.pack("<I", uid & 0xFFFFFFFF)

    def _ff40_result(self, action, uid=0, nick=""):
        """S2C 0x01 = RESULTADO de una operación de amistad (handler cliente 0x58B739). Primer byte=action.
        action=1 => ALTA COMPLETA con ficha ([u8=1][u32 uid][str8 nick] -> 0x42FD10 inserta + popup str#0x69).
        action=2 => add-si-ausente por nick. action=9 => error con nick. 3/4/8/otro => popup de error."""
        n = enc_text(nick)[:0xFF]
        if action == 1:
            return bytes([0x01, 1]) + struct.pack("<I", uid & 0xFFFFFFFF) + bytes([len(n)]) + n
        if action == 2:
            return bytes([0x01, 2, len(n)]) + n
        if action == 9:
            return bytes([0x01, 9, len(n)]) + n
        return bytes([0x01, action & 0xFF])

    def _ff40_friend_event(self, uid, nick, eventtype=1):
        """S2C 0x1D = evento async de amistad (handler cliente 0x58B49B, sub-switch 0x58DAF8).
        Layout: [u32 uid][u8 eventtype][str8 nick]. eventtype=1 => INSERTA amigo en el destinatario (0x42E3C0)."""
        n = enc_text(nick)[:0xFF]
        return bytes([0x1D]) + struct.pack("<I", uid & 0xFFFFFFFF) + bytes([eventtype & 0xFF, len(n)]) + n

    def _ff40_couple(self, sub2, uid=0, name="AMORTEST", code=0):
        """S2C FF40 sub-0x0A (familia COUPLE), sub-dispatcher cliente 0x58C3EB (tabla 0x58DB1C, sub2 1..18).
        Layouts byte-a-byte del mapeo estático (COUPLE_MAPEO_AGENTE.md Q1). name=nombre a mostrar."""
        n = enc_text(name)[:0xFF]
        idb = struct.pack("<I", uid & 0xFFFFFFFF)
        s = sub2 & 0xFF
        if s == 2:      # [0x02][u8 a=0][u8 code](+str8 name si code in 0,5). str#0x8C..0x94 (result/error)
            b = bytes([0x0A, 0x02, 0x00, code & 0xFF])
            return b + (bytes([len(n)]) + n if code in (0, 5) else b"")
        if s == 4:      # [0x04][u32 id][str8 name][u64 a][u32 b] = "casados"/roster (str#0x99)
            return bytes([0x0A, 0x04]) + idb + bytes([len(n)]) + n + struct.pack("<QI", 0, 0)
        if s == 8:      # [0x08][u32 id][str8 name][u32 b][u64 a][u32 c] (variante de 4, str#0x9A)
            return bytes([0x0A, 0x08]) + idb + bytes([len(n)]) + n + struct.pack("<IQI", 0, 0, 0)
        if s == 6:      # [0x06][u32 id][str8 name][u32 extra] (update lista, sin popup)
            return bytes([0x0A, 0x06]) + idb + bytes([len(n)]) + n + struct.pack("<I", 0)
        if s in (7, 10, 11, 13, 16, 17, 18):  # [sub2][u32 id] (por-id: remove/lookup)
            return bytes([0x0A, s]) + idb
        if s == 14:     # [0x0E][u32 idA][u32 idB][u8 sel][u32 v] (ranking/intimidad)
            return bytes([0x0A, 0x0E]) + idb + idb + bytes([0]) + struct.pack("<I", 0)
        if s == 15:     # [0x0F][u32 id1][u32 id2][u8 flag][str8 name] (str#0x88)
            return bytes([0x0A, 0x0F]) + idb + struct.pack("<I", (uid + 1) & 0xFFFFFFFF) + bytes([0, len(n)]) + n
        if s == 12:     # [0x0C][u8 a] (clear/reset, str#0x98)
            return bytes([0x0A, 0x0C, 0x00])
        # sub2 in (1,3,5): [sub2][u8 flag][u32 id][str8 name] (insert record + popup name)
        if s == 5:      # sub2=5 no lleva flag: [0x05][u32 id][str8 name]
            return bytes([0x0A, 0x05]) + idb + bytes([len(n)]) + n
        return bytes([0x0A, s, 0x01]) + idb + bytes([len(n)]) + n

    def _ff48_couple_room(self, sub, partner_name):
        """S2C COMANDO 0x48 (dec 72) = evento couple-EN-SALA. Dispatcher cliente **0x579E50** (en
        conn+0x17BC8 -> cmd (0x17BC8-0x17748)/0x10 = 0x48). ⚠ OJO: 0x579D70 (cmd 0x47) NO es esto,
        es "DelCH-R" (verificado en vivo: el handler corría pero no dispatchaba). El dispatcher real es
        0x579E50, cmd 0x48. Layout: [sub:u8][nameLen:u8][name] (el sub es el 1er byte, sin flag previo).
        sub: 6=ENTER (0x531300 adorna al ocupante-partner por nombre, occ+0x1D4=0xFE), 2=OUT (0x531380),
        5=BREAK (0x531340, occ+0x1D4=0xFF), 0/1=set-estado. ENTER adorna con SÓLO el nombre (no usa el
        gate 0x455130). Gate del ENTER: [0x766628]+0x6268==[0x766618] (contenedor-couple==sala actual)."""
        nb = enc_text(partner_name)[:0xFF]
        return bytes([sub & 0xFF, len(nb)]) + nb

    def _ff40_friend_card(self, row, result=1):
        """S2C 0x02 = ficha de amigo / alta-amigo OK (handler cliente 0x58B968 -> friend mgr [0x766648]).
        Layout: [u8 result][str8 nick][u32 uid][u16 a][u16 b][u16 room][u32 exp][u32 z][u8 c][u8 d].
        Campos intermedios (nivel/canal/estado) aún sin confirmar byte-a-byte -> 0; room=0xFFFF (no en sala)."""
        n = enc_text(row["nickname"])[:0xFF]
        b = bytearray([0x02, result & 0xFF])
        b.append(len(n)); b += n
        b += struct.pack("<I", row["uid"])
        b += struct.pack("<HHH", 0, 0, 0xFFFF)
        b += struct.pack("<II", (row["exp"] or 0) & 0xFFFFFFFF, 0)
        b += bytes([0, 0])
        return bytes(b)

    def _ff40_whisper_line(self, msg, uid=0, subflag=2):
        """S2C 0x05 = línea de sistema / estado (handler 0x58BEC9 -> 0x45C430). subflag 2/3 = rojo/sistema.
        Layout: [u8 subflag][u32 uid][str8 msg]. Para avisos de error del sistema de amigos/susurro."""
        m = enc_text(msg)[:0xFF]
        b = bytearray([0x05, subflag & 0xFF])
        b += struct.pack("<I", uid & 0xFFFFFFFF)
        b.append(len(m)); b += m
        return bytes(b)

    def send_friend_list(self):
        """S2C 0x03 = lista COMPLETA de amigos (online+offline). Layout por entrada:
        [u32 uid][u8 a][u32 b(exp)][str8 nick][u8 c][u16 d(location)][u8 e].
        ★ DOS campos deciden cosas distintas (docs/FF40_ESTADO_ONLINE.md):
          · `a` (+0x3D) = COLOR del nombre (1=verde online, 0=gris/blanco offline).
          · `d` (u16 location, +0x26) = SECCIÓN: ==0xFFFF -> DESCONECTADOS, ≠0xFFFF -> CONECTADOS
            (un finalizador 0x4300BC pone entry+0x24 = (loc==0xFFFF?0:1), que es lo que agrupa el render)."""
        rows = db_get_friends(self.user_id)
        b = bytearray([0x03])
        b += struct.pack("<H", len(rows))       # total
        b.append(len(rows) & 0xFF)              # count
        for r in rows:
            n = enc_text(r["nickname"])[:0xFF]
            online = r["uid"] in CONNECTIONS
            b += struct.pack("<I", r["uid"])
            b.append(1 if online else 0)                            # a = color (verde/gris)
            b += struct.pack("<I", (r["exp"] or 0) & 0xFFFFFFFF)    # b (exp)
            b.append(len(n)); b += n                                # nick
            b.append(0)                                             # c (descartado)
            b += struct.pack("<H", 0x0000 if online else 0xFFFF)    # d = location -> SECCIÓN
            b.append(0)                                             # e
        self.send_packet(0x40, bytes(b))

    def sync_all_friend_states(self):
        """Tras el bootstrap 0x03, manda un 0x04 por CADA amigo para fijar su SECCIÓN inicial correcta
        (Conectados/Desconectados). El 0x04 setea AMBOS u16 de location (0xFFFF=offline), cubriendo la
        ambigüedad +0x26/+0x28 que el 0x03 (un solo u16) no cubre -> el estado inicial es REAL, no el
        default "conectado" del constructor de ficha (0x42C8E0)."""
        for r in db_get_friends(self.user_id):
            online = r["uid"] in CONNECTIONS
            self.send_packet(0x40, self._ff40_friend_state(r["uid"], r["nickname"], online, (r["exp"] or 0)))

    def _ff40_friend_state(self, uid, nick, online, exp=0):
        """S2C 0x04 sub=1 = cambio de estado de UN amigo (handler 0x58BC4E, update-or-insert por NICK).
        Layout: [0x04][01][u32 uid][u8 a][u32 b][str8 nick][u16 f1][u16 f2][u8 0][u8 0].
        a = COLOR (1 online/0 offline). f1,f2 = location -> SECCIÓN (0xFFFF=desconectado). Seteo AMBOS u16
        a 0xFFFF/0x0000 porque el u16 exacto (+0x26 vs +0x28) no es 100% distinguible por estática."""
        n = enc_text(nick)[:0xFF]
        loc = 0x0000 if online else 0xFFFF
        return (bytes([0x04, 0x01]) + struct.pack("<I", uid & 0xFFFFFFFF) + bytes([1 if online else 0]) +
                struct.pack("<I", exp & 0xFFFFFFFF) + bytes([len(n)]) + n +
                struct.pack("<HH", loc, loc) + bytes([0, 0]))

    def notify_friends_status(self, online, reason=""):
        """Avisa a MIS amigos CONECTADOS que YO cambié de estado -> S2C 0x04 con a + location (0xFFFF offline),
        para que muevan mi entrada entre Conectados/Desconectados EN VIVO (el render re-secciona por entry+0x24).
        El caller llama DESPUÉS de actualizar CONNECTIONS (True tras agregarme, False tras el pop)."""
        me = db_get_char(self.user_id)
        if not me:
            return
        my_nick = me["nickname"]; my_exp = (me["exp"] or 0)
        sent = 0
        for r in db_get_friends(self.user_id):
            fconn = CONNECTIONS.get(r["uid"])
            if fconn is not None and fconn is not self:
                try:
                    fconn.send_packet(0x40, self._ff40_friend_state(self.user_id, my_nick, online, my_exp)); sent += 1
                except Exception as e:
                    log(f"[FF40] notify_status fallo a uid={r['uid']}: {e}")
        if sent:
            log(f"[FF40] uid={self.user_id} {reason} -> estado {'ONLINE' if online else 'OFFLINE'} a {sent} amigo(s)")
