# -*- coding: utf-8 -*-
"""La carrera y el cartel de resultados.

El premio se calcula ACA, del lado del servidor: el cliente manda
monedas=0 y el servidor decide cuanto le toca segun precision, combo y
puesto. Si lo calculara el cliente, cualquiera se pondria un millon.

--
Una pieza de LobbyProtocol. Se junta con las demas en lobby.py.
"""

import asyncio
import os
import time
import struct

from . import ajustes
from .ajustes import (
    CARTEL_MARGIN,
    CONNECTIONS,
    COUNTDOWN_EST,
    LOAD_TIMEOUT,
    RACE_MAX_SEC,
    ROOMS,
)
from .basedatos import db_add_exp_coin
from .canciones import (
    NPC_AUTOPLAY,
    NPC_MOVE,
    NPC_TRACK,
    SIM_RACE_PROFILES,
    npc_autoplay_score,
    npc_profile_for_slot,
    race_reward,
    song_base_coin,
)
from .progresion import LIBRE_MODO6, LIBRE_PORT
from .registro import log


class PiezaCarrera:
    """La carrera y el cartel de resultados."""

    def _spawn_npc_replays(self, rid):
        """★ FASE 2 — al arrancar la carrera, lanza un REPLAY de movimiento por cada NPC (ghost_slot): reproduce el
        track FF4E capturado como si el NPC estuviera corriendo -> los humanos lo VEN moverse en pista."""
        if not (NPC_MOVE and NPC_TRACK):
            return
        room = ROOMS.get(rid)
        if not room:
            return
        loop = asyncio.get_event_loop()
        ghosts = sorted(set(room.get("ghost_slots", []) or []))
        for i, slot in enumerate(ghosts):
            loop.create_task(self._npc_replay_task(rid, slot & 0x7F, offset=i * 0.15))
        if ghosts:
            log(f"[Lobby] NPC REPLAY: {len(ghosts)} NPC(s) corriendo en sala {rid} ({len(NPC_TRACK)} pasos c/u)")

    async def _npc_replay_task(self, rid, slot, offset=0.0):
        """Reproduce el track de movimiento capturado como el NPC en `slot`: emite FF4E a los humanos de la sala,
        re-cifrado por receptor (idéntico al relay FF4E real). offset = desfase inicial p/ que los NPC no vayan
        clavados iguales. Se detiene si la carrera termina. NOTA: por ahora replay VERBATIM del track (mapeo de
        slot a pulir según lo que se observe en pista)."""
        if offset:
            await asyncio.sleep(offset)
        for dt, case, a1, a2 in NPC_TRACK:
            if dt:
                await asyncio.sleep(min(dt, 3.0))
            room = ROOMS.get(rid)
            if not room or not room.get("game_started"):
                return   # carrera terminó -> parar el replay
            p2 = bytes([case & 0xFF]) + struct.pack("<I", a1) + struct.pack("<I", a2)  # noqa
            inner14 = bytearray(b"\xFF\x26" + struct.pack("<H", len(p2)) + p2 + b"\x00")   # = formato del relay FF4E
            done = room.get("reported") or room.get("finished_users") or set()
            for p in room.get("players", []):
                uid = p["uid"]
                if uid in done:
                    continue   # no mandar coords a un finalista/estatua (crash)
                conn = CONNECTIONS.get(uid)
                if conn is None or getattr(conn, "transport", None) is None:
                    continue
                key = (conn.send_counter ^ (conn.role_id + conn.mmd)) & 0xFFFFFFFF
                enc = bytearray(inner14)
                for i in (0, 4, 8):
                    struct.pack_into("<I", enc, i, (struct.unpack_from("<I", enc, i)[0] ^ key) & 0xFFFFFFFF)
                struct.pack_into("<H", enc, 12, (struct.unpack_from("<H", enc, 12)[0] ^ (key & 0xFFFF)) & 0xFFFF)
                conn.send_counter = (conn.send_counter + 1) & 0xFFFFFFFF
                try:
                    conn.transport.write(b"\xFF\x4E" + struct.pack("<H", 13) + bytes(enc[:13]))
                except Exception:
                    pass
        # ★ el NPC CRUZÓ LA META (fin de su track): si nadie disparó aún el contador de 5s, dispararlo -> la carrera
        #   concluye aunque el NPC haya llegado 1º (el humano ve el countdown y su "fallida" si no llega).
        self._npc_finished(rid)

    def _npc_finished(self, rid):
        """Un NPC terminó su recorrido. Si aún no arrancó el contador de fin (FF27), lo dispara para la sala
        (equivalente a un finalista real llegando 1º) + agenda el settlement del cartel."""
        room = ROOMS.get(rid)
        if not room or room.get("result_done") or room.get("countdown_started") or not room.get("game_started"):
            return
        room["countdown_started"] = True
        room["result_done"] = False
        room["result_players"] = list(room.get("players", []))
        for p in room["result_players"]:
            c = CONNECTIONS.get(p["uid"])
            if c is not None:
                try:
                    c.send_packet(0x27, b"")   # FF27 TIMEOVER -> contador 5→1 en los no-finalizados
                except Exception:
                    pass
        _fb = COUNTDOWN_EST + CARTEL_MARGIN
        asyncio.get_event_loop().call_later(_fb, lambda r=rid: self._room_game_result(r))
        log(f"[Lobby] NPC llegó a META (fin de replay) -> FF27 TIMEOVER a sala {rid}; fallback {_fb:.1f}s")

    def send_game_result(self):
        """Arma y envía el CARTEL DE RESULTADO (S→C FF28) tras el countdown. Estructura (guía, valores nuestros):
        [winteam:u8=0][count:u8] + por jugador: [pos:u8][rank:u8][rank_bonus:u16][exp:u16][combo:u16][pd:u8][star:u8]
        [coin:u16][u32=1000][frame:u32][u16=0]. Incluye al host (+ fantasma si activo, con score 'autoplay' ficticio).
        ⚠ FORMATO A VERIFICAR por RE del recv FF28 de nuestro cliente."""
        if getattr(self, "result_done", False):
            return
        self.result_done = True
        sc = getattr(self, "last_score", None) or {"frame": 0, "combo": 0, "pd": 0, "star": 0, "coin": 0}
        host_slot = getattr(self, "host_slot", 2) & 0x7F
        host_team = 1 if host_slot in (0, 1, 2) else 2   # rojo(1)=lado 0-2 / azul(2)=lado 3-5
        host_frame = max(1, sc.get("frame", 0) or 0)     # frame del host >0 (0 = trampa "instantáneo")
        # ★ host PRIMERO, rank=0 (=1er puesto, 0-BASED), frame MÁS BAJO. (RE a432ce07: el cartel no ordena; el podio
        #   resalta al equipo del header y respeta rank/frame. byte[0]=equipo del host -> el host GANA.)
        hsc = dict(sc); hsc["frame"] = host_frame
        players = [{"pos": host_slot, "score": hsc, "rank": 1}]   # 1-based: 1 = 1er puesto
        # un row por cada NPC: JUEGA el chart real (autoplay) -> combo/pd/star realistas. En este path de
        #   TEST el host gana (rank 0 = 1º), así que forzamos su frame DETRÁS del host (rank 1..6 = 2º..7º) pero con score propio.
        for i, gs in enumerate(sorted(getattr(self, "ghost_slots", []) or [])):
            if NPC_AUTOPLAY:
                gsc = npc_autoplay_score(getattr(self, "room_song", None), npc_profile_for_slot(gs), slot=gs)
                gsc["frame"] = host_frame + (i + 1) * 1000   # detrás del host (test path: host 1º)
            else:
                gsc = {"frame": host_frame + (i + 1) * 1000, "combo": sc.get("combo", 0),
                       "pd": sc.get("pd", 0), "star": sc.get("star", 0), "coin": 0}
            players.append({"pos": gs, "score": gsc, "rank": min(6, i + 2)})   # 1-based: 2..6 = 2º..6º
        # ★ byte[0] = EQUIPO GANADOR (el del host aquí; en sala multi lo decide _room_game_result por el rank1).
        self.send_packet(0x28, self._encode_result(host_team, players))
        self.countdown_started = False
        self.game_started = False
        _rid = getattr(self, "my_room_id", None)   # ★ fin de partida -> la sala vuelve a "esperando" en la lista (bit1=0)
        if _rid is not None and _rid in ROOMS:
            ROOMS[_rid]["game_started"] = False

    def _encode_result(self, win_team, players):
        """Codifica el cuerpo del CARTEL FF28: [winteam:u8][count:u8] + 23 bytes por jugador
        [pos][rank][rank_bonus:u16][exp:u16][combo:u16][pd][star][coin:u16][u32=1000][frame:u32][u16=0][pad:u8].
        Compartido por el resultado de SALA (multi) y el de test/single. El trailing pad hace 23B/fila (sin él el
        stream se desfasa 1B por jugador -> crash). byte[0]=equipo ganador (el cliente lo compara con SU equipo ->
        GANASTE/PERDISTE).
        ★★ 2026-08-01 — EL CAMPO `rank` ES 0-BASED (el cliente lo usa como ÍNDICE de la tabla de puestos):
           0=1º 1=2º 2=3º 3=4º 4=5º 5=6º 6=7º · 7=FALLIDO. Antes mandábamos 1..6 y TODO el cartel salía
           corrido un puesto ("2nd,3rd,...,7th" y nadie 1º). El "7 = FALLIDO" siempre fue el índice 8 de esa
           misma tabla — era el único dato consistente. frame 0xFFFFFFFF = DNF (no llegó a la meta)."""
        resp = bytearray()
        resp.append(win_team & 0xFF)
        # CABECERA EXTENDIDA DEL CANAL LIBRE (campoB==6)
        #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-cabecera-extendida-del-canal-libre-campob-6)
        if LIBRE_MODO6 and getattr(self, "port", 0) == LIBRE_PORT:
            resp += struct.pack("<II", 0, 0)
        resp.append(len(players) & 0xFF)
        for p in players:
            s = p["score"]
            frame = s.get("frame", 0) or 0; combo = s.get("combo", 0) or 0
            pd = s.get("pd", 0) or 0; star = s.get("star", 0) or 0; coin = s.get("coin", 0) or 0
            rank = p["rank"]
            # exp/rank_bonus del cartel: usar los YA calculados por race_reward (columnas "_exp"/"_bonus") si están;
            #   si no (path viejo/single sin reward), calcularlos como antes.
            exp = s.get("_exp", pd * 10 + combo + star)
            rank_bonus = s.get("_bonus", {1: 200, 2: 150, 3: 100}.get(rank, 50))   # rank 1-based (ver race_reward)
            resp.append(p["pos"] & 0xFF)
            resp.append(rank & 0xFF)
            resp += struct.pack("<H", rank_bonus & 0xFFFF)
            resp += struct.pack("<H", exp & 0xFFFF)
            resp += struct.pack("<H", combo & 0xFFFF)
            resp.append(pd & 0xFF)
            resp.append(star & 0xFF)
            resp += struct.pack("<H", coin & 0xFFFF)
            resp += struct.pack("<I", s.get("_basecoin", 1000) & 0xFFFFFFFF)   # d0 = rcoin BASE (el cliente lo suma al wallet)
            resp += struct.pack("<I", frame & 0xFFFFFFFF)       # frame
            resp += struct.pack("<H", 0)                        # inner_count M=0
            resp.append(0)                                      # trailing flag -> 23B/fila
        return bytes(resp)

    def _room_game_result(self, rid):
        """★ CARTEL DE RESULTADO MULTI (fase sync 2026-07-01) — se dispara a los ~5s del contador (FF27). UN SOLO
        cartel con TODOS: finalistas ordenados por frame (menor=1°, rank 0..6 = 1º..7º, 0-BASED); NO-finalistas =
        FALLIDO (rank 7, frame DNF); NPCs (autoplay) detrás. byte[0]=equipo del rank 0 (ganador). Broadcast a toda la sala. Modelo
        guía: settlear UNA vez (result_done) y SOLO tras el contador completo (mandarlo antes crashea al cliente)."""
        room = ROOMS.get(rid)
        if room is None or room.get("result_done"):
            return
        room["result_done"] = True
        # ★ REZAGADO SIN REPORTE al cierre (cuenta ~5s + CARTEL_MARGIN): NO se lo espera más -> DESCONECTAR su slot y armar
        #   el cartel SIN él. Así el cartel SIEMPRE queda con los que ENTREGARON su data (sea quien sea el que faltó).
        #   (En el caso normal "todos reportaron" no falta nadie -> no se kickea a nadie.)
        _expected = self._race_expected_uids(room)
        _reported = room.get("reported") or set()
        _missing = _expected - _reported
        if _missing:
            _snap0 = room.get("result_players") or room.get("players", [])
            for p in list(_snap0):
                if p["uid"] in _missing:
                    log(f"[Lobby] cartel: rezagado uid={p['uid']} NO reportó en {COUNTDOWN_EST}s+{CARTEL_MARGIN}s -> DESCONECTAR + fuera del cartel")
                    if room.get("result_players") is not None:
                        room["result_players"] = [q for q in room["result_players"] if q["uid"] != p["uid"]]
                    room["players"] = [q for q in room.get("players", []) if q["uid"] != p["uid"]]
                    c = CONNECTIONS.get(p["uid"])
                    if c is not None and getattr(c, "transport", None) is not None:
                        try: c.transport.close()   # dispara connection_lost -> limpieza; ya result_done -> no re-settlea
                        except Exception: pass
        snap = room.get("result_players") or room.get("players", [])
        scores = room.get("scores", {}) or {}
        finished = room.get("finished_users") or set()
        # ── CONTENDIENTES = humanos que cruzaron meta + NPCs (autoplay) COMPITEN JUNTOS por frame ──
        #   Cada uno aporta su frame de meta; se ordenan TODOS por frame ascendente (menor=más rápido=
        #   mejor puesto) y se asigna rank 0..6 (0-based = 1º..7º). Así un NPC bueno PUEDE ganarle a un humano lento
        #   -> "juega como un jugador de verdad". uid=None => NPC (no persiste premio en DB).
        contenders = []   # (frame, pos, score_dict, uid_or_None)
        for p in snap:
            if p["uid"] in finished and p["uid"] in scores:
                sc = scores[p["uid"]]
                contenders.append((sc.get("frame", 0) or 0, p["slot"] & 0x7F, dict(sc), p["uid"]))
        _sim = os.environ.get("R2_SIM_RACE", "0") == "1"   # DIAGNÓSTICO: NPCs con perfiles fijos (SIM_RACE_PROFILES)
        _sim_fail = []                                     # NPCs marcados DNF por el SIM -> van a rank 7
        for i, gs in enumerate(sorted(room.get("ghost_slots", []) or [])):
            if _sim and i < len(SIM_RACE_PROFILES):
                pr = SIM_RACE_PROFILES[i]
                gsc = {"frame": pr["frame"], "combo": pr["combo"], "pd": pr["pd"], "star": pr["star"], "coin": 0}
            elif NPC_AUTOPLAY:
                gsc = npc_autoplay_score(room.get("song_id"), npc_profile_for_slot(gs), slot=gs)  # ★ juega el chart real
            else:
                gsc = {"frame": 100000 + (i + 1) * 1000, "combo": 0, "pd": 0, "star": 0, "coin": 0}  # legacy: detrás, plano
            if gsc["frame"] == 0xFFFFFFFF:               # NPC fallido (solo SIM) -> se maneja como no-finalista
                _sim_fail.append((gs & 0x7F, gsc)); continue
            contenders.append((gsc["frame"], gs & 0x7F, gsc, None))
        contenders.sort(key=lambda x: x[0])              # por frame ascendente -> orden de llegada
        ranked = []
        # ★★★ 2026-08-01 (tarde) — REVERTIDO A 1-BASED. Hoy más temprano lo pasé a 0-based creyendo que los
        #   puestos salían corridos ("empezaban en 2nd"). ERROR MÍO: lo que estaba corrido era el DIBUJO, no el
        #   dato — en el canal Libre el cliente pinta CADA DÍGITO +1 (y el ':' como '0'), incluidos los ordinales.
        #   Probado con dos capturas de la MISMA canción: en Sol se ve `1ST 2ND 3RD 4TH 5TH`, en Libre `2ND 3RD
        #   4TH 5TH 6TH`, con el mismo paquete. Con 0-based el ganador salía "Ganador" y el 2º "1ST" = mal.
        #   ⇒ `rank` es 1-BASED: 1=1º … 6=6º · 7=FALLIDO. Ver el bug de sprites del canal Libre.
        for i, (frame, slot, sc, uid) in enumerate(contenders):
            entry = {"pos": slot, "rank": min(6, i + 1), "score": {**sc, "frame": max(1, frame)}}
            if uid is not None:
                entry["uid"] = uid                       # solo humanos persisten premio
            ranked.append(entry)
        # NO-finalistas humanos -> FALLIDO (rank 7, DNF). Score real si reportó fallo (premio de consuelo).
        for p in snap:
            if p["uid"] not in finished:
                sc = dict(scores.get(p["uid"], {"combo": 0, "pd": 0, "star": 0}))
                sc["frame"] = 0xFFFFFFFF; sc.setdefault("coin", 0)
                ranked.append({"pos": p["slot"] & 0x7F, "rank": 7, "uid": p["uid"], "score": sc})
        for slot, gsc in _sim_fail:                      # NPCs DNF del SIM
            ranked.append({"pos": slot, "rank": 7, "score": gsc})
        # ★★ TAREA 1 — RECOMPENSA: calcular exp/rcoin server-side, PERSISTIR en DB y poblar las columnas del cartel.
        rewards = {}   # uid -> (exp_ganado, coin_ganado, exp_total_db, coin_total_db)
        for r in ranked:
            uid = r.get("uid")
            if uid is None:
                continue
            dexp, _dcoin_perf, exp_show, bonus = race_reward(r["score"], r["rank"], finished=(r["rank"] != 7))
            # ★ RCOIN = SOLO el valor POR CANCIÓN (nº de notas/obstáculos × ajustes.COIN_PER_NOTE) × ajustes.COIN_RATE. SIN chunk de
            #   performance (pedido del usuario: "el base debería ser 0, el segundo valor [notas] es la base que sube
            #   x1/x1.5/x2"). Igual para todos los que TERMINAN; el que no finaliza (rank 7) = 0. El exp SÍ queda ligado
            #   al desempeño (race_reward). El campo d0 del cartel = 0.
            rcoin = int(round(song_base_coin(room.get("song_id")) * ajustes.COIN_RATE)) if r["rank"] != 7 else 0
            rcoin = min(rcoin, 0xFFFF)             # cabe en la columna coin:u16 (con rates normales)
            # ★ Columnas EXP/BONUS del cartel = YA CON EL RATE (lo que se ve = lo que se gana). Antes mostraban el base
            #   sin rate -> a x3 el cartel se veía igual que x1 aunque el nivel subía ×3. _exp+_bonus == dexp (persistido).
            _bonus_r = min(int(round(bonus * ajustes.EXP_RATE)), 0xFFFF)
            r["score"]["_bonus"] = _bonus_r        # columna BONUS por puesto (rateada)
            r["score"]["_exp"] = min(max(0, dexp - _bonus_r), 0xFFFF)   # columna EXP (rateada; suma con _bonus = dexp)
            r["score"]["coin"] = rcoin             # columna RCOIN = premio por canción × rate
            r["score"]["_basecoin"] = 0            # campo d0 (base) = 0
            exp_tot, coin_tot = db_add_exp_coin(uid, dexp, rcoin)   # el cliente suma coin(u16)+d0(0)=rcoin; persistir igual
            rewards[uid] = (dexp, rcoin, exp_tot, coin_tot)
        win_team = 1 if (ranked and ranked[0]["pos"] < 3) else (2 if ranked else 1)
        body = self._encode_result(win_team, ranked)
        # ★ LOG del cartel EXACTO (hex + filas decodificadas) para depurar tiempo/precisión/overlay
        log(f"[Cartel] rid={rid} win_team={win_team} n={len(ranked)} body={body.hex()}")
        for r in ranked:
            s = r["score"]
            log(f"[Cartel]   pos={r['pos']} rank={r['rank']} frame={s.get('frame')} combo={s.get('combo')} "
                f"pd={s.get('pd')} star={s.get('star')} exp={s.get('_exp')} bonus={s.get('_bonus')} coin={s.get('coin')}")
        # ★ Broadcast a TODOS los que quedan (post-kick del rezagado, todos ya reportaron -> frozen -> seguro a la vez).
        for p in (room.get("result_players") or room.get("players", [])):
            c = CONNECTIONS.get(p["uid"])
            if c is not None:
                try:
                    c.send_packet(0x28, body)
                    c.countdown_started = False; c.game_started = False
                    # ★ empujar el EXP nuevo (FF92) para que el nivel/barra refleje lo ganado sin re-loguear
                    rw = rewards.get(p["uid"])
                    if rw is not None:
                        c.send_packet(0x92, bytes([0]) + struct.pack("<I", rw[2] & 0xFFFFFFFF))
                except Exception as e:
                    log(f"[Lobby] cartel fallo a uid={p['uid']}: {e}")
        for uid, (dexp, dcoin, exp_tot, coin_tot) in rewards.items():
            log(f"[Lobby]   +recompensa uid={uid}: +{dexp} exp (->{exp_tot}) +{dcoin} rcoin (->{coin_tot})  [rate x{ajustes.EXP_RATE}/x{ajustes.COIN_RATE}]")
        room["game_started"] = False; room["countdown_started"] = False
        room["reported"] = set(); room["finished_users"] = set()   # ★ reset p/ la próxima carrera
        room["scores"] = {}; room["result_players"] = None
        room["_late_loaders"] = set()
        log(f"[Lobby] CARTEL MULTI sala {rid} -> ganador equipo {win_team}, {len(ranked)} filas (contendientes/finalistas={len(contenders)}, resto rank7)")
        # ★★ TAREA 3 — LIMPIEZA DIFERIDA: los que se fueron/crashearon DURANTE la carrera no se sincronizaron
        #   (para no tumbar a los demás). Ahora que terminó, recién limpiamos su avatar (FF0E) + líder (FF9B) + FF0C.
        self._flush_left_during_race(rid)

    def _race_expected_uids(self, room):
        """UIDs de los que DEBEN reportar (FF28) para settlear: snapshot de la carrera MENOS los que nunca cargaron
        (late-loaders forzados por timeout) — ésos no pueden mandar FF28, no deben bloquear el cartel."""
        base = {p["uid"] for p in (room.get("result_players") or room.get("players", []))}
        return base - (room.get("_late_loaders") or set())

    def _maybe_settle_after_leave(self, rid):
        """Tras una salida/crash EN CARRERA: re-evaluar si ya se puede mostrar el cartel. REGLA (usuario): si queda
        1 SOLO jugador, el sobreviviente termina y sale el cartel; con 2+ se sigue normal. En ambos casos, el que se
        fue ya NO está en `players`/`result_players` -> no bloquea `expected <= reported`."""
        room = ROOMS.get(rid)
        if not room or not room.get("game_started") or room.get("result_done"):
            return
        remaining = room.get("players", [])
        if not remaining:
            return
        expected = self._race_expected_uids(room)
        rep = room.get("reported") or set()
        # queda 1: si ya reportó (llegó/falló) -> cartel ya; si sigue corriendo, su propio FF28 lo gatilla luego.
        # 2+: sólo settlear si el contador arrancó y los PRESENTES ya reportaron (el que faltaba era el que se fue).
        ready = bool(expected) and expected <= rep and (len(remaining) <= 1 or room.get("countdown_started"))
        if ready:
            room["countdown_started"] = True
            asyncio.get_event_loop().call_later(0.3, lambda r=rid: self._room_game_result(r))
            log(f"[Lobby] settle tras salida en carrera -> cartel sala {rid} (quedan {len(remaining)}, presentes reportaron)")

    def _flush_left_during_race(self, rid):
        """★ TAREA 3 — LIDERAZGO DIFERIDO. El FF0E (vaciar slot) ya se mandó a media carrera. El FF9B (habilita la UI
        de anfitrión) NO se puede mandar apenas termina la carrera: el cliente está en la PANTALLA DE RESULTADO y recibir
        un FF9B/FF13 ahí lo CRASHEA (capturado: VM crasheó justo tras el cartel+FF9B). Se DIFIERE a cuando el nuevo líder
        vuelve a la SALA y PIDE FF13 (momento seguro; ver handler cmd==0x13). Acá sólo lo marcamos como pendiente."""
        room = ROOMS.get(rid)
        if not room or not room.pop("_defer_host_notify", False):
            return
        new_host = room.get("host_uid")
        if new_host in {p["uid"] for p in room.get("players", [])}:
            room["_pending_host_ff9b"] = new_host   # el FF9B se manda cuando ESE uid pida FF13 (de vuelta en la sala)
            log(f"[Lobby] liderazgo sala {rid}: nuevo líder uid={new_host} PENDIENTE de FF9B (al volver a la sala)")

    def _force_start_if_stuck(self, rid, magic):
        """★ TAREA 2 — TIMEOUT DE CARGA: si a los LOAD_TIMEOUT seg. no cargaron todos (alguien crasheó/lento), FORZAR
        el arranque (FF24 GO) a los presentes. Los que no cargaron van a `_late_loaders` -> no bloquean el settlement."""
        room = ROOMS.get(rid)
        if not room or room.get("game_started"):
            return
        if room.get("game_magic") != magic:
            return   # timer viejo (pertenece a otra partida ya reseteada)
        loaded = room.get("loaded") or set()
        real_uids = {p["uid"] for p in room.get("players", [])}
        missing = real_uids - loaded
        if not (loaded & real_uids):
            return   # nadie cargó todavía -> no forzar (evita GO al vacío); el próximo FF25 arrancará normal
        room["game_started"] = True
        room["_late_loaders"] = set(missing)
        go = 0
        # GO SOLO a los que YA cargaron (a los que faltan se les da GO individual cuando manden su FF25 tardío).
        for p in room.get("players", []):
            if p["uid"] not in loaded:
                continue
            c = CONNECTIONS.get(p["uid"])
            if c is not None:
                c.game_started = True; c._game_start_ts = time.monotonic()
                try: c.send_packet(0x24, b"\x00"); go += 1
                except Exception: pass
        log(f"[Lobby] ★ LOAD TIMEOUT {LOAD_TIMEOUT}s sala {rid}: FORZAR GO a {go} (cargaron {sorted(loaded & real_uids)}, "
            f"faltaron {sorted(missing)} -> no bloquean el cartel)")
        # fallback de settlement por si los presentes tampoco reportan (carrera colgada)
        asyncio.get_event_loop().call_later(RACE_MAX_SEC, lambda r=rid, m=magic: self._room_settle_fallback(r, m))

    def _room_settle_fallback(self, rid, magic):
        """Red de seguridad: si la carrera nunca settleó (todos colgados) -> forzar cartel al tope de tiempo."""
        room = ROOMS.get(rid)
        if not room or room.get("result_done") or not room.get("game_started"):
            return
        if room.get("game_magic") != magic:
            return
        log(f"[Lobby] ★ RACE_MAX {RACE_MAX_SEC}s sala {rid}: settlement forzado (carrera colgada)")
        self._room_game_result(rid)
