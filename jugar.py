#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
  ENTRAR AL JUEGO
=============================================================================

      python jugar.py

  Abre una ventanita para poner usuario y contraseña, y arranca el juego.

  ¿POR QUÉ NO ALCANZA CON ABRIR R2Beat.exe?
  -----------------------------------------
  Porque el juego, solo, no sabe decir quién sos. No tiene pantalla de login
  propia: espera que alguien lo abra ya autenticado, pasándole por línea de
  comandos a qué servidor ir y un permiso temporal.

  Eso es lo que hace este programa, en tres pasos:

    1. Se conecta al servidor y valida tu usuario y contraseña.
    2. Deja anotado tu número de usuario donde el juego lo va a buscar.
       (En Windows, en el registro. Es donde el juego original lo guardaba.)
    3. Abre R2Beat.exe pasándole:   SN <servidor> <puerto> <permiso>

  Si abrís el .exe a mano, se saltea los tres pasos, el servidor no sabe quién
  sos y te rechaza.

  LA PRIMERA VEZ
  --------------
  Te va a pedir dos cosas: la dirección de tu servidor y dónde está instalado
  el juego. Las guarda en jugar.json y no vuelve a preguntar.

  Para probar sin registrarte hay tres cuentas ya creadas: usuario "estrella",
  "luna" o "sol", contraseña "1234a" en las tres. (Ojo: el usuario va en
  minúscula. "EstrellaUser" es el apodo del personaje, no el usuario.)
=============================================================================
"""
import datetime
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys

CARPETA = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(CARPETA, "jugar.json")

PUERTO_LOGIN = 48004
ESPERA = 5                       # segundos antes de darse por vencido
CMD_REGISTRO = 0xF0
# Se prueban en este orden; se usa el primero que exista en la carpeta del juego.
EJECUTABLES = ["R2Beat.exe", "R2BeatMultiTest.exe"]


# ---------------------------------------------------------------- config ----

def leer_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"servidor": "", "juego": ""}


def guardar_config(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def buscar_exe(carpeta):
    for nombre in EJECUTABLES:
        ruta = os.path.join(carpeta, nombre)
        if os.path.isfile(ruta):
            return ruta
    return None


# -------------------------------------------------------------- protocolo ----

def hash_contrasena(texto):
    """El servidor nunca ve la contraseña: sólo este resumen."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest().upper()


def _preguntar(host, paquete):
    with socket.create_connection((host, PUERTO_LOGIN), timeout=ESPERA) as s:
        s.sendall(paquete)
        return s.recv(512)


def entrar(host, usuario, contrasena):
    """Valida la cuenta. Devuelve (ok, token_o_error, numero_de_usuario)."""
    u = usuario.encode("ascii", "ignore")
    h = hash_contrasena(contrasena).encode("ascii")
    cuerpo = bytearray()
    cuerpo += struct.pack("<I", 0)      # 0 = que el servidor lo resuelva por nombre
    cuerpo.append(0)
    cuerpo.append(len(u)); cuerpo += u
    cuerpo.append(len(h)); cuerpo += h
    paquete = b"\xFF\x01" + struct.pack("<H", len(cuerpo)) + cuerpo
    try:
        datos = _preguntar(host, paquete)
    except OSError as e:
        return False, f"no pude conectarme a {host}:{PUERTO_LOGIN}\n({e})", 0
    if len(datos) < 5 or datos[0] != 0xFF or datos[1] != 0x01:
        return False, "el servidor contestó algo que no entiendo", 0
    r = datos[4:]
    if r[0] != 0x00:
        return False, "usuario o contraseña incorrectos", 0
    uid = struct.unpack_from("<I", r, 1)[0]
    largo = r[9]
    token = r[10:10 + largo].decode("ascii", "replace")
    if not token:
        return False, "entró bien pero el servidor no dio permiso de entrada", uid
    return True, token, uid


def registrar(host, usuario, contrasena):
    if not usuario or not contrasena:
        return False, "hay que poner usuario y contraseña"
    u = usuario.encode("ascii", "ignore")
    h = hash_contrasena(contrasena).encode("ascii")
    cuerpo = bytearray()
    cuerpo.append(len(u)); cuerpo += u
    cuerpo.append(len(h)); cuerpo += h
    paquete = bytes([0xFF, CMD_REGISTRO]) + struct.pack("<H", len(cuerpo)) + cuerpo
    try:
        datos = _preguntar(host, paquete)
    except OSError as e:
        return False, f"no pude conectarme ({e})"
    r = datos[4:]
    estado = r[0]
    largo = r[1] if len(r) > 1 else 0
    msg = r[2:2 + largo].decode("utf-8", "replace") if largo else ""
    if msg:
        return estado == 0x00, msg
    return (estado == 0x00,
            "cuenta creada" if estado == 0x00 else
            "ese usuario ya existe" if estado == 0x01 else "no se pudo crear")


