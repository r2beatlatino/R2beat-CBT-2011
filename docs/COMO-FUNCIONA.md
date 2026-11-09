# Cómo funciona por dentro

Qué pasa realmente entre que hacés doble clic en el juego y estás patinando en una
sala. Sirve tanto para curiosear como para arreglar algo cuando falla.

---

## El recorrido completo

```
  1. LOGIN          puerto 48004     "¿quién sos?"
        │
        │  el servidor contesta y CIERRA la conexión (esto importa, ver abajo)
        ▼
  2. LOBBY          puerto 48005     el menú, la lista de salas, el chat
     (o 48015 / 48025 / 48035, uno por canal)
        │
        ▼
  3. SALA                            se juntan los jugadores, se elige canción
        │
        ▼
  4. CARRERA                         todos mandan por dónde van, todo el tiempo
        │
        ▼
  5. CARTEL                          resultados, experiencia y monedas
```

---

## 1. El login, y por qué se corta la conexión a propósito

El cliente se conecta al puerto **48004** y manda dos mensajes: la versión que
tiene y sus credenciales. El servidor las revisa contra la base de datos y le
contesta con la lista de canales que existen.

Y después **cierra el socket**. No es un error: es obligatorio.

Adentro del cliente hay una bandera que significa "la sesión con el servidor de
login sigue abierta". Se prende cuando el login sale bien y se apaga cuando el
servidor cierra la conexión. El botón de *cambiar de canal* arranca preguntando por
esa bandera: si está prendida, **no hace absolutamente nada**. Ni un cartel, ni un
sonido, nada.

Así que si el servidor no cierra esa conexión, el jugador entra a un canal y queda
encerrado ahí para siempre, sin ningún mensaje de error. Es de esos errores que
cuestan un día entero de buscar.

En el código: `login.py`, y el ajuste `RELOGIN_CLOSE` en `ajustes.py`.

---

## 2. El lobby: un servidor por canal

En R2Beat cada canal (Estrella, Luna, Sol, Libre) es **un servidor distinto en su
propio puerto**: 48005, 48015, 48025 y 48035.

El programa que atiende los cuatro es el mismo (`lobby.py`). Cuál canal es lo dice
el cliente al entrar, no el puerto. Pero hay que tener **los cuatro puertos
abiertos** en el firewall: si abrís sólo 48004 y 48005, se puede entrar a Estrella
y a ningún otro, y desde afuera parece que el juego está roto.

### Los canales están atados al nivel

No es una decisión de este servidor: viene del juego. Tu experiencia determina tu
nivel, tu nivel determina tu licencia, y tu licencia determina a qué canal te deja
entrar el cliente.

```
   experiencia  ──►  nivel  ──►  licencia  ──►  canal permitido
                     1-30        Estrella       Estrella
                     31-60       Luna           Luna
                     61+         Sol            Sol
```

Toda esa cadena está en `progresion.py`, que es un archivo corto y vale la pena
leerlo entero.

---

## 3. Los mensajes: por qué todo se llama `FFxx`

Cliente y servidor se hablan con mensajes cortos. Cada uno arranca con un número
que dice qué es: `FF08` es un chat, `FF11` es entrar a una sala, `FF2A` es comprar
algo en la tienda.

Hay una vuelta de tuerca: **algunos mensajes vienen adentro de otro**. El `FF55` es
un sobre cifrado, y lo interesante está adentro. El cifrado es un XOR simple; no es
seguridad de verdad, es lo que hacía el juego original, y hay que hacer lo mismo
porque el cliente espera exactamente eso.

Por eso el log del servidor muestra dos niveles: los mensajes de afuera, y
indentados debajo los que venían adentro del sobre.

La lista completa está en [PROTOCOLO.md](PROTOCOLO.md).

---

## 4. La regla que mantiene honesto a este servidor

**Nada se inventa para "engañar" al cliente.**

Si el servidor manda algo que no corresponde, el cliente no muestra un error
prolijo: se cierra de golpe, o peor, sigue andando con datos basura. Eso, que suena
horrible, en realidad es lo que mantiene el proyecto honesto. Cada vez que se
adivinó un formato en vez de averiguarlo, el juego lo dijo enseguida.

De ahí sale la regla: todo lo que se manda sale de la base de datos o de lo que el
cliente realmente pidió. Cuando no se sabía un formato, se lo averiguó mirando el
programa y capturando lo que manda el cliente de verdad. No se rellenó con algo
plausible.

Un ejemplo de lo que pasa cuando no se cumple: el juego reserva un espacio fijo
para cada jugador de la sala. Si le mandás un mensaje más corto que ese espacio, el
cliente **no lo rellena con ceros**: deja lo que había antes en la memoria. En
pantalla aparecen jugadores fantasma con nombres de basura.

---

## 5. La carrera

Durante la partida, cada cliente manda constantemente por dónde va: posición,
velocidad, cuántas notas acertó. El servidor lo reparte a los demás. Nadie ve al
resto directamente; todos ven lo que el servidor les cuenta.

Cuando terminan, el servidor calcula el premio **de su lado**. El cliente manda
`monedas = 0` y el servidor decide cuánto le toca, en base a precisión, combo y
puesto. Si el premio lo calculara el cliente, cualquiera se pondría un millón.

Un detalle propio de esta versión: el juego original traía el premio anotado
canción por canción, pero en esta versión ese campo viene en cero. Así que el
premio se deriva de **cuántas notas tiene la canción**, que es una buena medida de
lo larga y difícil que es. Está en `canciones.py`.

### El cartel de resultados y los que se cuelgan

Al terminar, el servidor espera a que todos reporten cómo les fue, y recién ahí
manda el cartel. Si alguien no contesta antes de que se acabe la paciencia, **se lo
desconecta** y el cartel se arma con los que sí contestaron.

Suena brusco, pero la alternativa es peor: un cliente colgado no cierra su
conexión, así que sin ese límite la sala entera queda esperando para siempre a
alguien que ya no está. Los tiempos se ajustan con `LOAD_TIMEOUT`,
`CARTEL_MARGIN` e `IDLE_TIMEOUT` en `ajustes.py`.

---

## Dónde toca cada cosa

| Si querés cambiar... | Andá a |
|---|---|
| puertos, tiempos de espera, premios | `ajustes.py` |
| el mensaje de bienvenida | `ajustes.py` (`WELCOME_TEXT`) |
| cuánta experiencia da una carrera | `canciones.py` (`race_reward`) |
| en qué nivel se desbloquea cada canal | `progresion.py` |
| precios de la tienda | `datos/shop_lt.txt` (sale del cliente) |
| qué pasa cuando alguien escribe en el chat | `lobby.py`, buscá `0x08` |
| agregar un comando de administrador | `lobby.py`, `apply_admin_command` |
