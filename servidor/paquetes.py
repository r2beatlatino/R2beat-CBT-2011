# -*- coding: utf-8 -*-
"""Leer y armar los paquetes del protocolo.

Cada mensaje entre cliente y servidor es un comando 'FFxx'. Aca estan
las funciones que interpretan los que llegan y arman los que salen.
La lista completa de comandos esta en docs/PROTOCOLO.md.

--
Parte del servidor abierto de R2Beat. Ver LEEME.md en la raiz del proyecto.
"""

import time
import socket
import struct

from .ajustes import CLIENT_CODEPAGE, PERMANENT_ENDTIME
from .basedatos import db_get_couple
from .canciones import SONGS, STAGES
from .catalogo import db_inv_list, item_slot, pack_date
from .cifrado import enc_text
from .progresion import MINIMAL_PICKERS
from .registro import log


def parse_ff55_commands(dec):
    """Extrae (inner_cmd, data) de los bytes FF55 descifrados. FE -> 0x100|cmd. Salta el relleno FF56."""
    cmds = []; i = 0
    while i < len(dec) - 3:
        prefix = dec[i]
        if prefix not in (0xFF, 0xFE):
            i += 1; continue
        cmd = dec[i + 1]
        if prefix == 0xFF and cmd == 0x56:
            if i + 4 > len(dec): break
            ln = struct.unpack("<H", dec[i + 2:i + 4])[0]; i += 4 + ln
        else:
            if i + 4 > len(dec): break
            ln = struct.unpack("<H", dec[i + 2:i + 4])[0]
            if i + 4 + ln > len(dec): break
            cmds.append((cmd | (0x100 if prefix == 0xFE else 0), dec[i + 4:i + 4 + ln]))
            i += 4 + ln
    return cmds

def parse_login(payload):
    """[uid:4][flag:1][userlen:1][user][passlen:1][pass_hash_hex(64)]"""
    if len(payload) < 7:
        return None
    ulen = payload[5]; off = 6
    user = payload[off:off + ulen]; off += ulen
    if off >= len(payload):
        return None
    plen = payload[off]; off += 1
    ph = payload[off:off + plen]
    try:
        return {"username": user.decode("ascii"), "pass_hash": ph.decode("ascii"), "flag": payload[4]}
    except UnicodeDecodeError:
        return None

def parse_register(payload):
    """[userlen:1][user][hashlen:1][hash_hex_64]"""
    if len(payload) < 2:
        return None, None
    ulen = payload[0]; off = 1
    user = payload[off:off + ulen]; off += ulen
    if off >= len(payload):
        return None, None
    hlen = payload[off]; off += 1
    h = payload[off:off + hlen]
    try:
        return user.decode("ascii"), h.decode("ascii")
    except UnicodeDecodeError:
        return None, None

def parse_create_char(data):
    """
    CREATE_CHAR (inner FF04) que MANDA el cliente. El layout no se adivinó: se
    confirmó capturando una creación de personaje real y leyendo los bytes.
        [4] char_id (u32 LE)
        [1] nameLen
        [n] name
        [1] partCount            (normalmente 4)
        partCount × [4] part_id  (orden: pelo, ropa_sup, ropa_inf, patines)
    Devuelve dict o None. NOTA: el sexo/gender NO viaja como campo propio aquí — va implícito
    en char_id o en una part_id (pendiente de confirmar con disasm del emisor; ver TODO abajo).
    """
    try:
        if len(data) < 6:
            return None
        char_id = struct.unpack("<I", data[0:4])[0]
        nlen = data[4]; off = 5
        name = data[off:off + nlen]; off += nlen
        if off >= len(data):
            return None
        pcount = data[off]; off += 1
        parts = []
        for _ in range(pcount):
            if off + 4 > len(data):
                break
            parts.append(struct.unpack("<I", data[off:off + 4])[0]); off += 4
        # mapear las 4 partes esperadas (pelo/sup/inf/patines)
        hair, top, bottom, skates = (parts + [0, 0, 0, 0])[:4]
        return {"char_id": char_id, "name": name.decode(CLIENT_CODEPAGE, "replace"),
                "parts": parts, "hair": hair, "top": top, "bottom": bottom, "skates": skates}
    except Exception:
        return None

