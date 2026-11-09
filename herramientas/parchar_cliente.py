#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
  PREPARAR EL CLIENTE DE R2BEAT
=============================================================================

  Le hace al juego los dos cambios que necesita para funcionar hoy. Los DOS son
  obligatorios. Son 12 bytes en total; no se toca ni una instrucción del programa.

  Es todo o nada: si alguno de los dos sitios no aparece (por ejemplo, porque es
  una versión distinta del cliente), no se escribe NADA y se avisa. Un cliente a
  medio parchear es peor que uno sin tocar.

      python herramientas/parchar_cliente.py  C:\\ruta\\al\\juego  192.168.1.50

  Antes de tocar nada deja una copia intacta al lado (R2Beat.exe.original), así
  siempre podés volver:

      python herramientas/parchar_cliente.py  C:\\ruta\\al\\juego  --restaurar
      python herramientas/parchar_cliente.py  C:\\ruta\\al\\juego  --ver

  ---------------------------------------------------------------------------
  1) LA DIRECCIÓN DEL SERVIDOR  (10 bytes)
  ---------------------------------------------------------------------------
  El juego trae grabada adentro la dirección de los servidores de Softnyx, que
  hace años que no existen. No alcanza con configurarla en el lanzador: el login
  sí la toma de ahí, pero el LOBBY (el menú, las salas, el chat) usa la que está
  grabada en el .exe.

  ---------------------------------------------------------------------------
  2) COLOR DE 32 BITS EN VEZ DE 16  (2 bytes)  — TAMBIÉN OBLIGATORIO
  ---------------------------------------------------------------------------
  El juego es de 2011 y pide color de 16 bits, que Windows moderno ya no maneja
  de forma nativa: lo emula, y esa emulación es lentísima. El síntoma no parece
  un problema de color — parece que el juego "va lento porque es viejo".

  No es eso. Con estos 2 bytes el juego pasa a pedir 32 bits, que es lo que la
  placa de video hace de verdad. Medido en el cliente donde se descubrió:
  **111 FPS estables y 17% de un núcleo**, contra 52-54% de un núcleo cuando
  emulaba.

  De yapa se caen dos cosas más:
    - Ya NO hace falta el modo de compatibilidad "Windows XP SP3". Ese modo
      existe justamente para tapar juegos que piden 16 bits; arreglado en el
      origen, sobra. Y mientras está puesto FUERZA la ruta de video vieja, o
      sea que mete el lag que venía a evitar.
    - Ya NO hace falta ejecutar como administrador (lo pedía el modo de
      compatibilidad, no el juego).

  ⚠ Si igual querés usar DXVK, este parche es obligatorio: el modo de
    compatibilidad de XP esconde Vulkan y los dos juntos no funcionan.

  Los dos bytes son valores del enum D3DFORMAT de DirectX:
      0x14 = R8G8B8 (24 bits)      0x17 = R5G6B5   (16 bits)
      0x16 = X8R8G8B8 (32 bits)    0x19 = A1R5G5B5 (16 bits)

  Se ubican POR PATRÓN, buscando las instrucciones alrededor, NO por dirección
  fija. Es lo único que sobrevive a que cambie la versión del cliente. Si no
  encuentra exactamente un sitio de cada uno, no toca nada y avisa.
