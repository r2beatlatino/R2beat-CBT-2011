# Empezar de cero

Esta guía es para alguien que **nunca programó**. No hay que saber nada: se copian
y pegan tres comandos. Toma unos 15 minutos.

Al final vas a tener el juego andando contra tu propia computadora.

---

## Lo que necesitás tener antes

**1. El juego instalado.** Este repositorio no lo trae (no es nuestro). Necesitás
una copia del cliente de R2Beat: la carpeta donde está `R2Beat.exe`. Si no la
tenés, el instalador del CBT 2011-03-21 está preservado en Internet Archive:
[Softnyx R2Beat CBT 20110321](https://archive.org/details/softnyx-r-2-beat-cbt-20110321-install-version).

**2. Python.** Es el programa que hace funcionar el servidor. Bajalo de
[python.org/downloads](https://www.python.org/downloads/) y instalalo.

> ⚠ En Windows, cuando el instalador te pregunte, **marcá la casilla
> "Add Python to PATH"**. Si no la marcás, los comandos de abajo no van a andar y
> vas a tener que reinstalar. Es la casilla de abajo de todo, fácil de pasar por alto.

**3. `r2pak.exe`.** Es una herramienta chica que abre los archivos `.pak` del
juego, donde está guardado todo (la tienda, las canciones, la ropa). **Ya viene
incluida** en `herramientas/r2pak/r2pak.exe`. No es nuestra: es de terceros, y
los detalles están en [herramientas/r2pak/LEEME.md](herramientas/r2pak/LEEME.md).

---

## Paso 0 — Abrir una consola en la carpeta correcta

Todos los comandos de esta guía se escriben en la **consola**, esa ventana negra
donde se tipea.

- **Windows:** abrí la carpeta de este proyecto en el Explorador. Hacé clic en la
  barra de dirección de arriba, escribí `cmd` y apretá Enter.
- **Linux o Mac:** abrí una terminal y usá `cd` hasta llegar a esta carpeta.

Para comprobar que Python quedó bien instalado, escribí:

```bash
python --version
```

Tiene que responder algo como `Python 3.12.1`. Si dice que no encuentra el
comando, Python no está instalado o te faltó marcar "Add Python to PATH".

---

## Paso 1 — Sacar los datos del juego

El servidor necesita saber qué hay en la tienda, qué caras y alas existen y cuáles
son las canciones. Todo eso está adentro del juego que ya tenés. Este comando lo
saca de ahí:

```bash
python herramientas/extraer_datos.py "C:\Juegos\R2Beat" "herramientas\r2pak\r2pak.exe"
```

Cambiá sólo la primera ruta por la tuya: la carpeta donde está `R2Beat.exe`.
La segunda es la herramienta que ya viene en este repositorio. **Las comillas
hacen falta** si la ruta del juego tiene espacios.

Si salió bien vas a ver algo así:

```
  [ok] face.txt -> datos/face.txt (4838 bytes)
  [ok] wing.txt -> datos/wing.txt (1514 bytes)
  [ok] shop_lt.txt -> datos/shop_lt.txt (41948 bytes)
  [ok] music_lt.txt -> datos/canciones.txt (64 canciones)
```

---

## Paso 2 — Preparar el juego

Hay que hacerle dos cambios al juego. **Los dos son obligatorios**, y los hace
una sola herramienta: son 12 bytes en total. Si alguno de los dos no se puede
aplicar, la herramienta no toca nada y te avisa, en vez de dejarte el cliente a
medio preparar.

**Primero averiguá la dirección de tu computadora en tu red.** En una consola:

- Windows: escribí `ipconfig` y buscá "Dirección IPv4". Es algo como `192.168.1.50`.
- Linux o Mac: escribí `ip addr` o `ifconfig`.

Después:

```bash
python herramientas/parchar_cliente.py "C:\Juegos\R2Beat" 192.168.1.50
```

Eso hace dos cosas:

1. **Le cambia la dirección del servidor.** El juego trae grabada adentro la de
   los servidores viejos de Softnyx, que ya no existen.
2. **Le pone color de 32 bits en vez de 16.** El juego es de 2011 y pide 16 bits,
   que Windows moderno ya no maneja de verdad: lo emula, y esa emulación es
   lentísima. No parece un problema de color — parece que "el juego va lento
   porque es viejo". No es eso. Con este cambio pasa a pedir lo que la placa de
   video hace de verdad: **111 FPS estables y 17% de un núcleo**, contra 52-54%
   cuando emulaba.

   No es un "extra para que ande más rápido": sin esto el juego depende del modo
   de compatibilidad "Windows XP SP3", que a su vez pide ejecutar como
   administrador, esconde Vulkan (adiós DXVK) y encima mete el lag que venía a
   evitar. Con los 2 bytes puestos, nada de eso hace falta.

> ⚠ **No pongas `127.0.0.1`**, aunque el servidor vaya a correr en la misma
> computadora que el juego. El cliente le pasa esa dirección a otras partes del
> juego y se pierde. Usá siempre la IP de tu red local.

La herramienta deja una copia intacta (`R2Beat.exe.original`) antes de tocar nada.
Para ver cómo está, o para volver atrás:

```bash
python herramientas/parchar_cliente.py "C:\Juegos\R2Beat" --ver
python herramientas/parchar_cliente.py "C:\Juegos\R2Beat" --restaurar
```

---

## Paso 3 — Prender el servidor

```bash
python correr_servidor.py
```

Vas a ver algo así:

```
[servidor] login  0.0.0.0:48004
[servidor] lobby  0.0.0.0:[48005, 48015, 48025, 48035]
[servidor] listo. Ctrl+C para cortar.
```

**Dejá esa ventana abierta.** Mientras esté abierta, el servidor está prendido.
Para apagarlo, apretá `Ctrl+C` ahí adentro.

---

## Paso 4 — Entrar al juego

En **otra** consola (dejá la del servidor abierta):

```bash
python jugar.py
```

> ⚠ **No abras `R2Beat.exe` directamente.** No va a funcionar, y no es un error
> tuyo: el juego no tiene pantalla de login propia. Espera que alguien lo abra ya
> autenticado, pasándole a qué servidor ir. Eso es lo que hace `jugar.py`.

La primera vez te pide la dirección de tu servidor y dónde está instalado el
juego. Las guarda y no vuelve a preguntar.

Ya hay tres cuentas de prueba creadas para que puedas entrar sin registrarte:

| usuario | contraseña | para qué sirve |
|---|---|---|
| `estrella` | `1234a` | nivel bajo — canal Estrella |
| `luna` | `1234a` | nivel medio — canal Luna |
| `sol` | `1234a` | nivel alto — canal Sol |

> El usuario es `estrella`, en minúscula. Adentro del juego el personaje se llama
> `EstrellaUser`, que es otra cosa: el apodo. Para entrar va el usuario.

Son tres porque en R2Beat el canal al que podés entrar depende de tu nivel. Con
estas tres probás los tres canales sin tener que jugar setenta niveles primero.

> ⚠ Estas cuentas tienen una contraseña que está publicada acá. Sirven para probar
> en tu casa. Si algún día abrís tu servidor a otra gente, **borralas**.

---

## Si algo no anda

**El juego abre pero se queda cargando para siempre.**
No llega al servidor. Revisá tres cosas, en este orden:

1. ¿La ventana del servidor sigue abierta?
2. ¿Le pusiste la IP correcta en el paso 2? Volvé a correr `ipconfig`: en muchas
   redes la IP cambia sola cada tantos días.
3. El firewall de Windows. La primera vez suele aparecer un cartel preguntando si
   permitís que Python use la red: hay que decir que sí. Si le diste que no, buscá
   "Firewall de Windows Defender" → "Permitir una aplicación" y habilitá Python.

**"python no se reconoce como un comando".**
Python no está instalado, o te faltó marcar "Add Python to PATH". Reinstalalo
marcando esa casilla.

**El servidor arranca pero dice `[shop] no pude leer ...shop_lt.txt`.**
Te falta el paso 1. El servidor igual arranca, pero la tienda va a estar vacía.

**"Address already in use" o "solo se permite un uso de cada dirección".**
Ya hay un servidor corriendo. Cerrá la otra ventana, o reiniciá la computadora si
no la encontrás.

**Quiero que entren amigos por internet, no sólo por mi red.**
Eso ya es otra cosa: hay que abrir los cinco puertos (48004, 48005, 48015, 48025 y
48035) en tu router, y tu IP de internet suele cambiar sola. Para eso conviene un
servidor alquilado: ver [docs/INSTALAR-LINUX.md](docs/INSTALAR-LINUX.md).

---

## Y ahora qué

- **Agregar canciones nuevas:** [herramientas/importador/LEEME.md](herramientas/importador/LEEME.md)
- **Entender cómo funciona por dentro:** [docs/COMO-FUNCIONA.md](docs/COMO-FUNCIONA.md)
- **Dejarlo prendido las 24 horas:** [docs/INSTALAR-LINUX.md](docs/INSTALAR-LINUX.md)
