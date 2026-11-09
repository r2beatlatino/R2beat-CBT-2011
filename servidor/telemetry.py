# -*- coding: utf-8 -*-
"""
telemetry.py — Telemetría de SOLO LECTURA del server R2Beat, como MÓDULO aparte.

Diseño: NO ensucia server-clean.py. Se engancha con 2-3 líneas:
  import telemetry
  ...  # en handle_inner:    telemetry.observe(self, cmd, data)
  ...  # en main():          asyncio.create_task(telemetry.writer(CONNECTIONS, nick_of))

Mantiene su PROPIO estado (no toca la clase de conexión). El conjunto "online" sale del dict
CONNECTIONS que el server ya mantiene; el ESTADO (lobby/sala/jugando) lo deriva de los comandos
que pasan por observe(). Escribe un snapshot JSON atómico que el patch server sirve en /status
(con password). Read-only: no da acceso al VPS, solo info agregada (online, nick, estado).

stdlib only.
"""
import os, json, time, asyncio

from .ajustes import CARPETA_DATOS

# Quien esta online ahora mismo. Se reescribe cada pocos segundos y se regenera
# solo, asi que borrarlo no rompe nada.
STATUS_FILE = os.environ.get("R2_STATUS") or os.path.join(CARPETA_DATOS, "estado.json")
WRITE_EVERY = 3.0  # segundos

# estado interno propio: uid -> {"state", "since", "login"}
_STATE = {}

# cmd inner -> estado del jugador. 0x3A depende del type (data[0]).
def _state_for(cmd, data):
    if cmd == 0x06:                      # JOINCHANNEL -> en el lobby/canal
        return "lobby"
    if cmd in (0x10, 0x11):              # CREATEROOM / JOINROOM -> en sala
        return "sala"
    if cmd == 0x12:                      # EXITROOM -> vuelve al lobby
        return "lobby"
    if cmd == 0x3A:                      # SINGLEPLAY: type2=entra a jugar, type3=vuelve a la sala
        t = data[0] if data else 0
        if t == 2: return "jugando"
        if t == 3: return "sala"
    return None                          # otros cmds no cambian el estado

def observe(conn, cmd, data=b""):
    """Engancha en handle_inner. Deriva el estado del jugador del comando. Nunca lanza."""
    try:
        uid = getattr(conn, "user_id", 0)
        if not uid:
            return
        now = time.time()
        st = _STATE.get(uid)
        if st is None:
            st = _STATE[uid] = {"state": "lobby", "since": now, "login": now}
        new = _state_for(cmd, data)
        if new and new != st["state"]:
            st["state"] = new; st["since"] = now
    except Exception:
        pass

def snapshot(connections, nick_of=None):
    """Construye el dict de estado SOLO de los uids realmente conectados (connections)."""
    now = time.time()
    online = set(connections.keys())
    # podar estados de uids ya desconectados
    for uid in list(_STATE.keys()):
        if uid not in online:
            _STATE.pop(uid, None)
    users = []
    for uid in sorted(online):
        st = _STATE.get(uid) or {"state": "lobby", "since": now, "login": now}
        nick = ""
        if nick_of:
            try: nick = nick_of(uid) or ""
            except Exception: nick = ""
        users.append({
            "uid": uid,
            "nick": nick or f"uid{uid}",
            "state": st["state"],
            "online_s": int(now - st["login"]),
            "state_s": int(now - st["since"]),
        })
    counts = {"lobby": 0, "sala": 0, "jugando": 0}
    for u in users:
        if u["state"] in counts:
            counts[u["state"]] += 1
    return {"ts": int(now), "online": len(users), "counts": counts, "users": users}

def write_once(connections, nick_of=None, path=None):
    """Escribe el snapshot atómicamente. Devuelve el dict escrito (o None si falló)."""
    path = path or STATUS_FILE
    try:
        d = snapshot(connections, nick_of)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, path)
        return d
    except Exception:
        return None

async def writer(connections, nick_of=None, path=None, every=WRITE_EVERY):
    """Task: escribe el snapshot cada `every` segundos. Lanzar con asyncio.create_task(...)."""
    while True:
        write_once(connections, nick_of, path)
        await asyncio.sleep(every)
