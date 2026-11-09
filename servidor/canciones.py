# -*- coding: utf-8 -*-
"""Canciones, notas y premio de cada carrera.

Las notas de cada cancion viven en datos/notas/<id>.bin. El premio en
monedas se deriva de la CANTIDAD de notas, porque esta version del
juego no trae el premio por cancion.

--
Parte del servidor abierto de R2Beat. Ver LEEME.md en la raiz del proyecto.
"""

import json
import os
import random
import struct
import zlib

from . import ajustes
from .ajustes import (
    CARPETA_NOTAS, ENABLE_DEV, EXP_LOCK, RUTA_CANCIONES, RUTA_ESCENARIOS,
)
from .registro import log



# Lista de canciones del picker de SALA (id, nombre). Extraída de rnr_script/music_lt.txt
# (las 64 que el cliente carga client-side). Se empuja por FF78 (uno por canción) al entrar
# a la sala -> puebla [0x766618]+0xc158 (records 0x1B0) y rompe la "barra loca"/loop.
def _load_songs(path=None):
    """Carga la lista del picker (sid|nombre) desde songs.txt. EXTERNALIZADO del .py 2026-07-22:
    el importador agrega canciones a songs.txt (via aplicar_al_server.py) sin tocar este codigo.
    Formato: una linea 'sid|nombre' por cancion; '#' = comentario. 50-55 = tutorial (nombre vacio)."""
    if path is None:
        path = RUTA_CANCIONES
    out = []
    if os.path.exists(path):
        for _ln in open(path, encoding="utf-8"):
            _s = _ln.rstrip("\r\n")
            if not _s.strip() or _s.lstrip().startswith("#"):
                continue
            _sid, _sep, _name = _s.partition("|")
            try:
                out.append((int(_sid), _name))
            except ValueError:
                pass
    out.sort(key=lambda t: t[0])
    return out


SONGS = _load_songs()

# ESCENARIOS que el jugador "tiene". Se le mandan al cliente al entrar.
#
# ⚠⚠ ESTA LISTA TIENE QUE SALIR DEL CLIENTE, NO INVENTARSE.
#   El cliente arma su lista de escenarios al arrancar leyendo su propio
#   stage_lt.txt. Cuando le decimos "tenés el escenario N", él lo busca en esa
#   lista y le escribe una marca. Si N no está, la búsqueda devuelve un puntero
#   NULO y el cliente escribe ahí igual: **se cierra de golpe, sin ningún
#   mensaje de error**, apenas entrás al lobby.
#   (Pasó de verdad: la lista estaba escrita a mano con 80 escenarios de un
#   cliente que tenía mapas importados. Contra un cliente normal, de esos 80
#   había 64 que no existían, y el primero que no existía lo mataba.)
#   Por eso sale de datos/escenarios.txt, que genera herramientas/extraer_datos.py
#   a partir del cliente de cada uno.
def _load_stages(path=None):
    """Lee los números de escenario, uno por línea, de datos/escenarios.txt."""
    if path is None:
        path = RUTA_ESCENARIOS
    out = []
    if os.path.exists(path):
        for _ln in open(path, encoding="utf-8"):
            _s = _ln.strip()
            if not _s or _s.startswith("#"):
                continue
            try:
                out.append(int(_s.split("|")[0]))
            except ValueError:
                pass
    if not out:
        # Sin el archivo no inventamos nada: mejor sin escenarios que con uno
        # que el cliente no tenga (eso lo cierra). Se arregla corriendo
        # herramientas/extraer_datos.py.
        log("[escenarios] falta %s -> mando la lista VACÍA. "
            "Corré herramientas/extraer_datos.py para generarla." % path)
    return sorted(set(out))


STAGES = _load_stages()