def _activar_keepalive(transport, quien=""):
    """★ SO_KEEPALIVE — arregla salas/jugadores FANTASMA y la fuga de descriptores.

    Sin esto, un cliente que desaparece EN SILENCIO (crash, corte de luz, wifi caído,
    notebook suspendida) deja su socket en ESTABLISHED **para siempre**: el kernel no
    tiene ningún temporizador para sockets que no pidieron keepalive. connection_lost
    nunca dispara -> leave_room nunca corre -> el char y su sala quedan de fantasma, y
    el descriptor no se libera (así llegamos a 976 sockets y al techo de 1024).

    Con esto: 60s de silencio -> 3 sondeos cada 10s -> muerto en ~90s -> el kernel cierra
    -> connection_lost -> leave_room, reusando toda la limpieza que YA existe.

    ★ Clave: los sondeos los responde el KERNEL del jugador, no el juego. Un AFK legítimo
    (quieto en el menú, sin mandar un solo paquete) sigue contestando y NO se lo patea.
    Por eso esto no reemplaza al _idle_watchdog de la carrera: ése ataca al cliente
    CONGELADO (proceso trabado pero máquina viva), que sí responde los sondeos.
    """
    try:
        sk = transport.get_extra_info("socket")
        if sk is None:
            return
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for _opt, _val in (("TCP_KEEPIDLE", 60), ("TCP_KEEPINTVL", 10), ("TCP_KEEPCNT", 3)):
            if hasattr(socket, _opt):
                sk.setsockopt(socket.IPPROTO_TCP, getattr(socket, _opt), _val)
    except Exception as _e:
        log(f"[keepalive] no se pudo activar en {quien}: {_e}")

# Inner-cmds del lobby con handler propio (los demás se loguean con ⚠ sin handler).
# Debe reflejar los casos de LobbyProtocol.handle_inner.
HANDLED_INNER = {0x00, 0x03, 0x04, 0x08, 0x45, 0x51, 0x06, 0x10, 0x11, 0x12, 0x13, 0x0C, 0x1C, 0x6E, 0x77, 0x78, 0x79,
                 0x3A, 0x25, 0x24, 0x57, 0xC8, 0x63, 0x28, 0x27, 0x8C, 0x97, 0x107, 0x3D, 0x1B, 0x87, 0xBD, 0x17, 0x18, 0x37, 0x4B, 0x23, 0x9B, 0x20, 0x2A, 0x1A, 0x40, 0xE2, 0x81, 0x49}

# FF8C = anti-tamper de los TIEMPOS de las 112 animaciones de "fallo" (실패)
#   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#paquetes-ff8c-anti-tamper-de-los-tiempos-de-las-11)
FF8C_FPS_PATTERN = [40, 40, 40, 40, 80, 80, 80, 80, 120, 120, 160, 160, 240, 320]
FF8C_ANIM_TIMES = FF8C_FPS_PATTERN * 8   # 112 floats

def build_ff8c():
    """[count:1=112][112 × float32 LE] con los FPS de las anims de fallo (BIT-EXACTO)."""
    body = bytearray([len(FF8C_ANIM_TIMES) & 0xFF])
    for v in FF8C_ANIM_TIMES:
        body += struct.pack("<f", float(v))
    return bytes(body)


# FF97 = SERVER_CLOCK
#   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#paquetes-ff97-server-clock)
def build_ff97(clock_value=None):
    """[len:1][string ascii decimal] — reloj del server que el cliente parsea con atoi."""
    if clock_value is None:
        clock_value = int(time.time() * 1000) & 0x7FFFFFFF   # tick ms (positivo)
    s = str(clock_value).encode("ascii")[:0xFF]
    return bytes([len(s)]) + s


