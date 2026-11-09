#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
  IMPORTADOR DE MUSICA PARA R2BEAT
=============================================================================

  Agrega canciones nuevas al juego: el audio, la tapa, la ficha en la lista y
  las notas que hay que pisar mientras suena.

  Se abre con doble clic (o `python importador.py`) y tiene ventana. Si preferis
  la consola, o queres importar muchas de una, usá `importar.py`, que usa
  exactamente estas mismas funciones.

  QUE LE TENES QUE DAR (carpeta de "entrada", se busca dentro de todo lo que haya)
  --------------------------------------------------------------------------
    *.txt  con <Music ...>   la ficha: nombre, autor, genero, dificultad
    *.xml  con <TITLE ...>   el chart, o sea donde cae cada nota
    *.bin                    notas ya convertidas, si alguien te las paso listas
    *.pak  en rnr_music/     el audio
    *.bmp                    la tapa (256x128)

  QUE HACE CUANDO APRETAS "APLICAR"
  ---------------------------------
  En el CLIENTE (lo modifica, con copia de seguridad .bak):
    - mete el chart y la ficha adentro de rnr_script.pak
    - mete la tapa adentro de rnr_image_lt.pak
    - copia el audio a rnr_music/
    - regenera preview_map.txt, que es lo que hace que la cancion SUENE cuando
      la recorres en el selector. Sin esto la cancion entra pero queda muda ahi.

  Para el SERVIDOR (NO lo toca; deja los archivos y vos decidis):
    - salida/notas/<Index>.bin      las notas
    - salida/canciones_nuevas.txt   las lineas para la lista del servidor
  Para copiarlos al servidor de verdad:  python importar.py aplicar

  SI EL INDEX YA EXISTE en el cliente, pregunta que hacer (reemplazar, cambiarle
  el numero, saltearla). "Reemplazar todo" y "Saltar todo" fijan la decision para
  el resto de la tanda.

  QUE NECESITAS: Python 3.10+ y r2pak.exe (la herramienta que abre y cierra los
  .pak del juego). Las rutas se configuran en config.json o desde la ventana.
