# -*- coding: utf-8 -*-
"""Tienda, inventario y ropa que lleva puesta el personaje.

El catalogo sale de datos/shop_lt.txt, el MISMO archivo que usa el
cliente para dibujar la tienda: si estan desincronizados, el jugador ve
un item que el servidor no conoce (o al reves).

--
Parte del servidor abierto de R2Beat. Ver LEEME.md en la raiz del proyecto.
"""

import datetime
import time

from .ajustes import DURATION_DAYS, PERMANENT_ENDTIME, RUTA_ALAS, RUTA_CARAS, RUTA_TIENDA
from .basedatos import db, db_get_char
from .registro import log



# ── Items: codificación itemID+duración y persistencia de inventario ──────────
# El cliente empaqueta la DURACIÓN en el dígito de las decenas de millón (10^7) del itemID.
# Confirmado en vivo con FF2A (BUY_DRESS): pelo=21xxxx, top=31xxxx, inf=41xxxx, patines=51xxxx.

# Precios: el shop_lt del cliente viene stripped (PI=9999999999) y la request FF2A no trae precio.
# Precios por SLOT según lo indicado por el usuario (aprox; el precio exacto por item vivía en el
# catálogo/DB del server original). ropa=3900, patines=2900 (varían 2900-3800), especiales=varían.
SHOP_PRICE_BY_SLOT = {"hair": 3900, "top": 3900, "bottom": 3900, "skates": 2900}
SHOP_PRICE_SPECIAL = 4000               # items funcionales 7-díg (varían 2100/4000/6000)
CONSUMABLE_USES = 300                    # usos por defecto de un item de usos (impulsor, etc.); el pack real vive en el catálogo
def price_for_item(item_id):
    slot = item_slot(item_id)
    return SHOP_PRICE_BY_SLOT[slot] if slot else SHOP_PRICE_SPECIAL

# ── Catálogo de tienda parseado de shop_lt.txt (MISMA fuente que el cliente) ──
# Da precio + datos de pack por item -> el server descuenta el precio EXACTO y otorga el item real.
SHOP_CATALOG = {}   # item_id(int) -> {price, pack_index, pack_count, name}
def load_shop_catalog():
    global SHOP_CATALOG
    import re
    path = RUTA_TIENDA
    try:
        raw = open(path, "rb").read().decode("cp1252", "replace")
    except Exception as e:
        log(f"[shop] no pude leer {path}: {e}"); return
    cat = {}
    for m in re.finditer(r'<Item\s+([^>]*?)>', raw):
        at = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        idx = at.get("Index", "")
        if not idx.isdigit():
            continue
        price = None
        for k in ("P", "PI", "P7", "P1", "P3"):   # distintos campos de precio por período/moneda
            v = at.get(k, "")
            if v.isdigit():
                price = int(v); break
        cat[int(idx)] = {
            "price": price,
            "pack_index": int(at["PackIndex"]) if at.get("PackIndex", "").isdigit() else None,
            "pack_count": int(at["PackCount"]) if at.get("PackCount", "").isdigit() else None,
            "name": at.get("Name", ""),
            # ItemEffect: lo usa la CARA para resolver su faceIndex vía face.txt (ver load_face_table)
            "effect": int(at["ItemEffect"]) if at.get("ItemEffect", "").isdigit() else None,
        }
    SHOP_CATALOG = cat
    log(f"[shop] shop_lt cargado: {len(cat)} items")
    load_face_table()          # depende del catálogo -> se rearma junto con él