def build_ff13_libre(name=b"Libre", status=0x09):
    """
    Payload de FF13 (channel-info, handler cliente 0x5780e0). 41+N bytes. RE completo:
    el byte de STATUS (offset 40+N) -> 0x52bbf0 -> [license+0x6d36]=status crudo + campoA.
    status NO en {1..7} (ej 0x09) => campoA 0xC = CANAL LIBRE => guard [0x6d98] libre =>
    selector de licencia en la sala de práctica. len(name) en off14 (debe coincidir; name<32).
    """
    if isinstance(name, str): name = name.encode("ascii", "replace")
    name = name[:31]; N = len(name)
    p  = struct.pack("<H", 0)            # off0-1: chan_id(11b)+tipo(5b)=0 (usa nombre string)
    p += bytes([0])                      # off2
    p += b"\x00"                         # off3 (no usado)
    p += bytes([0])                      # off4
    p += b"\x00\x00\x00"                 # off5,6,7 (no usados)
    p += bytes([0])                      # off8
    p += b"\x00"                         # off9 (no usado)
    p += struct.pack("<H", 0)            # off10-11
    p += struct.pack("<H", 0)            # off12-13
    p += bytes([N])                      # off14: largo del nombre (CRÍTICO = len(name))
    p += name                            # off15: nombre (N bytes)
    p += b"\x00\x00\x00\x00"             # off15+N (no usado)
    p += bytes([0])                      # off19+N
    p += struct.pack("<I", 0)            # off20+N
    p += struct.pack("<f", 0.0)          # off24+N float
    p += struct.pack("<f", 0.0)          # off28+N float
    p += struct.pack("<f", 0.0)          # off32+N float
    p += struct.pack("<I", 0)            # off36+N (no usado)
    p += bytes([status & 0xFF])          # off40+N: STATUS/TIER (CRÍTICO; 0x09=libre)
    return bytes(p)


def pack_item_record(iid, etime, inuse, cnt) -> bytes:
    """Los 11 bytes de UN ítem del inventario: [u32 item_id][u32 fecha][u8 en_uso][u16 cantidad].

    ★★ FUENTE ÚNICA a propósito: este MISMO registro va en el campo **#37 del MyRecord** y en el
       bloque **N2 del FF49**. Los dos escriben el MISMO contenedor del cliente (`record+0x158`),
       y el FF49 lo RESETEA antes de leerlo — así que si los dos formatos divergen, el que llegue
       último deja el inventario mal y el síntoma aparece lejos de la causa. Ya nos pasó con la
       música: el payload del FF49 estaba copiado en 3 lugares distintos.
    La fecha depende del tipo: los ítems de USOS (impulsor, etc.) no expiran y llevan `cnt` real;
    la ROPA lleva la fecha empaquetada día/mes/año."""
    fecha = PERMANENT_ENDTIME if item_slot(iid) is None else pack_date(etime)
    return (struct.pack("<I", iid & 0xFFFFFFFF) + struct.pack("<I", fecha & 0xFFFFFFFF)
            + struct.pack("<B", inuse & 0xFF) + struct.pack("<H", cnt & 0xFFFF))


def inventario_de(uid):
    """El inventario del jugador como lista de tuplas, listo para `pack_item_record`."""
    return [(r["item_id"], r["end_time"], r["in_use"], r["count"]) for r in db_inv_list(uid)]


