# -*- coding: utf-8 -*-
"""Experiencia -> nivel -> licencia -> canal.

En R2Beat el canal al que podes entrar depende de tu nivel: Estrella
(1-30), Luna (31-60) y Sol (61+). Aca se calcula esa cadena y se arma
la lista de canales que ve el jugador.

--
Parte del servidor abierto de R2Beat. Ver LEEME.md en la raiz del proyecto.
"""

import os

from .ajustes import LOCAL_IP, LUNA_MIN_EXP, SOL_MIN_EXP


def tier_from_exp(exp):
    """(chan_idx, license, tier_name) según banda de exp. chan_idx -> índice en LOBBY_CHANNELS: 0=Estrella 1=Luna 2=Sol.
    Licencias: N=estrella X=luna Z=sol. Honesto: misma banda que decide el matcher del cliente."""
    exp = exp or 0
    if exp >= SOL_MIN_EXP:   return (2, "Z", "sol")
    if exp >= LUNA_MIN_EXP:  return (1, "X", "luna")
    return (0, "N", "estrella")

# ══════════════════════════════════════════════════════════════════════════════════════════...
#   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#progresion)
LICENCIA_POR_BANDA = {"estrella": 3, "luna": 5, "sol": 7}
# R2_LICENCIA_HONESTA=0 vuelve al comportamiento viejo (todos con `LICENSE_BYTE`) sin tocar código.
LICENCIA_HONESTA = os.environ.get("R2_LICENCIA_HONESTA", "1") != "0"

# ★ 2026-08-02 — LOS 7 PELDAÑOS, iguales a los de `CHANNEL_LADDER`. El selector del juego lista
#   exactamente estos 7 y en este orden, y es un TOPE (marca el tuyo y todos los de abajo):
#     1 Novato · 2 Novato Estrella · 3 Estrella · 4 Novato Lunar · 5 Luna · 6 Novato Solar · 7 Sol
#   Antes se derivaba de `tier_from_exp`, que sólo distingue 3 bandas y caía en 3 por default => a un
#   Novato le marcaba "Estrella". La licencia tiene que seguir al NIVEL, igual que el canal.
NOMBRE_LICENCIA = {1: "Novato", 2: "Novato Estrella", 3: "Estrella", 4: "Novato Lunar",
                   5: "Luna", 6: "Novato Solar", 7: "Sol"}   # ★ ÚNICA fuente de estos nombres


def licencia_de_nivel(lvl):
    if lvl <= 10:  return 1     # novato            (bucket 1)
    if lvl <= 20:  return 2     # novato estrella   (bucket 2)
    if lvl <= 30:  return 3     # estrella          (bucket 3)
    if lvl <= 40:  return 4     # novato lunar      (bucket 4)
    if lvl <= 60:  return 5     # luna              (bucket 5)
    if lvl <= 70:  return 6     # novato solar      (bucket 6)
    return 7                    # sol               (bucket 7)


# ★ Tabla de umbrales de NIVEL, extraída del cliente (0x6E90B8, 100 u32). nivel = cantidad de umbrales
#   que el exp alcanza (el cliente para en el 1º que no alcanza; tabla creciente). SolUser exp 25M -> nivel 72.
LEVEL_TABLE = [
    0x1f4,0xbb8,0x1b58,0x2c88,0x3f70,0x543c,0x6b1c,0x8446,0x9ff5,0xc12c,
    0xe264,0x104ef,0x128dd,0x14e3a,0x17516,0x19d80,0x1c787,0x1f33d,0x220b3,0x25912,
    0x29170,0x2cd31,0x30c87,0x34faa,0x396d4,0x3e244,0x4323a,0x486fc,0x4e0d5,0x55209,
    0x5c33d,0x63d80,0x6c18b,0x75026,0x7ea29,0x8907f,0x94423,0xa0626,0xad7ae,0xbe3e2,
    0xcf016,0xe171c,0xf5ba3,0x10c09d,0x12494a,0x13f93b,0x15d45f,0x17df05,0x1a1df0,0x1cfdd5,
    0x1fddb9,0x2315ed,0x26b107,0x2abae8,0x2f40d9,0x3451be,0x39fe44,0x405917,0x477722,0x50b7cb,
    0x59f873,0x6484b8,0x708b06,0x7e404c,0x8de0dd,0x9fb183,0xb400a6,0xcb27ab,0xe58c77,0x1086371,
    0x12b3a6b,0x153a46e,0x18285ce,0x1b8e766,0x1f7fc72,0x2412958,0x2960b81,0x2f8827d,0x36aba98,0x403c855,
    0x49cd612,0x5517085,0x6268d2c,0x7220606,0x84ac2b5,0x9a8e908,0xb461642,0xd2da2aa,0xf6cf14d,0x127b5c40,
    0x1589c733,0x1934adf0,0x1d9b5c6c,0x22e36101,0x2939cce7,0x30d4b4c8,0x39f4fe3d,0x44e88995,0x5555da9a,0xffffffff,
]
def level_from_exp(exp):
    """Nivel numérico (1..99) desde exp, replicando el loop del cliente (0x451480 / tabla 0x6E90B8)."""
    exp = exp or 0
    lvl = 0
    for t in LEVEL_TABLE:
        if exp < t:
            break
        lvl += 1
    return lvl
