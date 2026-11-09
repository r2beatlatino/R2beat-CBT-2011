# -*- coding: utf-8 -*-
"""Comprar ropa y ponersela.

El precio y el stock salen de datos/shop_lt.txt, el MISMO archivo que
usa el cliente para dibujar la tienda.

--
Una pieza de LobbyProtocol. Se junta con las demas en lobby.py.
"""

import time
import struct

from .ajustes import PERMANENT_ENDTIME, ROOMS
from .basedatos import db_get_char, db_set_coin
from .catalogo import (
    CONSUMABLE_USES,
    SLOTS_SIN_EXCLUSIVIDAD,
    db_clear_worn,
    db_inv_add,
    db_inv_add_count,
    db_inv_clear_slot,
    db_inv_has,
    db_inv_set_inuse,
    db_set_worn_slot,
    db_worn_items,
    decode_item_id,
    duration_to_endtime,
    item_slot,
    pack_date,
    price_for_item,
    shop_lookup,
)
from .registro import log


class PiezaTienda:
    """Comprar ropa y ponersela."""

    def handle_buy_dress(self, data):
        """FF2A. Respuesta que espera el cliente (handler 0x578F20):
        [1]result [1]0 [4]item_id [4]end_time [4]coin_restante.  result: 0=OK 1=sin金화 2=ya lo tiene."""
        if len(data) < 5:
            log(f"[Lobby] BUY_DRESS payload corto: {data.hex().upper()}"); return
        raw = struct.unpack_from("<I", data, 1)[0]
        item_id, dur = decode_item_id(raw)
        now = int(time.time())
        shop_id, entry = shop_lookup(item_id)       # datos de shop_lt (precio, pack, usos)
        # ★2026-07-27 DURACIÓN DE LA ROPA. `decode_item_id` busca el código en 10^7, que es donde va
        #   en los items de 7+ dígitos; para la ROPA de 6 dígitos el cliente lo pone en las MILÉSIMAS.
        #   Como nadie lo leía, `dur` salía 0 y `dur or 2` lo mandaba SIEMPRE a 30 días: comprar
        #   "permanente" (código 9) guardaba 30 días. Si shop_lookup tuvo que quitar ese dígito, ES
        #   la duración elegida.
        #   ⚠Sólo para ROPA: en los PACKS ese mismo dígito es la CANTIDAD (1205002/3/4 = x100/x200/
        #   x300), no una duración. Hoy los packs no llegan a usar `dur` (van por la rama is_pack,
        #   que fuerza PERMANENT), pero se acota igual para que no se rompa si esa rama cambia.
        if shop_id != item_id and not dur and item_slot(shop_id) is not None:
            dur = (item_id // 1000) % 10
        # PRECIO real de shop_lt (9999999999 -> INT_MAX = "no precio" -> fallback por slot)
        if entry and entry["price"] and entry["price"] < 2_000_000_000:
            price = entry["price"]
        else:
            price = price_for_item(shop_id)
        # ¿es un PACK? -> otorga el ITEM ACTIVABLE (PackIndex, ej. 1000999 "Auto Impulsor") con los usos
        #   del pack. Ese es el que el cliente togglea por FF1A (activar/desactivar). Los usos se apilan.
        if entry and entry["pack_index"]:
            grant_id = entry["pack_index"]; grant_cnt = entry["pack_count"] or CONSUMABLE_USES
            grant_end = PERMANENT_ENDTIME; is_pack = True
        else:
            grant_id = shop_id; is_pack = False
            if item_slot(grant_id) is None:         # consumible suelto: usos, no expira
                grant_end = PERMANENT_ENDTIME; grant_cnt = CONSUMABLE_USES
            else:                                   # ropa: fecha de expiración
                grant_end = duration_to_endtime(dur or 2, now); grant_cnt = 0
        ch = db_get_char(self.user_id)
        coin = ch["coin"] if ch else 0
        already = db_inv_has(self.user_id, grant_id)
        if already and not is_pack:
            result = 2                                  # ropa/item único ya poseído
        elif coin < price:
            result = 1                                  # sin金화 suficiente
        else:
            result = 0
            coin -= price
            db_set_coin(self.user_id, coin)
            if is_pack and already:
                db_inv_add_count(self.user_id, grant_id, grant_cnt)   # apila usos del pack
            else:
                db_inv_add(self.user_id, grant_id, grant_end, count=grant_cnt)
        # 4º campo de la resp: fecha empaquetada para ROPA; permanente para item de usos/pack
        is_cons = is_pack or item_slot(grant_id) is None
        resp_date = grant_end if is_cons else pack_date(grant_end)
        resp = (bytes([result, 0]) + struct.pack("<I", grant_id)
                + struct.pack("<I", resp_date & 0xFFFFFFFF)
                + struct.pack("<I", coin & 0xFFFFFFFF))
        self.send_packet(0x2A, resp)
        log(f"[Lobby] BUY item={item_id} shop={shop_id} -> grant={grant_id} pack={is_pack} usos={grant_cnt} "
            f"precio={price} result={result} coin->{coin}")

    def handle_edit_avatar(self, data):
        """FF1A. Request [action:1][item_id:u32]. Respuesta [result=0][action:1][item_id:u32].
        action: 0=quitar 1=equipar 2=limpiar todo. Persiste el slot (hair/top/bottom/skates)."""
        action = data[0] if len(data) >= 1 else 0
        item_id = struct.unpack_from("<I", data, 1)[0] if len(data) >= 5 else 0
        slot = item_slot(item_id)
        quitados = []
        if action == 1:                       # equipar
            quitados = db_inv_clear_slot(self.user_id, item_id)   # 1 item por slot (mano/mascota/cara/pelo/…): saca el anterior del mismo slot
            db_inv_set_inuse(self.user_id, item_id, 1)
            if slot: db_set_worn_slot(self.user_id, slot, item_id)  # además la columna base (pelo/sup/inf/patines) para MyRecord
        elif action == 0:                     # quitar
            db_inv_set_inuse(self.user_id, item_id, 0)
            if slot: db_set_worn_slot(self.user_id, slot, 0)
        elif action == 2:                     # limpiar todo
            db_clear_worn(self.user_id)
        resp = bytes([0x00, action & 0xFF]) + struct.pack("<I", item_id)
        self.send_packet(0x1A, resp)
        # avisar los que salieron del slot
        #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#lobby-avisar-los-que-salieron-del-slot)
        _avisar = [q for q in quitados if (q // 100_000) in SLOTS_SIN_EXCLUSIVIDAD]
        for _q in _avisar:
            self.send_packet(0x1A, bytes([0x00, 0x00]) + struct.pack("<I", _q))
        if _avisar:
            log(f"[Lobby] EDIT_AVATAR des-equipados -> aviso FF1A action=0: {_avisar}")
        if quitados and not _avisar:
            log(f"[Lobby] EDIT_AVATAR quitados de slot {[q//100_000 for q in quitados]} "
                f"(sin aviso, la exclusividad la maneja el cliente): {quitados}")
        _worn_now = db_worn_items(self.user_id)   # DIAGNÓSTICO: qué queda equipado tras el cambio (persiste en FF0C/MyRecord)
        log(f"[Lobby] EDIT_AVATAR action={action} item={item_id} slot_base={slot} slot_key={item_id//100_000} "
            f"-> worn ahora={_worn_now}")
        # ★ si estoy EN SALA, re-broadcast (FF0C + FF15 cara) a todos -> el cambio de cara/ropa se ve en vivo
        _rid = getattr(self, "my_room_id", None)
        if _rid is not None and ROOMS.get(_rid):
            self.notify_room(_rid)

    # (send_serverlist FF04 ELIMINADO 2026-06-21: FF04 NO es la lista de canales — es la respuesta de
    #  crear-personaje. La lista de canales va por el FF02 LOBBYSERVER_LIST del login, ver cmd 0x02.)

    # ── Entrada al lobby (EXPERIMENTAL — basado en el RE del agente + tu observación del FF6E) ──
    def send_cash(self):
        """Responde el saldo cash que pide el cliente (FF51). [1]result=0 [4]cash LE."""
        ch = db_get_char(self.user_id)
        cash = ch["cash"] if ch else 0
        self.send_packet(0x51, bytes([0]) + struct.pack("<I", cash & 0xFFFFFFFF))
        log(f"[Lobby] CASH -> {cash}")
