# El protocolo

Cómo se hablan el cliente y el servidor. Esto es para programar; si sólo querés
entender el juego, [COMO-FUNCIONA.md](COMO-FUNCIONA.md) alcanza.

---

## La forma de un mensaje

Cada mensaje es:

```
   ┌──────────────┬──────────┬─────────────────────┐
   │  largo (4B)  │ cmd (1B) │  contenido          │
   └──────────────┴──────────┴─────────────────────┘
```

El comando es un byte. Por costumbre se escriben como `FFxx`, donde `xx` es ese
byte: el chat es el comando `0x08` y todo el mundo le dice `FF08`.

Una conexión puede traer varios mensajes pegados en el mismo paquete de red, o
medio mensaje que se completa en el siguiente. `login.py` y `lobby.py` acumulan lo
que llega y van cortando por el campo de largo.

## El sobre cifrado `FF55`

Muchos mensajes importantes no viajan sueltos: van **adentro** de un `FF55`, que
es un sobre cifrado. Adentro hay otro mensaje con la misma forma (comando +
contenido).

El cifrado es un XOR con una clave derivada de la contraseña. No es seguridad
seria y no pretende serlo: es lo que hacía el juego original y hay que replicarlo
tal cual, porque el cliente no entiende otra cosa. Está en `cifrado.py`, son diez
líneas.

> ⚠ Hay un contador que avanza con cada mensaje cifrado y es **compartido entre
> destinatarios**. Cuando el servidor le manda lo mismo a varios jugadores, tiene
> que cifrarlo **una vez por cada uno**, no una vez y repartir la copia.

## Texto: `cp1252`, no UTF-8

El cliente de esta versión decodifica el texto con `cp1252`. Si le mandás UTF-8,
los acentos y las eñes salen rotos. La conversión está centralizada en
`enc_text()` (`cifrado.py`); usala siempre en vez de llamar a `.encode()` a mano.

Los nombres largos son peligrosos: en algunas versiones del cliente, un apodo de
16 caracteres o más **cierra el juego**.

## El largo importa más de lo que parece

El cliente reserva un espacio fijo para ciertas estructuras. Si mandás un mensaje
**más corto** de lo que espera, no lo rellena con ceros: deja lo que había antes en
esa zona de memoria. El síntoma son jugadores fantasma, nombres de basura o
adornos que aparecen solos.

O sea: si algo se ve raro y no hay ningún error, sospechá primero del largo.

---

## Los comandos

Tabla generada del código: los nombres salen de `servidor/pktlog.py` y la columna
"lo atiende" de qué comandos tocan realmente `login.py` y `lobby.py`.

Un comando sin nadie que lo atienda quiere decir que el cliente lo puede mandar
pero el servidor todavía lo ignora. Casi siempre es una función sin implementar.

| Comando | Nombre | Lo atiende |
|---|---|---|
| `FF00` | *(sin nombre)* | lobby |
| `FF01` | CHECK_VER | login, lobby |
| `FF02` | LOGIN | login, lobby |
| `FF03` | MYRECORD | lobby |
| `FF04` | CREATE_CHAR | lobby |
| `FF06` | JOINCHANNEL | lobby |
| `FF08` | CHAT | lobby |
| `FF09` | WHISPER | — |
| `FF0A` | ROOMLIST | — |
| `FF0C` | ROOM_USERLIST | lobby |
| `FF0E` | REMOVEUSER | — |
| `FF10` | CREATEROOM | lobby |
| `FF11` | JOINROOM | lobby |
| `FF12` | EXITROOM | lobby |
| `FF13` | ROOMINFO | lobby |
| `FF17` | TOGGLEREADY | lobby |
| `FF18` | *(sin nombre)* | lobby |
| `FF19` | EDITCHAR | — |
| `FF1A` | EDIT_AVATAR | lobby |
| `FF1B` | EDIT_STAGE | lobby |
| `FF1C` | EDIT_MUSIC | lobby |
| `FF20` | KICK | lobby |
| `FF23` | MOVECHAR | lobby |
| `FF24` | STARTGAME | lobby |
| `FF25` | DO_LOADGAME | lobby |
| `FF26` | GAMEDATA_P1 | — |
| `FF28` | GAMEOVER | lobby |
| `FF2A` | BUY_DRESS | lobby |
| `FF35` | ANNOUNCE | — |
| `FF37` | *(sin nombre)* | lobby |
| `FF3A` | SINGLEPLAY | lobby |
| `FF3D` | DISCONNECT | lobby |
| `FF40` | SESSION_FORWARD | lobby |
| `FF41` | GET_ROOMINFO | — |
| `FF44` | GET_ITEM_ENDDATE | — |
| `FF45` | COUPLE | lobby |
| `FF49` | *(sin nombre)* | lobby |
| `FF4B` | CHANGE_ROOM_MODE | lobby |
| `FF4C` | ANTIBOT | — |
| `FF4E` | GAMEDATA | lobby |
| `FF50` | BUY_CASH_ITEM | — |
| `FF51` | CASH_AMOUNT | lobby |
| `FF54` | CLIENT_NOTIFY54 | lobby |
| `FF55` | ENC_FF55 | lobby |
| `FF56` | HDR_FF56 | — |
| `FF57` | GET_NOTE_DATA | lobby |
| `FF59` | CLIENT_TELEMETRY | — |
| `FF61` | CHATROOM_ENTER | — |
| `FF63` | CMD_63 | lobby |
| `FF65` | CHATROOM_LIST | — |
| `FF6E` | ROOMLIST2 | lobby |
| `FF77` | USERINFO | lobby |
| `FF78` | *(sin nombre)* | lobby |
| `FF79` | SELECTINGMUSICSTAGE | lobby |
| `FF81` | CMD_81 | lobby |
| `FF87` | *(sin nombre)* | lobby |
| `FF8C` | ANIMTIME_SYNC | lobby |
| `FF97` | SERVER_CLOCK | lobby |
| `FF9B` | *(sin nombre)* | lobby |
| `FF9E` | *(sin nombre)* | lobby |
| `FFBD` | *(sin nombre)* | lobby |
| `FFC8` | *(sin nombre)* | lobby |
| `FFE2` | *(sin nombre)* | lobby |
| `FF107` | POS_UPDATE | lobby |
| `FF13B` | MUSIC_ENABLE | — |


---

## Para investigar el protocolo vos mismo

El servidor imprime **cada** mensaje que entra y sale, con la dirección, el nombre
del comando, el largo y un preview en hexadecimal. Lo que venía dentro de un sobre
`FF55` aparece indentado abajo. Eso solo alcanza para entender casi cualquier cosa.

Ademas queda una captura estructurada en `paquetes/*.jsonl`, una linea por mensaje,
comoda para procesar despues.

Para bajarle el ruido, en `registro.py`:

- `LOG_HEXBYTES` — cuantos bytes del contenido mostrar (0 = ninguno)
- `LOG_COLOR` — colores en la consola

Un consejo que ahorra dias: **preguntale al juego antes de reversar el binario**.
Mandale algo y mira que hace. El cliente es un oraculo muy rapido, y desensamblar
lleva mucho mas tiempo que probar.
