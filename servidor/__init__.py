# -*- coding: utf-8 -*-
"""
Servidor de R2Beat, repartido en piezas.

BASES (cada una sólo depende de las de arriba, nunca al revés):

    ajustes.py      Configuración y estado en memoria. Empezá por acá.
    registro.py     El log de consola y de archivo.
    cifrado.py      El XOR del protocolo y el hash de las contraseñas.
    progresion.py   Experiencia -> nivel -> licencia -> canal.
    basedatos.py    Cuentas, personajes, amigos, pareja (SQLite).
    catalogo.py     Tienda, inventario y ropa puesta.
    canciones.py    Canciones, notas y premio de cada carrera.
    paquetes.py     Leer y armar los mensajes del protocolo.

LOS DOS SERVIDORES:

    login.py        Puerto 48004. Valida la cuenta y entrega la lista de canales.
    lobby.py        Puertos 48005/48015/48025/48035. Todo lo demás.

`lobby.py` es grande, así que está repartido en piezas por tema. Cada pieza es
una clase suelta, y `LobbyProtocol` las junta todas:

    lobby.py            conexión, y el despachador que reparte cada mensaje
    lobby_menu.py       personaje, menú principal, cambio de canal
    lobby_salas.py      crear/listar/entrar/salir de salas, quién está adentro
    lobby_carrera.py    la carrera y el cartel de resultados
    lobby_social.py     amigos, pareja, susurros, misiones
    lobby_tienda.py     comprar ropa y ponérsela
    lobby_admin.py      comandos de administrador (los que empiezan con punto)

Si buscás dónde se hace algo, andá directo a la pieza por el nombre. Si no sabés
en cuál está, buscá el comando en `lobby.py`: el despachador los nombra a todos.

Para arrancar el servidor no se usa este paquete directamente: se ejecuta
correr_servidor.py, que está en la carpeta de arriba.
"""
