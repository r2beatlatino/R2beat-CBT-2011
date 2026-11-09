#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
  IMPORTADOR DE MUSICA — VERSION DE CONSOLA
=============================================================================

  Hace lo mismo que importador.py pero sin ventana, para cuando queres importar
  muchas canciones de una o automatizarlo.

  No duplica nada: llama a las MISMAS funciones que usa la ventana. Si arreglas
  un error aca, queda arreglado en los dos lados.

  LOS CUATRO COMANDOS
  -------------------
    escanear    mira que hay en la carpeta de entrada y lo lista. No toca nada.
    importar    mete las canciones en el CLIENTE y prepara los archivos del servidor.
    reedicion   importar, pero renombrando todo para que no pise canciones que ya existen.
    aplicar     copia al SERVIDOR lo que dejo preparado el paso anterior.

  EJEMPLOS
  --------
    python importar.py escanear
    python importar.py importar --solo 1275
    python importar.py importar --solo 1275,1276,1277
    python importar.py importar --todas
    python importar.py aplicar --salida salida --servidor ..\\..\\
    python importar.py reedicion --entrada C:\\musica\\reediciones --base 5000

  EL ORDEN NORMAL DE TRABAJO
  --------------------------
    1. escanear   -> ver que se detecto bien y que no tenga problemas
    2. importar   -> queda metido en el cliente
    3. aplicar    -> queda metido en el servidor
    4. reiniciar el servidor (eso no lo hace nadie por vos, a proposito)