def build_myrecord_honesto(nick, char_id=0x0001FF54, parts=None, has_char=True, level=0, license="Y", coin=0, inventory=None, mission=0):
    """
    Payload INTERNO de FF03 (MyRecord) que espera el handler cliente HONESTO 0x589740 — 82
    reads de longitud VARIABLE (NO un blob fijo). Reconstruido por disasm del handler completo
    (0x589740..0x58A8C4, read()=0x5D4FE0). Record MÍNIMO: 1 personaje, sin música/items/listas
    (todos los counts en 0). REGLAS para no desincronizar / no colgar:
      - nick <= 12 bytes (cmp 0xC; ja salta el read del cuerpo).
      - campo #9 != 0x7FFFFFFF (si no, popup).
      - XOR 0x1FEF en #1 (char_id) y #3 (=0 -> 0x1FEF).
      - TODOS los counts u16/u8 en 0 (música #32/#74 = 0 -> se salta el loop "Music Not Found").
      - strings = [len:1] + bytes (cp1252 via enc_text).
    NO lleva campo nic: este handler NO valida nic (eso es el handler 0x577550 = FF00, aparte).
    """
    u8 = lambda v: struct.pack("<B", v & 0xFF)
    u16 = lambda v: struct.pack("<H", v & 0xFFFF)
    u32 = lambda v: struct.pack("<I", v & 0xFFFFFFFF)
    nb = enc_text(nick)[:0x0C]
    gb = enc_text(license)[:0xFF]   # "Y"=estrella "N"=luna "X"=sol "Z"=máx (->player+0x348=1/2/3/4)
    if parts is None:
        parts = [0x33686, 0x4BD25, 0x6437B, 0x7C9DE]   # pelo, sup, inf, patines (raw)
    p = bytearray()
    p += u32(char_id ^ 0x1FEF)   # #1  -> player+0x2DC (XOR 0x1FEF)
    p += u32(0)                  # #2  -> player+0x31C
    p += u32(coin & 0xFFFFFFFF)  # #3  -> RCOIN que muestra el shop (lo lee CRUDO; antes 0^0x1FEF=8175).
                                 #      Banda/nivel vienen de license/#71, no de este campo -> seguro poner el coin real.
    p += u32(0)                  # #4
    p += u16(0)                  # #5
    p += u32(0)                  # #6  -> player+0x31C
    p += u8(len(nb)) + nb        # #7/#8 nombre (<=12)
    p += u32(0)                  # #9  (NO 0x7FFFFFFF)
    p += u16(0)                  # #10 count loop A = 0
    p += u16(0)                  # #13 count loop B = 0
    p += u32(0)                  # #16
    p += u32(0)                  # #17 count loop C = 0
    p += u8(0)                   # #18/#19 string vacío (len=0)
    p += u32(0)                  # #20
    p += u32(0)                  # #21
    p += u8(0)                   # #22 len=0 (sin #23)
    p += u8(0)                   # #24 len=0 (sin #25/#26)
    p += u32(0)                  # #27
    # #28-#31 = loop D = LISTA DE PERSONAJES (la compuerta 0x50CB43 cuenta esto: count>=1 -> MENÚ,
    # count==0 -> CREACIÓN). char_id y parts van CRUDOS (sin XOR). Sub-registro: [char_id:u32][partcount:u8][parts:u32×n].
    if has_char:
        # apariencia: char_id es la pieza slot-1 (CUERPO base+cara) -> va PRIMERO en las partes,
        # luego la ropa (slots 2-5). Sin slot-1 el avatar del menú queda invisible.
        worn = [char_id] + list(parts)
        p += u16(1)                          # 1 personaje -> MENÚ
        p += u32(char_id)                    # char_id CRUDO (campo id del struct[0])
        p += u8(len(worn))                   # nº de partes (cuerpo + ropa)
        for pid in worn:
            p += u32(pid & 0xFFFFFFFF)       # part_id CRUDO (1ª=char_id=cuerpo, resto=ropa)
    else:
        p += u16(0)                          # 0 personajes -> CREACIÓN (honesto)
    # #32 = MÚSICA POSEÍDA del usuario: [u16 count][u16 songid]×count
    #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#paquetes-32-m-sica-pose-da-del-usuario-u16-count-u)
    if MINIMAL_PICKERS:
        p += u16(0)                          # #32 TEST: música count=0 (picker vacío) para aislar c0000417
    else:
        _picker_songs = [sid for sid, _ in SONGS]
        p += u16(len(_picker_songs))         # #32 count MÚSICA = 64
        for _sid in _picker_songs:
            p += u16(_sid)                   # cada songid (u16)
    # #34 = STAGES POSEÍDOS del usuario: [u16 count] + count×[u16 stageid][u8 flag]. El handler
    #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#paquetes-34-stages-pose-dos-del-usuario-u16-count-)
    if MINIMAL_PICKERS:
        p += u16(0)                      # #34 TEST: stages count=0 (picker vacío)
    else:
        p += u16(len(STAGES))            # #34 count STAGES
        for _stid in STAGES:
            p += u16(_stid)              # [u16 stageid]
            p += u8(0)                   # [u8 flag] (0 = sin estado especial)
    # #37 = INVENTARIO (loop G). Confirmado por disasm del handler 0x589740 (loop en 0x58a00a): por
    # cada item lee [u32 item_id][u32 end_time][u8 in_use][u16 count] (11B) y valida el item_id contra
    # el catálogo del cliente (0x4c5c60); si no lo reconoce, lo SALTEA sin desincronizar. Poblarlo =
    # el clóset muestra lo comprado como POSEÍDO (sin botón "comprar") entre corridas.
    inv = inventory or []
    p += u16(len(inv))                       # #37 count items
    for (iid, etime, inuse, cnt) in inv:
        p += pack_item_record(iid, etime, inuse, cnt)   # ★ mismo registro que el N2 del FF49
    p += u16(0)                  # #42 count loop H = 0
    p += u8(0)                   # #45 count loop I = 0
    p += u8(0)                   # #54 count loop J = 0
    p += u8(0)                   # #56 count loop K = 0
    p += u32(0)                  # #60 -> player+0x2F4
    p += u32(0)                  # #61 -> player+0x2F0
    p += u32(0)                  # #62 -> player+0x2EC
    p += u32(0)                  # #63 -> player+0x2F8
    p += u8(mission)             # #64 -> player+0x331 = PROGRESO DE MISIÓN (0..5 por misión escuela).
                                 # ★ Restaura al login el estado FF81: la lista de misiones gatea done/
                                 #   bloqueada leyendo [player]+0x331 (gates 0x425515/0x425667/0x4259a5/
                                 #   0x42586b). Confirmado por 2 análisis RE independientes. Sin esto, el
                                 #   byte queda 0 (constructor 0x45bf11) -> misión no persiste al re-logear.
    p += u8(0)                   # #65 count loop L = 0
    p += u8(0)                   # #67 count loop M = 0
    p += u32(0)                  # #69
    p += u8(len(gb))             # #70 len licencia
    p += u8(level)               # #71 nivel/grade (<=0x64 -> player+0x345)
    p += u8(0)                   # #72
    p += gb                      # #73 cuerpo licencia (len = #70)
    p += u16(0)                  # #74 count MÚSICA-2 = 0
    p += u8(0)                   # #76/#77 string vacío
    p += u8(0)                   # #78
    p += u32(0)                  # #79
    p += u8(0)                   # #80 len=0 (sin #81/#82)
    return bytes(p)