# Usuario de prueba: tiene CREDENCIALES pero NO personaje, para ejercitar la compuerta de creación.
SAMPLE_USER = "ruii"
SAMPLE_PASS_HASH = "E5C6D666CB418157A097E2B7E5E89A17F4E68535AD844C41D1E3051A3F037C07"  # SHA-256("1234a")

# DIAGNÓSTICO (R2_SIM_RACE=1): perfiles de NPC que imitan una carrera real de 6 (mezcla de f...
#   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#canciones-diagn-stico-r2-sim-race-1-perfiles-de-np)
SIM_RACE_PROFILES = [
    {"frame": 3200,       "combo": 210, "pd": 98, "star": 92, "rank": 2},   # finalista fuerte
    {"frame": 0xFFFFFFFF, "combo": 54,  "pd": 45, "star": 30, "rank": 7},   # JUGÓ pero cortado (fallida CON stats)
    {"frame": 5800,       "combo": 140, "pd": 80, "star": 55, "rank": 3},   # finalista medio
    {"frame": 0xFFFFFFFF, "combo": 0,   "pd": 0,  "star": 0,  "rank": 7},   # congelado desde el arranque
    {"frame": 7200,       "combo": 95,  "pd": 70, "star": 40, "rank": 4},   # finalista lento
]

# ══════════════════════════════════════════════════════════════════════════════
#   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#canciones)
NPC_AUTOPLAY = os.environ.get("R2_NPC_AUTOPLAY", "0") != "0"   # off por defecto; =1 vuelve al score simulado
DEFAULT_NPC_NOTES = 300                                        # fallback si la canción no tiene notes/{id}.bin

# Roster de NPCs (espejo del npc.xml del R2S). speed/accuracy/stability en escala 0..10.
# Se asigna por slot de forma determinista -> cada slot es un "rival" con estilo propio.
NPC_ROSTER = [
    {"name": "Rei",  "speed": 9, "accuracy": 9, "stability": 8},
    {"name": "Kai",  "speed": 7, "accuracy": 7, "stability": 6},
    {"name": "Sora", "speed": 8, "accuracy": 6, "stability": 5},
    {"name": "Mei",  "speed": 6, "accuracy": 8, "stability": 7},
    {"name": "Jin",  "speed": 5, "accuracy": 5, "stability": 4},
    {"name": "Yuki", "speed": 4, "accuracy": 6, "stability": 6},
]

def npc_profile_for_slot(slot):
    """Perfil (nombre + speed/accuracy/stability) del NPC en `slot`, determinista."""
    return NPC_ROSTER[(slot or 0) % len(NPC_ROSTER)]

def npc_autoplay_score(song_id, profile, slot=0):
    """Simula que el NPC JUEGA el chart real de la canción -> score final {frame,combo,pd,star,coin}.
    Coherente con el nº de notas (pd/combo/star <= n, igual que el clamp anti-cheat) y con el perfil
    (0..10). Determinista por (song_id, slot) -> sin depender de reloj/aleatoriedad global."""
    n = song_note_count(song_id) or DEFAULT_NPC_NOTES
    rng = random.Random(((int(song_id) if song_id else 0) << 8) ^ (int(slot) & 0xFF))
    acc  = max(0.0, min(1.0, profile.get("accuracy", 7) / 10.0))
    stab = max(0.0, min(1.0, profile.get("stability", 6) / 10.0))
    spd  = max(0.0, min(1.0, profile.get("speed", 7) / 10.0))
    # aciertos ~ accuracy, con varianza inversa a stability (menos estable = falla más irregular)
    hit_ratio = max(0.0, min(1.0, acc - rng.uniform(0.0, (1.0 - stab) * 0.18)))
    hits  = int(round(n * hit_ratio))
    # pd = PRECISIÓN en % (0..99), igual que el summary del cliente (juez per-nota (10-|delta|)*10, TOPE 99).
    #   NO es un conteo -> por eso antes se pasaba de 99. Buen jugador ~90-97; flojo ~75-85.
    pd    = max(40, min(99, int(round((0.70 + 0.28 * acc) * 100 - rng.uniform(0.0, (1.0 - stab) * 12)))))
    combo = min(n, int(round(hits * (0.40 + 0.60 * stab))))    # combo MÁX (conteo): alta stability -> cadena larga
    # ★★ 2026-08-01 — `star` NO es un conteo sobre n: es una escala 0..100 como la que reportan los humanos
    #   (en prod los reales dan 80..100). Antes era `min(n, hits*…)` -> con canciones largas daba 97/115/146/236
    #   (medido en el cartel del 17:13) y encima el campo se empaqueta en **u8** -> con n grande se DABA VUELTA.
    star  = max(0, min(100, int(round(hit_ratio * (70 + 30 * acc)))))
    # frame de META (cruza la línea): menor = más rápido = mejor puesto. Base - speed - desempeño + jitter.
    frame = int(6000 - spd * 2500 - hit_ratio * 1500 + rng.randint(0, 400 + int((1.0 - stab) * 600)))
    return {"frame": max(1, frame), "combo": combo, "pd": pd, "star": star, "coin": 0}