# ----------------------------------------------------------------- lanzar ----

def anotar_usuario(uid):
    """Deja el número de usuario donde el juego lo va a leer.

    El juego lo busca en el registro de Windows, en la misma ubicación que usaba
    el original. Después lo manda al servidor, y así el servidor sabe quién sos
    aunque dos personas compartan la misma conexión a internet."""
    if sys.platform != "win32":
        return
    import winreg
    clave = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                               r"Software\Softnyx\R2Beat", 0, winreg.KEY_SET_VALUE)
    for nombre in ("SUSN", "USN", "UID", "SESSION"):
        winreg.SetValueEx(clave, nombre, 0, winreg.REG_DWORD, int(uid))
    n = datetime.datetime.now()
    winreg.SetValueEx(clave, "TIME", 0, winreg.REG_SZ,
                      f"{n.year}-{n.month}-{n.day}-{n.hour}-{n.minute}")
    winreg.CloseKey(clave)


def abrir_juego(carpeta, host, token):
    exe = buscar_exe(carpeta)
    if not exe:
        raise FileNotFoundError(
            "en %s no hay ninguno de estos: %s" % (carpeta, ", ".join(EJECUTABLES)))
    return subprocess.Popen(f'"{exe}" SN {host} {PUERTO_LOGIN} {token}', cwd=carpeta)


# ---------------------------------------------------------------- ventana ----

def main():
    import tkinter as tk
    from tkinter import filedialog

    cfg = leer_config()
    raiz = tk.Tk()
    raiz.title("R2Beat — entrar")
    raiz.geometry("430x340")
    raiz.resizable(False, False)

    tk.Label(raiz, text="Servidor (la IP donde corre correr_servidor.py)").pack(pady=(12, 0))
    e_host = tk.Entry(raiz, width=44); e_host.pack()
    e_host.insert(0, cfg.get("servidor", ""))

    marco = tk.Frame(raiz); marco.pack(pady=(8, 0))
    tk.Label(marco, text="Carpeta del juego").pack()
    e_juego = tk.Entry(marco, width=36); e_juego.pack(side="left")
    e_juego.insert(0, cfg.get("juego", ""))

    def elegir():
        d = filedialog.askdirectory(title="¿Dónde está R2Beat.exe?")
        if d:
            e_juego.delete(0, "end"); e_juego.insert(0, d)
    tk.Button(marco, text="...", command=elegir, width=3).pack(side="left", padx=(4, 0))

    tk.Label(raiz, text="Usuario").pack(pady=(10, 0))
    e_user = tk.Entry(raiz, width=24); e_user.pack()
    tk.Label(raiz, text="Contraseña").pack(pady=(6, 0))
    e_pass = tk.Entry(raiz, width=24, show="*"); e_pass.pack()

    estado = tk.Label(raiz, text="", fg="gray", wraplength=400, justify="left")
    estado.pack(pady=8)

    def revisar():
        host, juego = e_host.get().strip(), e_juego.get().strip()
        if not host:
            estado.config(text="Falta la dirección del servidor.", fg="red"); return None
        if not os.path.isdir(juego):
            estado.config(text="Esa carpeta del juego no existe.", fg="red"); return None
        if not buscar_exe(juego):
            estado.config(text="En esa carpeta no está R2Beat.exe.", fg="red"); return None
        guardar_config({"servidor": host, "juego": juego})
        return host, juego

    def al_entrar():
        d = revisar()
        if not d:
            return
        host, juego = d
        estado.config(text="Conectando...", fg="gray"); raiz.update()
        ok, res, uid = entrar(host, e_user.get().strip(), e_pass.get())
        if not ok:
            estado.config(text=res, fg="red"); return
        try:
            anotar_usuario(uid)
            abrir_juego(juego, host, res)
            estado.config(text=f"Listo, entrando como usuario {uid}.\n"
                               "Esta ventana podés dejarla abierta.", fg="green")
        except OSError as e:
            estado.config(text=str(e), fg="red")

    def al_registrar():
        d = revisar()
        if not d:
            return
        estado.config(text="Creando la cuenta...", fg="gray"); raiz.update()
        ok, msg = registrar(d[0], e_user.get().strip(), e_pass.get())
        estado.config(text=msg, fg=("green" if ok else "red"))

    botones = tk.Frame(raiz); botones.pack()
    tk.Button(botones, text="Entrar y jugar", command=al_entrar, width=16).grid(row=0, column=0, padx=4)
    tk.Button(botones, text="Crear cuenta", command=al_registrar, width=16).grid(row=0, column=1, padx=4)

    tk.Label(raiz, text="cuentas de prueba:  estrella  /  luna  /  sol      clave: 1234a",
             fg="gray", font=("", 8)).pack(side="bottom", pady=6)

    raiz.mainloop()


if __name__ == "__main__":
    main()