def build_ff45_couple(entries=None, partner_name=""):
    """
    FF45 = lista de PAREJA (커플 / anillo de pareja). Recv handler cliente 0x579AE0. Formato:
        [count:2 LE]
        count× entrada: [namelen:1][name][a:4][b:8][c:8][d:1]   (record via cliente 0x45B220,
                        se anexa a la lista [0x766640]+0x48)  -- semántica de a/b/c/d = TODO (reversar)
        trailing:       [namelen:1][name]                       = nombre de la PAREJA actual ("" soltero)
    SOLTERO / sin pareja = b"\\x00\\x00" (count=0). Verificado vs oráculo: las sesiones que avanzan
    mandan exactamente 00 00 (el cliente tolera la ausencia del trailing). Cuando IMPLEMENTEMOS
    parejas: pasar `entries` (lista de dicts {name,a,b,c,d}) desde la DB y `partner_name`. El
    campo NO es [estado][nombre]: los 2 primeros bytes son el CONTADOR de entradas.
    """
    if not entries:
        return b"\x00\x00"   # count=0 -> sin pareja (idéntico al oráculo)
    body = bytearray(struct.pack("<H", len(entries)))
    for e in entries:
        nm = enc_text(e.get("name", ""))[:255]
        body += bytes([len(nm)]) + nm
        body += struct.pack("<I", e.get("a", 0))
        body += struct.pack("<Q", e.get("b", 0))
        body += struct.pack("<Q", e.get("c", 0))
        body += bytes([e.get("d", 0) & 0xFF])
    pn = enc_text(partner_name)[:255]
    body += bytes([len(pn)]) + pn       # nombre de la pareja actual
    return bytes(body)


def build_ff45_couple_for(uid):
    """FF45 según la pareja de `uid` en DB. Soltero -> 00 00; casado -> 1 entry + trailing con el nombre.
    El cliente poletea FF45 (inner 0x45) cada pocos segundos -> al casarse, 'Mi pareja' se actualiza solo."""
    partner = db_get_couple(uid)
    if not partner:
        return build_ff45_couple()
    pname = partner["nickname"]
    return build_ff45_couple(entries=[{"name": pname, "a": partner["uid"], "b": 0, "c": 0, "d": 0}],
                             partner_name=pname)
