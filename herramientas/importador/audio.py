#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
  HERRAMIENTAS DE AUDIO  (opcionales)
=============================================================================

  Dos utilidades para cuando una cancion "se siente corrida": las notas no caen
  donde suena el golpe. Ninguna de las dos hace falta para importar musica; son
  para diagnosticar y para re-masterizar.

    python audio.py grabar
        Abre una ventanita con un boton. Graba lo que sale de los parlantes
        (o sea: la musica del juego MAS los sonidos de las notas, exactamente
        como los escucha el jugador). Sirve para tener una prueba grabada de
        que algo esta corrido.

    python audio.py desfase e_song1275:1275 e_song1606:1606
        Mide el desfase SOLO, sin oido. Compara donde dice el chart que va cada
        nota contra donde estan los golpes reales en el audio, y te dice cuantos
        milisegundos hay de diferencia.

        Cada argumento es  <carpeta-de-audio>:<Index>  . Compará una cancion que
        suena BIEN contra una que suena mal: la diferencia entre los dos numeros
        es la correccion que hay que aplicar.

  QUE NECESITAN
  -------------
    grabar   ->  pip install soundcard numpy
    desfase  ->  pip install numpy  +  ffmpeg instalado y accesible

  Las rutas del cliente y de r2pak salen de config.json, igual que el importador.
