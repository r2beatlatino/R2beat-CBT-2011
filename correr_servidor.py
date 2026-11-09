# -*- coding: utf-8 -*-
"""
=============================================================================
  ARRANQUE DEL SERVIDOR DE R2BEAT
=============================================================================

  Esto es lo que se ejecuta para levantar el servidor:

      python correr_servidor.py

  No hay nada que instalar: alcanza con Python 3.10 o mas nuevo.
  La primera vez se crea sola la base de datos en datos/cuentas.db.

  Lo que hace, en orden:
     1. abre el archivo de log
     2. crea la base de datos si no existe
     3. levanta el servidor de LOGIN en el puerto 48004
     4. levanta un servidor de LOBBY por cada canal (48005/48015/48025/48035)
     5. carga el catalogo de la tienda
     6. se queda escuchando hasta que lo cortes con Ctrl+C

  Si algo falla al arrancar, el error queda impreso en la ventana y no se
  cierra sola, para que se pueda leer.
=============================================================================
"""
import asyncio
import os
import sys

from servidor import ajustes, registro, telemetry
from servidor.ajustes import ADMIN_UIDS, CONNECTIONS, ENABLE_DEV, HOST, LOGIN_PORT
from servidor.basedatos import db_get_char, db_init
from servidor.canciones import NPC_AUTOPLAY, NPC_MOVE
from servidor.catalogo import load_shop_catalog
from servidor.lobby import LobbyProtocol
from servidor.login import LoginProtocol
from servidor.progresion import LOBBY_PORTS
from servidor.registro import log


async def main():
    registro.abrir_log()
    db_init()

    loop = asyncio.get_running_loop()

    # Un servidor de login, y un servidor de lobby POR CANAL. LobbyProtocol es
    # el mismo para todos: cual canal es lo decide el cliente al entrar, no el
    # puerto. Por eso hay que tener los cuatro puertos abiertos en el firewall.
    login_srv = await loop.create_server(LoginProtocol, HOST, LOGIN_PORT)
    lobby_srvs = [await loop.create_server(LobbyProtocol, HOST, p) for p in LOBBY_PORTS]

    # Panel de estado (solo lectura): escribe quien esta online a datos/estado.json
    asyncio.create_task(telemetry.writer(
        CONNECTIONS,
        lambda uid: (lambda fila: fila["nickname"] if fila else "")(db_get_char(uid)),
    ))

    load_shop_catalog()

    log(f"[servidor] login  {HOST}:{LOGIN_PORT}")
    log(f"[servidor] lobby  {HOST}:{LOBBY_PORTS}   (IP que se anuncia: {ajustes.LOCAL_IP})")
    log(f"[servidor] base de datos: {ajustes.DB_PATH}")
    log(f"[servidor] modo desarrollo: {'SI' if ENABLE_DEV else 'no'}   "
        f"NPC automaticos: {'si' if NPC_AUTOPLAY else 'no'}   "
        f"NPC en movimiento: {'si' if NPC_MOVE else 'no'}")
    if ADMIN_UIDS:
        log(f"[servidor] administradores: {sorted(ADMIN_UIDS)} (comandos de chat habilitados)")
    else:
        log("[servidor] AVISO: no hay administradores. Los comandos de chat que "
            "empiezan con punto (.gm1 .anuncio .rate ...) no van a funcionar. "
            "Para habilitarlos: R2_ADMIN_UIDS=<tu uid> antes de arrancar.")

    log("[servidor] listo. Ctrl+C para cortar.")
    async with login_srv:
        await asyncio.gather(login_srv.serve_forever(),
                             *[s.serve_forever() for s in lobby_srvs])


if __name__ == "__main__":
    # Pararse en la carpeta del proyecto, asi funciona igual con doble clic.
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("[servidor] cortado a mano. Chau.")
    except Exception:
        import traceback
        print("\n" + "=" * 70)
        print("  EL SERVIDOR NO PUDO ARRANCAR. El error esta abajo.")
        print("=" * 70)
        traceback.print_exc()
        print("=" * 70)
        print("\nSi no entendes el error, copialo entero y pedi ayuda con eso.")
        try:
            input("\nApreta ENTER para cerrar esta ventana...")
        except Exception:
            pass
        sys.exit(1)
