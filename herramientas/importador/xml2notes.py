#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xml2notes.py  -  Conversor STANDALONE de charts XML de R2Beat al .bin FF57.

Toma los charts de notas en XML de los R2 antiguos (los <BPM>/<AREA> que viven en
  rnr_music/<song>/stagedata.xml   y   rnr_school/music/note_script/*.xml)
y produce el BLOB binario que sirve nuestro server (notes/<id>.bin) y que el parser
del cliente (0x4AFCE0) acepta.

FORMATO DE SALIDA (.bin = blob FF57 completo)
---------------------------------------------
  Bloque A  (timeline/BPM, SIN comprimir):
      [count:int32] + count × ( [marker:int32][bpm:float32] )
  Bloque B  (notas, zlib COMPRIMIDO):
      zlib( N × [Coord:uint16][Kind:uint8][Level:uint8][Fx:uint16] )

  El cliente lee 'count' como int32 CRUDO del inicio (NO descomprime el Bloque A) y
  solo infla el Bloque B; el nº de notas = len(inflado)//6. Por eso el Bloque A debe ir
  en claro antes del zlib (mandar solo zlib(notas) -> lee el header 78 9C como count ->
  std::map vacío -> crash 0xC0000417).

MAPEO XML -> bin
---------------
  <BPM  Frame=F  BPM=V >                 -> [marker=int(round(F)) : int32][bpm=V : float32]
  <AREA Coord=C Kind=K Level=L FxSndIndex=X > -> [C:u16][K:u8][L:u8][X:u16]
  <AREA ... AutoPlay="1" >                -> MARCADA con Fx|=0x8000 (nota invisible/sonido-solo;
                                            el FxSndIndex real queda en los 14 bits bajos; ver parse_xml)

  BIT DE MARCA EN Fx: 0x8000 = AutoPlay. ★SOLUCIÓN PURA-BIN, CERO parches al exe: el parser nativo
  ya trata ese bit como "nota invisible + no-juzgable" (chart+9=0x80 -> rt+0x10=1), y su mask 0x7fff
  deja el FxSndIndex real intacto -> la nota suena sola pero no se dibuja ni rompe combo (= gfis).
  Estas notas NO cuentan en el Combo (el importador las excluye vía combo_del_exe, bit Fx&0x8000).

USO
---
  python xml2notes.py  ENTRADA.xml  SALIDA.bin
  python xml2notes.py  ENTRADA.xml  -o notes/170.bin
  python xml2notes.py  --batch  CARPETA  -o notes        (convierte todos los charts hallados)
  python xml2notes.py  --dump   notes/170.bin            (inspecciona un .bin existente)

Solo stdlib. No toca nada externo.
"""
import struct
import zlib
import re
import os
import sys
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Atributos vienen como ASCII aunque el XML sea euc-kr; trabajamos en bytes para no pelear con encodings.
ATTR_RE = re.compile(rb'([A-Za-z_]\w*)\s*=\s*"([^"]*)"')
BPM_TAG_RE  = re.compile(rb'<BPM\b([^>]*)>', re.IGNORECASE)
AREA_TAG_RE = re.compile(rb'<AREA\b([^>]*)>', re.IGNORECASE)


def _attrs(blob):
    return {k.decode('ascii').lower(): v.decode('ascii', 'replace')
            for k, v in ATTR_RE.findall(blob)}


def parse_xml(path):
    """Devuelve (bpms, notes, warnings).
       bpms  = [(frame:float, bpm:float), ...]   (orden del documento)
       notes = [(coord:int, kind:int, level:int, fx:int), ...]  (ordenadas por Coord)"""
    data = open(path, 'rb').read()
    warnings = []

    bpms = []
    for m in BPM_TAG_RE.finditer(data):
        a = _attrs(m.group(1))
        if 'frame' in a and 'bpm' in a:
            try:
                bpms.append((float(a['frame']), float(a['bpm'])))
            except ValueError:
                warnings.append(f"BPM con valores no numéricos: {a}")

    notes = []
    marked_autoplay = 0
    for m in AREA_TAG_RE.finditer(data):
        a = _attrs(m.group(1))
        try:
            # Algunos charts del R2 Online traen Coord fraccionario (ej. "65.08"); el sistema del
            # juego usa Coord entero -> parseamos como float y redondeamos. Para enteros es idéntico.
            coord = int(round(float(a['coord']))); kind = int(round(float(a['kind'])))
            level = int(round(float(a['level']))); fx = int(round(float(a['fxsndindex'])))
        except (KeyError, ValueError):
            warnings.append(f"AREA con atributos faltantes/no numéricos: {a}")
            continue
        # AutoPlay="1": nota que en R2Single SUENA sola pero es INVISIBLE/no-pulsable. La marcamos
        # con Fx|=0x8000 (★SOLUCIÓN FINAL, PROBADA en 444 easy+normal, CERO parches al exe): el
        # parser FF57 (0x4b01a3) ve ese bit y pone la propiedad chart+9=0x80, que en el spawn
        # (0x4bdfd6) enciende rt+0x10=1 → el render la SALTEA (invisible) Y el juez la EXCLUYE
        # (no cuenta como miss → combo intacto). El mask 0x7fff del parser deja el FxSndIndex real
        # limpio en los 14 bits bajos, así que sigue sonando. = comportamiento gfis exacto.
        # VERIFICADO SOBRE 1255 charts: 0 notas normales usan Fx&0x8000 y ningún autoplay es hold
        # → marcador 100% seguro. El ÚNICO detector fiable de autoplay es el atributo AutoPlay del
        # chart (los Kind son variados: 16-23,26,27). Ver autoplay_test/CONTEXTO_AUTOPLAY.md.
        # ⚠ Estas notas NO deben contar en el Combo del music_lt (el cliente no las cuenta):
        #   el importador las excluye vía combo_del_exe (bit Fx&0x8000).
        ap = a.get('autoplay')
        if ap is not None and ap.strip() not in ('', '0'):
            if fx & 0x8000:
                warnings.append(f"AutoPlay coord={coord} ya traía bit 0x8000 en Fx={fx}; se respeta")
            fx |= 0x8000
            marked_autoplay += 1
        if not (0 <= coord <= 0xFFFF):
            warnings.append(f"Coord fuera de u16 ({coord}); recortado")
        if not (0 <= kind <= 0xFF):
            warnings.append(f"Kind fuera de u8 ({kind}); recortado")
        if not (0 <= fx <= 0xFFFF):
            warnings.append(f"FxSndIndex fuera de u16 ({fx}); recortado")
        notes.append((coord, kind, level, fx))

    # El cliente espera las notas en orden de tiempo (Coord ascendente). Sort estable.
    notes.sort(key=lambda n: n[0])
    # DEDUP de coords: el validador del cliente marca "올콤카운트에러 / 중복좌표갯수" (error de full-combo
    # por coords duplicadas) si dos notas comparten Coord. Moverlas +1 NO alcanza (quedan demasiado cerca
    # y el juez las sigue tomando como el mismo punto) -> DESCARTAMOS la nota duplicada (se queda la 1a de
    # cada Coord). Así steps == combo y no hay error.
    dedup = []; seen = set(); dropped = 0
    for note in notes:
        if note[0] in seen:
            dropped += 1; continue
        seen.add(note[0]); dedup.append(note)
    if dropped:
        warnings.append(f"notas en coord duplicado DESCARTADAS: {dropped}")
    if marked_autoplay:
        warnings.append(f"notas AutoPlay MARCADAS con Fx|=0x8000 (invisibles/sonido-solo, no cuentan combo): {marked_autoplay}")
    return bpms, dedup, warnings


def build_blob(bpms, notes):
    """Arma el blob FF57 (Bloque A en claro + Bloque B zlib)."""
    if not bpms:
        # Sin BPM no hay timeline; el cliente lo necesita. Avisamos arriba; metemos 1 por defecto.
        bpms = [(0.0, 120.0)]
    a = bytearray()
    a += struct.pack('<i', len(bpms))
    for frame, bpm in bpms:
        a += struct.pack('<i', int(round(frame)))
        a += struct.pack('<f', bpm)

    raw = bytearray()
    for coord, kind, level, fx in notes:
        raw += struct.pack('<HBBH', coord & 0xFFFF, kind & 0xFF, level & 0xFF, fx & 0xFFFF)
    b = zlib.compress(bytes(raw), 9)
    return bytes(a) + b, len(raw)


def verify_blob(blob):
    """Re-lee un blob y devuelve (n_bpm, n_notas, ok, detalle)."""
    try:
        count = struct.unpack_from('<i', blob, 0)[0]
        off = 4 + count * 8
        if off > len(blob):
            return count, 0, False, "Bloque A más largo que el archivo"
        raw = zlib.decompress(blob[off:])
        if len(raw) % 6 != 0:
            return count, -1, False, f"Bloque B inflado no es múltiplo de 6 (len={len(raw)})"
        return count, len(raw) // 6, True, "ok"
    except Exception as e:
        return -1, -1, False, f"error: {e}"


def convert_file(inp, outp):
    bpms, notes, warns = parse_xml(inp)
    blob, rawlen = build_blob(bpms, notes)
    os.makedirs(os.path.dirname(os.path.abspath(outp)), exist_ok=True)
    with open(outp, 'wb') as f:
        f.write(blob)
    nb, nn, ok, det = verify_blob(blob)
    status = "OK" if (ok and nn == len(notes)) else "REVISAR"
    print(f"[{status}] {os.path.basename(inp)} -> {outp}")
    print(f"        BPM={len(bpms)}  notas={len(notes)}  bin={len(blob)}b  "
          f"(verificación: {nb} BPM / {nn} notas, {det})")
    if not bpms or (len(bpms) == 1 and bpms == [(0.0, 120.0)]):
        if not parse_xml(inp)[0]:
            print("        [!] el XML no traía <BPM>; se usó BPM=120 por defecto.")
    for w in warns[:8]:
        print("        [!]", w)
    if len(warns) > 8:
        print(f"        [!] (+{len(warns)-8} avisos más)")
    return ok and nn == len(notes)


def dump_bin(path):
    blob = open(path, 'rb').read()
    nb, nn, ok, det = verify_blob(blob)
    print(f"{path}: {len(blob)}b  ->  {nb} BPM entries, {nn} notas, {det}")
    if ok:
        count = struct.unpack_from('<i', blob, 0)[0]
        off = 4
        for i in range(count):
            marker, bpm = struct.unpack_from('<if', blob, off); off += 8
            print(f"  BPM[{i}] marker={marker} bpm={bpm}")
        raw = zlib.decompress(blob[off:])
        for i in range(0, min(60, len(raw)), 6):
            c, k, l, fx = struct.unpack_from('<HBBH', raw, i)
            print(f"  nota Coord={c} Kind={k} Level={l} Fx={fx}")
        if len(raw) > 60:
            print(f"  ... (+{len(raw)//6 - 10} notas)")


def find_charts(root):
    """Busca charts (XML con <AREA>) bajo root. Devuelve [(xml_path, nombre_sugerido), ...]."""
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.lower().endswith('.xml'):
                continue
            full = os.path.join(dirpath, fn)
            try:
                head = open(full, 'rb').read()
            except OSError:
                continue
            if b'<AREA' not in head:
                continue
            # nombre sugerido: si es stagedata.xml usa la carpeta padre; si no, el stem del archivo
            if fn.lower() == 'stagedata.xml':
                name = os.path.basename(dirpath)
            else:
                name = os.path.splitext(fn)[0]
            out.append((full, name))
    return out


def main():
    ap = argparse.ArgumentParser(description="Convierte charts XML de R2Beat al .bin FF57.")
    ap.add_argument('input', nargs='?', help="XML de entrada (o .bin si se usa --dump)")
    ap.add_argument('output', nargs='?', help="ruta .bin de salida")
    ap.add_argument('-o', '--out', help="archivo .bin de salida, o carpeta destino (con --batch)")
    ap.add_argument('--batch', metavar='DIR', help="convierte todos los charts XML bajo DIR")
    ap.add_argument('--dump', metavar='BIN', help="inspecciona un .bin existente y sale")
    args = ap.parse_args()

    if args.dump:
        dump_bin(args.dump)
        return

    if args.batch:
        outdir = args.out or 'notes'
        charts = find_charts(args.batch)
        if not charts:
            print(f"No se hallaron charts (XML con <AREA>) bajo {args.batch}")
            return
        print(f"Hallados {len(charts)} charts. Salida -> {outdir}/")
        ok = 0
        for xml, name in charts:
            if convert_file(xml, os.path.join(outdir, f"{name}.bin")):
                ok += 1
        print(f"\nListo: {ok}/{len(charts)} convertidos correctamente.")
        return

    if not args.input:
        ap.error("falta el XML de entrada (o usa --batch / --dump)")
    outp = args.output or args.out
    if not outp:
        ap.error("falta la salida: da OUTPUT.bin o -o ruta")
    convert_file(args.input, outp)


if __name__ == '__main__':
    main()
