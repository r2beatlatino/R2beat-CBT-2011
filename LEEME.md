# R2Beat — servidor abierto

Un servidor para **R2Beat**, el juego de patinar y apretar flechas al ritmo de la
música que Softnyx sacó en 2011. El servicio oficial global cerró el 23 de abril
de 2026. Esto permite volver a jugarlo: armás tu propio servidor, apuntás el
cliente a tu computadora y andás.

No hay nada que comprar, ni ítems, ni VIP, ni ventajas pagas. No las hay porque
no las va a haber: es la idea del proyecto, no una promesa de marketing.

```
                 tu PC                              la PC de un amigo
    ┌──────────────────────────┐                ┌──────────────────────┐
    │  correr_servidor.py      │ ◄────────────► │   R2Beat.exe         │
    │  (esto, en Python)       │   red local    │   (el juego de siempre)
    └──────────────────────────┘   o internet   └──────────────────────┘
```

---

## Empezá por acá

Si nunca programaste, no importa: **[EMPEZAR.md](EMPEZAR.md)** es un paso a paso
sin jerga, desde cero hasta jugar. Son unos 15 minutos.

El resumen, para el apurado:

```bash
python herramientas/extraer_datos.py    "C:\ruta\al\juego"  "herramientas\r2pak\r2pak.exe"
python herramientas/parchar_cliente.py  "C:\ruta\al\juego"  192.168.1.50
python correr_servidor.py
python jugar.py                          # en otra consola
```

No hay que instalar librerías: alcanza con **Python 3.10 o más nuevo**.

---

## De dónde bajar el cliente

Este repositorio es **sólo el servidor**. El cliente original no se distribuye
acá: es de Softnyx y de VALOFE. Para preservación, el instalador del CBT
2011-03-21 está alojado en Internet Archive:

**[Softnyx R2Beat CBT 20110321 — install version](https://archive.org/details/softnyx-r-2-beat-cbt-20110321-install-version)**

Es un `.exe` de unos 430 MB. Descargalo, instalá el juego y después seguí con
[EMPEZAR.md](EMPEZAR.md). Ese archivo no es nuestro: el enlace es externo y
puede dejar de estar disponible. Si no anda, buscá "R2Beat CBT 2011" en
archive.org.

---

## Qué hay acá adentro

```
correr_servidor.py      ← esto es lo que se ejecuta

servidor/               el servidor, en piezas chicas. Se leen en este orden:
  ajustes.py              configuración y estado. Empezá por acá.
  registro.py             el log de consola y de archivo
  cifrado.py              el XOR del protocolo y el hash de contraseñas
  progresion.py           experiencia → nivel → licencia → canal
  basedatos.py            cuentas, personajes, amigos, pareja (SQLite)
  catalogo.py             tienda, inventario y ropa puesta
  canciones.py            canciones, notas y premio de cada carrera
  paquetes.py             leer y armar los mensajes del protocolo
  login.py                servidor de login  (puerto 48004)
  lobby.py                servidor de lobby y juego (48005/48015/48025/48035),
                          repartido por tema en:
    lobby_menu.py           personaje, menú principal, cambio de canal
    lobby_salas.py          crear/listar/entrar/salir de salas
    lobby_carrera.py        la carrera y el cartel de resultados
    lobby_social.py         amigos, pareja, susurros, misiones
    lobby_tienda.py         comprar ropa y ponérsela
    lobby_admin.py          comandos de administrador

jugar.py                ← esto abre el juego (no abras R2Beat.exe a mano)

herramientas/
  extraer_datos.py        saca del cliente las tablas que el servidor necesita
  parchar_cliente.py      apunta el cliente a tu servidor + le arregla el lag
  importador/             agregar canciones nuevas al juego

docs/
  COMO-FUNCIONA.md        qué pasa entre que abrís el juego y entrás a una sala
  PROTOCOLO.md            los mensajes entre cliente y servidor (para programar)
  INSTALAR-LINUX.md       dejarlo corriendo solo en un servidor de verdad

datos/                    vacío al clonar. Se llena con extraer_datos.py.
```

Cada módulo depende sólo de los que tiene **arriba** en esa lista, nunca al revés.
Si querés entender el código, leelos en ese orden y no vas a encontrarte nunca con
algo que todavía no viste.

---

## Lo que este repositorio NO trae

**El juego.** Ni el cliente, ni los `.pak`, ni la música, ni los gráficos, ni las
notas de las canciones. Nada de eso es nuestro: es de Softnyx y de VALOFE, y no
tenemos derecho a repartirlo.

Lo que sí es nuestro, y está todo acá, es el **servidor**: el programa que
responde del otro lado. Se escribió desde cero, observando qué manda el cliente y
qué espera recibir. No hay nada de código original de Softnyx adentro.

Para usarlo necesitás tu propia copia del juego. `extraer_datos.py` saca de ahí
las tablas que el servidor necesita: no las bajás de acá, las sacás de lo que ya
tenés. Ver [AVISO-LEGAL.md](AVISO-LEGAL.md).

---

## Estado

Anda y se juega. Funciona: crear cuenta y personaje, el menú, los cuatro canales,
salas, chat, susurros, amigos, pareja, la tienda y el inventario, la ropa, las
misiones del tutorial, las carreras de varios jugadores y el cartel de resultados.

Hay cosas a medio hacer y errores conocidos; están anotados como comentarios en el
código, ahí donde importan.

---

## Licencia

**AGPL-3.0.** En criollo: hacé lo que quieras con esto, pero si levantás un
servidor con él, tus jugadores tienen derecho a ver tu versión del código, con
tus cambios incluidos. No se puede tomar esto, mejorarlo y quedarse con las
mejoras.

Ver [LICENSE](LICENSE).