=============================================================================
"""
import argparse
import ast
import json
import os
import re
import shutil
import sys

CARPETA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CARPETA)

# Se importa el modulo de la ventana, pero NUNCA se abre una ventana: solo se
# usan sus funciones.
import importador as G


# ===========================================================================
#  El "puente": le presta a la consola los metodos reales de la ventana
# ===========================================================================

class _VentanaFalsa:
    """La ventana de verdad usa root.after(...) para no congelarse mientras
    trabaja. Aca no hay ventana que congelar, asi que ejecutamos directo."""
    def after(self, _ms, fn=None, *a):
        if callable(fn):
            fn(*a)


class SinVentana:
    """Se hace pasar por la ventana. Toma prestados sus metodos tal cual estan,
    sin copiar una sola linea de logica."""

    run_pak             = G.App.run_pak
    unpak               = G.App.unpak
    clean_svn           = staticmethod(G.App.clean_svn)
    _unpack_client      = G.App._unpack_client
    _do_scan            = G.App._do_scan
    _do_apply           = G.App._do_apply
    _find_input_pak     = G.App._find_input_pak
    _deploy_preview_map = G.App._deploy_preview_map

    def __init__(self, cfg):
        self.cfg = dict(cfg)
        self._snap = dict(cfg)      # los procesos largos trabajan sobre esta foto
        self.songs = []
        self.input_paks = {}
        self.audio_extra = {}
        self.busy = False
        self.root = _VentanaFalsa()

    def log_msg(self, msg):
        print(msg, flush=True)

    def refresh_table(self):
        pass                        # la ventana redibuja su tabla; aca no hay tabla


# ===========================================================================
#  Configuracion y chequeos previos
# ===========================================================================

def cargar_config(args):
    cfg = dict(G.DEFAULTS)
    try:
        cfg.update(json.load(open(G.CONFIG_PATH, encoding="utf-8")))
    except (OSError, ValueError):
        pass
    for clave in ("input", "client", "output", "r2pak", "audio_extra"):
        valor = getattr(args, clave, None)
        if valor:
            cfg[clave] = valor
    return cfg


def revisar_config(cfg):
    """Los mismos chequeos que hace la ventana antes de dejarte apretar Aplicar."""
    problemas = []
    if not cfg.get("input") or not os.path.isdir(cfg["input"]):
        problemas.append("la carpeta de entrada no existe: %s" % cfg.get("input"))
    cliente = cfg.get("client") or ""
    if not cliente:
        problemas.append("falta decir donde esta instalado el juego (client en config.json)")
    else:
        for pieza in ("rnr_script.pak", "rnr_image_lt.pak"):
            if not os.path.exists(os.path.join(cliente, pieza)):
                problemas.append("al cliente le falta %s (%s)" % (pieza, cliente))
        if not os.path.isdir(os.path.join(cliente, "rnr_music")):
            problemas.append("al cliente le falta la carpeta rnr_music (%s)" % cliente)
    if not cfg.get("r2pak") or not os.path.exists(cfg["r2pak"]):
        problemas.append("no encuentro r2pak.exe: %s" % cfg.get("r2pak"))
    if G.xml2notes is None:
        problemas.append("no pude cargar xml2notes.py (tiene que estar al lado de importar.py)")
    return problemas


def preparar(args, necesita_todo=True):
    """Carga config, la valida, escanea la entrada y devuelve el objeto listo."""
    cfg = cargar_config(args)
    print("entrada : %s" % cfg.get("input"))
    print("cliente : %s" % cfg.get("client"))
    print("salida  : %s" % cfg.get("output"))
    if necesita_todo:
        problemas = revisar_config(cfg)
        if problemas:
            print()
            for p in problemas:
                print("[ERROR] %s" % p)
            print("\nRevisá config.json (podés copiar config-ejemplo.json).")
            return None
    os.makedirs(cfg["output"], exist_ok=True)
    app = SinVentana(cfg)
    app._do_scan()
    return app


def mostrar_tabla(songs):
    print("\n%-7s %-34s %-5s %-14s %-9s %-7s %s" %
          ("Index", "Nombre", "Nivel", "Genero", "Notas", "Tapa", "Problemas"))
    print("-" * 118)
    for s in sorted(songs, key=lambda x: x["index"]):
        print("%-7d %-34.34s %-5s %-14.14s %-9s %-7s %s" % (
            s["index"], s["name"], s["level"], s["genre"],
            (s["notes_from"] or "-").split(" ")[0], s["img_loc"],
            "; ".join(s["problems"]) or "ok"))
    print("-" * 118)
    audios = {carpeta: estado for s in songs for carpeta, estado in s["audio_stat"]}
    if audios:
        print("audio: " + ", ".join("%s=%s" % kv for kv in sorted(audios.items())))


def _nivel(s):
    try:
        return int(s["level"])
    except (TypeError, ValueError):
        return 0


# ===========================================================================
#  escanear
# ===========================================================================

def cmd_escanear(args):
    app = preparar(args)
    if app is None:
        return 2
    mostrar_tabla(app.songs)
    marcables = [s for s in app.songs if not s["problems"]]
    print("\n%d canciones detectadas, %d sin problemas." % (len(app.songs), len(marcables)))
    print("Para importar:  python importar.py importar --solo <Index>")
    return 0


# ===========================================================================
#  importar
# ===========================================================================

def cmd_importar(args):
    if args.combo_crudo:
        G.COMBO_CRUDO = True
        print("*** MODO PRUEBA: Combo = numero de notas crudo (el comportamiento viejo\n"
              "    y roto). El juego va a tirar el cartel de error de combo. ***")

    app = preparar(args)
    if app is None:
        return 2
    mostrar_tabla(app.songs)

    if args.todas:
        for s in app.songs:
            s["include"] = not s["problems"]
    if args.solo:
        pedidos = {int(x) for x in args.solo.replace(" ", "").split(",") if x}
        for s in app.songs:
            s["include"] = s["index"] in pedidos
        faltan = pedidos - {s["index"] for s in app.songs}
        if faltan:
            print("[ERROR] estos Index no aparecieron en el escaneo: %s"
                  % ", ".join(map(str, sorted(faltan))))
            return 2

    tanda = [s for s in app.songs if s["include"]]
    if not tanda:
        print("[ERROR] no marcaste nada. Usá --solo <Index> o --todas.")
        return 2

    if args.desde_index is not None:
        # Renumera correlativo, agrupando por cancion y dificultad. Sirve para
        # "empezar de cero" sin conservar los Index del material de origen.
        tanda.sort(key=lambda s: (s["name"].lower(), _nivel(s)))
        for k, s in enumerate(tanda):
            s["index"] = args.desde_index + k
        print(">> Index reasignado: %d..%d (%d filas)"
              % (args.desde_index, args.desde_index + len(tanda) - 1, len(tanda)))

    con_problemas = [s for s in tanda if s["problems"]]
    if con_problemas:
        print("\n[ERROR] estas tienen problemas, no se importa ninguna:")
        for s in con_problemas:
            print("   %d: %s" % (s["index"], "; ".join(s["problems"])))
        return 2

    print("\n>> importando %d: %s" %
          (len(tanda), ", ".join("%d (%s)" % (s["index"], s["name"]) for s in tanda)))
    app._do_apply(tanda)
    print("\nListo en el cliente. Ahora, para el servidor:")
    print("   python importar.py aplicar --salida %s" % app.cfg["output"])
    return 0


# ===========================================================================
#  reedicion
# ===========================================================================
#  Una "reedicion" es una version alternativa de una cancion que ya existe.
#  El problema: reusa el nombre interno original (e_song4230, n_song4935...),
#  que ya esta ocupado. Si la importaras tal cual, pisarias el chart y el audio
#  de la cancion vieja. Aca se le inventa un nombre unico ANTES de importar:
#
#      e_song4230  (Index nuevo 5030)  ->  e_reed5030
#
#  Se conserva el prefijo e_/n_/h_ porque de ahi sale la banda (estrella/luna/sol).

def _reescribir_carpeta(chart, carpeta_vieja, token_nuevo):
    """Cambia toda referencia a la carpeta de audio dentro del chart (BGM y EFFECT)."""
    nuevo = token_nuevo.encode()
    variantes = set()
    m = re.search(rb'<BGM\s+Name="([^/"]+)/', chart)
    if m:
        variantes.add(m.group(1))
    variantes.add(carpeta_vieja.encode())
    variantes.add(carpeta_vieja.upper().encode())
    for v in variantes:
        if v and v != nuevo:
            chart = chart.replace(v + b"/", nuevo + b"/")
    return chart


def cmd_reedicion(args):
    app = preparar(args)
    if app is None:
        return 2

    songs = sorted(app.songs, key=lambda s: (s["name"].lower(), _nivel(s)))
    cliente = app.cfg["client"]
    charts_cliente = {c.lower() for c in getattr(app, "client_charts", set())}
    audio_cliente = {f.lower() for f in os.listdir(os.path.join(cliente, "rnr_music"))
                     if f.lower().endswith(".pak")}

    tmp = os.path.join(CARPETA, "_tmp_reedicion")
    if args.aplicar:
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)

    tanda, problemas = [], 0
    print("\n%-6s %-33s %-13s %-6s %-13s %s"
          % ("Index", "Nombre", "nombre nuevo", "banda", "audio origen", "choca?"))
    print("-" * 100)

    for k, s in enumerate(songs):
        idx = args.base + k
        base = os.path.basename(s.get("file_rel") or "").lower()
        prefijo = base[:2] if base[:2] in ("e_", "n_", "h_") else ""
        token = "%sreed%d" % (prefijo, idx)
        banda = {"e_": "estr", "n_": "luna", "h_": "sol"}.get(prefijo, "?")
        info = s.get("chart_info") or {}
        carpeta_vieja = (info.get("bgm") or "").lower()

        choques = []
        if (token + ".xml") in charts_cliente:
            choques.append("CHART")
        if (token + ".pak") in audio_cliente:
            choques.append("AUDIO")
        pak_origen = app.input_paks.get(carpeta_vieja)
        if not pak_origen:
            choques.append("falta-audio(%s)" % carpeta_vieja)
        if choques:
            problemas += 1

        print("%-6d %-33.33s %-13s %-6s %-13.13s %s"
              % (idx, s["name"], token, banda, carpeta_vieja, ",".join(choques) or "ok"))

        if args.aplicar and not choques:
            s["chart_data"] = _reescribir_carpeta(s["chart_data"], carpeta_vieja, token)
            s["index"] = idx
            s["file_rel"] = "rnr_script/music/" + token + ".xml"
            s["image_rel"] = "rnr_image/img_music/" + token + ".bmp"
            s["audio_stat"] = [(token, "entrada")]
            s["audio_override"] = {token: G.repack_audio(pak_origen, token, app.cfg["r2pak"], tmp)}
            if info:
                info["bgm"] = token
                info["effects"] = [token if e.lower() == carpeta_vieja else e
                                   for e in info.get("effects", [])]
            s["collision"] = False
            s["include"] = True
            tanda.append(s)

    print("-" * 100)
    print("chocan con el cliente: %d" % problemas)

    if not args.aplicar:
        print("\nNo se toco nada (esto fue solo la vista previa).")
        print("Si el mapeo se ve bien, volvé a correrlo agregando --aplicar")
        return 0
    if problemas:
        print("\n[ERROR] hay choques o falta audio. No importo nada. Mirá arriba.")
        return 2

    print("\n>> importando %d reediciones renombradas..." % len(tanda))
    app._do_apply(tanda)
    return 0


# ===========================================================================
#  aplicar  (lo unico que toca el servidor)
# ===========================================================================

def _leer_lista_canciones(path):
    """Devuelve (comentarios, {index: nombre}) del archivo de canciones del servidor."""
    comentarios, canciones = [], {}
    if os.path.exists(path):
        for linea in open(path, encoding="utf-8"):
            linea = linea.rstrip("\r\n")
            if not linea.strip():
                continue
            if linea.strip().startswith("#"):
                comentarios.append(linea)
                continue
            sid, _, nombre = linea.partition("|")
            canciones[int(sid)] = nombre
    return comentarios, canciones


def cmd_aplicar(args):
    salida = args.salida if os.path.isabs(args.salida) else os.path.join(CARPETA, args.salida)
    servidor = os.path.abspath(args.servidor)
    notas_destino = os.path.join(servidor, "datos", "notas")
    lista_destino = os.path.join(servidor, "datos", "canciones.txt")

    if not os.path.isdir(salida):
        print("[ERROR] no existe la carpeta de salida: %s" % salida)
        return 2
    if not os.path.isdir(notas_destino):
        print("[ERROR] no encuentro %s" % notas_destino)
        print("        --servidor tiene que apuntar a la carpeta que tiene correr_servidor.py")
        return 2

    # --- 1) las notas ---
    notas_origen = os.path.join(salida, "notas")
    bins = sorted(f for f in os.listdir(notas_origen) if f.endswith(".bin")) \
        if os.path.isdir(notas_origen) else []
    nuevas = sum(1 for f in bins if not os.path.exists(os.path.join(notas_destino, f)))
    print("notas: %d archivos (%d nuevas, %d se reemplazan)"
          % (len(bins), nuevas, len(bins) - nuevas))
    if not args.simular:
        for f in bins:
            shutil.copy(os.path.join(notas_origen, f), os.path.join(notas_destino, f))

    # --- 2) la lista de canciones ---
    txt = os.path.join(salida, "canciones_nuevas.txt")
    entradas = {}
    if os.path.exists(txt):
        for linea in open(txt, encoding="utf-8"):
            linea = linea.strip().rstrip(",")
            if linea.startswith("("):
                idx, nombre = ast.literal_eval(linea)
                entradas[idx] = nombre
    print("lista: %d entradas nuevas" % len(entradas))
    if any("|" in n for n in entradas.values()):
        print("[ERROR] un nombre tiene el caracter '|', que es el separador del archivo.")
        return 2

    comentarios, actuales = _leer_lista_canciones(lista_destino)
    agrega = {i: n for i, n in entradas.items() if i not in actuales}
    cambia = {i: n for i, n in entradas.items() if i in actuales and actuales[i] != n}
    print("   ya estaban       : %d" % (len(entradas) - len(agrega)))
    print("   se agregan       : %d" % len(agrega))
    print("   les cambia el nom: %d" % len(cambia))
    for i, n in list(cambia.items())[:5]:
        print("      %d: %r -> %r" % (i, actuales[i], n))

    actuales.update(entradas)
    contenido = "\n".join(list(comentarios)
                          + ["%d|%s" % (sid, actuales[sid]) for sid in sorted(actuales)]) + "\n"

    if args.simular:
        print("\n(simulacion: no escribi nada. Quedarian %d canciones.)" % len(actuales))
        return 0
    open(lista_destino, "w", encoding="utf-8", newline="\n").write(contenido)
    print("\nEl servidor queda con %d canciones." % len(actuales))
    print(">>> FALTA: reiniciar el servidor para que las lea.")
    return 0


# ===========================================================================

def main():
    ap = argparse.ArgumentParser(
        description="Importador de musica de R2Beat (consola).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Mirá LEEME.md para el paso a paso completo.")
    sub = ap.add_subparsers(dest="comando")

    def rutas(p):
        p.add_argument("--entrada", dest="input", help="carpeta con la musica nueva")
        p.add_argument("--cliente", dest="client", help="carpeta donde esta instalado el juego")
        p.add_argument("--salida", dest="output", help="donde dejar lo que va al servidor")
        p.add_argument("--r2pak", help="ruta a r2pak.exe")
        p.add_argument("--audio-extra", dest="audio_extra")

    p = sub.add_parser("escanear", help="listar que hay en la entrada, sin tocar nada")
    rutas(p)

    p = sub.add_parser("importar", help="meter las canciones en el cliente")
    rutas(p)
    p.add_argument("--solo", default="", help="Index a importar, separados por coma")
    p.add_argument("--todas", action="store_true", help="importar todo lo que no tenga problemas")
    p.add_argument("--desde-index", type=int, default=None,
                   help="renumerar la tanda correlativa desde este Index")
    p.add_argument("--combo-crudo", action="store_true",
                   help="solo para experimentos: vuelve al calculo viejo y roto del combo")

    p = sub.add_parser("reedicion", help="importar versiones alternativas sin pisar las originales")
    rutas(p)
    p.add_argument("--base", type=int, default=5000, help="Index desde el que numerar (por defecto 5000)")
    p.add_argument("--aplicar", action="store_true",
                   help="importar de verdad (sin esto solo muestra que haria)")

    p = sub.add_parser("aplicar", help="copiar al servidor las notas y la lista")
    p.add_argument("--salida", default="salida", help="carpeta de salida del importador")
    p.add_argument("--servidor", default=os.path.join(CARPETA, "..", ".."),
                   help="carpeta del servidor (la que tiene correr_servidor.py)")
    p.add_argument("--simular", action="store_true", help="mostrar sin escribir nada")

    args = ap.parse_args()
    if not args.comando:
        ap.print_help()
        return 2
    return {"escanear": cmd_escanear, "importar": cmd_importar,
            "reedicion": cmd_reedicion, "aplicar": cmd_aplicar}[args.comando](args)


if __name__ == "__main__":
    sys.exit(main())
