# -*- coding: utf-8 -*-
"""Log en consola y en archivo.

Imprime cada paquete que entra y sale con flecha de direccion, nombre
del comando, largo y un preview en hexadecimal. Lo que va dentro del
FF55 (la capa cifrada) se muestra indentado.

--
Parte del servidor abierto de R2Beat. Ver LEEME.md en la raiz del proyecto.
"""

import datetime
import os

from . import pktlog
from .ajustes import RUTA_LOG


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# Log a ARCHIVO en vivo (como el fake_server.log de legacy/work): texto plano sin
# color, para poder leerlo sin copiar/pegar. Se abre en main() (modo "w" = fresco
# por corrida). La consola sigue mostrando color; el archivo guarda texto limpio.
LOG_FILE = RUTA_LOG
_LOGFH = None

def _logwrite(s):
    if _LOGFH is not None:
        try:
            _LOGFH.write(s + "\n"); _LOGFH.flush()
        except Exception:
            pass

def log(msg):
    ts = "[" + datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3] + "]"
    line = f"{ts} {msg}"
    print(line)
    _logwrite(line)

# ============================================================
# Log de consola RICO (mejor que work/legacy): cada packet se imprime EN VIVO con
# flecha de dirección, nombre de comando (tabla pktlog.CMD_NAMES), longitud y
# preview hex; la capa 'inner' (lo descifrado dentro de FF55 = el oro) se indenta
# y resalta. La captura estructurada a packets/clean.jsonl se mantiene intacta.
# ============================================================
from .pktlog import cmd_name as _cmd_name

LOG_HEXBYTES = 28        # bytes de payload a mostrar en consola (0 = none)
LOG_COLOR = True         # colores ANSI

def _enable_vt():
    """Habilita secuencias ANSI en la consola de Windows (VT processing)."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        k.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return True
    except Exception:
        return False

if LOG_COLOR and not _enable_vt():
    LOG_COLOR = False

class _C:
    C2S = "\033[92m"   # verde   = cliente -> server
    S2C = "\033[96m"   # cyan    = server -> cliente
    INNER = "\033[95m" # magenta = capa interna (FF55 descifrado)
    DIM = "\033[90m"
    WARN = "\033[93m"
    RST = "\033[0m"

def _c(s, color):
    return f"{color}{s}{_C.RST}" if LOG_COLOR else s

def _hexprev(data, n=LOG_HEXBYTES):
    b = bytes(data) if isinstance(data, (bytes, bytearray)) else b""
    if n <= 0 or not b:
        return ""
    s = " ".join(f"{x:02X}" for x in b[:n])
    if len(b) > n:
        s += f" …+{len(b) - n}b"
    return s

def wire(conn, direction, layer, cmd, data, note="", warn=False):
    """Captura (pktlog/JSONL) + log a archivo (plano) + consola (color) de un packet."""
    pktlog.pkt(conn, direction, layer, cmd, data, note=note)
    ts = "[" + datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3] + "]"
    arrow = "C→S" if direction == "C2S" else "S→C"
    is_inner = (layer == "inner")
    base = _C.INNER if is_inner else (_C.C2S if direction == "C2S" else _C.S2C)
    dlen = len(data) if isinstance(data, (bytes, bytearray)) else len(str(data)) // 2
    cmdtag = f"0x{cmd:03X}" if cmd >= 0x100 else f"0x{cmd:02X}"
    name = _cmd_name(cmd)
    indent = "    └ " if is_inner else "  "
    head_plain = f"{arrow} {layer:<5} {cmdtag:<5} {name:<16}"
    tail = f" len={dlen:<4} {_hexprev(data)}"
    if note:
        tail += "  · " + note
    if warn:
        tail += "  ⚠ sin handler"
    # archivo: texto plano
    _logwrite(f"{ts} {indent}{head_plain}{tail}")
    # consola: con color
    head = _c(head_plain, _C.WARN if warn else base)
    line = f"{ts} {indent}{head} len={dlen:<4} {_c(_hexprev(data), _C.DIM)}"
    if note:
        line += "  " + _c("· " + note, _C.DIM)
    if warn:
        line += "  " + _c("⚠ sin handler", _C.WARN)
    print(line)


def abrir_log():
    """Abre el archivo de log. Lo llama correr_servidor.py al arrancar.

    Se escribe con BOM para que el Bloc de notas de Windows lo abra como UTF-8
    y no muestra los acentos rotos."""
    global _LOGFH
    try:
        _LOGFH = open(LOG_FILE, "w", encoding="utf-8-sig")
    except OSError as e:
        print(f"(aviso: no se pudo abrir el log {LOG_FILE}: {e})")