# ── FF4E MOVEMENT TRACK (fase 2) — movimiento REAL capturado (raw, ya sin magic) para el RE...
#   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#canciones-ff4e-movement-track-fase-2-movimiento-re)
NPC_MOVE = os.environ.get("R2_NPC_MOVE", "0") != "0"   # emitir movimiento (FF4E) por los NPC; R2_NPC_MOVE=1 lo enciende
def _track_secs(ts):
    h, m, s = ts.split(":"); return int(h) * 3600 + int(m) * 60 + float(s)
def load_ff4e_track(path=None):
    """Carga el track de movimiento (packets/ff4e_track.jsonl) = lista de (dt, case, a1, a2) con timing RELATIVO
    (dt = seg desde el paso anterior). Lo grabó el flag R2_FF4E_CAPTURE ya descifrado/sin magic -> reproducible."""
    path = path or os.path.join("packets", "ff4e_track.jsonl")
    steps = []; prev = None
    try:
        for line in open(path, encoding="utf-8"):
            o = json.loads(line)
            cur = _track_secs(o["t"])
            dt = 0.0 if prev is None else max(0.0, cur - prev)
            prev = cur
            steps.append((dt, int(o["case"]) & 0xFF, int(o["a1"]) & 0xFFFFFFFF, int(o["a2"]) & 0xFFFFFFFF))
    except Exception:
        pass
    return steps
# ★ Solo se CARGA en DEV (R2_DEV=1). NPC_TRACK solo lo usa el replay de `.npc`/SALA TEST (rutas DEV);
#   en producción (R2_DEV=0) NO cargar packets/ff4e_track.jsonl -> ese archivo crece con la captura
#   (R2_FF4E_CAPTURE) y son cientos de MB -> parsearlo a RAM disparaba ~450M de RSS. La captura sigue
#   apilando el dataset (uso futuro NPC); acá simplemente no lo releemos en prod.
NPC_TRACK = load_ff4e_track() if ENABLE_DEV else []