=============================================================================
"""
import os, re, sys, json, shutil, struct, zlib, subprocess, threading, queue, unicodedata, filecmp, tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
# Caché ESTABLE de portadas, fuera del árbol de la herramienta (que puede vivir dentro de
# OneDrive) y fuera de _tmp_scan (que se re-arma en cada escaneo). El preview y el apply
# leen la portada de acá, así no dependen de la ruta efímera del .r2n desempacado, que
# cambia de índice entre escaneos y OneDrive puede deshidratar -> "sin portada" falso.
COVER_CACHE = os.path.join(tempfile.gettempdir(), "r2b_importer_covers")
# Caché de mp3 extraídos para el botón Preview (misma idea que COVER_CACHE, fuera de OneDrive).
PREVIEW_CACHE = os.path.join(tempfile.gettempdir(), "r2b_importer_preview")
ROOT = os.path.dirname(TOOL_DIR)
# El conversor de notas vive al lado de este archivo.
sys.path.insert(0, TOOL_DIR)
try:
    import xml2notes  # parse_xml / build_blob / verify_blob
except ImportError:
    xml2notes = None

CONFIG_PATH = os.path.join(TOOL_DIR, "config.json")
DEFAULTS = {
    # Todas estas rutas se pisan con config.json o desde la ventana.
    # Copiá config-ejemplo.json a config.json y editalo con TUS carpetas.
    "input":  os.path.join(TOOL_DIR, "entrada"),   # de donde saco la musica nueva
    "client": "",                                  # carpeta donde esta instalado el juego
    "r2pak":  "",                                  # ruta a r2pak.exe
    "output": os.path.join(TOOL_DIR, "salida"),    # donde dejo lo que va al servidor
    # Carpeta rnr_music de OTRO cliente, para sacar de ahi los .pak compartidos
    # que el material suelto no trae (por ejemplo zsound.pak). Opcional.
    "audio_extra": "",
}

# portadas del cliente: 256x128 16bpp. Un desfase de hasta este margen se corrige solo
# (una imagen 256x129 es un error de exportación, no otra imagen); más que eso se pregunta.
PORTADA_SIZE = (256, 128)

# ★ SOLO PARA EXPERIMENTOS (A/B): True vuelve al comportamiento VIEJO y ROTO de poner
#   Combo = nº de notas crudo, en vez de calcularlo como el exe (ver combo_del_exe).
#   Con esto el juego tira el popup '[올콤카운트에러] Combo:c(X),s(Y)'. Dejar en False.
COMBO_CRUDO = False

# a partir de esta cantidad de filas, el escaneo arranca todo DESMARCADO (flujo selectivo)
UMBRAL_TANDA_GRANDE = 50
PORTADA_MARGEN = 16

# campos editables de una canción -> atributo del music_lt (para el parcheo quirúrgico)
EDITABLE_ATTRS = {"name": "Name", "level": "Level", "genre": "Genre", "artist": "Artist",
                  "coin": "Coin", "time": "Time", "combo": "Combo", "stat": "Stat",
                  "file_rel": "File", "image_rel": "Image"}

# cierre "></Music>" (music_lt clásico) o "/>" (los txt de los .r2n del R2 Online)
MUSIC_BLK_RE = re.compile(rb"<Music\b.*?(?:</Music>|/>)", re.S)
ATTR_RE = re.compile(r'([A-Za-z_]\w*)\s*=\s*"([^"]*)"')


# ---------------------------------------------------------------- helpers ----

def decode_best(b):
    """Decodifica bytes de campos de music_lt probando encodings conocidos del proyecto."""
    for enc in ("ascii", "utf-8", "euc-kr", "gb18030"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("latin-1", "replace")


def unencodable(txt):
    """Caracteres que el music_lt (euc-kr/cp949) NO puede representar. Ojo: ni euc-kr ni
    cp949 tienen á/é/í/ó/ú/ñ, así que un nombre con acentos se guarda como '?' — conviene
    avisarlo antes de que aparezca 'Coraz?n' en el selector."""
    return sorted({c for c in str(txt)
                   if c != "?" and c.encode("euc-kr", "replace") == b"?"})


def texto_para_euckr(txt):
    """Deja un texto escribible en euc-kr SIN perder legibilidad.

    El music_lt va en euc-kr, que no tiene á/é/í/ó/ú/ñ ni chino: escribirlos directo
    deja 'Confusi?n' / 'Jungle Fever (?林?)' en el selector. En vez de avisar y que el
    usuario lo corrija a mano (y se olvide), se normaliza acá:
      1. tildes -> letra base   (Confusión -> Confusion, Sueño -> Sueno)
      2. lo que AÚN no entra (CJK) se descarta
      3. se limpian los paréntesis/corchetes que quedaron vacíos y los espacios dobles
         (Jungle Fever (丛林热) -> Jungle Fever, no 'Jungle Fever ()')
    """
    if not txt:
        return txt
    # NFD + quitar combining marks baja tildes (ó->o); pero NFD también parte el Hangul
    # en jamo (외 -> ㅇ+ㅚ, combining-class 0, NO se filtran) que euc-kr no escribe suelto.
    # Recomponer con NFC re-arma las sílabas coreanas y deja las tildes ya bajadas.
    base = unicodedata.normalize("NFC", "".join(
        c for c in unicodedata.normalize("NFD", str(txt))
        if not unicodedata.combining(c)))
    # un grupo entre paréntesis/corchetes que pierda ALGO se va entero: dejarlo a medias
    # es peor que sacarlo ('Jungle Fever (丛林热)' -> 'Jungle Fever', no 'Jungle Fever (林)',
    # porque 林 sí existe en euc-kr pero 丛/热 no).
    base = re.sub(r"[\(\[][^\)\]]*[\)\]]",
                  lambda m: "" if unencodable(m.group(0)) else m.group(0), base)
    base = "".join(c for c in base if not unencodable(c))
    base = re.sub(r"[\(\[]\s*[\)\]]", "", base)
    return re.sub(r"\s{2,}", " ", base).strip()


def parse_music_entries(raw):
    """bytes de un music_lt (o fragmento) -> lista de dicts con los attrs decodificados."""
    out = []
    for m in MUSIC_BLK_RE.finditer(raw):
        txt = decode_best(m.group(0))
        attrs = {k.lower(): v for k, v in ATTR_RE.findall(txt)}
        if "index" not in attrs:
            continue
        attrs["_raw"] = m.group(0)
        out.append(attrs)
    return out


def chart_info(data):
    """bytes de un chart -> dict con title/bpm/length/bgm_folder/effect_folders/has_area."""
    g = lambda pat: (re.search(pat, data) or [None, b""])[1]
    title = decode_best(g(rb'<TITLE Name="([^"]*)"'))
    bpm = g(rb'<BPM[^>]*BPM="([\d.]+)"')
    length = g(rb'<LENGTH Value="([\d.]+)"')
    bgm = g(rb'<BGM Name="([^/"]+)/').decode("ascii", "replace").lower()
    effs = {e.decode("ascii", "replace").lower()
            for e in re.findall(rb'<EFFECT Snd="([^/"]+)/', data)}
    # mapa carpeta -> {basenames que el chart pide ahí} (para resolver carpetas hermanas
    # sin pak propio re-empacando el pak que SÍ tiene esos archivos; quirk de R2Single)
    folder_files = {}
    for m in re.finditer(rb'(?:<BGM Name|<EFFECT Snd)="([^/"\\]+)[/\\]([^"]+)"', data):
        fo = m.group(1).decode("ascii", "replace").lower()
        fn = m.group(2).decode("latin-1", "replace").replace("\\", "/").split("/")[-1].lower()
        folder_files.setdefault(fo, set()).add(fn)
    return {
        "title": title,
        "bpm": float(bpm) if bpm else 0.0,
        "length": float(length) if length else 0.0,
        "bgm": bgm,
        "effects": sorted(f for f in effs if f),
        "folder_files": folder_files,
        "has_area": b"<AREA" in data,
        "area_count": data.count(b"<AREA"),
    }


def strip_area(data):
    r"""Quita las notas (`<AREA ...>`) del chart — el server las sirve. Hay que filtrar la
    apertura Y el cierre: `</AREA>` NO contiene la subcadena `<AREA` (tras el `<` viene `/`),
    así que si el AREA viene en DOS líneas (`<AREA ...>` en una, `</AREA>` en la siguiente,
    formato de varios charts de R2Single) el filtro viejo dejaba los `</AREA>` HUÉRFANOS ->
    `XMLParser: Close tag not match, </AREA` en el cliente -> crash/silencio. Se filtran
    ambos: sirve para AREA de 1 línea (`<AREA...></AREA>`) y de 2."""
    return b"\n".join(ln for ln in data.split(b"\n")
                      if b"<AREA" not in ln and b"</AREA" not in ln)


def fix_chart_folders(data, folder_fix):
    r"""Reescribe en el chart las referencias de carpeta typo (BGM/EFFECT): `="<typo>/` ->
    `="<ok>/`. Para el quirk de R2Single donde una dificultad pide sus keysounds con un typo
    de carpeta (ej. `e_song_2257` con "_" de más) que apunta a los MISMOS archivos de una
    carpeta real (`e_song2257`) — se reusa el pak que ya existe, sin duplicarlo."""
    for typo, ok in (folder_fix or {}).items():
        pat = re.compile(rb'((?:Snd|Name)=")' + re.escape(typo.encode()) + rb'([/\\])', re.I)
        data = pat.sub(lambda m, ok=ok: m.group(1) + ok.encode() + m.group(2), data)
    return data


def is_notes_bin(path):
    """True si el archivo parsea como blob FF57 (Bloque A claro + Bloque B zlib)."""
    try:
        d = open(path, "rb").read()
        c = struct.unpack_from("<i", d, 0)[0]
        if c < 0 or c > 10000:
            return False
        off = 4 + c * 8
        raw = zlib.decompress(d[off:])
        return len(raw) % 6 == 0
    except Exception:
        return False


def bin_note_count(path):
    d = open(path, "rb").read()
    c = struct.unpack_from("<i", d, 0)[0]
    return len(zlib.decompress(d[4 + c * 8:])) // 6


def bin_notes(path):
    """Notas crudas del blob FF57: [(coord, kind, level, fx), ...]."""
    d = open(path, "rb").read()
    c = struct.unpack_from("<i", d, 0)[0]
    raw = zlib.decompress(d[4 + c * 8:])
    return [struct.unpack_from("<HBBH", raw, i) for i in range(0, len(raw), 6)]


def combo_del_exe(notas):
    """El Combo que el cliente EXIGE en el music_lt (el 's' del popup de error).

    ★ NO es la cantidad de notas. Sacado del propio exe (R2BeatMultiTest.exe,
      loop de notas 0x4B0170-0x4B0672, que avanza de a 6 bytes = 1 nota):
        - el contador vive OFUSCADO en [obj+0x10] (xor con [0x73D1D4]), y al final
          0x4B0589 compara `music_lt.Combo == contador`; si difieren salta el popup
          '[올콤카운트에러] Combo:c(X),s(Y)'.
        - Kind 0x18  -> suma 2   (0x4B0358 `cmp bl,0x18` -> rama del `add ecx,2` en 0x4B042A)
        - Kind >=0x80 (holds) -> suma 1, SALVO (Kind-0x80)%3 == 1, que suma 0
          (0x4B029A: idiv 3; `cmp edx,1; je` saltea el `inc`)
        - cualquier otro Kind -> suma 1  (0x4B0445 `inc edx`)

    ⚠ El exe no compara el Kind crudo: lo pasa antes por 0x49CA80 y 0x49B910 junto con
      Level y Fx. En todo lo medido hasta ahora ese valor coincide con el Kind, pero si
      alguna canción falla el Combo, ESE es el primer lugar donde mirar.

    ★ AUTOPLAY (Fx&0x8000): las notas invisibles/sonido-solo NO las cuenta el cliente
      (rt+0x10=1 las excluye del juez), así que acá TAMPOCO se cuentan. Sin esto salta el
      popup c(con autoplay)/s(sin) — caso 444: c561/s555 = 6 autoplay de más. Ver
      memoria r2beat-autoplay-notes (SOLUCIÓN PURA-BIN 2026-07-22).
    """
    total = 0
    for _coord, kind, _level, fx in notas:
        if fx & 0x8000:                 # autoplay invisible/no-juzgable -> el cliente NO la cuenta
            continue
        if kind >= 0x80:
            if (kind - 0x80) % 3 != 1:
                total += 1
        elif kind == 0x18:
            total += 2
        else:
            total += 1
    return total


def paks_bloqueados(client):
    """Archivos del cliente que NO se pueden escribir (típico: el juego está abierto).

    Se chequea ANTES de tocar nada: si no, el importador copia el audio, genera las
    notas y recién falla al desplegar el pak, dejando el cliente a medio importar
    (audio sí, entrada/chart/portada no). Windows bloquea el archivo mientras el
    R2Beat.exe lo tiene mapeado.
    """
    malos = []
    for nombre in ("rnr_script.pak", "rnr_image_lt.pak", "preview_map.txt"):
        p = os.path.join(client, nombre)
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r+b"):
                pass
        except OSError:
            malos.append(nombre)
    return malos


# --- Bandas de dificultad del picker (RE 2026-07-21, parser 0x40db80 VAs 0x40df19-0x40df3b) ---
# ⚠⚠ SUPERADO 2026-07-23: la banda de importación YA NO sale del Level (esto), sale del PREFIJO del chart
#   (ver nivel_por_prefijo más abajo). resolve_difficulty_levels / TIER_* quedan SOLO como referencia del RE
#   y NO se usan en _do_apply. NO revertir a este modelo sin permiso explícito del usuario.
# El cliente DERIVA el "slot de dificultad" del atributo Level con 7 TIERS (NO 4 bandas, y NO
# usa el prefijo e_/n_/h_ del chart). Umbrales verificados en el binario (gate de licencia
# 0x451c60, caps 4/5/7/10/14/17/20) + validados contra 137 canciones del cliente 2013 (136 con
# todas sus dificultades en tiers distintos). Cada CANAL visible admite 2 dificultades:
#   Estrella: novato(1-4) + novato-estrella(5) + estrella(6-7)
#   Luna:     novato-luna(8-10) + luna(11-14)
#   Sol:      novato-sol(15-17) + sol(18-20)
# Dos dificultades en el MISMO tier de 7 colapsan (caso viejo "888" con el modelo grueso de 4
# bandas: metía 9 y 14 en "luna 8-14" y empujaba una a sol). Con 7 tiers, 6/9/14 caen en
# estrella/novato-luna/luna → las 3 se ven, NINGUNA en sol. Ver SESION_2026-07-22_tiers.md.
TIER_CAPS   = [4, 5, 7, 10, 14, 17, 20]                       # cota superior de cada tier
TIER_RANGES = [(1, 4), (5, 5), (6, 7), (8, 10), (11, 14), (15, 17), (18, 20)]
# 0 novato · 1 novato-estrella · 2 estrella · 3 novato-luna · 4 luna · 5 novato-sol · 6 sol

def tier_of(L):
    for i, cap in enumerate(TIER_CAPS):
        if L <= cap:
            return i
    return 6   # >=18 = sol (clamp)

def resolve_difficulty_levels(levels):
    """levels = niveles (int) de las dificultades de UNA canción. Devuelve niveles que caen en
    TIERS (de 7) distintos, tocando lo MÍNIMO y preservando el orden. Con niveles ya en tiers
    distintos (el caso normal) NO cambia nada. Con >7 dificultades los deja como están."""
    import itertools
    order = sorted(range(len(levels)), key=lambda i: levels[i])
    sl = [levels[i] for i in order]
    k = len(sl)
    if k < 2 or k > 7:
        return list(levels)
    if len({tier_of(L) for L in sl}) == k:       # ya en tiers distintos -> no tocar nada
        return list(levels)
    best = None
    for combo in itertools.combinations(range(7), k):   # tiers estrictamente crecientes
        cost, solbad, newl = 0, 0, []
        for L, t in zip(sl, combo):
            if tier_of(L) == t:
                newl.append(L)
            else:
                lo, hi = TIER_RANGES[t]
                nl = min(max(L, lo), hi); cost += abs(nl - L); newl.append(nl)
            # un chart de nivel <=14 NO debe terminar en el CANAL sol (tiers 5/6 = niveles
            # 15-20). Ante una colisión en luna (p.ej. 12 y 14), preferir bajar la fácil a
            # nov-luna antes que empujar la difícil a nov-sol (que es +1 más barato pero manda
            # una canción de luna a sol). Se prioriza esto sobre el costo total.
            if t >= 5 and L <= 14:
                solbad += 1
        key = (solbad, cost)
        if best is None or key < best[0]:
            best = (key, newl)
    out = list(levels)
    for pos, i in enumerate(order):
        out[i] = best[1][pos]
    return out


# ★ REGLA VIGENTE (2026-07-23, pedido del usuario): la BANDA/canal la decide el PREFIJO del
#   chart, NO el Level: e_ -> Estrella, n_ -> Luna, h_ -> Sol. El juego sigue derivando el slot
#   del Level (gate 0x451c60), así que clampeamos el Level a la banda del prefijo — preservando
#   su sub-tier si ya cae dentro (ej. e_ Lv3 queda 3=novato; e_ Lv10 -> 7=estrella). Reemplaza
#   al resolver Level->tier de arriba (que se conserva solo como referencia del RE).
PREFIJO_BANDA = {"e_": (1, 7), "n_": (8, 14), "h_": (15, 20)}

def nivel_por_prefijo(file_rel, level):
    """Clampa `level` (str/int) a la banda que dicta el prefijo del chart (e_/n_/h_).
    Devuelve str. Prefijo desconocido -> level sin tocar."""
    base = os.path.basename((file_rel or "").replace("\\", "/")).lower()
    rng = PREFIJO_BANDA.get(base[:2])
    if not rng:
        return str(level)
    lo, hi = rng
    try:
        L = int(level)
    except (TypeError, ValueError):
        return str(level)
    return str(min(max(L, lo), hi))


def build_music_lt_entry(s):
    """Arma la entrada <Music ...> byte-level (tabs+CRLF, euc-kr) como integrar_cancion.py."""
    CRLF = b"\r\n"
    ek = lambda t: t.encode("euc-kr", "replace")
    rows = [b"<Music\t",
            f'\tIndex="{s["index"]}"'.encode(),
            b'\tName="' + ek(s["name"]) + b'"',
            f'\tImage="{s["image_rel"]}"'.encode(),
            f'\tFile="{s["file_rel"]}"'.encode(),
            f'\tLevel="{s["level"]}"'.encode(),
            b'\tGenre="' + ek(s["genre"]) + b'"',
            b'\tArtist="' + ek(s["artist"]) + b'"',
            f'\tCoin="{s["coin"]}"'.encode(),
            f'\tTime="{s["time"]}"'.encode(),
            f'\tCombo="{s["combo"]}"'.encode(),
            f'\tStat="{s["stat"]}"'.encode(),
            b"></Music>\t"]
    return CRLF.join(rows) + CRLF


def pak_folder(pak_path, r2pak, tmp):
    """Carpeta interna que provee un pak de audio (los mp3 viven en '<carpeta>/x.mp3').

    Importa porque el cliente resuelve el `<BGM Name="carpeta/x.mp3">` por esa ruta INTERNA:
    renombrar el archivo .pak NO alcanza. Verificado: 'Down The Stairs.pak' contiene
    'Down The Stairs/mr.mp3'.
    """
    d = os.path.join(tmp, "peek")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    shutil.copy(pak_path, d)
    subprocess.run([r2pak, "-d", os.path.basename(pak_path)], cwd=d, capture_output=True)
    dirs = [x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))]
    return dirs[0] if dirs else None


def repack_audio(pak_path, destino, r2pak, tmp):
    """Reempaca un pak de audio para que su carpeta interna se llame `destino`.

    Es lo que hace falta para reusar el audio de otra canción: el chart pide
    'destino/x.mp3' y el pak original trae 'otra_cosa/x.mp3'. Devuelve la ruta del pak nuevo.
    ★ `r2pak -c DIR` mete las rutas CON el nombre de DIR, así que hay que empacar una carpeta
    que se llame exactamente como el destino.
    """
    work = os.path.join(tmp, "repack")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    shutil.copy(pak_path, work)
    subprocess.run([r2pak, "-d", os.path.basename(pak_path)], cwd=work, capture_output=True)
    dirs = [x for x in os.listdir(work) if os.path.isdir(os.path.join(work, x))]
    if not dirs:
        raise RuntimeError("el pak no tiene carpeta adentro")
    origen = os.path.join(work, dirs[0])
    if dirs[0] != destino:
        nuevo = os.path.join(work, destino)
        shutil.move(origen, nuevo)
        origen = nuevo
    subprocess.run([r2pak, "-c", destino], cwd=work, capture_output=True)
    gen = os.path.join(work, destino + ".pak")
    if not os.path.exists(gen) or os.path.getsize(gen) == 0:
        raise RuntimeError("r2pak no generó %s.pak" % destino)
    salida = os.path.join(tmp, "audio_arreglado")
    os.makedirs(salida, exist_ok=True)
    final = os.path.join(salida, destino + ".pak")
    shutil.move(gen, final)
    return final


def pak_members(pak_path):
    """Lista [(name, type)] de las entradas internas de un .pak de audio, parseando el
    footer+índice (formato reversado de gfis/CBT/2013): footer = últimos 9 bytes
    `[u32 indexOffset][u32 fileCount][u8 marker]`; cada registro =
    `[u8 nameLen][u8 type][u32 off][u32 size1][u32 size2][name][0x00]`. type 2=nodo-raíz
    (carpeta), 1=archivo. Devuelve [] si no parsea."""
    try:
        d = open(pak_path, "rb").read()
        idx_off, count = struct.unpack_from("<II", d, len(d) - 9)
        p, out = idx_off, []
        for _ in range(count):
            nl = d[p]; ty = d[p + 1]
            name = d[p + 14:p + 14 + nl].decode("latin-1", "replace")
            p += nl + 15
            out.append((name, ty))
        return out
    except Exception:
        return []


def find_pak_for_files(needed, cand_paths):
    """Devuelve el 1er pak (de cand_paths) cuyos archivos internos (por basename, minúscula)
    CUBREN 'needed'. Sirve para el quirk de R2Single donde una dificultad pide sus keysounds
    bajo una carpeta HERMANA sin pak propio (ej. e_song2984 cuando solo existe e_song2983.pak),
    con los MISMOS archivos → se re-empaca ese pak con el nombre de carpeta que pide el chart."""
    if not needed:
        return None
    for p in cand_paths:
        try:
            bn = {n.split("/")[-1].lower() for n, t in pak_members(p) if t == 1}
        except Exception:
            continue
        if needed <= bn:
            return p
    return None


def verify_bgm_playable(music_dir, bgm_name):
    """¿El cliente reproduciría este `<BGM Name="carpeta/x.mp3">`? Replica la resolución del
    CBT (reversada, ver SESION_2026-07-20): el pak `carpeta.pak` tiene que existir en
    rnr_music, su nodo-raíz (type 2) tiene que llamarse `carpeta` con el MISMO case (el CBT
    hace strncmp case-sensitive del prefijo), y alguna entrada tiene que ser `carpeta/x.mp3`
    (case-insensitive, stricmp). Devuelve None si SUENA, o el motivo (str) si quedaría MUDA."""
    bgm_name = bgm_name.replace("\\", "/").strip()
    if "/" not in bgm_name:
        return None  # sin carpeta: no lo evaluamos
    folder = bgm_name.split("/", 1)[0]
    pak = os.path.join(music_dir, folder + ".pak")
    if not os.path.exists(pak):
        return f"falta {folder}.pak en rnr_music"
    members = pak_members(pak)
    if not members:
        return f"{folder}.pak no parsea (footer/índice corrupto)"
    roots = [n for n, t in members if t == 2]
    if roots and roots[0] != folder:
        return f"nodo-raíz del pak es {roots[0]!r} ≠ {folder!r} (case) -> el CBT saltea el pak"
    if not any(n.lower() == bgm_name.lower() for n, _t in members):
        return f"{folder}.pak no contiene la entrada {bgm_name!r}"
    return None


def bmp_info(path):
    """(ancho, alto, bpp) de un BMP, sin leerlo entero."""
    h = open(path, "rb").read(34)
    return (struct.unpack_from("<i", h, 18)[0], abs(struct.unpack_from("<i", h, 22)[0]),
            struct.unpack_from("<H", h, 28)[0])


def bmp_to_ppm(path):
    """BMP -> PPM P6 (bytes), para verlo con tk.PhotoImage sin dependencias externas.

    Tk no abre BMP; las portadas del cliente son 16bpp RGB555, que además Pillow-menos
    hay que desarmar a mano. Soporta 8 (paleta) / 16 / 24 / 32 bpp sin compresión RLE.
    """
    d = open(path, "rb").read()
    if d[:2] != b"BM":
        raise ValueError("no es un BMP")
    off = struct.unpack_from("<I", d, 10)[0]
    hdr = struct.unpack_from("<I", d, 14)[0]
    w = struct.unpack_from("<i", d, 18)[0]
    h = struct.unpack_from("<i", d, 22)[0]
    bpp = struct.unpack_from("<H", d, 28)[0]
    comp = struct.unpack_from("<I", d, 30)[0]
    top_down, h = h < 0, abs(h)
    if comp not in (0, 3):
        raise ValueError("BMP comprimido (RLE), no soportado")
    if bpp not in (8, 16, 24, 32):
        raise ValueError("BMP de %d bpp, no soportado" % bpp)

    pal = []
    if bpp == 8:
        p = 14 + hdr
        for i in range(256):
            b, g, r = d[p + i * 4], d[p + i * 4 + 1], d[p + i * 4 + 2]
            pal.append((r, g, b))

    # BI_BITFIELDS trae las máscaras después del DIB; si no, 16bpp es RGB555 (el del cliente)
    masks = None
    if bpp == 16:
        masks = (struct.unpack_from("<III", d, 14 + 40) if comp == 3
                 else (0x7C00, 0x03E0, 0x001F))

    def chan(v, m):
        if not m:
            return 0
        sh = (m & -m).bit_length() - 1
        mx = m >> sh
        return ((v & m) >> sh) * 255 // mx

    stride = ((bpp * w + 31) // 32) * 4
    rows = []
    for y in range(h):
        src = y if top_down else h - 1 - y
        base = off + src * stride
        out = bytearray()
        for x in range(w):
            if bpp == 8:
                r, g, b = pal[d[base + x]]
            elif bpp == 16:
                v = struct.unpack_from("<H", d, base + x * 2)[0]
                r, g, b = chan(v, masks[0]), chan(v, masks[1]), chan(v, masks[2])
            else:
                k = base + x * (bpp // 8)
                b, g, r = d[k], d[k + 1], d[k + 2]
            out += bytes((r, g, b))
        rows.append(bytes(out))
    return b"P6\n%d %d\n255\n" % (w, h) + b"".join(rows)


def ppm_pixels(ppm):
    """PPM P6 -> (ancho, alto, bytes RGB arriba->abajo)."""
    rest = ppm.split(b"\n", 1)[1]
    dims, rest = rest.split(b"\n", 1)
    w, h = (int(x) for x in dims.split())
    return w, h, rest.split(b"\n", 1)[1]         # saltar el "255"


def crop_rgb(rgb, w, h, tw, th):
    """Recorte CENTRADO a tw×th. Para sobrantes de pocos píxeles es lo que menos daña:
    no resamplea nada, el resto de la imagen queda idéntico."""
    x0, y0 = (w - tw) // 2, (h - th) // 2
    out = bytearray()
    for y in range(th):
        i = ((y0 + y) * w + x0) * 3
        out += rgb[i:i + tw * 3]
    return bytes(out)


def scale_rgb(rgb, w, h, tw, th):
    """Reescalado bilineal a tw×th (para cuando la imagen es MÁS CHICA que el destino:
    ahí no se puede recortar y hay que estirar)."""
    out = bytearray()
    fx, fy = w / tw, h / th
    for y in range(th):
        # clampear ANTES de sacar el peso: con sy<0 el peso da negativo y en vez de
        # interpolar extrapola, saliéndose del rango 0-255
        sy = max(0.0, min(h - 1.0, (y + 0.5) * fy - 0.5))
        y0 = int(sy)
        y1 = min(y0 + 1, h - 1)
        wy = sy - y0
        for x in range(tw):
            sx = max(0.0, min(w - 1.0, (x + 0.5) * fx - 0.5))
            x0 = int(sx)
            x1 = min(x0 + 1, w - 1)
            wx = sx - x0
            a, b = (y0 * w + x0) * 3, (y0 * w + x1) * 3
            c, d = (y1 * w + x0) * 3, (y1 * w + x1) * 3
            for k in range(3):
                top = rgb[a + k] * (1 - wx) + rgb[b + k] * wx
                bot = rgb[c + k] * (1 - wx) + rgb[d + k] * wx
                out.append(int(top * (1 - wy) + bot * wy + 0.5))
    return bytes(out)


def fit_rgb(rgb, w, h, tw, th, margen=0):
    """Ajusta a tw×th. Devuelve (rgb, w, h, descripción).

    Dos casos distintos a propósito:
    - Sobra poco (hasta `margen` px, ej. una 256x129): **recorte centrado**. Es el error
      típico de exportación y así no se resamplea nada: el resto queda intacto.
    - Cualquier otra cosa: **cover** = escalar respetando la proporción hasta cubrir y
      recortar lo que sobre. Estirar sin más deforma, y recortar sin escalar una imagen
      grande se quedaría con un pedacito del centro.
    """
    if (w, h) == (tw, th):
        return rgb, w, h, None
    if tw <= w <= tw + margen and th <= h <= th + margen:
        return crop_rgb(rgb, w, h, tw, th), tw, th, \
            "recortada %dx%d -> %dx%d (centrado, sin resamplear)" % (w, h, tw, th)

    f = max(tw / w, th / h)
    nw, nh = max(tw, round(w * f)), max(th, round(h * f))
    out = scale_rgb(rgb, w, h, nw, nh)
    txt = "escalada %dx%d -> %dx%d (bilineal, sin deformar)" % (w, h, nw, nh)
    if (nw, nh) != (tw, th):
        out = crop_rgb(out, nw, nh, tw, th)
        txt += " + recorte centrado a %dx%d" % (tw, th)
    return out, tw, th, txt


def bmp_to_16bpp(path, dst, size=None):
    """Reescribe cualquier BMP soportado como el 16bpp que usa el cliente, opcionalmente
    ajustado a `size` (ancho, alto).

    Formato copiado de las portadas nativas: DIB de 40 bytes, BI_RGB, RGB555, alto
    NEGATIVO (top-down) y datos desde el offset 54.
    """
    w, h, rgb = ppm_pixels(bmp_to_ppm(path))     # ya normaliza bpp, paleta y orientación
    ajuste = None
    if size and (w, h) != tuple(size):
        rgb, w, h, ajuste = fit_rgb(rgb, w, h, size[0], size[1], PORTADA_MARGEN)

    stride = ((16 * w + 31) // 32) * 4           # 16bpp: ancho par -> w*2, pero se redondea
    filas = []
    for y in range(h):                           # bmp_to_ppm entrega arriba->abajo
        fila = bytearray()
        base = y * w * 3
        for x in range(w):
            r, g, b = rgb[base + x * 3], rgb[base + x * 3 + 1], rgb[base + x * 3 + 2]
            fila += struct.pack("<H", ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3))
        fila += b"\x00" * (stride - len(fila))
        filas.append(bytes(fila))
    data = b"".join(filas)

    dib = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 16, 0, len(data), 2835, 2835, 0, 0)
    out = b"BM" + struct.pack("<IHHI", 14 + len(dib) + len(data), 0, 0, 14 + len(dib)) \
        + dib + data
    open(dst, "wb").write(out)
    return dst, ajuste


def build_preview_map(script_dir, music_dir):
    """{Index -> "e_songN/xxx.mp3"} para el preview de audio del picker.

    Cadena: music_lt.txt (Index + File) -> ese chart -> <BGM Name="...">. Solo entra
    la canción si su .pak de audio EXISTE en rnr_music (si no, el preview sonaría a nada).
    Equivalente a importador-ropa/gen_preview_map.py, ver docs/INTEGRAR_CANCION_NUEVA.md §8.
    """
    paks = set()
    if os.path.isdir(music_dir):
        paks = {os.path.splitext(f)[0].lower() for f in os.listdir(music_dir)
                if f.lower().endswith(".pak")}

    ml = open(os.path.join(script_dir, "music_lt.txt"), "rb").read()
    mapping = {}
    for e in parse_music_entries(ml):
        f = e.get("file")
        if not f:                       # Aleatoria / Lupa: no tienen chart
            continue
        chart = os.path.join(script_dir, os.path.normpath(f).split(os.sep, 1)[-1])
        if not os.path.isfile(chart):
            continue
        m = re.search(r'<BGM\s+Name\s*=\s*"([^"]+)"',
                      open(chart, "rb").read().decode("cp949", "replace"), re.I)
        if not m:
            continue
        bgm = m.group(1)
        # el pak se llama como la carpeta virtual del BGM: "e_song07/mr.mp3" -> e_song07.pak
        if paks and bgm.replace("\\", "/").split("/")[0].lower() not in paks:
            continue
        try:
            mapping[int(e["index"])] = bgm
        except ValueError:
            continue
    return mapping


def write_preview_map(mapping, path):
    """Escribe el 'id|ruta' que lee preview.dll. Las rutas van en CP949 (hay audios con
    nombre coreano): en UTF-8 el juego no encuentra el archivo y no suena nada."""
    n = omit = 0
    with open(path, "wb") as fh:
        for sid in sorted(mapping):
            try:
                raw = mapping[sid].encode("cp949")
            except Exception:
                omit += 1
                continue
            fh.write(("%d|" % sid).encode("ascii") + raw + b"\x0d\x0a")
            n += 1
    return n, omit


def patch_music_lt_entry(raw, changes):
    """Reescribe SOLO los atributos indicados dentro del bloque <Music ...> original.

    Para editar canciones que ya están en el cliente NO se puede regenerar la entrada con
    build_music_lt_entry(): las entradas reales traen atributos que ese constructor no
    emite (`Text=` de la Lupa, `Show=`) y varias no tienen Time/Combo/Stat. Regenerarlas
    las mutilaría, así que se parchea in-place y todo lo demás queda intacto.
    """
    out = raw
    for attr, val in changes.items():
        v = str(val).encode("euc-kr", "replace")
        pat = re.compile(rb'((?<![A-Za-z])' + attr.encode() + rb'\s*=\s*")[^"]*(")')
        if pat.search(out):
            out = pat.sub(lambda m: m.group(1) + v + m.group(2), out, count=1)
        else:                                   # atributo nuevo: antes del cierre
            out = out.replace(b"></Music>",
                              b'\t' + attr.encode() + b'="' + v + b'"\r\n></Music>', 1)
    return out + b"\t\r\n"


def insert_or_replace_entry(ml, idx, entry_bytes):
    """Inserta (o reemplaza si Index existe) la entrada en los bytes del music_lt."""
    key = f'Index="{idx}"'.encode()
    if key in ml:
        p = ml.find(key)
        a = ml.rfind(b"<Music", 0, p)
        b = ml.find(b"</Music>", p) + len(b"</Music>")
        # comer hasta el fin de línea del bloque viejo para no duplicar CRLF
        e = ml.find(b"\n", b)
        b = e + 1 if e >= 0 else len(ml)
        return ml[:a] + entry_bytes + ml[b:]
    i_last = ml.rfind(b"></Music>")
    if i_last < 0:
        return ml + entry_bytes
    eol = ml.find(b"\n", i_last)
    if eol < 0:
        return ml + b"\r\n" + entry_bytes
    return ml[:eol + 1] + entry_bytes + ml[eol + 1:]


# ------------------------------------------------------------------- GUI ----

class App:
    def __init__(self, root):
        self.root = root
        root.title("Importador de Música — R2Beat cliente 2011 (CBT-CLEAN)")
        root.geometry("1240x760")

        self.cfg = dict(DEFAULTS)
        self._snap = {}            # foto de rutas para los hilos (ver save_cfg)
        if os.path.exists(CONFIG_PATH):
            try:
                self.cfg.update(json.load(open(CONFIG_PATH, encoding="utf-8")))
            except Exception:
                pass

        self.songs = []            # filas de la tabla (dicts)
        self.client_index = {}     # Index -> nombre (del music_lt del cliente)
        self.client_genres = []
        self.client_charts = set() # basenames de charts ya en rnr_script.pak/music
        self.client_images = set() # basenames de bmp ya en rnr_image_lt.pak
        self._client_img_dir = None # img_music del cliente desempacado (visor de portadas)
        self._pv = None            # ventana del visor de portadas
        self._mci_alias = None     # alias MCI del preview de audio en curso (None = nada suena)
        self._prev_after = None    # id del root.after que sondea el fin del preview
        self.logq = queue.Queue()
        self.busy = False

        self._build_ui()
        self.root.after(100, self._poll_log)

    # ---------------- UI ----------------
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill="x")
        self.var_input = tk.StringVar(value=self.cfg["input"])
        self.var_client = tk.StringVar(value=self.cfg["client"])
        self.var_r2pak = tk.StringVar(value=self.cfg["r2pak"])
        self.var_output = tk.StringVar(value=self.cfg["output"])
        self.var_audioextra = tk.StringVar(value=self.cfg.get("audio_extra", ""))

        def row(r, label, var, isfile=False):
            ttk.Label(top, text=label, width=22).grid(row=r, column=0, sticky="w")
            ttk.Entry(top, textvariable=var, width=110).grid(row=r, column=1, sticky="we", padx=4)
            def browse():
                if isfile:
                    p = filedialog.askopenfilename(initialdir=os.path.dirname(var.get()))
                else:
                    p = filedialog.askdirectory(initialdir=var.get())
                if p:
                    var.set(os.path.normpath(p))
            ttk.Button(top, text="...", width=3, command=browse).grid(row=r, column=2)

        row(0, "Carpeta de entrada:", self.var_input)
        row(1, "Cliente a parchar (2011):", self.var_client)
        row(2, "r2pak.exe:", self.var_r2pak, isfile=True)
        row(3, "Salida server (notes+txt):", self.var_output)
        row(4, "Audio de respaldo (opcional):", self.var_audioextra)
        top.columnconfigure(1, weight=1)

        bar = ttk.Frame(self.root, padding=(6, 2))
        bar.pack(fill="x")
        self.btn_scan = ttk.Button(bar, text="1) Escanear", command=self.scan)
        self.btn_scan.pack(side="left")
        self.btn_loadcli = ttk.Button(bar, text="Cargar música del cliente",
                                      command=self.load_client)
        self.btn_loadcli.pack(side="left", padx=6)
        ttk.Button(bar, text="Editar canción…", command=self.edit_selected).pack(side="left", padx=4)
        ttk.Button(bar, text="Editar selección (Index/Género/Artista)…",
                   command=self.bulk_edit).pack(side="left", padx=4)
        ttk.Button(bar, text="Ver portada…", command=self.show_portadas).pack(side="left", padx=4)
        self.btn_preview = ttk.Button(bar, text="▶ Preview", command=self.toggle_preview)
        self.btn_preview.pack(side="left", padx=4)
        ttk.Button(bar, text="Resolver audio…", command=self.resolver_audio).pack(side="left", padx=4)
        ttk.Button(bar, text="Incluir/Excluir", command=self.toggle_selected).pack(side="left", padx=4)
        self.btn_apply = ttk.Button(bar, text="2) APLICAR (parchar cliente + generar output)",
                                    command=self.apply)
        self.btn_apply.pack(side="left", padx=16)
        ttk.Button(bar, text="Corregir Combo…", command=self.fix_combo).pack(side="left", padx=4)
        ttk.Button(bar, text="Abrir salida", command=self.open_output).pack(side="left", padx=4)

        fb = ttk.Frame(self.root, padding=(6, 0))
        fb.pack(fill="x")
        ttk.Label(fb, text="Filtrar:").pack(side="left")
        self.var_filter = tk.StringVar()
        self.var_filter.trace_add("write", lambda *a: self.refresh_table())
        ttk.Entry(fb, textvariable=self.var_filter, width=28).pack(side="left", padx=4)
        ttk.Button(fb, text="✕", width=3,
                   command=lambda: self.var_filter.set("")).pack(side="left")
        self.var_onlyedit = tk.BooleanVar(value=False)
        ttk.Checkbutton(fb, text="solo las de la tanda", variable=self.var_onlyedit,
                        command=self.refresh_table).pack(side="left", padx=10)
        ttk.Button(fb, text="Marcar visibles", width=15,
                   command=lambda: self.marcar_todas(True)).pack(side="left", padx=2)
        ttk.Button(fb, text="Desmarcar visibles", width=17,
                   command=lambda: self.marcar_todas(False)).pack(side="left", padx=2)
        self.lbl_count = ttk.Label(fb, text="", foreground="#606060")
        self.lbl_count.pack(side="left", padx=6)

        cols = ("inc", "origen", "estado", "index", "nombre", "nivel", "genero", "artista",
                "combo", "time", "chart", "audio", "portada", "notas")
        heads = ("✓", "Origen", "Estado", "Index", "Nombre", "Nivel", "Género", "Artista",
                 "Combo", "Time", "Chart", "Audio", "Portada", "Notas")
        widths = (30, 62, 105, 55, 185, 45, 90, 105, 55, 50, 130, 140, 85, 90)
        # la tabla va en un contenedor para poder colgarle la barra de scroll (antes solo
        # se podía mover con la rueda del mouse)
        cont = ttk.Frame(self.root)
        cont.pack(fill="both", expand=True, padx=6, pady=4)
        self.tree = ttk.Treeview(cont, columns=cols, show="headings", height=18,
                                 selectmode="extended")
        vsb = ttk.Scrollbar(cont, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(cont, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="we")
        self.tree.grid(row=0, column=0, sticky="nsew")
        cont.rowconfigure(0, weight=1)
        cont.columnconfigure(0, weight=1)
        self.heads = dict(zip(cols, heads))
        self.sort_col, self.sort_rev = None, False
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h, command=lambda cc=c: self.sort_by(cc))
            self.tree.column(c, width=w, anchor="w", stretch=(c in ("nombre", "audio", "chart")))
        self.tree.bind("<Double-1>", lambda e: (
            self.bulk_edit() if len(self.tree.selection()) > 1 else self.edit_selected()))
        # marcar/desmarcar: click en la columna ✓ o barra espaciadora sobre lo seleccionado
        self.tree.bind("<Button-1>", self._click_marcar, add="+")
        self.tree.bind("<space>", lambda e: (self.toggle_selected(), "break")[1])
        # navegación por teclado (antes solo se podía con la rueda)
        self.tree.bind("<Prior>", lambda e: self._saltar(-15))
        self.tree.bind("<Next>", lambda e: self._saltar(15))
        self.tree.bind("<Home>", lambda e: self._ir_a(0))
        self.tree.bind("<End>", lambda e: self._ir_a(-1))
        # el visor de portadas sigue a la selección: te movés por la lista y vas viendo
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._pv_update())
        self.tree.tag_configure("err", foreground="#b00000")
        self.tree.tag_configure("col", foreground="#a06000")
        self.tree.tag_configure("off", foreground="#909090")
        self.tree.tag_configure("edit", foreground="#006090")   # del cliente, ya editada

        self.log = ScrolledText(self.root, height=10, state="disabled",
                                font=("Consolas", 9))
        self.log.pack(fill="both", padx=6, pady=(0, 6))

    def log_msg(self, msg):
        self.logq.put(msg)

    def _poll_log(self):
        try:
            while True:
                msg = self.logq.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", msg + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log)

    def save_cfg(self):
        self.cfg = {"input": self.var_input.get(), "client": self.var_client.get(),
                    "r2pak": self.var_r2pak.get(), "output": self.var_output.get(),
                    "audio_extra": self.var_audioextra.get()}
        # los workers NO deben tocar variables de Tk desde otro hilo: trabajan sobre
        # esta foto, que se saca siempre en el hilo principal antes de lanzarlos
        self._snap = dict(self.cfg)
        try:
            json.dump(self.cfg, open(CONFIG_PATH, "w", encoding="utf-8"),
                      indent=2, ensure_ascii=False)
        except Exception:
            pass

    # -------------- r2pak --------------
    def run_pak(self, args, cwd):
        exe = self._snap.get("r2pak") or self.var_r2pak.get()
        subprocess.run([exe] + args, cwd=cwd, capture_output=True)

    def unpak(self, pak_path, dest_dir):
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy(pak_path, dest_dir)
        base = os.path.splitext(os.path.basename(pak_path))[0]
        self.run_pak(["-d", os.path.basename(pak_path)], dest_dir)
        return os.path.join(dest_dir, base)

    @staticmethod
    def clean_svn(root):
        for dp, _dirs, files in os.walk(root):
            for f in files:
                if f == ".svn":
                    try:
                        os.remove(os.path.join(dp, f))
                    except OSError:
                        pass

    def _check_paths(self, need_input=True):
        # need_input=False lo usan "Cargar música del cliente" y "Corregir Combo", que
        # trabajan solo sobre el cliente
        if need_input and not os.path.isdir(self.var_input.get()):
            messagebox.showerror("Error", "La carpeta de entrada no existe.")
            return False
        cl = self.var_client.get()
        if not (os.path.isdir(cl) and os.path.exists(os.path.join(cl, "rnr_script.pak"))
                and os.path.exists(os.path.join(cl, "rnr_image_lt.pak"))
                and os.path.isdir(os.path.join(cl, "rnr_music"))):
            messagebox.showerror("Error", "El cliente seleccionado no parece un cliente 2011 "
                                 "(faltan rnr_script.pak / rnr_image_lt.pak / rnr_music).")
            return False
        if not os.path.exists(self.var_r2pak.get()):
            messagebox.showerror("Error", "No encuentro r2pak.exe en la ruta indicada.")
            return False
        if xml2notes is None:
            messagebox.showerror("Error", "No pude importar conversor_notas/xml2notes.py "
                                 "(¿movieron la carpeta del tool fuera del root?).")
            return False
        return True

    # -------------- SCAN --------------
    def scan(self):
        if self.busy or not self._check_paths():
            return
        self.save_cfg()
        self.busy = True
        self.btn_scan.config(state="disabled")
        self.btn_apply.config(state="disabled")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        try:
            self._do_scan()
        except Exception as e:
            self.log_msg(f"[ERROR] escaneo falló: {e!r}")
        finally:
            self.busy = False
            self.root.after(0, lambda: (self.btn_scan.config(state="normal"),
                                        self.btn_apply.config(state="normal"),
                                        self.refresh_table()))

    def _unpack_client(self):
        """Desempaca los paks del cliente y refresca el índice de lo que ya tiene.
        Lo usan tanto el escaneo de la entrada como la carga de las del cliente.
        Devuelve (rs, music_lt_bytes, paks_de_audio)."""
        client = self._snap["client"]
        tmp = os.path.join(TOOL_DIR, "_tmp_scan")
        shutil.rmtree(tmp, ignore_errors=True)

        self.log_msg(">> desempacando rnr_script.pak del cliente (para music_lt y charts)...")
        rs = self.unpak(os.path.join(client, "rnr_script.pak"), os.path.join(tmp, "rs"))
        ml = open(os.path.join(rs, "music_lt.txt"), "rb").read()
        self.client_index = {}
        genres = set()
        for e in parse_music_entries(ml):
            self.client_index[int(e["index"])] = e.get("name", "")
            if e.get("genre"):
                genres.add(e["genre"])
        self.client_genres = sorted(genres)
        self.client_charts = {f.lower() for f in os.listdir(os.path.join(rs, "music"))}
        self._client_rs_unpacked = rs  # lo reusa el detalle de charts en cliente

        self.log_msg(">> desempacando rnr_image_lt.pak del cliente (para las portadas)...")
        img = self.unpak(os.path.join(client, "rnr_image_lt.pak"), os.path.join(tmp, "img"))
        imdir = os.path.join(img, "rnr_image", "img_music")
        self.client_images = ({f.lower() for f in os.listdir(imdir)}
                              if os.path.isdir(imdir) else set())
        self._client_img_dir = imdir   # queda desempacado: lo usa el visor de portadas
        client_audio = {f.lower() for f in os.listdir(os.path.join(client, "rnr_music"))
                        if f.lower().endswith(".pak")}
        self.client_audio = client_audio
        self.log_msg(f"   cliente: {len(self.client_index)} canciones, "
                     f"{len(self.client_charts)} charts, {len(self.client_images)} portadas, "
                     f"{len(client_audio)} paks de audio")
        return rs, ml, client_audio

    def _make_client_song(self, e, client_audio):
        """Fila de una canción que YA está en el cliente, para editarla.
        No trae piezas nuevas: chart/audio/notas/portada ya viven en el cliente, así que
        no se tocan salvo que el usuario las reemplace a mano. Arranca EXCLUIDA: se suma
        a la tanda recién cuando la editás (dirty)."""
        idx = int(e["index"])
        # File="" a propósito en las pseudo-entradas (Aleatoria/Lupa): NO inventarle un
        # chart, o al reescribirlas se romperían.
        file_rel = e.get("file", "")
        chart_base = os.path.basename(file_rel.replace("\\", "/"))
        if chart_base:
            file_rel = "rnr_script/music/" + chart_base
        img_b = os.path.basename((e.get("image", "") or "").replace("\\", "/")) \
            or f"music_{idx}.bmp"
        image_rel = "rnr_image/img_music/" + img_b

        data, src = None, None
        if chart_base.lower() in self.client_charts:
            src = os.path.join(self._client_rs_unpacked, "music", chart_base)
            try:
                data = open(src, "rb").read()
            except OSError:
                data = None
        info = chart_info(data) if data else None

        audio_stat = []
        if info:
            folders = [info["bgm"]] + [f for f in info["effects"] if f != info["bgm"]]
            for fo in dict.fromkeys(f for f in folders if f):
                audio_stat.append((fo, "cliente" if fo + ".pak" in client_audio else "FALTA"))
        img_loc = "cliente" if img_b.lower() in self.client_images else "FALTA"

        problems = []
        if chart_base and not data:
            problems.append("chart no está en el pak")
        if img_loc == "FALTA":
            problems.append("sin portada en el cliente")
        s = {"index": idx, "name": e.get("name", ""), "level": e.get("level", "5"),
             "genre": e.get("genre", ""), "artista_orig": e.get("artist", ""),
             "artist": e.get("artist", ""), "coin": e.get("coin", "0"),
             "time": int(e["time"]) if e.get("time", "").strip().isdigit() else 7000,
             "combo": int(e["combo"]) if e.get("combo", "").isdigit() else 0,
             "combo_prov": False, "stat": e.get("stat", "1"),
             "file_rel": file_rel, "image_rel": image_rel,
             "chart_src": src, "chart_loc": "cliente", "chart_data": data,
             "chart_info": info, "bin_src": None,
             # las notas ya las sirve el server: no hay que generarlas de nuevo
             "notes_from": "ya en el cliente",
             "audio_stat": audio_stat, "audio_override": {}, "img_loc": img_loc,
             "img_src": None, "problems": problems, "origin": "cliente",
             "src_kind": "cliente", "dirty": False, "include": False, "collision": False,
             # bloque original: se parchea sobre él para no perder Text=/Show=/etc.
             "raw_entry": e["_raw"]}
        # foto de los campos como vinieron: al aplicar se reescribe SOLO lo que cambió
        s["orig"] = {k: s[k] for k in EDITABLE_ATTRS}
        return s

    # -------------- CARGAR LAS DEL CLIENTE (modo edición) --------------
    def load_client(self):
        """Botón dedicado: llena la tabla con TODAS las canciones que ya tiene el cliente,
        para editarlas. No mira la carpeta de entrada."""
        if self.busy or not self._check_paths(need_input=False):
            return
        if self.songs and not messagebox.askyesno(
                "Cargar del cliente",
                f"Se reemplazan las {len(self.songs)} filas de la tabla por las canciones "
                "del cliente.\n\nSi tenías una tanda de importación a medio preparar, se "
                "pierde.\n\n¿Dale?"):
            return
        self.save_cfg()
        self.busy = True
        self.btn_scan.config(state="disabled")
        self.btn_apply.config(state="disabled")
        self.btn_loadcli.config(state="disabled")
        threading.Thread(target=self._loadclient_worker, daemon=True).start()

    def _loadclient_worker(self):
        try:
            self._do_load_client()
        except Exception as e:
            self.log_msg(f"[ERROR] no pude cargar las del cliente: {e!r}")
        finally:
            self.busy = False
            self.root.after(0, lambda: (self.btn_scan.config(state="normal"),
                                        self.btn_apply.config(state="normal"),
                                        self.btn_loadcli.config(state="normal"),
                                        self.refresh_table()))

    def _do_load_client(self):
        _rs, ml, client_audio = self._unpack_client()
        self.input_paks = {}
        songs = [self._make_client_song(e, client_audio) for e in parse_music_entries(ml)]
        self.songs = songs
        self.log_msg(f">> {len(songs)} canciones del cliente cargadas para editar. "
                     "Editá lo que quieras y dale APLICAR: se reescribe solo lo que toques.")

    def _do_scan(self):
        inp = self._snap["input"]
        tmp = os.path.join(TOOL_DIR, "_tmp_scan")
        rs, ml, client_audio = self._unpack_client()

        # --- recolectar archivos de entrada (incluye contenedores .r2n del R2 Online) ---
        lt_frags, charts, bins, paks, bmps, r2ns = [], {}, {}, {}, {}, []

        def collect(root_dir, allow_r2n):
            for dp, _dirs, files in os.walk(root_dir):
                for f in files:
                    p = os.path.join(dp, f)
                    fl = f.lower()
                    if fl.endswith(".txt"):
                        d = open(p, "rb").read()
                        # los txt de los .r2n vienen en UTF-16 -> normalizar a utf-8
                        if d[:2] in (b"\xff\xfe", b"\xfe\xff") or b"<\x00M\x00" in d:
                            try:
                                d = d.decode("utf-16").encode("utf-8")
                            except UnicodeDecodeError:
                                pass
                        if b"<Music" in d:
                            lt_frags.append((p, d))
                    elif fl.endswith(".xml"):
                        d = open(p, "rb").read()
                        if b"<TITLE" in d:
                            charts[fl] = (p, d)
                    elif fl.endswith(".bin"):
                        if is_notes_bin(p):
                            m = re.match(r"(\d+)\.bin$", fl)
                            if m:
                                bins[int(m.group(1))] = p
                    elif fl.endswith(".pak"):
                        paks[os.path.splitext(fl)[0]] = p
                    elif fl.endswith(".bmp"):
                        bmps[fl] = p
                    elif fl.endswith(".r2n") and allow_r2n:
                        r2ns.append(p)

        collect(inp, True)
        for i, rp in enumerate(r2ns):
            self.log_msg(f"   desempacando {os.path.basename(rp)} (contenedor R2 Online)...")
            collect_dir = os.path.join(tmp, "r2n", str(i))
            self.unpak(rp, collect_dir)
            collect(collect_dir, False)
        self.input_paks = dict(paks)
        # paks del cliente fuente: respaldo para los bancos compartidos (zsound, etc.)
        audio_extra = {}
        ex = self._snap.get("audio_extra") or ""
        if ex and os.path.isdir(ex):
            for f in os.listdir(ex):
                if f.lower().endswith(".pak"):
                    # en minúsculas: el cliente NO distingue mayúsculas (en producción hay
                    # 148 referencias tipo 'Down The Stairs' -> 'down the stairs.pak')
                    audio_extra[os.path.splitext(f)[0].lower()] = os.path.join(ex, f)
            self.log_msg("   audio de respaldo: %d paks en %s" % (len(audio_extra), ex))
        elif ex:
            self.log_msg("   [aviso] la carpeta de audio de respaldo no existe: %s" % ex)
        self.audio_extra = audio_extra
        self.log_msg(f"   entrada: {len(lt_frags)} txt con <Music>, {len(charts)} charts XML, "
                     f"{len(bins)} bins de notas, {len(paks)} paks de audio, {len(bmps)} bmp, "
                     f"{len(r2ns)} r2n")

        songs = []
        used_charts = set()

        def resolve_chart(file_rel):
            """basename del File= -> (src_path, data, 'entrada'|'cliente'|None)"""
            base = os.path.basename(file_rel).lower()
            if base in charts:
                return charts[base][0], charts[base][1], "entrada"
            if base in self.client_charts:
                p = os.path.join(self._client_rs_unpacked, "music", os.path.basename(file_rel))
                return p, open(p, "rb").read(), "cliente"
            return None, None, None

        def make_song(idx, name, level, genre, artist, coin, time_v, combo, stat,
                      file_rel, image_rel, origin):
            # normalizar a las rutas del cliente (los txt de los .r2n traen rutas
            # relativas al contenedor, ej. File="h_song5107/n_song4796.xml")
            chart_base = os.path.basename(file_rel.replace("\\", "/"))
            file_rel = "rnr_script/music/" + chart_base
            img_b = os.path.basename(image_rel.replace("\\", "/")) or f"music_{idx}.bmp"
            image_rel = "rnr_image/img_music/" + img_b
            src, data, chart_loc = resolve_chart(file_rel)
            info = chart_info(data) if data else None
            problems = []
            if not data:
                problems.append("SIN CHART")
            # notas: bin explícito > convertir chart con AREA
            bin_src = bins.get(idx)
            notes_from = None
            if bin_src:
                notes_from = f"bin ({bin_note_count(bin_src)} notas)"
            elif info and info["has_area"]:
                notes_from = f"convertir XML ({info['area_count']} AREAs)"
            else:
                problems.append("SIN NOTAS (ni bin ni AREA)")
            # audio: BGM + EFFECT folders
            audio_stat = []
            audio_repack = {}       # carpeta -> pak fuente a re-empacar (carpeta hermana sin pak)
            chart_folder_fix = {}   # carpeta typo -> carpeta correcta (reescribir el chart)
            resolved_paths = {}     # carpeta OK -> path del pak (para detectar typos)
            if info:
                folders = [info["bgm"]] + [f for f in info["effects"] if f != info["bgm"]]
                folders = [f for f in dict.fromkeys(folders) if f]
                for fo in folders:
                    if fo in paks:
                        audio_stat.append((fo, "entrada")); resolved_paths[fo] = paks[fo]
                    elif fo + ".pak" in client_audio:
                        audio_stat.append((fo, "cliente"))
                        resolved_paths[fo] = os.path.join(self._snap["client"], "rnr_music", fo + ".pak")
                    elif fo in audio_extra and find_pak_for_files(
                            info.get("folder_files", {}).get(fo, set()), [audio_extra[fo]]):
                        # bancos compartidos (zsound, keysounds de otra difi) que el material
                        # suelto no trae pero sí están en el cliente de respaldo.
                        # ★ Se acepta SOLO si el pak de respaldo REALMENTE contiene los
                        #   keysounds que el chart pide (find_pak_for_files). Si solo coincide
                        #   el NOMBRE de carpeta pero el contenido es de otra canción (respaldo
                        #   de un cliente que no tiene este tema), se RECHAZA y cae a FALTA en
                        #   vez de meter audio equivocado sin avisar.
                        audio_stat.append((fo, "respaldo")); resolved_paths[fo] = audio_extra[fo]
                    else:
                        needed = info.get("folder_files", {}).get(fo, set())
                        # 1) ¿TYPO de una carpeta YA resuelta del mismo chart? (mismos archivos,
                        #    ej. EFFECT pide `e_song_2257/` con "_" de más y el BGM usa `e_song2257`)
                        #    -> reescribir la referencia, SIN duplicar pak.
                        rewrite = None
                        for rf, rp in resolved_paths.items():
                            if find_pak_for_files(needed, [rp]):
                                rewrite = rf; break
                        if rewrite:
                            audio_stat.append((fo, "typo->" + rewrite))
                            chart_folder_fix[fo] = rewrite
                        else:
                            # 2) carpeta hermana sin pak propio pero con los mismos archivos de
                            #    OTRO pak de entrada -> re-empacar ese pak con el nombre pedido.
                            src_rp = find_pak_for_files(needed, list(paks.values())
                                                        + list(audio_extra.values()))
                            if src_rp:
                                audio_stat.append((fo, "repack")); audio_repack[fo] = src_rp
                            else:
                                audio_stat.append((fo, "FALTA"))
                                es_bgm = fo == info["bgm"]
                                problems.append("SIN AUDIO %s (%s)" %
                                                (fo, "PISTA MUDA" if es_bgm else "keysounds mudos"))
            # portada
            img_base = os.path.basename(image_rel).lower()
            if img_base in bmps:
                img_loc = "entrada"
            elif img_base in self.client_images:
                img_loc = "cliente"
            else:
                # ★ FALLBACK a la portada del CONTENEDOR: las entradas de dificultad que
                #   vienen empaquetadas DENTRO del .r2n de otra canción (ej. "It's Mine"
                #   Idx 1898 dentro de e_song1278.r2n) referencian music_{idx}.bmp que NO
                #   existe; sólo está la portada del contenedor (music_1278.bmp), justo al
                #   lado del chart en el mismo r2n. Usamos ESA en vez de dar "sin portada".
                cont = None
                if src and os.path.isfile(src):
                    d = os.path.dirname(src)
                    try:
                        cands = sorted(f for f in os.listdir(d) if f.lower().endswith(".bmp"))
                    except OSError:
                        cands = []
                    if cands:
                        cont = cands[0]
                if cont:
                    img_loc = "contenedor"
                    image_rel = "rnr_image/img_music/" + cont
                    img_base = cont.lower()
                else:
                    img_loc = "FALTA"
                    problems.append("SIN PORTADA")
            # copiar la portada resuelta a un caché ESTABLE (ver COVER_CACHE): el preview y
            # el apply leen de acá, no de la ruta efímera del .r2n desempacado.
            img_src = bmps.get(img_base)
            if img_src and os.path.isfile(img_src):
                try:
                    os.makedirs(COVER_CACHE, exist_ok=True)
                    cached = os.path.join(COVER_CACHE, os.path.basename(img_src))
                    if not os.path.exists(cached) or os.path.getsize(cached) != os.path.getsize(img_src):
                        shutil.copy(img_src, cached)
                    img_src = cached
                except OSError:
                    pass   # si el caché falla, seguimos con la ruta original
            # defaults desde el chart
            if info:
                if not time_v and info["bpm"]:
                    time_v = int(round(306 * info["length"] / info["bpm"]))
                if not name:
                    name = info["title"]
            combo_prov = combo is None
            if combo_prov and bin_src:
                combo = bin_note_count(bin_src)
            s = {"index": idx, "name": name or f"song{idx}", "level": level or "5",
                 "genre": genre or "R2Beat", "artista_orig": artist, "artist": artist or "",
                 "coin": coin or "0", "time": time_v or 7000, "combo": combo or 0,
                 "combo_prov": combo_prov, "stat": stat or "1",
                 "file_rel": file_rel, "image_rel": image_rel,
                 "chart_src": src, "chart_loc": chart_loc, "chart_data": data,
                 "chart_info": info, "bin_src": bin_src, "notes_from": notes_from,
                 "audio_stat": audio_stat, "audio_override": {}, "audio_repack": audio_repack,
                 "chart_folder_fix": chart_folder_fix,
                 "img_loc": img_loc, "img_src": img_src,
                 "problems": problems, "origin": origin,
                 "src_kind": "entrada", "dirty": False,
                 # ★ SIN AUDIO tampoco entra: se importaría igual y la canción sonaría con
                 #   las notas MUDAS, sin ningún error (caso Burning Sky/Abejorro).
                 "include": not problems}
            return s

        # 1) filas desde los fragmentos music_lt de la entrada
        # ★ dedup por CHART, no por Index (2026-07-23): en el catálogo espejo varios r2n declaran
        #   un Index interno DESALINEADO del nombre (ej. e_song671 -> Index="681") y chocan entre sí;
        #   deduplicar por Index BORRABA canciones DISTINTAS (por eso "Sanche" no aparecía al escanear
        #   todo el catálogo). Cada canción real tiene un chart propio -> se deduplica por el basename
        #   del chart (File=), y así NO se omite nada. (La tabla usa iid=posición, tolera Index repetido.)
        seen_idx = set()
        seen_chart = set()
        for path, d in lt_frags:
            for e in parse_music_entries(d):
                idx = int(e["index"])
                file_rel = e.get("file", "")
                ckey = (os.path.basename(file_rel).lower() if file_rel
                        else f"__noidx::{path}::{idx}")
                if ckey in seen_chart:
                    continue
                seen_chart.add(ckey)
                seen_idx.add(idx)
                used_charts.add(os.path.basename(file_rel).lower())
                combo = int(e["combo"]) if e.get("combo", "").isdigit() else None
                time_v = int(e["time"]) if e.get("time", "").strip().isdigit() else None
                songs.append(make_song(idx, e.get("name", ""), e.get("level", ""),
                                       e.get("genre", ""), e.get("artist", ""),
                                       e.get("coin", "0"), time_v, combo,
                                       e.get("stat", "1"), file_rel,
                                       e.get("image", ""), os.path.basename(path)))

        # 2) charts huérfanos (sin entrada en los txt) -> fila auto
        for base, (p, d) in sorted(charts.items()):
            if base in used_charts:
                continue
            m = re.match(r"[enh]_song(\w+?)(kr)?\.xml$", base)
            num = m.group(1) if m else None
            idx = int(num) if num and num.isdigit() else None
            # antes se descartaba si el Index ya estaba visto (idx in seen_idx) -> omitía charts
            # huérfanos DISTINTOS que comparten Index. Ya se deduplica por chart (base in used_charts),
            # así que solo hace falta un Index válido.
            if idx is None:
                continue
            seen_idx.add(idx)
            img_rel = f"rnr_image/img_music/music_{idx}.bmp"
            s = make_song(idx, "", "", "", "", "0", None, None, "1",
                          f"rnr_script/music/{os.path.basename(p)}", img_rel,
                          "chart suelto")
            songs.append(s)

        # 3) bins huérfanos (aviso nomás; sin chart no se puede integrar)
        covered = {s["index"] for s in songs}
        for idx in sorted(set(bins) - covered):
            self.log_msg(f"   [aviso] {idx}.bin sin chart ni entrada -> ignorado "
                         "(solo se copiaría a mano al server)")

        for s in songs:
            s["collision"] = s["index"] in self.client_index

        # tanda grande = flujo selectivo: arrancan TODAS desmarcadas y marcás las que querés
        # (con 1200 filas, desmarcar de a una lo hace inusable)
        if len(songs) > UMBRAL_TANDA_GRANDE:
            for s in songs:
                s["include"] = False
            self.log_msg(">> son %d canciones: arrancan DESMARCADAS. Marcá las que quieras "
                         "(clic en la columna ✓, o barra espaciadora, o 'Marcar visibles' "
                         "después de filtrar)." % len(songs))

        self.songs = songs
        self.log_msg(f">> escaneo listo: {len(songs)} de la entrada "
                     f"({sum(1 for s in songs if s['collision'])} colisiones de Index, "
                     f"{sum(1 for s in songs if s['problems'])} con problemas)")

    # -------------- tabla --------------
    def _row_matches(self, s):
        """Filtro de la vista (no toca la tanda): texto libre + 'solo las de la tanda'."""
        if self.var_onlyedit.get() and not s["include"]:
            return False
        q = self._txt(self.var_filter.get().strip())
        if not q:
            return True
        campos = (s["name"], s["artist"], s["genre"], str(s["index"]),
                  os.path.basename(s["file_rel"]))
        return any(q in self._txt(c) for c in campos)

    def refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        vis = 0
        for i, s in enumerate(self.songs):
            if not self._row_matches(s):
                continue
            vis += 1
            cli = s["src_kind"] == "cliente"
            if s["problems"]:
                estado = "; ".join(s["problems"])
            elif cli:
                estado = "editada" if s["dirty"] else "en el cliente"
            else:
                estado = (f"COLISIÓN ({self.client_index.get(s['index'], '')})"
                          if s["collision"] else "OK")
            audio = ", ".join(f"{f}[{st}]" for f, st in s["audio_stat"]) or "?"
            chart = (os.path.basename(s["file_rel"]) +
                     (f" [{s['chart_loc']}]" if s["chart_loc"] else ""))
            tags = ()
            if not s["include"]:
                tags = ("off",)
            elif s["problems"]:
                tags = ("err",)
            elif cli:
                tags = ("edit",)
            elif s["collision"]:
                tags = ("col",)
            self.tree.insert("", "end", iid=str(i), tags=tags, values=(
                "✓" if s["include"] else "", "cliente" if cli else "entrada",
                estado, s["index"], s["name"], s["level"],
                s["genre"], s["artist"], f'{s["combo"]}{"*" if s["combo_prov"] else ""}',
                s["time"], chart, audio,
                s["img_loc"], s["notes_from"] or "-"))
        self.lbl_count.config(text="%d de %d filas — %d en la tanda"
                              % (vis, len(self.songs),
                                 sum(1 for s in self.songs if s["include"])))

    # ---- orden por columna (clic en el encabezado; 2º clic invierte) ----
    SORT_KEYS = {
        "inc":     lambda s: not s["include"],
        "origen":  lambda s: (s["src_kind"], not s["dirty"]),
        "estado":  lambda s: (0 if s["problems"] else (1 if s["collision"] else 2),
                              "; ".join(s["problems"])),
        "index":   lambda s: s["index"],
        "nombre":  lambda s: App._txt(s["name"]),
        "nivel":   lambda s: App._num(s["level"]),
        "genero":  lambda s: App._txt(s["genre"]),
        "artista": lambda s: App._txt(s["artist"]),
        "combo":   lambda s: App._num(s["combo"]),
        "time":    lambda s: App._num(s["time"]),
        "chart":   lambda s: App._txt(os.path.basename(s["file_rel"])),
        "audio":   lambda s: ", ".join(f"{f}[{st}]" for f, st in s["audio_stat"]),
        "portada": lambda s: s["img_loc"] or "",
        "notas":   lambda s: s["notes_from"] or "",
    }

    @staticmethod
    def _txt(v):
        """Clave alfabética: sin mayúsculas y sin acentos, para que 'Ábaco' caiga con la A
        y no al final de la lista."""
        n = unicodedata.normalize("NFD", str(v))
        return "".join(c for c in n if not unicodedata.combining(c)).casefold()

    @staticmethod
    def _num(v):
        """Ordena numérico cuando se puede (Nivel/Combo/Time a veces vienen como texto)."""
        try:
            return (0, float(str(v).strip()))
        except (TypeError, ValueError):
            return (1, str(v).casefold())

    def sort_by(self, col):
        key = self.SORT_KEYS.get(col)
        if not key or not self.songs:
            return
        self.sort_rev = not self.sort_rev if self.sort_col == col else False
        self.sort_col = col
        try:
            self.songs.sort(key=key, reverse=self.sort_rev)
        except TypeError:                       # mezcla de tipos: cae a texto
            self.songs.sort(key=lambda s: str(key(s)).casefold(), reverse=self.sort_rev)
        for c, h in self.heads.items():         # flecha solo en la columna activa
            self.tree.heading(c, text=h + (" ▼" if self.sort_rev else " ▲") if c == col else h)
        self.refresh_table()

    def _sel(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    # ---- marcado y navegación de la lista ----
    def _click_marcar(self, ev):
        """Click en la columna ✓ = marcar/desmarcar esa fila, sin abrir nada."""
        if self.tree.identify_region(ev.x, ev.y) != "cell":
            return
        if self.tree.identify_column(ev.x) != "#1":      # #1 = columna "inc"
            return
        iid = self.tree.identify_row(ev.y)
        if not iid:
            return
        s = self.songs[int(iid)]
        if s["problems"] and not s["include"]:
            messagebox.showwarning("No se puede incluir",
                                   "«%s» tiene: %s.\nResolvelo antes de importarla."
                                   % (s["name"], "; ".join(s["problems"])))
            return
        s["include"] = not s["include"]
        if s["src_kind"] == "cliente":
            s["dirty"] = s["include"]
        self.refresh_table()
        return "break"

    def marcar_todas(self, valor):
        """Marca/desmarca TODAS las filas visibles (respeta el filtro)."""
        n = 0
        for iid in self.tree.get_children():
            s = self.songs[int(iid)]
            if valor and s["problems"]:
                continue                                # no se puede importar rota
            if s["include"] != valor:
                s["include"] = valor
                if s["src_kind"] == "cliente":
                    s["dirty"] = valor
                n += 1
        self.refresh_table()
        self.log_msg(">> %s %d filas visibles" % ("marcadas" if valor else "desmarcadas", n))

    def _saltar(self, n):
        hijos = self.tree.get_children()
        if not hijos:
            return "break"
        act = self.tree.selection()
        i = hijos.index(act[0]) if act and act[0] in hijos else 0
        self._ir_a(max(0, min(len(hijos) - 1, i + n)))
        return "break"

    def _ir_a(self, i):
        hijos = self.tree.get_children()
        if not hijos:
            return "break"
        iid = hijos[i if i >= 0 else len(hijos) - 1]
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        return "break"

    def toggle_selected(self):
        for iid in self.tree.selection():
            s = self.songs[int(iid)]
            s["include"] = not s["include"]
            if s["src_kind"] == "cliente":
                s["dirty"] = s["include"]
        self.refresh_table()

    # -------------- preview de audio (escuchar el BGM) --------------
    @staticmethod
    def _strip_id3v2(data):
        """Quita el tag ID3v2 del inicio de un mp3 (si lo hay). El MCI de Windows 11
        se cae con err 277 «problema al inicializar MCI» ante ciertos ID3v2.4; el juego
        (Miles/preview.dll) los saltea sin drama, así que esto es SOLO para el preview."""
        if len(data) < 10 or data[:3] != b"ID3":
            return data
        flags = data[5]
        sz = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]   # syncsafe
        total = 10 + sz + (10 if flags & 0x10 else 0)                       # +footer v2.4
        return data[total:] if 0 < total < len(data) else data

    def _mci(self, cmd):
        """Manda un comando a MCI (winmm) y devuelve (err_code, texto). Solo Windows."""
        import ctypes
        buf = ctypes.create_unicode_buffer(256)
        err = ctypes.windll.winmm.mciSendStringW(cmd, buf, 255, 0)
        return err, buf.value

    def _preview_stop(self):
        """Corta el preview en curso (si hay) y restaura el botón."""
        if self._prev_after:
            try:
                self.root.after_cancel(self._prev_after)
            except Exception:
                pass
            self._prev_after = None
        if self._mci_alias:
            self._mci(f"stop {self._mci_alias}")
            self._mci(f"close {self._mci_alias}")
            self._mci_alias = None
        try:
            self.btn_preview.config(text="▶ Preview")
        except Exception:
            pass

    def _preview_poll(self):
        """Sondea si el mp3 terminó para restaurar el botón solo."""
        if not self._mci_alias:
            return
        _err, mode = self._mci(f"status {self._mci_alias} mode")
        if mode and mode not in ("playing", "seeking"):
            self._preview_stop()
            return
        self._prev_after = self.root.after(500, self._preview_poll)

    def _extract_bgm_mp3(self, s):
        """Extrae (cacheado) el mp3 del BGM de la canción `s`. Devuelve (ruta|None, motivo)."""
        data = s.get("chart_data")
        if not data:
            return None, "esta fila no tiene el chart cargado (¿ya está en el cliente sin editar?)."
        m = re.search(rb'<BGM\s+Name\s*=\s*"([^"]+)"', data)
        if not m:
            return None, "el chart no declara un <BGM Name>."
        ref = m.group(1).decode("latin-1").replace("\\", "/")
        folder = ref.split("/")[0].lower()
        fname = ref.split("/")[-1]
        os.makedirs(PREVIEW_CACHE, exist_ok=True)
        cached = os.path.join(PREVIEW_CACHE, f"{folder}__{fname}")
        if os.path.isfile(cached) and os.path.getsize(cached) > 0:
            return cached, None
        # resolver el pak del BGM: entrada (o respaldo) primero, si no, el del cliente
        pak = self._find_input_pak(folder)
        if not pak:
            cli = os.path.join(self._snap.get("client", "") or self.var_client.get(),
                               "rnr_music", folder + ".pak")
            if os.path.exists(cli):
                pak = cli
        if not pak:
            return None, f"no encuentro el pak de audio «{folder}.pak»."
        # extraer con r2pak a un tmp y copiar el mp3 al caché
        tmp = os.path.join(PREVIEW_CACHE, "_ex")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        shutil.copy(pak, tmp)
        self.run_pak(["-d", os.path.basename(pak)], tmp)
        d = os.path.join(tmp, folder)
        src = os.path.join(d, fname)
        if not os.path.isfile(src) and os.path.isdir(d):     # match case-insensitive
            for f in os.listdir(d):
                if f.lower() == fname.lower():
                    src = os.path.join(d, f); break
        if not os.path.isfile(src):
            shutil.rmtree(tmp, ignore_errors=True)
            return None, f"no pude extraer «{fname}» de «{folder}.pak»."
        try:
            with open(src, "rb") as fh:
                raw = fh.read()
            with open(cached, "wb") as fh:            # sin ID3v2 -> MCI no tira err 277
                fh.write(self._strip_id3v2(raw))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return cached, None

    def toggle_preview(self):
        """Escuchar el BGM de la canción seleccionada (toggle play/stop) vía MCI de Windows."""
        if self._mci_alias:              # ya suena algo -> parar
            self._preview_stop()
            return
        i = self._sel()
        if i is None:
            messagebox.showinfo("Preview", "Seleccioná una canción primero.")
            return
        s = self.songs[i]
        self.btn_preview.config(text="…")
        self.root.update_idletasks()
        try:
            path, motivo = self._extract_bgm_mp3(s)
        except Exception as e:
            path, motivo = None, repr(e)
        if not path:
            self.btn_preview.config(text="▶ Preview")
            messagebox.showwarning("Preview", "No pude reproducir: " + motivo)
            return
        alias = "r2bprev"
        self._mci(f"close {alias}")                          # por si quedó abierto
        err, _ = self._mci(f'open "{path}" type mpegvideo alias {alias}')
        if err:
            err, _ = self._mci(f'open "{path}" alias {alias}')   # fallback sin type
        if err:
            self.btn_preview.config(text="▶ Preview")
            messagebox.showwarning("Preview", f"MCI no pudo abrir el audio (código {err}).")
            return
        self._mci(f"play {alias}")
        self._mci_alias = alias
        self.btn_preview.config(text="■ Detener")
        self.log_msg(f"♪ preview: {s['index']} — {s['name']}")
        self._preview_poll()

    # -------------- portadas (ver / reemplazar) --------------
    def _img_path_for(self, s):
        """Ruta del BMP que se va a usar para esta canción, venga de la entrada, de un
        override manual o del propio cliente (rnr_image_lt.pak desempacado en el escaneo).
        Devuelve (ruta|None, origen)."""
        if s["img_src"] and os.path.isfile(s["img_src"]):
            return s["img_src"], s["img_loc"]
        if self._client_img_dir:
            p = os.path.join(self._client_img_dir, os.path.basename(s["image_rel"]))
            if os.path.isfile(p):
                return p, "cliente"
        return None, s["img_loc"]

    def _load_portada(self, path, box=(320, 200)):
        """BMP -> (PhotoImage, texto descriptivo). Achica por subsample entero si no entra."""
        w, h, bpp = bmp_info(path)
        # data= con los bytes CRUDOS del PPM: en base64 Tk contesta "couldn't recognize
        # image data" (el base64 solo lo entiende para GIF/PNG).
        photo = tk.PhotoImage(data=bmp_to_ppm(path))
        f = max(1, -(-w // box[0]), -(-h // box[1]))
        if f > 1:
            photo = photo.subsample(f, f)
        txt = f"{w}×{h} {bpp}bpp — {os.path.getsize(path):,} bytes"
        if (w, h, bpp) != (256, 128, 16):
            txt += "   ⚠ el cliente usa 256×128 16bpp"
        return photo, txt

    def _preparar_audio(self, pak, folder, parent):
        """Deja un pak listo para servir como el audio de `folder`.

        ★ El cliente resuelve el audio por la ruta INTERNA del pak ('folder/x.mp3'), no por
        el nombre del archivo: copiar un pak ajeno renombrado deja la canción MUDA sin dar
        ningún error. Si la carpeta interna no coincide, se reempaca.
        """
        tmp = os.path.join(TOOL_DIR, "_tmp_audio")
        try:
            interna = pak_folder(pak, self.var_r2pak.get(), tmp)
        except Exception as e:
            messagebox.showerror("Error", "No pude abrir ese pak: %s" % e, parent=parent)
            return None
        if interna == folder:
            return pak
        if not messagebox.askyesno(
                "Reempacar",
                "Ese pak trae la carpeta «%s» adentro, pero el chart pide «%s».\n\n"
                "Si se copia tal cual, la canción queda MUDA (el cliente busca por la ruta\n"
                "interna, no por el nombre del archivo).\n\n"
                "¿Lo reempaco como «%s»?" % (interna, folder, folder), parent=parent):
            return None
        try:
            nuevo = repack_audio(pak, folder, self.var_r2pak.get(), tmp)
        except Exception as e:
            messagebox.showerror("Error", "No pude reempacar: %s" % e, parent=parent)
            return None
        self.log_msg("   audio reempacado: %s [%s] -> %s.pak"
                     % (os.path.basename(pak), interna, folder))
        return nuevo

    def resolver_audio(self):
        """Botón: completa los audios FALTA de la fila seleccionada eligiendo un pak."""
        i = self._sel()
        if i is None:
            messagebox.showinfo("Audio", "Marcá una canción de la lista.")
            return
        s = self.songs[i]
        faltan = [fo for fo, st in s["audio_stat"] if st == "FALTA"]
        if not faltan:
            messagebox.showinfo("Audio", "A «%s» no le falta ningún audio." % s["name"])
            return
        for fo in faltan:
            p = filedialog.askopenfilename(
                parent=self.root, title="Audio para «%s» (canción: %s)" % (fo, s["name"]),
                filetypes=[("Pak de audio", "*.pak"), ("Todos", "*.*")])
            if not p:
                break
            p = self._preparar_audio(p, fo, self.root)
            if not p:
                continue
            s["audio_override"][fo] = p
            s["audio_stat"] = [(f, "manual" if f == fo else st) for f, st in s["audio_stat"]]
            self._mark_dirty(s)
        self._recalc_problems(s)
        self.refresh_table()

    def _mark_dirty(self, s):
        """Una canción del cliente entra a la tanda recién cuando se toca algo de ella."""
        if s["src_kind"] == "cliente":
            s["dirty"] = True
            s["include"] = True

    def _recalc_problems(self, s):
        """Recalcula la columna Estado de una fila (se usa al cambiar piezas)."""
        if s["src_kind"] == "cliente":
            # ya vive en el cliente: no le faltan piezas, solo se reescribe lo que cambies
            s["problems"] = []
            return
        probs = []
        if s["chart_data"] is None:
            probs.append("SIN CHART")
        if not s["notes_from"]:
            probs.append("SIN NOTAS (ni bin ni AREA)")
        probs += [f"SIN AUDIO {fo}" for fo, st in s["audio_stat"] if st == "FALTA"]
        if s["img_loc"] == "FALTA":
            probs.append("SIN PORTADA")
        s["problems"] = probs
        if not probs:
            s["include"] = True

    def _pick_portada(self, s, parent):
        """Diálogo de reemplazo de portada. True si cambió."""
        p = filedialog.askopenfilename(parent=parent, title="Elegir portada BMP",
                                       filetypes=[("Bitmap", "*.bmp"), ("Todos", "*.*")])
        if not p:
            return False
        try:
            w, h, bpp = bmp_info(p)
            tw, th = PORTADA_SIZE
            size = None
            if (w, h) != PORTADA_SIZE:
                if abs(w - tw) <= PORTADA_MARGEN and abs(h - th) <= PORTADA_MARGEN:
                    size = PORTADA_SIZE          # desfase chico (ej. 256x129): se arregla solo
                elif messagebox.askyesno(
                        "Tamaño distinto",
                        f"La portada es {w}x{h} y el cliente espera {tw}x{th}.\n\n"
                        "La diferencia es grande: ajustarla la va a recortar o deformar "
                        "bastante.\n\n¿La ajusto igual?\n"
                        "(No = se usa tal cual y probablemente se vea mal)", parent=parent):
                    size = PORTADA_SIZE
            if bpp != 16 or size:
                conv_dir = os.path.join(TOOL_DIR, "_portadas_16bpp")
                os.makedirs(conv_dir, exist_ok=True)
                dst = os.path.join(conv_dir, "%s_%s" % (s["index"], os.path.basename(p)))
                p, ajuste = bmp_to_16bpp(p, dst, size)
                det = [x for x in (("%dbpp -> 16bpp (RGB555)" % bpp) if bpp != 16 else None,
                                   ajuste) if x]
                self.log_msg(f"   [{s['index']}] portada: {'; '.join(det)} -> "
                             f"{os.path.basename(dst)}")
        except Exception as e:
            messagebox.showwarning("Ojo", f"No pude leer/convertir el BMP ({e}). Se usa tal "
                                   "cual: si no es 256x128 16bpp puede no cargar.",
                                   parent=parent)
        s["img_src"], s["img_loc"] = p, "manual"
        self._mark_dirty(s)
        self._recalc_problems(s)
        self.refresh_table()
        return True

    def show_portadas(self):
        """Visor: muestra la portada de la canción seleccionada y deja reemplazarla.
        Queda abierto y se actualiza solo al moverte por la lista."""
        if self._pv and self._pv["win"].winfo_exists():
            self._pv["win"].lift()
            self._pv_update()
            return
        win = tk.Toplevel(self.root)
        win.title("Portada")
        win.resizable(False, False)
        lbl_t = ttk.Label(win, text="", font=("Segoe UI", 10, "bold"), wraplength=340)
        lbl_t.pack(padx=10, pady=(10, 6))
        lbl_img = tk.Label(win, bd=1, relief="sunken", bg="#1e1e1e",
                           width=320, height=170, fg="#c0c0c0")
        lbl_img.pack(padx=10)
        lbl_i = ttk.Label(win, text="", foreground="#606060")
        lbl_i.pack(pady=(6, 2))
        b = ttk.Frame(win)
        b.pack(pady=(2, 10))

        def replace():
            i = self._sel()
            if i is None:
                return
            if self._pick_portada(self.songs[i], win):
                self._pv_update()

        ttk.Button(b, text="Reemplazar portada…", command=replace).pack(side="left", padx=4)
        ttk.Button(b, text="Cerrar", command=win.destroy).pack(side="left", padx=4)
        self._pv = dict(win=win, title=lbl_t, img=lbl_img, info=lbl_i, photo=None)
        self._pv_update()

    def _pv_update(self):
        pv = self._pv
        if not pv or not pv["win"].winfo_exists():
            return
        i = self._sel()
        if i is None:
            pv["title"].config(text="(ninguna canción seleccionada)")
            pv["img"].config(image="", text="")
            pv["info"].config(text="")
            pv["photo"] = None
            return
        s = self.songs[i]
        pv["title"].config(text=f'{s["index"]} — {s["name"]}')
        path, origen = self._img_path_for(s)
        if not path:
            pv["img"].config(image="", text="SIN PORTADA\n(queda en blanco en el selector)")
            pv["info"].config(text=os.path.basename(s["image_rel"]))
            pv["photo"] = None
            return
        try:
            photo, txt = self._load_portada(path)
        except Exception as e:
            pv["img"].config(image="", text="no se pudo mostrar:\n%s" % e)
            pv["info"].config(text=os.path.basename(path))
            pv["photo"] = None
            return
        pv["photo"] = photo                      # hay que retener la referencia o Tk la borra
        pv["img"].config(image=photo, text="")
        pv["info"].config(text=f"[{origen}] {os.path.basename(path)}\n{txt}")

    # ------- edición masiva (Index correlativo, Género y Artista) -------
    def _bulk_apply(self, rows, start, genre, artist=""):
        """Aplica index correlativo desde start (None=no tocar), género y/o artista
        (''=no tocar) a las filas indicadas (orden visual). Devuelve error o None."""
        if start is not None:
            cli = [self.songs[j]["index"] for j in rows
                   if self.songs[j]["src_kind"] == "cliente"]
            if cli:
                return ("La selección tiene canciones que ya están en el cliente "
                        f"({cli[:5]}…): a esas no se les puede cambiar el Index. "
                        "Desmarcalas o usá solo Género/Artista.")
            new_ids = list(range(start, start + len(rows)))
            others = {self.songs[j]["index"] for j in range(len(self.songs))
                      if j not in set(rows)}
            dup = sorted(set(new_ids) & others)
            if dup:
                return (f"Los Index {dup} chocan con otras canciones de esta tanda "
                        "(no seleccionadas). Elegí otro inicio.")
            for k, j in enumerate(rows):
                s = self.songs[j]
                s["index"] = new_ids[k]
                s["collision"] = new_ids[k] in self.client_index
        if genre:
            for j in rows:
                self.songs[j]["genre"] = genre
                self._mark_dirty(self.songs[j])
        if artist:
            for j in rows:
                self.songs[j]["artist"] = artist
                self._mark_dirty(self.songs[j])
        return None

    def bulk_edit(self):
        sel = set(self.tree.selection())
        rows = [int(iid) for iid in self.tree.get_children() if iid in sel]
        if not rows:
            messagebox.showinfo("Selección", "Marcá filas en la lista "
                                "(Ctrl+click o Shift+click para varias).")
            return
        dlg = tk.Toplevel(self.root)
        dlg.title(f"Editar {len(rows)} seleccionadas")
        dlg.grab_set()
        dlg.resizable(False, False)
        ttk.Label(dlg, padding=8, text=(
            f"{len(rows)} canciones seleccionadas (orden de la lista).\n"
            "Dejá vacío lo que no quieras cambiar.")).grid(row=0, column=0, columnspan=2)
        v_idx, v_gen, v_art = tk.StringVar(), tk.StringVar(), tk.StringVar()
        ttk.Label(dlg, text="Index inicial (correlativo):").grid(
            row=1, column=0, sticky="e", padx=8, pady=3)
        ttk.Entry(dlg, textvariable=v_idx, width=10).grid(row=1, column=1, sticky="w")
        ttk.Label(dlg, text="Género:").grid(row=2, column=0, sticky="e", padx=8, pady=3)
        ttk.Combobox(dlg, textvariable=v_gen, width=24,
                     values=self.client_genres or ["R2Beat"]).grid(
            row=2, column=1, sticky="w")
        ttk.Label(dlg, text="Artista:").grid(row=3, column=0, sticky="e", padx=8, pady=3)
        ttk.Entry(dlg, textvariable=v_art, width=26).grid(row=3, column=1, sticky="w")
        hint = tk.StringVar(value="")
        ttk.Label(dlg, textvariable=hint, foreground="#606060").grid(
            row=4, column=0, columnspan=2, padx=8)

        def upd_hint(*_):
            t = v_idx.get().strip()
            if t.isdigit():
                a = int(t)
                hint.set(f"Se asignan: {a} … {a + len(rows) - 1}")
            else:
                hint.set("")
        v_idx.trace_add("write", upd_hint)

        def ok():
            t = v_idx.get().strip()
            start = None
            if t:
                try:
                    start = int(t)
                except ValueError:
                    messagebox.showerror("Error", "El Index inicial debe ser un número.",
                                         parent=dlg)
                    return
            genre = v_gen.get().strip()
            artist = v_art.get().strip()
            if start is None and not genre and not artist:
                dlg.destroy()
                return
            err = self._bulk_apply(rows, start, genre, artist)
            if err:
                messagebox.showerror("Error", err, parent=dlg)
                return
            dlg.destroy()
            self.refresh_table()

        ttk.Button(dlg, text="Aplicar", command=ok).grid(row=5, column=0, pady=8)
        ttk.Button(dlg, text="Cancelar", command=dlg.destroy).grid(row=5, column=1, pady=8)

    # -------------- edición --------------
    def edit_selected(self):
        i = self._sel()
        if i is None:
            return
        s = self.songs[i]
        dlg = tk.Toplevel(self.root)
        dlg.title(f"Editar canción — Index {s['index']}")
        dlg.grab_set()
        dlg.resizable(False, False)
        fields = [("Index", "index"), ("Nombre", "name"), ("Nivel", "level"),
                  ("Género", "genre"), ("Artista", "artist"), ("Combo", "combo"),
                  ("Time", "time"), ("Coin", "coin"), ("Stat (1=visible)", "stat")]
        vars_ = {}
        for r, (lab, key) in enumerate(fields):
            ttk.Label(dlg, text=lab).grid(row=r, column=0, sticky="w", padx=8, pady=3)
            v = tk.StringVar(value=str(s[key]))
            vars_[key] = v
            if key == "genre":
                w = ttk.Combobox(dlg, textvariable=v, width=38,
                                 values=self.client_genres or ["R2Beat"])
            elif key == "index" and s["src_kind"] == "cliente":
                # cambiarle la id a una canción que ya está en el cliente dejaría la vieja
                # entrada huérfana (chart/audio/notas/portada siguen atados a la id original)
                w = ttk.Entry(dlg, textvariable=v, width=40, state="readonly")
            else:
                w = ttk.Entry(dlg, textvariable=v, width=40)
            w.grid(row=r, column=1, padx=8, pady=3)
        # ---- piezas: chart / audio / portada, cambiables con selector de archivo ----
        pf = ttk.LabelFrame(dlg, text="Piezas (cambiar acá impacta al instante, aún con Cancelar)")
        pf.grid(row=len(fields), column=0, columnspan=2, sticky="we", padx=8, pady=6)

        def recalc_problems():
            self._recalc_problems(s)

        def pick_chart():
            p = filedialog.askopenfilename(parent=dlg, title="Elegir chart XML",
                                           filetypes=[("Chart XML", "*.xml"),
                                                      ("Todos", "*.*")])
            if not p:
                return
            d = open(p, "rb").read()
            if b"<TITLE" not in d:
                messagebox.showerror("Error", "Ese XML no parece un chart (<TITLE>).",
                                     parent=dlg)
                return
            info = chart_info(d)
            s["chart_src"], s["chart_data"], s["chart_loc"] = p, d, "manual"
            s["chart_info"] = info
            s["file_rel"] = "rnr_script/music/" + os.path.basename(p)
            folders = [info["bgm"]] + [f for f in info["effects"] if f != info["bgm"]]
            folders = [f for f in dict.fromkeys(folders) if f]
            st = []
            for fo in folders:
                if fo in s["audio_override"]:
                    st.append((fo, "manual"))
                elif fo in getattr(self, "input_paks", {}):
                    st.append((fo, "entrada"))
                elif fo + ".pak" in getattr(self, "client_audio", set()):
                    st.append((fo, "cliente"))
                elif fo in getattr(self, "audio_extra", {}):
                    st.append((fo, "respaldo"))
                else:
                    st.append((fo, "FALTA"))
            s["audio_stat"] = st
            if s["bin_src"]:
                s["notes_from"] = f"bin ({bin_note_count(s['bin_src'])} notas)"
            elif info["has_area"]:
                s["notes_from"] = f"convertir XML ({info['area_count']} AREAs)"
            else:
                s["notes_from"] = None
            if info["bpm"]:
                vars_["time"].set(str(int(round(306 * info["length"] / info["bpm"]))))
            if not vars_["name"].get().strip():
                vars_["name"].set(info["title"])
            self._mark_dirty(s)
            recalc_problems()
            refresh_pieces()

        def pick_audio(fo):
            p = filedialog.askopenfilename(parent=dlg,
                                           title=f"Elegir pak de audio para «{fo}»",
                                           filetypes=[("Audio pak", "*.pak"),
                                                      ("Todos", "*.*")])
            if not p:
                return
            p = self._preparar_audio(p, fo, dlg)
            if not p:
                return
            s["audio_override"][fo] = p
            s["audio_stat"] = [(f, "manual" if f == fo else st)
                               for f, st in s["audio_stat"]]
            self._mark_dirty(s)
            recalc_problems()
            refresh_pieces()

        def pick_img():
            if self._pick_portada(s, dlg):
                refresh_pieces()

        def refresh_pieces():
            for w in pf.winfo_children():
                w.destroy()
            r = 0
            info = s["chart_info"]
            if s["chart_data"] is not None:
                ch_txt = (f"{os.path.basename(s['file_rel'])} [{s['chart_loc']}]"
                          + (f"   BPM={info['bpm']:g} LENGTH={info['length']:g}" if info else ""))
            else:
                ch_txt = "SIN CHART"
            ttk.Label(pf, text="Chart:", width=9).grid(row=r, column=0, sticky="e", padx=4)
            ttk.Label(pf, text=ch_txt).grid(row=r, column=1, sticky="w")
            ttk.Button(pf, text="Cambiar…", command=pick_chart).grid(
                row=r, column=2, padx=6, pady=2)
            r += 1
            ttk.Label(pf, text="Notas:", width=9).grid(row=r, column=0, sticky="e", padx=4)
            ttk.Label(pf, text=s["notes_from"] or "SIN NOTAS").grid(row=r, column=1, sticky="w")
            r += 1
            if s["audio_stat"]:
                for fo, st in s["audio_stat"]:
                    src = s["audio_override"].get(fo)
                    txt = f"{fo}.pak [{st}]"
                    if src:
                        txt += f"  ←  {os.path.basename(src)}"
                    ttk.Label(pf, text="Audio:", width=9).grid(row=r, column=0,
                                                               sticky="e", padx=4)
                    ttk.Label(pf, text=txt).grid(row=r, column=1, sticky="w")
                    ttk.Button(pf, text="Elegir pak…",
                               command=lambda f=fo: pick_audio(f)).grid(
                        row=r, column=2, padx=6, pady=2)
                    r += 1
            else:
                ttk.Label(pf, text="Audio:", width=9).grid(row=r, column=0, sticky="e", padx=4)
                ttk.Label(pf, text="(lo define el BGM/EFFECT del chart)").grid(
                    row=r, column=1, sticky="w")
                r += 1
            im_txt = f"{os.path.basename(s['image_rel'])} [{s['img_loc']}]"
            if s["img_loc"] == "manual" and s["img_src"]:
                im_txt += f"  ←  {os.path.basename(s['img_src'])}"
            ttk.Label(pf, text="Portada:", width=9).grid(row=r, column=0, sticky="e", padx=4)
            ttk.Label(pf, text=im_txt).grid(row=r, column=1, sticky="w")
            ttk.Button(pf, text="Cambiar…", command=pick_img).grid(
                row=r, column=2, padx=6, pady=2)
            # miniatura de la portada que va a quedar (la de entrada, la manual o la del cliente)
            r += 1
            ipath, _org = self._img_path_for(s)
            prev = tk.Label(pf, bd=1, relief="sunken", bg="#1e1e1e", fg="#c0c0c0")
            prev.grid(row=r, column=1, sticky="w", pady=(2, 4))
            if ipath:
                try:
                    photo, txt = self._load_portada(ipath, box=(256, 128))
                    prev.configure(image=photo)
                    prev.image = photo          # retener o Tk la recolecta y sale en blanco
                    ttk.Label(pf, text=txt, foreground="#606060").grid(
                        row=r, column=2, sticky="w", padx=6)
                except Exception as e:
                    prev.configure(text="no se pudo mostrar el BMP:\n%s" % e)
            else:
                prev.configure(text="  SIN PORTADA  ")

        refresh_pieces()

        def close():
            dlg.destroy()
            self.refresh_table()

        dlg.protocol("WM_DELETE_WINDOW", close)

        def ok():
            try:
                new_idx = int(vars_["index"].get())
                combo_v, time_v = int(vars_["combo"].get()), int(vars_["time"].get())
            except ValueError:
                messagebox.showerror("Error", "Index/Combo/Time deben ser números.",
                                     parent=dlg)
                return
            if (combo_v, time_v) != (s["combo"], s["time"]):
                self._mark_dirty(s)
            s["combo"], s["time"] = combo_v, time_v
            if s["src_kind"] == "cliente":
                new_idx = s["index"]          # el Index de una canción del cliente no se toca
            if new_idx != s["index"]:
                if any(o is not s and o["index"] == new_idx for o in self.songs):
                    messagebox.showerror("Error", f"El Index {new_idx} ya está usado por "
                                         "otra canción de esta tanda.", parent=dlg)
                    return
                # renombrar id coherente: bin de notas se busca/copia por el nuevo Index
                s["index"] = new_idx
                s["collision"] = new_idx in self.client_index
            s["combo_prov"] = False
            for key in ("name", "level", "genre", "artist", "coin", "stat"):
                if vars_[key].get() != str(s[key]):
                    self._mark_dirty(s)
                s[key] = vars_[key].get()
            dlg.destroy()
            self.refresh_table()

        ttk.Button(dlg, text="Guardar", command=ok).grid(
            row=len(fields) + 1, column=0, pady=8)
        ttk.Button(dlg, text="Cancelar", command=close).grid(
            row=len(fields) + 1, column=1, pady=8)

    # -------------- colisiones --------------
    def _free_index(self):
        used = set(self.client_index) | {s["index"] for s in self.songs}
        return max(used) + 1 if used else 3500

    def resolve_collision(self, s):
        """Diálogo modal: devuelve 'replace' | 'replace_all' | 'skip' | 'skip_all' |
        'cancel' (rename muta s y devuelve 'replace'). Los *_all fijan la decisión
        para TODAS las colisiones restantes de la tanda (sin volver a preguntar)."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Colisión de Index")
        dlg.grab_set()
        dlg.resizable(False, False)
        result = {"v": "cancel"}
        cur = self.client_index.get(s["index"], "?")
        ttk.Label(dlg, padding=10, text=(
            f'El Index {s["index"]} ya existe en el cliente como "{cur}".\n'
            f'Canción nueva: "{s["name"]}"\n\n'
            "Reemplazar: pisa la entrada del cliente (misma id).\n"
            "Cambiar Index: usa otra id — se renombra en notas, SONGS y music_lt.\n"
            "Saltar: no importar esta canción.")).grid(row=0, column=0, columnspan=4)
        v_idx = tk.StringVar(value=str(self._free_index()))
        ttk.Label(dlg, text="Nuevo Index:").grid(row=1, column=0, sticky="e", padx=6)
        ttk.Entry(dlg, textvariable=v_idx, width=8).grid(row=1, column=1, sticky="w")

        def do(val):
            if val == "rename":
                try:
                    n = int(v_idx.get())
                except ValueError:
                    messagebox.showerror("Error", "Index inválido.", parent=dlg)
                    return
                if n in self.client_index or any(o is not s and o["index"] == n
                                                 for o in self.songs):
                    messagebox.showerror("Error", f"El Index {n} también está ocupado.",
                                         parent=dlg)
                    return
                s["index"] = n
                s["collision"] = False
                result["v"] = "replace"  # ya sin colisión; sigue el flujo normal
            else:
                result["v"] = val
            dlg.destroy()

        f = ttk.Frame(dlg, padding=8)
        f.grid(row=2, column=0, columnspan=4)
        ttk.Button(f, text="Reemplazar", command=lambda: do("replace")).pack(side="left", padx=4)
        ttk.Button(f, text="Reemplazar todo", command=lambda: do("replace_all")).pack(side="left", padx=4)
        ttk.Button(f, text="Cambiar Index", command=lambda: do("rename")).pack(side="left", padx=4)
        ttk.Button(f, text="Saltar", command=lambda: do("skip")).pack(side="left", padx=4)
        ttk.Button(f, text="Saltar todo", command=lambda: do("skip_all")).pack(side="left", padx=4)
        ttk.Button(f, text="Cancelar todo", command=lambda: do("cancel")).pack(side="left", padx=4)
        self.root.wait_window(dlg)
        return result["v"]

    # -------------- APLICAR --------------
    def apply(self):
        # la carpeta de entrada solo hace falta si hay canciones NUEVAS en la tanda:
        # editando las del cliente no se usa para nada
        hay_nuevas = any(s["src_kind"] == "entrada" for s in self.songs)
        if self.busy or not self._check_paths(need_input=hay_nuevas):
            return
        if not self.songs:
            messagebox.showinfo("Nada", "Primero escaneá la carpeta de entrada "
                                "o cargá la música del cliente.")
            return
        self.save_cfg()
        batch = [s for s in self.songs if s["include"]]
        blocked = [s for s in batch if s["src_kind"] == "entrada"
                   and any(p.startswith(("SIN CHART", "SIN NOTAS", "SIN AUDIO"))
                           for p in s["problems"])]
        for s in blocked:
            self.log_msg(f"[{s['index']}] excluida: {'; '.join(s['problems'])}")
            batch.remove(s)
        if not batch:
            messagebox.showinfo("Nada", "No hay canciones válidas incluidas.")
            return
        # colisiones: diálogo por canción (en el hilo principal, ANTES del worker)
        # auto = decisión "para todas" ya tomada: None | "replace" | "skip"
        final = []
        auto = None
        for s in batch:
            # una fila del cliente ES esa entrada: reescribirla es justamente la intención
            if s["collision"] and s["src_kind"] == "entrada":
                if auto is not None:
                    r = auto
                else:
                    r = self.resolve_collision(s)
                    if r == "replace_all":
                        auto, r = "replace", "replace"
                    elif r == "skip_all":
                        auto, r = "skip", "skip"
                if r == "cancel":
                    self.log_msg(">> aplicación cancelada por el usuario.")
                    self.refresh_table()
                    return
                if r == "skip":
                    self.log_msg(f"[{s['index']}] saltada (colisión).")
                    continue
            final.append(s)
        self.refresh_table()
        if not final:
            return
        n_new = sum(1 for s in final if s["src_kind"] == "entrada")
        n_edit = len(final) - n_new
        if not messagebox.askyesno("Confirmar", (
                f"Cliente: {self.var_client.get()}\n\n"
                f"• {n_new} canciones NUEVAS (chart + audio + portada + notas)\n"
                f"• {n_edit} ya existentes: se reescribe SOLO su entrada de music_lt\n"
                "  (y la portada si la reemplazaste); chart, audio y notas quedan como están.\n\n"
                "Se parchan rnr_script.pak y rnr_image_lt.pak (backup .bak).\n\n"
                "¿Dale?")):
            return
        self.busy = True
        self.btn_scan.config(state="disabled")
        self.btn_apply.config(state="disabled")
        threading.Thread(target=self._apply_worker, args=(final,), daemon=True).start()

    def _apply_worker(self, batch):
        try:
            self._do_apply(batch)
        except Exception as e:
            self.log_msg(f"[ERROR] aplicación falló: {e!r}")
        finally:
            self.busy = False
            self.root.after(0, lambda: (self.btn_scan.config(state="normal"),
                                        self.btn_apply.config(state="normal")))

    def _do_apply(self, batch):
        client = self._snap["client"]
        out = self._snap["output"]
        # ★ antes de escribir NADA: si el juego está abierto, Windows tiene tomados los
        #   paks y el deploy falla a mitad (audio copiado, entrada/chart/portada no).
        bloqueados = paks_bloqueados(client)
        if bloqueados:
            self.log_msg("[ERROR] no puedo escribir en el cliente: " + ", ".join(bloqueados)
                         + "\n        Cerrá el R2Beat (el juego bloquea los .pak mientras "
                           "está abierto) y volvé a APLICAR. No se tocó nada.")
            return
        notes_out = os.path.join(out, "notas")
        os.makedirs(notes_out, exist_ok=True)
        tmp = os.path.join(TOOL_DIR, "_tmp_apply")
        shutil.rmtree(tmp, ignore_errors=True)

        self.log_msg(">> desempacando paks del cliente...")
        rs = self.unpak(os.path.join(client, "rnr_script.pak"), os.path.join(tmp, "rs"))
        img = self.unpak(os.path.join(client, "rnr_image_lt.pak"), os.path.join(tmp, "img"))
        ml_path = os.path.join(rs, "music_lt.txt")
        ml = open(ml_path, "rb").read()
        imdir = os.path.join(img, "rnr_image", "img_music")
        os.makedirs(imdir, exist_ok=True)

        songs_lines, edit_lines, warns = [], [], []
        # ★ BANDA POR PREFIJO (regla 2026-07-23): e_=Estrella, n_=Luna, h_=Sol. Se clampea el
        #   Level de CADA chart a la banda de su prefijo (preserva el sub-tier si ya cae dentro).
        #   Reemplaza al viejo resolver Level->tier por portada. Ver nivel_por_prefijo.
        for s in batch:
            ol = s.get("level")
            nl = nivel_por_prefijo(s.get("file_rel", ""), ol)
            if str(nl) != str(ol):
                s["level"] = nl
                pre = os.path.basename((s.get("file_rel") or "")).lower()[:1]
                canal = {"e": "Estrella", "n": "Luna", "h": "Sol"}.get(pre, "?")
                self.log_msg("   [%d] %s: Level %s→%s (banda por prefijo %s_ → %s)"
                             % (s["index"], s["name"], ol, nl, pre, canal))
        # euc-kr no soporta acentos ni CJK: se normaliza ACÁ (no se avisa y se deja roto),
        # así el nombre que va al music_lt y el que va a SONGS del server son el mismo.
        for s in batch:
            for k in ("name", "genre", "artist"):
                limpio = texto_para_euckr(s[k])
                # un nombre 100% CJK se vacía entero: sin esto la canción quedaría
                # SIN nombre en el selector (peor que un nombre feo)
                if k == "name" and s[k] and not limpio:
                    limpio = "song%d" % s["index"]
                    warns.append("%d: el nombre era todo CJK y no entra en euc-kr -> "
                                 "quedó como %r, poneselo a mano" % (s["index"], limpio))
                if limpio != s[k]:
                    if k == "name":
                        self.log_msg("   [%d] nombre adaptado a euc-kr: %r -> %r"
                                     % (s["index"], s[k], limpio))
                    s[k] = limpio

        for s in batch:
            idx = s["index"]

            # --- canciones que YA están en el cliente: solo se reescribe su entrada de
            #     music_lt (+ portada si la reemplazaron, + audio/chart si los cambiaron).
            #     Notas, chart y audio existentes NO se tocan: ya funcionan.
            if s["src_kind"] == "cliente":
                if s["chart_loc"] == "manual":
                    open(os.path.join(rs, "music", os.path.basename(s["file_rel"])),
                         "wb").write(strip_area(s["chart_data"]))
                for fo, _st in s["audio_stat"]:
                    src = s["audio_override"].get(fo)
                    if src:
                        dst = os.path.join(client, "rnr_music", fo + ".pak")
                        if os.path.exists(dst):
                            bak = dst + ".bak"
                            if not os.path.exists(bak):
                                shutil.copy(dst, bak)
                        shutil.copy(src, dst)
                        self.log_msg(f"   [{idx}] audio {os.path.basename(src)} -> {fo}.pak")
                if s["img_src"]:
                    shutil.copy(s["img_src"],
                                os.path.join(imdir, os.path.basename(s["image_rel"])))
                cambios = {EDITABLE_ATTRS[k]: s[k] for k in EDITABLE_ATTRS
                           if str(s[k]) != str(s["orig"][k])}
                if cambios:
                    ml = insert_or_replace_entry(
                        ml, idx, patch_music_lt_entry(s["raw_entry"], cambios))
                if "Name" in cambios:
                    edit_lines.append(f"({idx}, {s['name']!r}),")
                det = ", ".join(cambios) or "sin cambios de texto"
                self.log_msg(f"[{idx}] {s['name']:<28} EDITADA ({det}"
                             + (" + portada" if s["img_src"] else "") + ")")
                continue

            # 1) notas -> output/notes/{idx}.bin (renombrado coherente si cambió el Index)
            dst_bin = os.path.join(notes_out, f"{idx}.bin")
            if s["bin_src"]:
                shutil.copy(s["bin_src"], dst_bin)
                n_notes = bin_note_count(dst_bin)
            else:
                bpms, notes, w = xml2notes.parse_xml(s["chart_src"])
                blob, _ = xml2notes.build_blob(bpms, notes)
                open(dst_bin, "wb").write(blob)
                n_notes = len(notes)
                for x in w[:3]:
                    self.log_msg(f"   [{idx}] xml2notes: {x}")
            if s["combo_prov"]:
                # el Combo NO es el nº de notas: se calcula como lo hace el exe
                # (holds y Kind 0x18 pesan distinto). Ver combo_del_exe().
                s["combo"] = (n_notes if COMBO_CRUDO
                              else combo_del_exe(bin_notes(dst_bin)))
                if s["combo"] != n_notes:
                    self.log_msg(f"   [{idx}] Combo={s['combo']} (notas={n_notes}: "
                                 f"{s['combo'] - n_notes:+d} por holds/Kind 0x18)")

            # 2) chart stub SIN AREA -> rs/music/ (con el nombre que dice File=)
            chart_base = os.path.basename(s["file_rel"])
            chart_dst = os.path.join(rs, "music", chart_base)
            if chart_base.lower() in self.client_charts and not s["collision"]:
                warns.append(f"{idx}: el chart {chart_base} ya existía en el cliente y se "
                             "sobreescribe (todas las entradas que lo referencian lo comparten)")
            chart_data = fix_chart_folders(s["chart_data"], s.get("chart_folder_fix"))
            if s.get("chart_folder_fix"):
                self.log_msg("   [%d] chart: carpeta(s) typo corregida(s) %s"
                             % (idx, ", ".join("%s->%s" % kv for kv in s["chart_folder_fix"].items())))
            open(chart_dst, "wb").write(strip_area(chart_data))

            # 3) audio -> cliente/rnr_music/ (BGM + paks de EFFECT; override manual
            #    se copia RENOMBRADO al folder que referencia el chart)
            for fo, st in s["audio_stat"]:
                dst = os.path.join(client, "rnr_music", fo + ".pak")
                src = s["audio_override"].get(fo)
                if not src and st in ("entrada", "respaldo"):
                    src = self._find_input_pak(fo)
                if not src and st == "repack":
                    # carpeta hermana sin pak propio: re-empaca el pak que SÍ tiene esos
                    # keysounds (mismos archivos, otro nombre de carpeta interna).
                    origen = s.get("audio_repack", {}).get(fo)
                    if origen:
                        try:
                            src = repack_audio(origen, fo, self._snap["r2pak"], tmp)
                            self.log_msg(f"   [{idx}] repack {os.path.basename(origen)} -> "
                                         f"{fo}.pak (keysounds de carpeta hermana)")
                        except Exception as e:
                            warns.append(f"{idx}: no pude re-empacar {fo} ({e!r}) -> keysounds mudos")
                if src:
                    # comparar por CONTENIDO, no solo por tamaño: un pak stale del MISMO
                    # tamaño bloqueaba la copia y dejaba la pista muda sin avisar.
                    if not os.path.exists(dst) or not filecmp.cmp(src, dst, shallow=False):
                        if os.path.exists(dst):
                            bak = dst + ".bak"
                            if not os.path.exists(bak):
                                shutil.copy(dst, bak)
                        shutil.copy(src, dst)
                        self.log_msg(f"   [{idx}] audio {os.path.basename(src)} -> {fo}.pak")
                elif st == "FALTA":
                    que = ("pista muda" if fo == (s["chart_info"] or {}).get("bgm")
                           else "efectos mudos")
                    warns.append(f"{idx}: audio {fo}.pak NO disponible -> {que}")

            # 4) portada -> img/rnr_image/img_music/
            if s["img_src"]:
                shutil.copy(s["img_src"],
                            os.path.join(imdir, os.path.basename(s["image_rel"])))
            elif s["img_loc"] == "FALTA":
                warns.append(f"{idx}: sin portada (queda en blanco en el selector)")

            # 5) entrada music_lt (byte-level; reemplaza si el Index ya estaba)
            ml = insert_or_replace_entry(ml, idx, build_music_lt_entry(s))
            songs_lines.append(f"({idx}, {s['name']!r}),")
            self.log_msg(f"[{idx}] {s['name']:<28} listo (notas={n_notes}, "
                         f"combo={s['combo']}, nivel={s['level']}, género={s['genre']})")

        open(ml_path, "wb").write(ml)

        # repack + deploy con backup
        self.log_msg(">> repack + deploy...")
        for sub, dirn, pak in [(os.path.dirname(rs), "rnr_script", "rnr_script.pak"),
                               (os.path.dirname(img), "rnr_image_lt", "rnr_image_lt.pak")]:
            self.clean_svn(os.path.join(sub, dirn))
            outp = os.path.join(sub, pak)
            if os.path.exists(outp):
                os.remove(outp)
            self.run_pak(["-c", dirn], sub)
            if not os.path.exists(outp) or os.path.getsize(outp) == 0:
                self.log_msg(f"[ERROR] r2pak no generó {pak}; NO se deployó nada de este pak.")
                continue
            live = os.path.join(client, pak)
            bak = live + ".bak"
            if not os.path.exists(bak):
                shutil.copy(live, bak)
            shutil.copy(outp, live)
            self.log_msg(f"   {pak} desplegado ({os.path.getsize(live):,} bytes)")

        # preview de audio del picker (desde 2026-07-18): el mapa Index -> mp3 que lee
        # preview.dll. Si no se regenera, la canción nueva aparece en el picker pero NO
        # suena el preview, y falla en SILENCIO (no hay error ni crash que avise).
        warns += self._deploy_preview_map(client, rs, [s["index"] for s in batch])

        # ★ verificación final: ¿cada canción REALMENTE va a sonar? Replica la resolución del
        #   BGM del CBT (reversada) contra el rnr_music ya desplegado. Un fallo acá = la pista
        #   quedaría MUDA en el juego (causa histórica n_song: el .pak de audio no aterrizó).
        music_dir = os.path.join(client, "rnr_music")
        for s in batch:
            data = s.get("chart_data")
            if not data:
                continue
            m = re.search(rb'<BGM\s+Name\s*=\s*"([^"]+)"', data)
            if not m:
                continue
            bgm = m.group(1).decode("latin-1", "replace")
            motivo = verify_bgm_playable(music_dir, bgm)
            if motivo:
                warns.append(f"{s['index']}: ★ PISTA MUDA -> {motivo} (BGM {bgm!r}). "
                             "Agregá el .pak de audio al input y reimportá.")
            else:
                self.log_msg(f"   [{s['index']}] audio verificado OK: {bgm} suena")
            # chart: que no queden restos de <AREA>/</AREA> tras el strip (huérfanos ->
            # XMLParser del cliente peta con "Close tag not match" -> crash/silencio).
            stripped = strip_area(data)
            resto = stripped.count(b"<AREA") + stripped.count(b"</AREA")
            if resto:
                warns.append(f"{s['index']}: ★ CHART con {resto} tag(s) <AREA>/</AREA> "
                             "sin limpiar -> el cliente va a CRASHEAR al entrar. Revisá strip_area.")

        with open(os.path.join(out, "canciones_nuevas.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(songs_lines) + "\n")
        if edit_lines:
            # solo las que cambiaron de NOMBRE: en el server SONGS es (id, nombre)
            p_edit = os.path.join(out, "songs_editadas.txt")
            with open(p_edit, "w", encoding="utf-8") as f:
                f.write("\n".join(edit_lines) + "\n")
            warns.append(f"{len(edit_lines)} canciones existentes cambiaron de nombre -> "
                         f"actualizá esas líneas en SONGS del server ({p_edit})")
        shutil.rmtree(tmp, ignore_errors=True)

        self.log_msg(f"\n>>> LISTO: {len(songs_lines)} canciones importadas"
                     + (f" + {sum(1 for s in batch if s['src_kind'] == 'cliente')} editadas"
                        if any(s["src_kind"] == "cliente" for s in batch) else "")
                     + " al cliente.")
        for w in warns:
            self.log_msg(f"   [!] {w}")
        if songs_lines:
            self.log_msg(f">>> PASOS MANUALES (server):\n"
                         f"    1. Copiá {notes_out}\\*.bin a la carpeta datos/notas del server\n"
                         f"    2. Pegá las líneas de {os.path.join(out, 'canciones_nuevas.txt')} "
                         f"en SONGS de el servidor\n"
                         f"    3. Reiniciá el server.")
        elif edit_lines:
            self.log_msg(">>> PASO MANUAL (server): actualizá en SONGS de el servidor "
                         "las líneas de songs_editadas.txt (cambió el nombre) y reiniciá.")
        else:
            self.log_msg(">>> No hace falta tocar el server: no hay canciones nuevas "
                         "ni cambios de nombre.")
        # refrescar colisiones para una próxima pasada; las editadas vuelven a "limpias"
        # (si no, un 2º APLICAR las reescribiría sin que hayas tocado nada)
        for s in batch:
            self.client_index[s["index"]] = s["name"]
            if s["src_kind"] == "cliente":
                s["dirty"], s["include"] = False, False
                s["img_src"], s["audio_override"] = None, {}
                s["orig"] = {k: s[k] for k in EDITABLE_ATTRS}   # lo aplicado es el nuevo piso
                if s["chart_loc"] == "manual":
                    s["chart_loc"] = "cliente"
            else:
                s["collision"] = True
        self.root.after(0, self.refresh_table)

    def _deploy_preview_map(self, client, rs, idxs):
        """Regenera client/preview_map.txt desde el music_lt recién parchado. Devuelve
        la lista de avisos (canciones de la tanda que quedaron sin preview)."""
        pmap = os.path.join(client, "preview_map.txt")
        if not os.path.exists(os.path.join(client, "preview.dll")) and not os.path.exists(pmap):
            self.log_msg("   preview: el cliente no tiene preview.dll -> no hay mapa que regenerar")
            return []
        try:
            mapping = build_preview_map(rs, os.path.join(client, "rnr_music"))
            bak = pmap + ".bak"
            if os.path.exists(pmap) and not os.path.exists(bak):
                shutil.copy(pmap, bak)
            n, omit = write_preview_map(mapping, pmap)
            self.log_msg("   preview_map.txt regenerado (%d canciones%s)"
                         % (n, ", %d omitidas por encoding" % omit if omit else ""))
        except Exception as e:
            self.log_msg(f"[ERROR] no pude regenerar preview_map.txt ({e}) -> el preview de "
                         "las canciones nuevas NO va a sonar.")
            return ["preview_map.txt no se pudo regenerar (ver el error arriba)"]
        return [f"{i}: sin preview de audio (no quedó en preview_map.txt; "
                f"suele ser que falta su .pak en rnr_music)"
                for i in idxs if i not in mapping]

    def _find_input_pak(self, folder):
        # primero lo que indexó el escaneo (cubre paks dentro de .r2n desempacados)
        p = getattr(self, "input_paks", {}).get(folder)
        if p and os.path.exists(p):
            return p
        p = getattr(self, "audio_extra", {}).get(folder)      # banco compartido del respaldo
        if p and os.path.exists(p):
            return p
        inp = self._snap.get("input") or self.var_input.get()
        for dp, _dirs, files in os.walk(inp):
            for f in files:
                if f.lower() == folder + ".pak":
                    return os.path.join(dp, f)
        return None

    # -------------- fixcombo --------------
    def fix_combo(self):
        if self.busy or not self._check_paths(need_input=False):
            return
        self.save_cfg()          # el worker trabaja sobre la foto de rutas
        dlg = tk.Toplevel(self.root)
        dlg.title("Corregir Combo")
        dlg.grab_set()
        ttk.Label(dlg, padding=8, text=(
            "Si al entrar a una canción sale el popup 올콤카운트에러 Combo:c(X),s(Y),\n"
            "poné acá el Index y el valor s(Y) — edita solo el music_lt y repackea.")
        ).grid(row=0, column=0, columnspan=2)
        v_id, v_s = tk.StringVar(), tk.StringVar()
        ttk.Label(dlg, text="Index:").grid(row=1, column=0, sticky="e", padx=6)
        ttk.Entry(dlg, textvariable=v_id, width=10).grid(row=1, column=1, sticky="w")
        ttk.Label(dlg, text="Combo real s(Y):").grid(row=2, column=0, sticky="e", padx=6)
        ttk.Entry(dlg, textvariable=v_s, width=10).grid(row=2, column=1, sticky="w")

        def ok():
            try:
                idx, sval = int(v_id.get()), int(v_s.get())
            except ValueError:
                messagebox.showerror("Error", "Deben ser números.", parent=dlg)
                return
            dlg.destroy()
            self.busy = True
            threading.Thread(target=self._fixcombo_worker, args=(idx, sval),
                             daemon=True).start()
        ttk.Button(dlg, text="Aplicar", command=ok).grid(row=3, column=0, pady=8)
        ttk.Button(dlg, text="Cancelar", command=dlg.destroy).grid(row=3, column=1, pady=8)

    def _fixcombo_worker(self, idx, sval):
        try:
            client = self._snap["client"]
            tmp = os.path.join(TOOL_DIR, "_tmp_fix")
            shutil.rmtree(tmp, ignore_errors=True)
            rs = self.unpak(os.path.join(client, "rnr_script.pak"), os.path.join(tmp, "rs"))
            mlp = os.path.join(rs, "music_lt.txt")
            d = open(mlp, "rb").read()
            i = d.find(f'Index="{idx}"'.encode())
            if i < 0:
                self.log_msg(f"[fixcombo] no encontré Index {idx} en el music_lt del cliente")
                return
            a = d.rfind(b"<Music", 0, i)
            b = d.find(b"</Music>", i) + len(b"</Music>")
            nb = re.sub(rb'Combo="\d+"', f'Combo="{sval}"'.encode(), d[a:b], count=1)
            open(mlp, "wb").write(d[:a] + nb + d[b:])
            self.clean_svn(rs)
            outp = os.path.join(os.path.dirname(rs), "rnr_script.pak")
            if os.path.exists(outp):
                os.remove(outp)
            self.run_pak(["-c", "rnr_script"], os.path.dirname(rs))
            shutil.copy(outp, os.path.join(client, "rnr_script.pak"))
            shutil.rmtree(tmp, ignore_errors=True)
            self.log_msg(f"[fixcombo] Combo de {idx} -> {sval}. rnr_script.pak redeployado.")
        except Exception as e:
            self.log_msg(f"[ERROR] fixcombo: {e!r}")
        finally:
            self.busy = False

    def open_output(self):
        out = self.var_output.get()
        os.makedirs(out, exist_ok=True)
        os.startfile(out)


def main():
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = App(root)
    # al cerrar, cortar cualquier preview de audio en curso (libera el device MCI)
    root.protocol("WM_DELETE_WINDOW", lambda: (app._preview_stop(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
