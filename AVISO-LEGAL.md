# Aviso legal

Vale la pena ser claro sobre qué es de quién, porque la diferencia importa.

## Qué es nuestro

El **servidor**: el programa que responde del otro lado cuando el juego se
conecta. Está en `servidor/`, junto con las herramientas de `herramientas/` y toda
la documentación.

Se escribió desde cero. El método fue mirar qué mensajes manda el cliente y qué
espera recibir, y escribir un programa que conteste eso. **No hay código de
Softnyx adentro**, ni descompilado ni copiado.

Eso es lo único que licenciamos, y va bajo [AGPL-3.0](LICENSE).

## Qué no es nuestro

Todo el juego:

- el cliente y sus ejecutables (`R2Beat.exe`, las `.dll`)
- los archivos de contenido (`.pak`, `.xfs`)
- la música, los gráficos, los modelos, las animaciones
- los charts, o sea las notas de cada canción

Nada de eso está en este repositorio, ni lo va a estar. Pertenece a
**Softnyx Co., Ltd.** y a **VALOFE Co., Ltd.**

Por eso `datos/` viene vacío, y por eso `herramientas/extraer_datos.py` saca las
tablas **de tu propia copia del juego** en vez de bajarlas de acá. Nadie te está
dando nada que no tuvieras ya.

## Quién es el dueño hoy

R2Beat lo hizo **Softnyx**. **VALOFE** lo revivió en 2021 y cerró el servicio
global el **23 de abril de 2026**.

Pero la marca **sigue teniendo dueño activo**: *R2Beat: 音速觉醒* (2022) continúa
online en China, Hong Kong, Corea y Taiwán. Lo que quedó abandonado es esta
versión puntual —la de 2011/2012/2013—, no la propiedad intelectual.

O sea: esto es un proyecto de fans sobre un juego abandonado, no sobre un juego
sin dueño. Si VALOFE pidiera bajarlo, tendría derecho a hacerlo.

## Este proyecto no cobra

Nunca. Ni ítems, ni VIP, ni ventajas pagas, ni "apoyos" que den algo a cambio.

No es una postura estética. Cobrar por revivir el juego de otro es exactamente lo
que convierte un proyecto de fans en algo indefendible, y es lo que hace que a los
proyectos de fans en general los cierren. Si algún día el proyecto no se puede
sostener, se libera el código y se apaga; no se monetiza.

Si levantás tu propio servidor con este código, hacé lo que te parezca: la AGPL te
lo permite, incluso cobrar. Pero entonces tus jugadores tienen derecho a tu código.

## Si sos de Softnyx o VALOFE

Abrí un issue en el repositorio y lo hablamos. No hay ninguna intención de competir
con nada de ustedes: la versión que revive este código lleva años fuera de servicio
y no se parece a la que ustedes operan hoy.
