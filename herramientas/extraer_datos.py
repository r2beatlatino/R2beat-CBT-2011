#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
  SACAR DEL CLIENTE LAS TABLAS QUE NECESITA EL SERVIDOR
=============================================================================

  POR QUE ESTA HERRAMIENTA EXISTE
  -------------------------------
  El servidor necesita saber cosas que estan escritas adentro del juego: que
  items hay en la tienda y cuanto salen, que caras y que alas existen, y la
  lista de canciones.

  Esos archivos son de Softnyx, no nuestros, asi que NO estan en este
  repositorio. Pero ya los tenes: vienen adentro del cliente que instalaste.
  Esta herramienta los saca de ahi y los deja donde el servidor los busca.

  COMO SE USA
  -----------
      python herramientas/extraer_datos.py  C:\\ruta\\al\\cliente  C:\\ruta\\a\\r2pak.exe

  Al terminar vas a tener, en la carpeta datos/:

      face.txt         las caras
      wing.txt         las alas
      shop_lt.txt      el catalogo de la tienda (que hay y a que precio)
      canciones.txt    la lista de canciones del selector

  QUE NO SACA: las notas de cada cancion (datos/notas/*.bin). Esas se generan
  aparte, a partir de los charts del cliente, con el importador de musica:
  mirá herramientas/importador/LEEME.md.

  QUE NECESITAS: r2pak.exe, la herramienta que abre los .pak del juego. No viene
  con este repositorio porque no es nuestra.
=============================================================================
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

CARPETA = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(os.path.dirname(CARPETA), "datos")

# Los archivos que buscamos, y como se van a llamar en datos/.
BUSCAR = {
    "face.txt":     "face.txt",
    "wing.txt":     "wing.txt",
    "shop_lt.txt":  "shop_lt.txt",
    "music_lt.txt": None,        # no se copia: se convierte a canciones.txt
    "stage_lt.txt": None,        # no se copia: se convierte a escenarios.txt
}

# En que .pak mirar. Se prueban en este orden y se para cuando ya encontro todo.
# Si tu cliente los tiene en otro lado, pasá --todos y busca en todos.
PAKS_PROBABLES = [
    "rnr_script.pak",
    "rnr_game.pak",
    "rnr_item.pak",
    "rnr_ui_LT.pak",
    "rnr_ui_bg.pak",
]


def desempacar(pak, r2pak, destino):
    """Desempaca un .pak en una carpeta temporal. Devuelve True si funciono."""
    os.makedirs(destino, exist_ok=True)
    copia = os.path.join(destino, os.path.basename(pak))
    shutil.copy(pak, copia)
    try:
        subprocess.run([r2pak, "-d", os.path.basename(pak)],
                       cwd=destino, capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        print("      no pude desempacarlo: %s" % e)
        return False
    finally:
        try:
            os.remove(copia)
        except OSError:
            pass
    return True


def buscar_en(carpeta):
    """Devuelve {nombre_buscado: ruta} de lo que aparezca dentro de carpeta."""
    hallado = {}
    for raiz, _dirs, archivos in os.walk(carpeta):
        for a in archivos:
            if a.lower() in BUSCAR:
                hallado.setdefault(a.lower(), os.path.join(raiz, a))
    return hallado


def hacer_lista_canciones(music_lt, destino):
    """Convierte el music_lt del cliente en la lista simple que lee el servidor.

    El servidor solo necesita 'numero|nombre' por linea. El music_lt trae eso y
    veinte campos mas que no le hacen falta."""
    crudo = open(music_lt, "rb").read()
    entradas = {}
    for bloque in re.findall(rb"<Music\b.*?(?:</Music>|/>)", crudo, re.S):
        texto = bloque.decode("euc-kr", "replace")
        m_idx = re.search(r'Index\s*=\s*"(\d+)"', texto)
        m_nom = re.search(r'Name\s*=\s*"([^"]*)"', texto)
        if m_idx:
            entradas[int(m_idx.group(1))] = (m_nom.group(1) if m_nom else "").replace("|", "-")

    lineas = ["# Lista de canciones del selector: numero|nombre",
              "# La lee el servidor al arrancar. Los numeros 50-55 son el tutorial.",
              "# Generado por herramientas/extraer_datos.py"]
    lineas += ["%d|%s" % (i, entradas[i]) for i in sorted(entradas)]
    open(destino, "w", encoding="utf-8", newline="\n").write("\n".join(lineas) + "\n")
    return len(entradas)


def hacer_lista_escenarios(stage_lt, destino):
    """Saca del cliente los números de escenario que ese cliente REALMENTE tiene.

    ⚠ Esto no es un detalle: si el servidor le dice al jugador que tiene un
    escenario que su cliente no conoce, el juego SE CIERRA de golpe al entrar al
    lobby, sin ningún mensaje. Por eso la lista se saca de acá y no se escribe
    a mano."""
    crudo = open(stage_lt, "rb").read().decode("euc-kr", "replace")
    ids = sorted({int(m) for m in re.findall(r'Index\s*=\s*"(\d+)"', crudo)})
    lineas = ["# Escenarios (pistas) que tiene ESTE cliente, uno por línea.",
              "# Generado por herramientas/extraer_datos.py — no editar a mano:",
              "# anunciar un escenario que el cliente no tiene lo hace cerrarse."]
    lineas += [str(i) for i in ids]
    open(destino, "w", encoding="utf-8", newline="\n").write("\n".join(lineas) + "\n")
    return len(ids)


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2

    cliente, r2pak = argv[1], argv[2]
    todos = "--todos" in argv

    if not os.path.isdir(cliente):
        print("ERROR: no existe la carpeta del cliente: %s" % cliente)
        return 1
    if not os.path.isfile(r2pak):
        print("ERROR: no encuentro r2pak.exe en: %s" % r2pak)
        return 1

    if todos:
        paks = sorted(f for f in os.listdir(cliente) if f.lower().endswith(".pak"))
    else:
        paks = [p for p in PAKS_PROBABLES if os.path.isfile(os.path.join(cliente, p))]
    if not paks:
        print("ERROR: no hay ningun .pak en %s" % cliente)
        return 1

    os.makedirs(DATOS, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="r2b_extraer_")
    encontrados = {}

    print("Buscando las tablas en el cliente...\n")
    try:
        for pak in paks:
            faltan = [k for k in BUSCAR if k not in encontrados]
            if not faltan:
                break
            print("  mirando %s ..." % pak)
            sub = os.path.join(tmp, pak.replace(".", "_"))
            if not desempacar(os.path.join(cliente, pak), r2pak, sub):
                continue
            for nombre, ruta in buscar_en(sub).items():
                if nombre not in encontrados:
                    encontrados[nombre] = ruta
                    print("      encontre %s" % nombre)

        print()
        copiados = 0
        for nombre, destino_nombre in BUSCAR.items():
            origen = encontrados.get(nombre)
            if not origen:
                print("  [falta] %s" % nombre)
                continue
            if destino_nombre:
                destino = os.path.join(DATOS, destino_nombre)
                shutil.copy(origen, destino)
                print("  [ok] %s -> datos/%s (%d bytes)"
                      % (nombre, destino_nombre, os.path.getsize(destino)))
                copiados += 1

        if "music_lt.txt" in encontrados:
            destino = os.path.join(DATOS, "canciones.txt")
            n = hacer_lista_canciones(encontrados["music_lt.txt"], destino)
            print("  [ok] music_lt.txt -> datos/canciones.txt (%d canciones)" % n)
            copiados += 1
        if "stage_lt.txt" in encontrados:
            destino = os.path.join(DATOS, "escenarios.txt")
            n = hacer_lista_escenarios(encontrados["stage_lt.txt"], destino)
            print("  [ok] stage_lt.txt -> datos/escenarios.txt (%d escenarios)" % n)
            copiados += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if copiados == len(BUSCAR):
        print("Listo, estan todas. Ya podés arrancar el servidor:")
        print("    python correr_servidor.py")
        return 0

    print("Faltaron algunas. Probá con --todos, que busca en TODOS los .pak")
    print("(tarda bastante mas, pero no se le escapa ninguna):")
    print("    python herramientas/extraer_datos.py \"%s\" \"%s\" --todos" % (cliente, r2pak))
    print()
    print("El servidor arranca igual sin ellas, pero con limitaciones: sin")
    print("shop_lt.txt no funciona la tienda, y sin canciones.txt el selector")
    print("aparece vacio.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