def shop_lookup(item_id):
    """Entrada de shop para un item_id comprado. Maneja el raw de packs (dígito extra en 10^3:
    1205004 -> 1200004). Devuelve (shop_id, entry|None)."""
    if item_id in SHOP_CATALOG:
        return item_id, SHOP_CATALOG[item_id]
    # El raw trae un dígito de período/variante en las MILÉSIMAS (10^3) que el shop_lt tiene en 0.
    # Ej: 919003->910003, 1205004->1200004, 839009->830009, 229960->220960.
    # Hay que quitarlo o el server otorga un id que el cliente NO conoce ("No hay información del
    # ítem") y que además queda HUÉRFANO en el inventario (el patrón que rompe el login).
    #
    # ★2026-07-27: antes esto se aplicaba SÓLO a id>=600000 porque se creía que en la ropa el 10^3
    #   era parte del id ("212046 es válido"). Es FALSO en este catálogo: se verificaron los 1731
    #   items y **todos** tienen las milésimas en 0. Sin el strip, cada prenda comprada entraba al
    #   inventario con el dígito de duración pegado (comprar "Peinado demonia" 220960 guardaba
    #   229960). El guard de abajo lo hace seguro igual: sólo se acepta el candidato si EXISTE en el
    #   catálogo, y un id con milésimas reales ya habría salido por el `return` de arriba.
    cand = item_id - (item_id // 1000 % 10) * 1000
    if cand != item_id and cand in SHOP_CATALOG:
        return cand, SHOP_CATALOG[cand]
    return item_id, None

def decode_item_id(raw):
    """raw (itemID real + código de duración @10^7) -> (item_id_real, dur_code)."""
    dur = (raw // 10_000_000) % 10
    item_id = raw % 10_000_000 + (raw // 100_000_000) * 10_000_000
    return item_id, dur

def pack_date(ts):
    """Convierte un end_time (unix ts) al formato de fecha EMPAQUETADA que el cliente MUESTRA
    (conversor 0x4c534c): bits0-11=año(12b), bits12-15=mes(4b), bits16-20=día(5b).
    PERMANENT (0xEEFFCBB8) YA es 31/12/3000 en este formato -> pasar tal cual; 0 (cantidad) igual.
    En la DB guardamos el unix ts (para comparar en db_expire_items); esto es solo para el cable."""
    if ts in (PERMANENT_ENDTIME, 0):
        return ts
    d = datetime.datetime.fromtimestamp(ts)
    return ((d.day & 0x1F) << 16) | ((d.month & 0xF) << 12) | (d.year & 0xFFF)

def duration_to_endtime(dur, get_time=None):
    """código de duración -> end_time (unix ts). 9=permanente, 6=por cantidad(0), resto=get_time+días."""
    if dur == 9: return PERMANENT_ENDTIME
    if dur == 6: return 0
    if get_time is None: get_time = int(time.time())
    days = DURATION_DAYS.get(dur, 30)    # dur=0/desconocido -> 30 días (lo que muestra el popup de la tienda)
    return (get_time + days * 86400) & 0xFFFFFFFF

def db_inv_has(uid, item_id):
    c = db(); r = c.execute("SELECT 1 FROM inventory WHERE uid=? AND item_id=?",
                            (uid, item_id)).fetchone(); c.close()
    return r is not None

def db_inv_add(uid, item_id, end_time=PERMANENT_ENDTIME, count=0):
    c = db()
    c.execute("""INSERT OR IGNORE INTO inventory
                 (uid,item_id,end_time,in_use,count,get_time,storage) VALUES (?,?,?,0,?,?,0)""",
              (uid, item_id, end_time, count, int(time.time())))
    c.commit(); c.close()

def db_inv_add_count(uid, item_id, delta):
    """Apila usos en un item que ya se posee (comprar otro pack del mismo item de usos)."""
    c = db(); c.execute("UPDATE inventory SET count=count+? WHERE uid=? AND item_id=?",
                        (delta, uid, item_id)); c.commit(); c.close()

def db_expire_items(uid, now=None):
    """Quita de la DB los items VENCIDOS (end_time real y <= now; ignora permanente y cantidad=0).
    Devuelve [(item_id, end_time)...] de los que expiraron -> el server avisa al usuario (FF43)."""
    if now is None: now = int(time.time())
    c = db()
    rows = c.execute("""SELECT item_id,end_time FROM inventory
                        WHERE uid=? AND end_time NOT IN (?,0) AND end_time<=?""",
                     (uid, PERMANENT_ENDTIME, now)).fetchall()
    for r in rows:
        c.execute("DELETE FROM inventory WHERE uid=? AND item_id=?", (uid, r["item_id"]))
        # si estaba equipado, liberar el slot del personaje
        slot = item_slot(r["item_id"])
        if slot:
            c.execute(f"UPDATE chars SET {slot}=0 WHERE uid=? AND {slot}=?", (uid, r["item_id"]))
    c.commit(); c.close()
    return [(r["item_id"], r["end_time"]) for r in rows]

def db_inv_list(uid):
    c = db(); rows = c.execute("""SELECT item_id,end_time,in_use,count,get_time,storage
                                  FROM inventory WHERE uid=?""", (uid,)).fetchall(); c.close()
    return rows

# Slot de vestimenta según el 1er dígito del itemID (2=pelo 3=sup 4=inf 5=patines).
WEAR_SLOT = {2: "hair", 3: "top", 4: "bottom", 5: "skates"}

# ★2026-07-27 — QUÉ SLOTS NECESITAN QUE EL SERVER AVISE EL DES-EQUIPADO (FF1A action=0).
# La respuesta de FF1A sólo nombra al item NUEVO. Para casi todos los slots eso alcanza porque el
# CLIENTE aplica la exclusividad solo; pero para la CARA no lo hace y las caras se "apilaban"
# (varias marcadas a la vez en el armario). Ver handle_edit_avatar.
#   Determinado PROBANDO EN VIVO, slot por slot — NO es "item_slot() is None" (ese fue mi primer
#   intento y rompió pelo/top/inferior/patines Y mano):
#     2,3,4,5 pelo/sup/inf/patines -> el cliente lo maneja  (avisar ROMPE: parpadeo, 2 clics)
#     9       mano                 -> el cliente lo maneja  (avisar ROMPE: idem)
#     10      CARA                 -> NADIE lo maneja       -> hay que avisar
#     6       mascota              -> sin confirmar; se deja fuera por precaución (si algún día se
#                                     ven dos mascotas marcadas a la vez, agregar el 6 acá y probar)
# El patrón que explica el corte: pelo/ropa/patines/mano son MALLAS del avatar (worn normal); la
# cara es una TEXTURA que resuelve otro subsistema (ver memoria r2beat-cara-sala).
SLOTS_SIN_EXCLUSIVIDAD = frozenset({10})
def item_slot(item_id):
    return WEAR_SLOT.get(item_id // 100_000)     # None si no es prenda (items 7-díg, etc.)

# CARA/PIEL EN SALA: item de cara -> faceIndex (carpeta rnr_char/face/NN/)
#   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#catalogo-cara-piel-en-sala-item-de-cara-faceindex-)
FACE_TABLE = {}
FACE_TABLE_FALLBACK = {1010076: 11, 1010042: 5, 1020043: 6, 1020077: 12}   # las 4 de fábrica

def load_face_table():
    """Arma FACE_TABLE = {item_id -> faceIndex} cruzando SHOP_CATALOG (item->ItemEffect) con
    data/face.txt (ItemEffect-><Face Index>). Si falta face.txt, cae a las 4 de fábrica."""
    global FACE_TABLE
    import re
    path = RUTA_CARAS
    try:
        raw = open(path, "rb").read().decode("euc-kr", "replace")
    except Exception as e:
        FACE_TABLE = dict(FACE_TABLE_FALLBACK)
        log(f"[face] no pude leer {path} ({e}) -> fallback {len(FACE_TABLE)} caras de fábrica")
        return
    eff2idx = {}
    for m in re.finditer(r'<Face\s+Index="(\d+)"\s+ItemEffect="(\d+)"', raw):
        eff2idx[int(m.group(2))] = int(m.group(1))
    tbl = {}
    for item_id, ent in SHOP_CATALOG.items():
        eff = ent.get("effect")
        if eff is not None and eff in eff2idx:
            tbl[item_id] = eff2idx[eff]
    for k, v in FACE_TABLE_FALLBACK.items():
        tbl.setdefault(k, v)          # red de seguridad: las de fábrica nunca se pierden
    FACE_TABLE = tbl
    log(f"[face] face.txt: {len(eff2idx)} efectos -> {len(FACE_TABLE)} caras mapeadas")
    load_wing_table()          # misma idea: data-driven desde el wing.txt del cliente

def occ_face_index(items):
    """faceIndex de la cara equipada dentro de `items` (worn del ocupante); 0 = sin cara -> default."""
    for it in (items or []):
        fi = FACE_TABLE.get(it)
        if fi:
            return fi
    return 0

# ALAS EN SALA (, RE del handler 0x5863F0 + 0x565F50)
#   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#catalogo-alas-en-sala-re-del-handler-0x5863f0-0x56)
WING_TABLE = {}     # item_id (=ItemCode) -> Wing.Id

def load_wing_table():
    """WING_TABLE = {ItemCode -> Wing.Id} desde data/wing.txt (el MISMO que lee el cliente)."""
    global WING_TABLE
    import re
    path = RUTA_ALAS
    try:
        raw = open(path, "rb").read().decode("euc-kr", "replace")
    except Exception as e:
        WING_TABLE = {}
        log(f"[wing] no pude leer {path} ({e}) -> sin alas en sala"); return
    tbl = {}
    for m in re.finditer(r'<Wing\s+Id="(\d+)"\s+ItemCode="(\d+)"', raw):
        tbl[int(m.group(2))] = int(m.group(1))
    WING_TABLE = tbl
    log(f"[wing] wing.txt: {len(WING_TABLE)} alas mapeadas (ItemCode->Id)")

def occ_wing_id(items):
    """Wing.Id del ala equipada dentro de `items` (worn del ocupante); 0 = sin ala -> detach."""
    for it in (items or []):
        wid = WING_TABLE.get(it)
        if wid:
            return wid
    return 0

def db_inv_set_inuse(uid, item_id, in_use):
    c = db(); c.execute("UPDATE inventory SET in_use=? WHERE uid=? AND item_id=?",
                        (1 if in_use else 0, uid, item_id)); c.commit(); c.close()

def db_inv_clear_slot(uid, item_id):
    """Pone in_use=0 en las OTRAS piezas del MISMO slot (mismo item_id//100000) antes de equipar una
    nueva -> un solo item por slot (no se apilan dos pelos / dos manos / dos caras / dos mascotas).

    ★DEVUELVE la lista de item_ids que quedaron des-equipados. El que llama TIENE que avisárselos al
    cliente (FF1A action=0), porque la respuesta de FF1A sólo menciona el item NUEVO: si no, el
    armario sigue mostrando marcada la pieza vieja aunque en la DB ya esté en 0 (2026-07-27)."""
    slot_key = item_id // 100_000
    c = db()
    rows = c.execute("SELECT item_id FROM inventory WHERE uid=? AND in_use=1", (uid,)).fetchall()
    quitados = []
    for r in rows:
        if r["item_id"] and r["item_id"] // 100_000 == slot_key and r["item_id"] != item_id:
            c.execute("UPDATE inventory SET in_use=0 WHERE uid=? AND item_id=?", (uid, r["item_id"]))
            quitados.append(r["item_id"])
    c.commit(); c.close()
    return quitados

def db_worn_items(uid, ch=None):
    """TODAS las piezas equipadas del personaje -> lista de item_ids, para FF0C (sala) y MyRecord (menú).
    UNIÓN de dos fuentes para que PERSISTAN entre cerrar/crear sala, salir/entrar:
      • las 4 columnas base de `chars` (pelo/sup/inf/patines) -> siempre presentes (incluye cuentas seed).
      • los slots EXTRA equipados en `inventory` con in_use=1 (MANO/MASCOTA/CARA/accesorios) -> antes se
        perdían porque nadie leía in_use; ahora sí -> persisten.
    NO incluye el char_id (cuerpo base): ese va aparte (r11/r15 del FF0C, 1er part de MyRecord). Un item
    por slot (dedupe por item_id//100000). El orden no importa: el cliente clasifica por 0x4C43C0."""
    if ch is None:
        ch = db_get_char(uid)
    base = [ch[k] for k in ("hair", "top", "bottom", "skates")] if ch else []
    base = [b for b in base if b]
    seen = {b // 100_000 for b in base}
    out = list(base)
    c = db()
    rows = c.execute("SELECT item_id FROM inventory WHERE uid=? AND in_use=1 ORDER BY item_id", (uid,)).fetchall()
    c.close()
    for r in rows:
        iid = r["item_id"]
        if iid and (iid // 100_000) not in seen:      # slot extra (no cubierto por las columnas base)
            seen.add(iid // 100_000); out.append(iid)
    return out

def db_set_worn_slot(uid, slot, item_id):
    """Equipa/quita una prenda en el personaje (columna hair/top/bottom/skates) -> lo lee MyRecord."""
    if slot not in ("hair", "top", "bottom", "skates"):
        return
    c = db(); c.execute(f"UPDATE chars SET {slot}=? WHERE uid=?", (item_id, uid)); c.commit(); c.close()

def db_clear_worn(uid):
    c = db()
    c.execute("UPDATE chars SET hair=0,top=0,bottom=0,skates=0 WHERE uid=?", (uid,))
    c.execute("UPDATE inventory SET in_use=0 WHERE uid=?", (uid,))
    c.commit(); c.close()
