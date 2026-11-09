# -*- coding: utf-8 -*-
"""Base de datos SQLite: cuentas, personajes, amigos y pareja.

Un solo archivo (datos/cuentas.db) que se crea solo la primera vez.
Lo de tienda/inventario/ropa esta en catalogo.py.

--
Parte del servidor abierto de R2Beat. Ver LEEME.md en la raiz del proyecto.
"""

import datetime
import os
import time
import secrets

from .ajustes import (
    DB_PATH,
    DEFAULT_COIN,
    DEFAULT_EXP,
    EXP_LOCK,
    LICENSE_BYTE,
    PERMANENT_ENDTIME,
    SAMPLE_PASS_HASH,
    SAMPLE_USER,
    TOKEN_TTL_HOURS,
)
from .progresion import (
    LICENCIA_HONESTA,
    TIER_ACCOUNTS,
    level_from_exp,
    licencia_de_nivel,
    tier_from_exp,
)
from .registro import log, now_str


# ============================================================
# Capa DB (SQLite stdlib) — honesta y explícita
#   users : SOLO autenticación
#   chars : el PERSONAJE. Si no hay fila -> el user no tiene personaje -> creación.
#   wear  : items puestos (pelo/ropa/patines) referenciados por el personaje
#   sessions : tokens emitidos por el login
# ============================================================
import sqlite3