=============================================================================
"""
import os
import re
import shutil
import sys

# --- 1) la dirección del servidor -------------------------------------------
MARCADOR_IP = b"SERVER"      # el campo de la dirección va justo antes
LARGO_IP = 16                # 15 caracteres como máximo + el byte 0 que termina

# --- 2) el color -------------------------------------------------------------
# El filtro de la búsqueda de modo de video, justo después del `cmp bl,0x20`
# que separa la rama de 32 bits de la de 16.
PATRON_FILTRO = re.compile(
    rb"\x80\xfb\x20\x75.\x83\x38\x15\xeb.\x8b\x10\x83\xfa([\x14\x16\x17])"
    rb"\x74.\x83\xfa\x18\x74.\x83\xfa\x19", re.S)
# La tabla de formatos que el juego arma en la pila; nos importa el último.
PATRON_TABLA = re.compile(
    rb"\xc7\x44\x24.\x18\x00\x00\x00\xc7\x44\x24.\x19\x00\x00\x00"
    rb"\xc7\x44\x24.\x17\x00\x00\x00\xc7\x44\x24.([\x14\x16])\x00\x00\x00", re.S)
COLOR_32 = 0x16              # X8R8G8B8

EJECUTABLES = ["R2Beat.exe", "R2BeatMultiTest.exe", "R2Beat_experimental.exe"]


def es_ipv4(texto):
    partes = texto.split(".")
    if len(partes) != 4:
        return False
    for p in partes:
        if not p.isdigit() or not 0 <= int(p) <= 255:
            return False
        if len(p) > 1 and p[0] == "0":
            return False
    return True


def buscar_ip(datos):
    """(offset, direccion_actual) del campo de la dirección, o (None, None)."""
    desde = 0
    while True:
        i = datos.find(MARCADOR_IP, desde)
        if i < 0:
            return None, None
        off = i - LARGO_IP
        if off >= 0:
            texto = bytes(datos[off:off + LARGO_IP]).split(b"\x00")[0].decode("ascii", "ignore")
            if es_ipv4(texto):
                return off, texto
        desde = i + 1


def buscar_color(datos):
    """[(nombre, offset, valor)] de los 2 bytes del color, o None si algo no cierra."""
    f = list(PATRON_FILTRO.finditer(bytes(datos)))
    t = list(PATRON_TABLA.finditer(bytes(datos)))
    if len(f) != 1 or len(t) != 1:
        return None
    return [("filtro de formatos", f[0].start(1), datos[f[0].start(1)]),
            ("tabla de la pila",   t[0].start(1), datos[t[0].start(1)])]


def mirar(ruta):
    datos = bytearray(open(ruta, "rb").read())
    nombre = os.path.basename(ruta)
    off_ip, ip = buscar_ip(datos)
    color = buscar_color(datos)
    print("  %s" % nombre)
    if off_ip is None:
        print("      dirección : no la encuentro (¿otra versión del cliente?)")
    else:
        print("      dirección : %s" % ip)
    if color is None:
        print("      color     : no encuentro los 2 sitios -> no lo voy a tocar")
    else:
        listos = sum(1 for _n, _o, v in color if v == COLOR_32)
        print("      color     : %s (%d/2 bytes en 32 bits)"
              % ("32 bits" if listos == 2 else "16 bits — conviene parchear", listos))
    return off_ip is not None and color is not None


def parchar(ruta, nueva_ip):
    datos = bytearray(open(ruta, "rb").read())
    nombre = os.path.basename(ruta)
    cambios = []

    off_ip, ip_actual = buscar_ip(datos)
    if off_ip is None:
        print("  [!] %s: no encontré el campo de la dirección. No lo toco." % nombre)
        return False
    color = buscar_color(datos)
    if color is None:
        print("  [!] %s: no encontré los 2 sitios del color. No lo toco." % nombre)
        return False

    # copia de seguridad, UNA sola vez: si ya existe no se pisa, así el
    # ".original" sigue siendo el archivo virgen aunque parchees diez veces
    respaldo = ruta + ".original"
    if not os.path.exists(respaldo):
        shutil.copy2(ruta, respaldo)

    if ip_actual != nueva_ip:
        b = nueva_ip.encode("ascii")
        datos[off_ip:off_ip + LARGO_IP] = b + b"\x00" * (LARGO_IP - len(b))
        cambios.append("dirección %s -> %s" % (ip_actual, nueva_ip))
    for _n, off, val in color:
        if val != COLOR_32:
            datos[off] = COLOR_32
    faltaban = sum(1 for _n, _o, v in color if v != COLOR_32)
    if faltaban:
        cambios.append("color de 16 -> 32 bits (%d byte%s)" % (faltaban, "s" if faltaban > 1 else ""))

    if not cambios:
        print("  [=] %s: ya estaba listo." % nombre)
        return True

    open(ruta, "wb").write(bytes(datos))

    # releer del disco y confirmar
    rel = bytearray(open(ruta, "rb").read())
    _o, ip_ok = buscar_ip(rel)
    col_ok = buscar_color(rel)
    if ip_ok != nueva_ip or col_ok is None or any(v != COLOR_32 for _n, _o2, v in col_ok):
        print("  [!] %s: la verificación falló. Restaurá con --restaurar." % nombre)
        return False
    print("  [ok] %s: %s" % (nombre, " · ".join(cambios)))
    return True


def restaurar(ruta):
    respaldo = ruta + ".original"
    nombre = os.path.basename(ruta)
    if not os.path.exists(respaldo):
        print("  [=] %s: no hay copia guardada, lo dejo como está." % nombre)
        return True
    shutil.copy2(respaldo, ruta)
    _o, ip = buscar_ip(bytearray(open(ruta, "rb").read()))
    print("  [ok] %s: restaurado (vuelve a apuntar a %s)" % (nombre, ip))
    return True


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2

    carpeta, accion = argv[1], argv[2]
    if not os.path.isdir(carpeta):
        print("ERROR: no existe la carpeta %s" % carpeta)
        return 1

    presentes = [os.path.join(carpeta, n) for n in EJECUTABLES
                 if os.path.isfile(os.path.join(carpeta, n))]
    if not presentes:
        print("ERROR: en %s no hay ningún ejecutable de R2Beat." % carpeta)
        print("       Busqué: %s" % ", ".join(EJECUTABLES))
        return 1

    if accion == "--ver":
        print("Estado del cliente en %s\n" % carpeta)
        ok = all([mirar(p) for p in presentes])
    elif accion == "--restaurar":
        print("Restaurando el cliente de %s\n" % carpeta)
        ok = all([restaurar(p) for p in presentes])
    else:
        if not es_ipv4(accion):
            print("ERROR: '%s' no es una dirección IP válida." % accion)
            print("       Tiene que ser algo como 192.168.1.50")
            return 1
        if len(accion) > LARGO_IP - 1:
            print("ERROR: la dirección '%s' no entra (máximo %d caracteres)."
                  % (accion, LARGO_IP - 1))
            return 1
        if accion.startswith("127."):
            print("AVISO: 127.0.0.1 no suele funcionar acá. El cliente le pasa esta")
            print("       dirección a otras partes del juego y se pierde. Usá la IP de")
            print("       tu red local (192.168.x.x) aunque el servidor sea esta PC.\n")
        print("Preparando el cliente de %s\n            para el servidor %s\n" % (carpeta, accion))
        ok = all([parchar(p, accion) for p in presentes])

    print()
    if ok:
        if accion not in ("--ver", "--restaurar"):
            print("Listo. Ya podés entrar con:  python jugar.py")
        return 0
    print("Terminó con errores. Mirá los mensajes de arriba.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
