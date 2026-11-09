"""
pktlog.py — Logger estructurado de packets (JSONL) + herramienta de diff.

Objetivo: convertir el RE de "humano mira hex y adivina" en
"la maquina dice que opcode falta". Dos modos:

  1) Importado por el server  -> graba cada packet como una linea JSON.
  2) CLI                       -> lee esos JSONL y los compara/imprime.

Esquema de cada evento (una linea JSON en packets/<sesion>.jsonl):
  seq   : contador monotono global de la captura
  t     : timestamp HH:MM:SS.mmm
  sess  : etiqueta de la sesion (p.ej. "oraculo" / "mio")
  conn  : id de conexion/usuario
  dir   : "C2S" (cliente->server) o "S2C"
  layer : "login" | "lobby" | "inner"  (inner = comando descifrado dentro de FF55 = el ORO)
  cmd   : "0xNN"
  name  : nombre L9_TYPE_* legible
  len   : longitud del payload
  hex   : bytes crudos en hex (mayusculas)
  note  : texto opcional (campos ya decodificados, etc.)

CLI:
  python pktlog.py dump  <archivo.jsonl>              imprime legible
  python pktlog.py flow  <archivo.jsonl>              solo la secuencia de acciones (name/dir)
  python pktlog.py diff  <oraculo.jsonl> <mio.jsonl>  alinea y marca divergencias
"""

import json
import os
import sys
import datetime

# ── Tabla de nombres de comando (FFnn / FEnn extendidos) ────────────
CMD_NAMES = {
    0x01: "CHECK_VER",      0x02: "LOGIN",          0x03: "MYRECORD",
    0x04: "CREATE_CHAR",    0x06: "JOINCHANNEL",    0x08: "CHAT",
    0x09: "WHISPER",        0x0A: "ROOMLIST",       0x0C: "ROOM_USERLIST",
    0x0E: "REMOVEUSER",     0x10: "CREATEROOM",     0x11: "JOINROOM",
    0x12: "EXITROOM",       0x13: "ROOMINFO",       0x17: "TOGGLEREADY",
    0x19: "EDITCHAR",       0x1A: "EDIT_AVATAR",    0x1B: "EDIT_STAGE",
    0x1C: "EDIT_MUSIC",     0x20: "KICK",           0x23: "MOVECHAR",
    0x24: "STARTGAME",      0x25: "DO_LOADGAME",    0x26: "GAMEDATA_P1",
    0x28: "GAMEOVER",       0x2A: "BUY_DRESS",      0x35: "ANNOUNCE",
    0x3A: "SINGLEPLAY",     0x3D: "DISCONNECT",     0x40: "SESSION_FORWARD",
    0x41: "GET_ROOMINFO",   0x44: "GET_ITEM_ENDDATE", 0x45: "COUPLE",
    0x4B: "CHANGE_ROOM_MODE", 0x4C: "ANTIBOT",      0x4E: "GAMEDATA",
    0x50: "BUY_CASH_ITEM",  0x51: "CASH_AMOUNT",    0x54: "CLIENT_NOTIFY54",
    0x55: "ENC_FF55",       0x56: "HDR_FF56",       0x57: "GET_NOTE_DATA",
    0x59: "CLIENT_TELEMETRY", 0x61: "CHATROOM_ENTER",
    0x63: "CMD_63",         0x65: "CHATROOM_LIST",  0x6E: "ROOMLIST2",
    0x77: "USERINFO",       0x79: "SELECTINGMUSICSTAGE", 0x81: "CMD_81",
    0x8C: "ANIMTIME_SYNC",  0x97: "SERVER_CLOCK",
    0x107: "POS_UPDATE",    0x13B: "MUSIC_ENABLE",
}

# ── ESTADO de los inner-cmds C->S informativos / sin handler (re-escaneo ) ──
#   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#pktlog-estado-de-los-inner-cmds-c-s-informativos-s)

def cmd_name(cmd):
    return CMD_NAMES.get(cmd, f"UNK_{cmd:02X}")

# ── Estado del logger (lado server) ─────────────────────────────────
_DIR = "packets"
_session = os.environ.get("PKTLOG_SESSION", "mio")
_seq = 0
_fh = None

def _open():
    global _fh
    if _fh is None:
        os.makedirs(_DIR, exist_ok=True)
        _fh = open(os.path.join(_DIR, f"{_session}.jsonl"), "a", encoding="utf-8")
    return _fh

def set_session(label):
    """Cambia la etiqueta de captura (cierra el archivo anterior)."""
    global _session, _fh, _seq
    if _fh:
        try: _fh.close()
        except Exception: pass
    _fh = None
    _session = label
    _seq = 0