def db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def db_init():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        uid INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        pass_hash TEXT NOT NULL,
        reg_time TEXT, last_ip TEXT, last_time TEXT);
    CREATE TABLE IF NOT EXISTS chars (
        uid INTEGER PRIMARY KEY,            -- 1 personaje por cuenta (ampliable luego)
        nickname TEXT NOT NULL,
        gender   INTEGER NOT NULL DEFAULT 0,
        char_id  INTEGER NOT NULL DEFAULT 0,
        hair     INTEGER DEFAULT 0,
        top      INTEGER DEFAULT 0,         -- ropa superior
        bottom   INTEGER DEFAULT 0,         -- ropa inferior
        skates   INTEGER DEFAULT 0,         -- patines
        exp INTEGER DEFAULT 0, coin INTEGER DEFAULT 0, cash INTEGER DEFAULT 0,
        mission INTEGER DEFAULT 0,
        created_at TEXT);
    CREATE TABLE IF NOT EXISTS wear (
        uid INTEGER NOT NULL, role_id INTEGER DEFAULT 0, item_id INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS inventory (
        uid      INTEGER NOT NULL,
        item_id  INTEGER NOT NULL,          -- ID real 6-díg (sin dígito de duración)
        end_time INTEGER DEFAULT 0,         -- 0xEEFFCBB8 = permanente
        in_use   INTEGER DEFAULT 0,         -- 1 = equipado
        count    INTEGER DEFAULT 0,         -- items por cantidad
        get_time INTEGER DEFAULT 0,
        storage  INTEGER DEFAULT 0,
        UNIQUE(uid, item_id));
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY, uid INTEGER NOT NULL, ip TEXT, valid_time TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS friends (
        uid        INTEGER NOT NULL,        -- dueño de la lista
        friend_uid INTEGER NOT NULL,        -- amigo agregado
        added_at   TEXT,
        UNIQUE(uid, friend_uid));
    CREATE TABLE IF NOT EXISTS couples (
        uid         INTEGER PRIMARY KEY,     -- una pareja por persona (matrimonio exclusivo)
        partner_uid INTEGER NOT NULL,
        since       TEXT);
    """)
    # uids desde 1001 (convención USN de Softnyx): si la tabla users está vacía, sembrar
    # la secuencia AUTOINCREMENT en 1000 -> el primer usuario será 1001.
    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        # sqlite_sequence no tiene UNIQUE en name -> DELETE+INSERT (no INSERT OR REPLACE, que duplica)
        c.execute("DELETE FROM sqlite_sequence WHERE name='users'")
        c.execute("INSERT INTO sqlite_sequence(name, seq) VALUES('users', 1000)")
    c.commit(); c.close()
    # ★ cuentas test estrella/luna/sol: por defecto se siembran (local/test). En el VPS de PRODUCCIÓN
    #   se apaga con env R2_SEED_TIERS=0 para NO tocar la clean.db real (slate limpio, solo cuentas reales).
    if os.environ.get("R2_SEED_TIERS", "1") != "0":
        db_seed_tiers()   # ★ 3 cuentas válidas (estrella/luna/sol) con su char y exp de banda

def db_seed_tiers():
    """Crea (idempotente) las 3 cuentas válidas de test, cada una con su char y exp de banda real."""
    c = db()
    for username, nick, exp in TIER_ACCOUNTS:
        row = c.execute("SELECT uid FROM users WHERE username=?", (username,)).fetchone()
        if row:
            uid = row["uid"]
        else:
            c.execute("INSERT INTO users (username, pass_hash, reg_time) VALUES (?,?,?)",
                      (username, SAMPLE_PASS_HASH.upper(), now_str()))
            uid = c.execute("SELECT uid FROM users WHERE username=?", (username,)).fetchone()["uid"]
        # char con su exp de banda (INSERT OR REPLACE -> fija el exp correcto siempre)
        c.execute("""INSERT OR REPLACE INTO chars
                     (uid,nickname,gender,char_id,hair,top,bottom,skates,exp,coin,cash,mission,created_at)
                     VALUES (?,?,0,?,?,?,?,?,?,0,0,0,?)""",
                  (uid, nick, 0x0001FF54, 0x00033686, 0x0004BD25, 0x0006437B, 0x0007C9DE, exp, now_str()))
        _ci, _lic, _tn = tier_from_exp(exp)
        log(f"[DB] cuenta tier '{username}' uid={uid} nick={nick} exp={exp} -> banda {_tn} (lic {_lic}, canal {_ci})")
    c.commit(); c.close()

def db_seed():
    """Siembra SOLO la cuenta (sin personaje), para probar la compuerta de creación."""
    c = db()
    if c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0:
        c.execute("INSERT INTO users (username, pass_hash, reg_time) VALUES (?,?,?)",
                  (SAMPLE_USER, SAMPLE_PASS_HASH.upper(), now_str()))
        c.commit()
        log(f"[DB] seed: cuenta '{SAMPLE_USER}' creada (SIN personaje -> debería ir a creación)")
    c.close()

# ── registro / auth ──
def db_register(username, pass_hash_hex):
    """Crea una cuenta nueva (sin personaje). Devuelve (ok, mensaje). La DB vive aquí, no en el launcher."""
    username = (username or "").strip()
    if not username or not pass_hash_hex or len(pass_hash_hex) != 64:
        return False, "datos inválidos"
    c = db()
    try:
        if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            return False, "el usuario ya existe"
        c.execute("INSERT INTO users (username, pass_hash, reg_time) VALUES (?,?,?)",
                  (username, pass_hash_hex.upper(), now_str()))
        c.commit()
        log(f"[DB] registro: cuenta '{username}' creada (sin personaje)")
        return True, "cuenta creada"
    except Exception as e:
        return False, f"error: {e}"
    finally:
        c.close()

def db_verify(username, pass_hash_hex):
    c = db(); row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone(); c.close()
    if row and row["pass_hash"].upper() == (pass_hash_hex or "").upper():
        return row
    return None

def db_issue_token(uid, ip):
    token = secrets.token_hex(8).upper()
    valid = (datetime.datetime.now() + datetime.timedelta(hours=TOKEN_TTL_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    c = db()
    c.execute("INSERT INTO sessions (token,uid,ip,valid_time,created_at) VALUES (?,?,?,?,?)",
              (token, uid, ip, valid, now_str()))
    c.commit(); c.close()
    return token

def db_latest_session_uid():
    """uid de la sesión emitida más recientemente (la que el launcher acaba de crear al loguearse)."""
    c = db()
    row = c.execute("SELECT uid FROM sessions ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
    c.close()
    return row["uid"] if row else None

def db_validate_token(token):
    """Valida un token presentado por el JUEGO. Devuelve uid o None. (Login honesto: el original valida.)"""
    if not token:
        return None
    c = db(); row = c.execute("SELECT * FROM sessions WHERE token=?", (token.upper(),)).fetchone(); c.close()
    if not row:
        return None
    try:
        if datetime.datetime.strptime(row["valid_time"], "%Y-%m-%d %H:%M:%S") < datetime.datetime.now():
            return None
    except Exception:
        pass
    return row["uid"]

def db_touch_login(uid, ip):
    c = db(); c.execute("UPDATE users SET last_ip=?, last_time=? WHERE uid=?", (ip, now_str(), uid)); c.commit(); c.close()

# ── ban de cuenta (reversible; prefijo BANNED: en pass_hash -> bloquea login). Ver docs/BANEAR.md ──
def db_ban_account(uid):
    """Neutraliza el pass_hash con prefijo BANNED: (bloquea login; reversible). Ver BANEAR.md."""
    c = db()
    row = c.execute("SELECT pass_hash FROM users WHERE uid=?", (uid,)).fetchone()
    if not row:
        c.close(); return False, "uid sin cuenta"
    ph = row["pass_hash"]
    if ph.startswith("BANNED:"):
        c.close(); return True, "ya estaba baneada"
    c.execute("UPDATE users SET pass_hash=? WHERE uid=?", ("BANNED:" + ph, uid)); c.commit(); c.close()
    return True, "cuenta baneada"

def db_unban_account(uid):
    c = db()
    row = c.execute("SELECT pass_hash FROM users WHERE uid=?", (uid,)).fetchone()
    if not row:
        c.close(); return False, "uid sin cuenta"
    ph = row["pass_hash"]
    if not ph.startswith("BANNED:"):
        c.close(); return True, "no estaba baneada"
    c.execute("UPDATE users SET pass_hash=? WHERE uid=?", (ph[len("BANNED:"):], uid)); c.commit(); c.close()
    return True, "cuenta des-baneada"

# ── personaje ──
def db_get_user(uid):
    c = db(); row = c.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone(); c.close(); return row

def db_get_char(uid):
    c = db(); row = c.execute("SELECT * FROM chars WHERE uid=?", (uid,)).fetchone(); c.close(); return row

def has_character(uid):
    return db_get_char(uid) is not None

# ── AMIGOS (FF40) ────────────────────────────────────────────────────────────
def db_add_friend(uid, friend_uid):
    """Agrega friend_uid a la lista de uid (idempotente). One-way (el que agrega ve al otro)."""
    if uid == friend_uid:
        return
    c = db()
    c.execute("INSERT OR IGNORE INTO friends (uid, friend_uid, added_at) VALUES (?,?,?)",
              (uid, friend_uid, now_str()))
    c.commit(); c.close()

def db_del_friend(uid, friend_uid):
    c = db()
    c.execute("DELETE FROM friends WHERE uid=? AND friend_uid=?", (uid, friend_uid))
    c.commit(); c.close()

def db_get_friends(uid):
    """Lista de uids amigos de uid (los char rows)."""
    c = db()
    rows = c.execute("""SELECT ch.* FROM friends f JOIN chars ch ON ch.uid = f.friend_uid
                        WHERE f.uid=? ORDER BY f.added_at""", (uid,)).fetchall()
    c.close(); return rows

# ── PAREJA / MATRIMONIO (FF40 sub 0x0A + FF45) ────────────────────────────────
def db_set_couple(uid_a, uid_b):
    """Casa a uid_a y uid_b (mutuo, exclusivo). Sobrescribe pareja previa de cada uno."""
    if uid_a == uid_b:
        return
    c = db()
    c.execute("INSERT OR REPLACE INTO couples (uid, partner_uid, since) VALUES (?,?,?)",
              (uid_a, uid_b, now_str()))
    c.execute("INSERT OR REPLACE INTO couples (uid, partner_uid, since) VALUES (?,?,?)",
              (uid_b, uid_a, now_str()))
    c.commit(); c.close()

def db_get_couple(uid):
    """Devuelve el char row de la pareja de uid, o None si soltero."""
    c = db()
    row = c.execute("""SELECT ch.* FROM couples cp JOIN chars ch ON ch.uid = cp.partner_uid
                       WHERE cp.uid=?""", (uid,)).fetchone()
    c.close(); return row

def db_del_couple(uid):
    """Rompe la pareja de uid (borra ambas direcciones)."""
    c = db()
    row = c.execute("SELECT partner_uid FROM couples WHERE uid=?", (uid,)).fetchone()
    if row:
        puid = row["partner_uid"] if hasattr(row, "keys") else row[0]
        c.execute("DELETE FROM couples WHERE uid=? OR uid=?", (uid, puid))
    c.commit(); c.close()

def db_create_char(uid, nickname, gender, char_id, hair, top, bottom, skates):
    """Persiste el personaje recién creado. Espina del flujo de creación."""
    c = db()
    c.execute("""INSERT OR REPLACE INTO chars
                 (uid,nickname,gender,char_id,hair,top,bottom,skates,exp,coin,cash,mission,created_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,0,0,?)""",
              (uid, nickname, gender, char_id, hair, top, bottom, skates, DEFAULT_EXP, DEFAULT_COIN, now_str()))
    # items puestos -> wear (pelo/ropa/patines), para que MyRecord los muestre
    c.execute("DELETE FROM wear WHERE uid=?", (uid,))
    for iid in (hair, top, bottom, skates):
        if iid:
            c.execute("INSERT INTO wear (uid,role_id,item_id) VALUES (?,0,?)", (uid, iid))
    # ★ los items de CREACIÓN también van al INVENTARIO como PERMANENTES (0xEEFFCBB8): aparecen en el
    #   clóset como poseídos y NUNCA expiran (db_expire_items ignora el end_time permanente).
    for iid in (hair, top, bottom, skates):
        if iid:
            c.execute("""INSERT OR IGNORE INTO inventory
                         (uid,item_id,end_time,in_use,count,get_time,storage) VALUES (?,?,?,1,0,?,0)""",
                      (uid, iid, PERMANENT_ENDTIME, int(time.time())))
    c.commit(); c.close()
    log(f"[DB] personaje creado uid={uid} nick={nickname!r} gender={gender} "
        f"char_id={char_id} hair={hair} top={top} bottom={bottom} skates={skates}")

def licencia_ffc9_de(uid):
    """Byte de licencia del FFC9 para `uid`. Honesto = derivado de su NIVEL real; si no, la constante vieja."""
    if not LICENCIA_HONESTA:
        return LICENSE_BYTE
    try:
        ch = db_get_char(uid)
        return licencia_de_nivel(level_from_exp((ch["exp"] if ch else 0) or 0))
    except Exception:
        return 1   # ante cualquier fallo, la MÁS BAJA (nunca regalar banda por un error)

def db_set_coin(uid, coin):
    c = db(); c.execute("UPDATE chars SET coin=? WHERE uid=?", (coin & 0xFFFFFFFF, uid)); c.commit(); c.close()

def db_get_mission(uid):
    r = db_get_char(uid)
    return int(r["mission"]) if r else 0

def db_set_mission(uid, mission):
    """FF81 (tutorial/misión escuela): persiste el progreso de misión. Monótono: nunca baja."""
    c = db()
    c.execute("UPDATE chars SET mission = MAX(mission, ?) WHERE uid=?", (int(mission) & 0xFFFFFFFF, uid))
    c.commit(); c.close()

def db_add_exp_coin(uid, dexp, dcoin):
    """★ PERSISTENCIA REAL de la recompensa: suma exp y rcoin al char (clamp a >=0). Devuelve (exp_total, coin_total)."""
    c = db()
    if EXP_LOCK:
        # ★ CANDADO: se ESCRIBE 0, no se "suma 0". Así la exp vuelve a 0 aunque otra vía la haya
        #   subido. Es el único punto del server que escribe chars.exp -> acá no se escapa nada.
        c.execute("UPDATE chars SET exp = 0, coin = MAX(0, coin + ?) WHERE uid=?",
                  (int(dcoin), uid))
    else:
        c.execute("UPDATE chars SET exp = MAX(0, exp + ?), coin = MAX(0, coin + ?) WHERE uid=?",
                  (int(dexp), int(dcoin), uid))
    c.commit()
    row = c.execute("SELECT exp, coin FROM chars WHERE uid=?", (uid,)).fetchone()
    c.close()
    return (row[0], row[1]) if row else (0, 0)