def race_reward(score, rank, finished):
    """★ EXP y RCOIN GANADOS en una carrera — SERVER-SIDE (el cliente manda coin=0). Ligado a judgment(pd)/
    combo/estrellas + bonus por puesto, escalado por ajustes.EXP_RATE/ajustes.COIN_RATE. ★ rank 0-BASED: 0..6 = 1º..7º
    (7=DNF/no-finalista). Devuelve (exp_total, coin_total, exp_show, rank_bonus): exp_total/coin_total se
    PERSISTEN; exp_show/rank_bonus van en las columnas del cartel FF28 (el cliente los muestra por separado)."""
    combo = int(score.get("combo", 0) or 0); pd = int(score.get("pd", 0) or 0); star = int(score.get("star", 0) or 0)
    # ⚠ Este dict va ACOPLADO a la base del `rank` que se manda en el cartel: si se cambia una, hay que cambiar
    #   la otra o el bonus del podio le toca al equivocado. rank es 1-BASED (1=1º 2=2º 3=3º).
    rank_bonus = ({1: 200, 2: 150, 3: 100}.get(rank, 50)) if finished else 0
    exp_show  = (pd * 10 + combo + star) if finished else (pd * 5 + combo // 2)   # exp base (columna del cartel)
    base_coin = (pd * 2 + combo + star * 3 + rank_bonus)                          # rcoin ~ obstáculos/combo/puesto
    exp_total  = int(round((exp_show + rank_bonus) * ajustes.EXP_RATE))                   # exp que se ACUMULA (base + bonus)
    coin_total = int(round(base_coin * ajustes.COIN_RATE))
    if EXP_LOCK:
        exp_total = 0        # ★ candado: además de no persistir, el cartel muestra 0 (honesto)
    return exp_total, coin_total, exp_show, rank_bonus

_NOTE_COUNT_CACHE = {}   # song_id -> nº de notas (cache lazy; -1 = sin datos)

def song_note_count(song_id, notes_dir=CARPETA_NOTAS):
    """Nº de NOTAS (obstáculos) de una canción, leyendo notes/{id}.bin. Formato (ver load_notes):
    [count:int32] + count×8B (bloque A timeline/BPM) + zlib(notas de 6B). n_notas = len(inflate(bloqueB))//6.
    Cache lazy. Devuelve None si no hay archivo o falla el parseo."""
    if song_id in _NOTE_COUNT_CACHE:
        n = _NOTE_COUNT_CACHE[song_id]
        return None if n < 0 else n
    n = -1
    try:
        path = os.path.join(notes_dir, f"{song_id}.bin")
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
            cnt = struct.unpack_from("<i", data, 0)[0]           # bloque A: count de markers timeline
            off = 4 + max(0, cnt) * 8                            # saltar bloque A (8B por marker)
            notes = zlib.decompress(data[off:])                 # bloque B comprimido = notas de 6B
            n = len(notes) // 6
    except Exception:
        n = -1
    _NOTE_COUNT_CACHE[song_id] = n
    return None if n < 0 else n

def song_base_coin(song_id):
    """rcoin BASE por canción = nº de notas (obstáculos) × ajustes.COIN_PER_NOTE. El original traía el premio por canción;
    como el 2013 Latino lo tiene en 0, lo derivamos de la cantidad de notas (más largas/difíciles pagan más).
    Fallback al base plano ajustes.BASE_COIN si la canción no tiene notas."""
    n = song_note_count(song_id) if song_id is not None else None
    return int(round(n * ajustes.COIN_PER_NOTE)) if n else ajustes.BASE_COIN



def load_notes(song_id, notes_dir=CARPETA_NOTAS):
    """Carga el BLOB FF57 (CmdGetNoteData) de una canción. notes/{songid}.bin contiene el blob
    COMPLETO que el parser cliente 0x4AFCE0 espera (verificado por RE del binario 3.5MB):
        [count:int32] + count×([marker:int32][bpm:float32])   <- Bloque A timeline/BPM, SIN comprimir
        zlib(notas de 6 bytes [Coord:u16][Kind:u8][Level:u8][Fx:u16])  <- Bloque B, COMPRIMIDO
    El handler FF57 envía [0x01][songid:u16][len:u16][blob]. ⚠ El cliente NO descomprime el Bloque A
    (lee count=*(int32) crudo); solo infla el Bloque B. Mandar solo zlib(notas) (sin Bloque A) hacía
    que leyera el header zlib como count -> std::map vacío -> crash 0xC0000417. Generado del mod
    offline (BPM de <BPM Frame/BPM>, notas de <AREA>). 38 canciones."""
    path = os.path.join(notes_dir, f"{song_id}.bin")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    return None