def pkt(conn, direction, layer, cmd, data, note=""):
    """Graba un evento. NUNCA lanza excepcion: no debe tumbar el server."""
    global _seq
    try:
        _seq += 1
        if isinstance(data, (bytes, bytearray)):
            hx = bytes(data).hex().upper(); ln = len(data)
        else:
            hx = str(data); ln = len(hx) // 2
        ev = {
            "seq": _seq,
            "t": datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "sess": _session, "conn": conn, "dir": direction, "layer": layer,
            "cmd": f"0x{cmd:02X}", "name": cmd_name(cmd), "len": ln, "hex": hx,
        }
        if note:
            ev["note"] = note
        f = _open()
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        f.flush()
    except Exception:
        pass  # el logging jamas debe romper el server

# ── Lado CLI ────────────────────────────────────────────────────────
def _load(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try: out.append(json.loads(line))
                except Exception: pass
    return out

def _arrow(d):
    return "->" if d == "C2S" else "<-"

def cmd_dump(path):
    for e in _load(path):
        arrow = _arrow(e["dir"])
        note = f"  {e['note']}" if e.get("note") else ""
        print(f"[{e['seq']:4d}] {e['t']} u{e['conn']} {arrow} {e['layer']:5s} "
              f"{e['cmd']} {e['name']:18s} len={e['len']:<4d} {e['hex'][:48]}{note}")

def cmd_flow(path):
    """Solo el flujo de acciones semanticas: capa inner + login, ignorando crypto."""
    for e in _load(path):
        if e["layer"] in ("inner", "login") and e["name"] not in ("ENC_FF55", "HDR_FF56"):
            print(f"{_arrow(e['dir'])} {e['name']} (len={e['len']})")

def _flow_keys(events):
    """Secuencia comparable: (dir, name) de la capa semantica."""
    return [(e["dir"], e["name"], e["len"], e.get("hex", ""))
            for e in events
            if e["layer"] in ("inner", "login") and e["name"] not in ("ENC_FF55", "HDR_FF56")]

def cmd_diff(oracle_path, mine_path):
    """
    Alinea las dos sesiones por la secuencia (dir, name) con un LCS simple
    y reporta: solo-en-oraculo (= lo que me FALTA implementar),
    solo-en-mio (= ruido/extra), y diferencias de longitud en los que coinciden.
    """
    a = _flow_keys(_load(oracle_path))   # oraculo = verdad
    b = _flow_keys(_load(mine_path))     # mio
    # LCS por (dir, name)
    na, nb = len(a), len(b)
    dp = [[0] * (nb + 1) for _ in range(na + 1)]
    for i in range(na - 1, -1, -1):
        for j in range(nb - 1, -1, -1):
            if a[i][:2] == b[j][:2]:
                dp[i][j] = dp[i + 1][j + 1] + 1
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])
    i = j = 0
    print(f"=== DIFF  oraculo={oracle_path} ({na})  vs  mio={mine_path} ({nb}) ===")
    print("  '-' = falta en mi server (implementar)   '+' = extra en mi server")
    falta = 0
    while i < na and j < nb:
        if a[i][:2] == b[j][:2]:
            la, lb = a[i][2], b[j][2]
            tag = "  =" if la == lb else f"  ! len {la} vs {lb}"
            print(f"  {_arrow(a[i][0])} {a[i][1]:20s}{tag if tag.strip()!='=' else ''}")
            i += 1; j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            print(f"- {_arrow(a[i][0])} {a[i][1]:20s}  <== ORACULO manda esto, mi server NO")
            falta += 1; i += 1
        else:
            print(f"+ {_arrow(b[j][0])} {b[j][1]:20s}  (extra en mio)")
            j += 1
    while i < na:
        print(f"- {_arrow(a[i][0])} {a[i][1]:20s}  <== ORACULO manda esto, mi server NO")
        falta += 1; i += 1
    while j < nb:
        print(f"+ {_arrow(b[j][0])} {b[j][1]:20s}  (extra en mio)")
        j += 1
    print(f"\n>>> {falta} opcode(s) que el oraculo usa y tu server todavia no. Esos son tu TODO.")

def _usage():
    print(__doc__)
    sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        _usage()
    op = sys.argv[1]
    if op == "dump":
        cmd_dump(sys.argv[2])
    elif op == "flow":
        cmd_flow(sys.argv[2])
    elif op == "diff" and len(sys.argv) >= 4:
        cmd_diff(sys.argv[2], sys.argv[3])
    else:
        _usage()