# Cuentas de test (username, char-nick, exp de su banda). pass = "1234a" (mismo hash que SAMPLE). Se siembran en db_init.
TIER_ACCOUNTS = [
    # (USUARIO para entrar, APODO del personaje, experiencia). ⚠ Son dos cosas
    # distintas: se entra con "estrella", no con "EstrellaUser".
    ("estrella", "EstrellaUser", 300000),     # banda estrella (bucket 3)
    ("luna",     "LunaUser",     2000000),     # banda luna     (bucket 5)
    ("sol",      "SolUser",      25000000),    # banda sol      (bucket 7) — nivel 72 (tabla 0x6E90B8)
]
MINIMAL_PICKERS = False  # False = MyRecord puebla #32 SONGS(64) + #34 STAGES(16) => pickers con música (combo que juega con game2_lab). True = counts=0 (test c0000417 virgen).
                        # crash del menú viene de la lista que recorre el picker (EDI=0xffff, stride 0x2cc)
                        #   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#progresion-crash-del-men-viene-de-la-lista-que-rec)
LIBRE_STATUS = int(os.environ.get("R2_LIBRE_STATUS", "0x09"), 0)
LIBRE_MODO6  = LIBRE_STATUS in (0x47, 0x48, 0x49)   # ⇒ campoB=6 ⇒ el cartel lleva la cabecera extendida

# HALLAZGO: **el PUERTO 48035 está HORNEADO EN EL CLIENTE**
#   (cómo se descubrió: docs/NOTAS-DE-REVERSING.md#progresion-hallazgo-el-puerto-48035-est-horneado-e)
LIBRE_PORT = int(os.environ.get("R2_LIBRE_PORT", "48035"), 0)

LOBBY_CHANNELS = [
    # ★ LOS 3 CANALES REALES, todos VIVOS, cada uno con SU banda y SU propio PUERTO (canal = servidor aparte;
    # cambiar de uno a otro = reconexión a su puerto = los "segundos" que describió el usuario).
    # (cid, nombre, status, port).  status->campoA (0x56FEEA): 0x0F->bl(estrella), 0x0C->1(luna), 0x08->2(sol).
    (1, "Estrella", 0x0F, 48005),   # banda estrella (bucket ~3, level 21-30)
    (2, "Luna",     0x0C, 48015),   # banda luna     (bucket  5, level 41-60)
    (3, "Sol",      0x08, 48025),   # banda sol      (bucket  7, level 71+)
    (4, "Libre", LIBRE_STATUS, LIBRE_PORT),   # ★ LIBRE: status 0x09 -> campoA 12 -> NO matchea bucket -> no auto-connect,
                                    #   solo aparece en la lista para JOIN MANUAL (cualquier licencia entra).
]
LOBBY_PORTS = sorted({c[3] for c in LOBBY_CHANNELS})   # puertos a escuchar (un listener de lobby por puerto)

# Los 7 PELDAÑOS de canal. No son "3 bandas repetidas": cada uno tiene su nombre
# propio, igual que en el selector de licencia del juego, así el canal y la licencia
# se llaman igual y no hay dos vocabularios. Los tres lobbys (Estrella/Luna/Sol) se
# reparten estos 7 peldaños; varios comparten puerto.
CHANNEL_LADDER = [
    # byte, nombre visible,    lobby (=puerto), niveles
    (0x01, "Novato",          "Estrella", "1-10"),
    (0x04, "Novato Estrella", "Estrella", "11-20"),
    (0x0F, "Estrella",        "Estrella", "21-30"),
    (0x05, "Novato Lunar",    "Luna",     "31-40"),
    (0x0C, "Luna",            "Luna",     "41-60"),
    (0x07, "Novato Solar",    "Sol",      "61-70"),
    (0x08, "Sol",             "Sol",      "71+"),
]


def build_channel_list():
    """La lista de canales que ve el jugador: [(id, nombre, byte, IP, puerto)].

    ⚠ El id tiene que ser ÚNICO. El cliente lo usa como clave para guardar cada
    canal; si dos se repiten, uno pisa al otro y desaparece de la lista sin ningún
    mensaje de error."""
    puerto_de = {nombre: puerto for (_c, nombre, _s, puerto) in LOBBY_CHANNELS}
    out = []
    for i, (byte, nombre, lobby, _niveles) in enumerate(CHANNEL_LADDER):
        puerto = puerto_de.get(lobby)
        if puerto is not None:          # si ese lobby no está publicado, el peldaño tampoco
            out.append((100 + i, nombre, byte, LOCAL_IP, puerto))
    # Libre va siempre al final y con su byte propio: no participa de la escalera,
    # no se auto-conecta a él nadie, y entra cualquiera sin importar el nivel.
    for (cid, nombre, byte, puerto) in LOBBY_CHANNELS:
        if nombre == "Libre":
            out.append((cid, nombre, byte, LOCAL_IP, puerto))
    return out


# Sólo para medir: reactiva un envío extra al entrar al canal Libre que en su momento
# dejaba al jugador SIN banda. Apagado a propósito; no hace falta para jugar.
LIBRE_FF13 = os.environ.get("R2_LIBRE_FF13", "0") != "0"
# Tope de licencia en el canal Libre. 0 = usar la licencia real del jugador (lo normal).
# Ponerlo en 7 hace que en Libre todos puedan jugar todas las canciones.
LIBRE_LIC = int(os.environ.get("R2_LIBRE_LIC", "0"), 0)