=============================================================================
"""
import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import zlib

CARPETA = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(CARPETA, "config.json")

SR_GRABACION = 48000       # calidad de la grabacion
SR_ANALISIS = 22050        # para medir alcanza con menos
N_FFT = 1024
SALTO = 256                # ~11.6 ms por cuadro


def cargar_config():
    cfg = {"client": "", "r2pak": ""}
    try:
        cfg.update(json.load(open(CONFIG, encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def buscar_ffmpeg():
    ruta = shutil.which("ffmpeg")
    if not ruta:
        print("[ERROR] no encuentro ffmpeg. Instalalo y asegurate de que este en el PATH.")
        print("        Windows:  winget install Gyan.FFmpeg")
    return ruta


# ===========================================================================
#  GRABAR: captura lo que suena en los parlantes
# ===========================================================================

def cmd_grabar(_args):
    try:
        import numpy as np
        import soundcard as sc
    except ImportError:
        print("[ERROR] falta instalar las dependencias:  pip install soundcard numpy")
        return 2
    import threading
    import time
    import tkinter as tk
    import wave

    destino = os.path.join(CARPETA, "grabaciones")
    os.makedirs(destino, exist_ok=True)

    class Grabador:
        def __init__(self, root):
            self.root, self.grabando, self.trozos, self.t0 = root, False, [], 0

            root.title("Grabador R2Beat")
            root.geometry("420x260")
            root.configure(bg="#1e1e2e")

            tk.Label(root, text="Grabador de audio del juego", fg="#cdd6f4", bg="#1e1e2e",
                     font=("Segoe UI", 13, "bold")).pack(pady=(16, 2))
            self.dispositivo = tk.Label(root, text="", fg="#89b4fa", bg="#1e1e2e",
                                        font=("Segoe UI", 8))
            self.dispositivo.pack()
            self.estado = tk.Label(root, text="Listo para grabar", fg="#a6e3a1", bg="#1e1e2e",
                                   font=("Segoe UI", 11))
            self.estado.pack(pady=(12, 6))
            self.reloj = tk.Label(root, text="00:00", fg="#f9e2af", bg="#1e1e2e",
                                  font=("Consolas", 26, "bold"))
            self.reloj.pack()
            self.boton = tk.Button(root, text="●  GRABAR", command=self.alternar,
                                   font=("Segoe UI", 14, "bold"), width=16, height=1,
                                   bg="#a6e3a1", fg="#11111b", activebackground="#94e2d5",
                                   relief="flat", cursor="hand2")
            self.boton.pack(pady=14)
            tk.Label(root, text="Boton o tecla ENTER para grabar / detener",
                     fg="#6c7086", bg="#1e1e2e", font=("Segoe UI", 8)).pack()
            self.ruta = tk.Label(root, text="", fg="#89b4fa", bg="#1e1e2e",
                                 font=("Segoe UI", 8), wraplength=400)
            self.ruta.pack(pady=(6, 0))

            root.bind("<Return>", lambda e: self.alternar())
            try:
                self.dispositivo.config(text="grabando de: " + sc.default_speaker().name)
            except Exception as e:
                self.dispositivo.config(text="aviso: " + str(e))
            self._tic()

        def alternar(self):
            if not self.grabando:
                self.grabando, self.trozos, self.t0 = True, [], time.time()
                self.estado.config(text="● GRABANDO...", fg="#f38ba8")
                self.boton.config(text="■  DETENER", bg="#f38ba8", activebackground="#eba0ac")
                self.ruta.config(text="")
                threading.Thread(target=self._trabajar, daemon=True).start()
            else:
                self.grabando = False
                self.estado.config(text="guardando...", fg="#f9e2af")

        def _trabajar(self):
            # "loopback" = grabar la salida, no el microfono. No hace falta Stereo Mix.
            parlante = sc.default_speaker()
            micro = sc.get_microphone(parlante.name, include_loopback=True)
            with micro.recorder(samplerate=SR_GRABACION, blocksize=2048) as rec:
                while self.grabando:
                    self.trozos.append(rec.record(numframes=4800))   # ~0.1 s
            self._guardar()

        def _guardar(self):
            if not self.trozos:
                self.root.after(0, lambda: self.estado.config(text="no se grabo nada",
                                                              fg="#f38ba8"))
                return
            datos = np.concatenate(self.trozos, axis=0)
            mono = datos.mean(axis=1) if datos.ndim > 1 else datos
            pcm16 = (np.clip(mono, -1, 1) * 32767).astype("<i2")
            nombre = "grabacion_%s.wav" % time.strftime("%Y%m%d_%H%M%S")
            ruta = os.path.join(destino, nombre)
            with wave.open(ruta, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SR_GRABACION)
                w.writeframes(pcm16.tobytes())
            segundos = len(mono) / SR_GRABACION
            self.root.after(0, lambda: (
                self.estado.config(text="guardado (%.0f s)" % segundos, fg="#a6e3a1"),
                self.boton.config(text="●  GRABAR", bg="#a6e3a1", activebackground="#94e2d5"),
                self.ruta.config(text=ruta)))

        def _tic(self):
            if self.grabando:
                s = int(time.time() - self.t0)
                self.reloj.config(text="%02d:%02d" % (s // 60, s % 60))
            self.root.after(200, self._tic)

    root = tk.Tk()
    Grabador(root)
    root.mainloop()
    return 0


# ===========================================================================
#  DESFASE: cuanto estan corridas las notas respecto de la musica
# ===========================================================================
#  Como funciona, en criollo:
#    1. ffmpeg convierte el mp3 de la cancion a numeros crudos.
#    2. Se calcula la "envolvente de golpes": los momentos donde la energia
#       sube de golpe. Eso es un bombo, una nota, un ataque de percusion.
#    3. Del archivo de notas se sacan los tiempos en que el juego dice que hay
#       que apretar.
#    4. Se desliza la lista de notas hacia adelante y hacia atras buscando en
#       que posicion coincide mejor con los golpes reales. Ese corrimiento es
#       el desfase.

def _sacar_mp3(cfg, carpeta_audio, tmp):
    """Saca el mr.mp3 del .pak de la cancion. Devuelve la ruta o None."""
    destino = os.path.join(tmp, carpeta_audio)
    mp3 = os.path.join(destino, carpeta_audio, "mr.mp3")
    if os.path.exists(mp3):
        return mp3
    origen = os.path.join(cfg["client"], "rnr_music", carpeta_audio + ".pak")
    if not os.path.exists(origen):
        return None
    os.makedirs(destino, exist_ok=True)
    shutil.copy(origen, destino)
    subprocess.run([cfg["r2pak"], "-d", carpeta_audio + ".pak"],
                   cwd=destino, capture_output=True)
    return mp3 if os.path.exists(mp3) else None


def _envolvente(ffmpeg, mp3):
    import numpy as np
    pcm = subprocess.run(
        [ffmpeg, "-v", "quiet", "-i", mp3, "-ac", "1", "-ar", str(SR_ANALISIS),
         "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(pcm, dtype=np.float32)
    if len(x) < N_FFT:
        return None, 0
    ventana = np.hanning(N_FFT).astype(np.float32)
    cuadros = 1 + (len(x) - N_FFT) // SALTO
    magnitud = np.empty((cuadros, N_FFT // 2 + 1), dtype=np.float32)
    for i in range(cuadros):
        trozo = x[i * SALTO: i * SALTO + N_FFT] * ventana
        magnitud[i] = np.abs(np.fft.rfft(trozo))
    # solo nos interesan las SUBIDAS de energia (un ataque), no las bajadas
    flujo = np.maximum(0.0, np.diff(magnitud, axis=0)).sum(axis=1)
    return flujo / (flujo.max() + 1e-9), SR_ANALISIS / SALTO


def _leer_notas(ruta_bin):
    import numpy as np
    b = open(ruta_bin, "rb").read()
    n = struct.unpack_from("<i", b, 0)[0]
    bpm = struct.unpack_from("<f", b, 8)[0] if n else 120.0
    crudo = zlib.decompress(b[4 + n * 8:])
    coords = sorted(struct.unpack_from("<H", crudo, i)[0] for i in range(0, len(crudo), 6))
    return np.array([c / 12.0 / bpm * 60 for c in coords]), bpm


def _mejor_desfase(envolvente, fps, tiempos):
    import numpy as np
    candidatos = np.arange(-0.6, 0.601, 0.005)      # probamos de -600 a +600 ms
    base = (tiempos * fps).astype(int)
    puntajes, mejor, mejor_phi = [], -1, 0.0
    for phi in candidatos:
        idx = base + int(round(phi * fps))
        dentro = (idx >= 0) & (idx < len(envolvente))
        p = envolvente[idx[dentro]].sum() / max(1, dentro.sum())
        puntajes.append(p)
        if p > mejor:
            mejor, mejor_phi = p, phi
    # "confianza": cuanto sobresale el mejor puntaje del monton. Menos de ~2x
    # quiere decir que la medicion no es concluyente (audio muy parejo, poca
    # percusion, o el .bin no corresponde a ese audio).
    confianza = mejor / (float(np.median(puntajes)) + 1e-9)
    return mejor_phi, confianza


def cmd_desfase(args):
    try:
        import numpy  # noqa: F401
    except ImportError:
        print("[ERROR] falta instalar numpy:  pip install numpy")
        return 2

    ffmpeg = buscar_ffmpeg()
    if not ffmpeg:
        return 2

    cfg = cargar_config()
    if not cfg.get("client") or not cfg.get("r2pak"):
        print("[ERROR] config.json tiene que tener 'client' y 'r2pak'.")
        return 2

    carpeta_notas = os.path.abspath(args.notas)
    if not os.path.isdir(carpeta_notas):
        print("[ERROR] no existe la carpeta de notas: %s" % carpeta_notas)
        return 2

    tmp = os.path.join(CARPETA, "_tmp_audio")
    os.makedirs(tmp, exist_ok=True)

    print("\n%-22s %-6s %-11s %-8s %s" % ("cancion", "BPM", "desfase", "confianza", "notas"))
    print("-" * 64)
    for pedido in args.canciones:
        if ":" not in pedido:
            print("%-22s (formato invalido: usá carpeta_audio:Index)" % pedido)
            continue
        carpeta_audio, _, idx = pedido.partition(":")
        etiqueta = "%s/%s" % (carpeta_audio, idx)
        ruta_bin = os.path.join(carpeta_notas, "%s.bin" % idx)
        if not os.path.exists(ruta_bin):
            print("%-22s (no existe %s.bin)" % (etiqueta, idx))
            continue
        mp3 = _sacar_mp3(cfg, carpeta_audio, tmp)
        if not mp3:
            print("%-22s (no encuentro el audio %s.pak)" % (etiqueta, carpeta_audio))
            continue
        env, fps = _envolvente(ffmpeg, mp3)
        if env is None:
            print("%-22s (el audio es demasiado corto)" % etiqueta)
            continue
        tiempos, bpm = _leer_notas(ruta_bin)
        phi, conf = _mejor_desfase(env, fps, tiempos)
        aviso = "" if conf >= 2.0 else "   <- poco confiable"
        print("%-22s %-6.0f %+8.0f ms %6.1fx  %d%s"
              % (etiqueta, bpm, phi * 1000, conf, len(tiempos), aviso))
    print("-" * 64)
    print("El desfase por si solo no dice mucho. Compará una cancion que suena BIEN")
    print("contra una que suena mal: la DIFERENCIA entre los dos numeros es lo que")
    print("hay que corregir.")
    return 0


# ===========================================================================

def main():
    ap = argparse.ArgumentParser(description="Herramientas de audio del importador.")
    sub = ap.add_subparsers(dest="comando")

    sub.add_parser("grabar", help="grabar lo que sale de los parlantes")

    p = sub.add_parser("desfase", help="medir cuanto estan corridas las notas")
    p.add_argument("canciones", nargs="+", metavar="carpeta_audio:Index",
                   help="por ejemplo  e_song1275:1275")
    p.add_argument("--notas", default=os.path.join(CARPETA, "..", "..", "datos", "notas"),
                   help="carpeta con los .bin de notas")

    args = ap.parse_args()
    if not args.comando:
        ap.print_help()
        return 2
    return {"grabar": cmd_grabar, "desfase": cmd_desfase}[args.comando](args)


if __name__ == "__main__":
    sys.exit(main())
