# Importador de música

Agrega canciones nuevas a R2Beat: el audio, la tapa, la ficha del selector y las
notas que hay que pisar mientras suena.

Son cuatro archivos y ninguno depende de nada raro:

| archivo | qué es |
|---|---|
| `importador.py` | la ventana. Doble clic y listo. Es también la biblioteca que usan los demás. |
| `importar.py` | lo mismo pero por consola, para importar de a muchas. |
| `xml2notes.py` | convierte un chart `.xml` en el `.bin` de notas que lee el servidor. |
| `audio.py` | opcional: grabar el juego y medir si las notas están corridas. |

---

## Antes de empezar

Necesitás tres cosas:

1. **Python 3.10 o más nuevo.**
2. **`r2pak.exe`** — la herramienta que abre y cierra los archivos `.pak` del juego.
   Ya viene en `herramientas/r2pak/r2pak.exe` (es de terceros, no nuestra).
3. **Una copia del cliente de R2Beat instalada**, la que vas a modificar.

Después copiá `config-ejemplo.json` a `config.json` y poné tus rutas:

```json
{
  "input":  "C:\\musica-nueva",
  "client": "C:\\Juegos\\R2Beat",
  "r2pak":  "herramientas\\r2pak\\r2pak.exe",
  "output": "salida"
}
```

`config.json` no se sube a GitHub, porque las rutas son de cada uno.

---

## Qué le tenés que dar

En la carpeta de entrada, mezclado como venga (se busca dentro de todas las
subcarpetas):

| archivo | para qué |
|---|---|
| `*.txt` con `<Music ...>` | la ficha: nombre, artista, género, dificultad |
| `*.xml` con `<TITLE ...>` | el chart, o sea dónde cae cada nota |
| `*.bin` | notas ya convertidas, si alguien te las pasó listas |
| `*.pak` bajo `rnr_music/` | el audio |
| `*.bmp` | la tapa, de 256×128 |

---

## El camino normal

```bash
python importar.py escanear                 # 1. ver qué detectó y qué problemas hay
python importar.py importar --solo 1275     # 2. meterla en el CLIENTE
python importar.py aplicar                  # 3. meterla en el SERVIDOR
```

Y después reiniciar el servidor. Eso no lo hace nadie por vos, a propósito: es la
única parte que corta la partida de quien esté jugando.

Con la ventana es lo mismo: **Escanear**, marcar las que querés, **Aplicar**.

El paso 2 modifica el cliente, pero antes deja una copia `.bak` de cada `.pak`
que toca. El paso 3 es el único que toca el servidor.

---

## Las trampas

Esto es lo que hizo perder horas. Vale la pena leerlo antes, no después.

### El audio se resuelve por la ruta ADENTRO del `.pak`, no por el nombre

`Down The Stairs.pak` contiene adentro `Down The Stairs/mr.mp3`. El juego busca
por esa ruta interna. **Renombrar un `.pak` para reusar el audio de otra canción
no funciona**: la canción entra y queda muda, sin ningún error.

Si elegís un `.pak` cuya carpeta interna no coincide, el importador te ofrece
re-empaquetarlo con el nombre correcto. Aceptá.

### `SIN AUDIO xxx` no se ignora

Cada nota lleva un índice que apunta a la lista `<EFFECT>` del chart. Si falta el
`.pak` de *esa* carpeta, **la música suena pero las notas quedan mudas**, otra vez
sin ningún error visible. Por eso una fila con `SIN AUDIO` no entra a la tanda
hasta que la resuelvas. Tres formas:

1. Configurar `audio_extra` apuntando al `rnr_music` del cliente de donde sacaste
   el material: de ahí salen los bancos compartidos que el material suelto no trae
   (el típico es `zsound.pak`).
2. Elegir a mano el `.pak` de esa carpeta.
3. Copiar el `.pak` que falta a la carpeta de entrada y volver a escanear.

### Sin `preview_map.txt` la canción no suena en el selector

Es el mapa que dice qué mp3 corresponde a cada Index cuando recorrés la lista.
El importador lo regenera solo en cada Aplicar. Si alguna vez copiás archivos a
mano, acordate de esto: la canción entra, se puede jugar, pero al pasar por
encima en el selector no suena nada.

### El Combo no es la cantidad de notas

El juego lo calcula con su propia fórmula. Si le ponés el número crudo de notas,
el juego tira un cartel de error de combo. El importador ya lo calcula bien; la
opción `--combo-crudo` existe **solo** para reproducir el comportamiento viejo y
roto a propósito, cuando estás investigando.

### `canciones_nuevas.txt` se pisa en cada Aplicar

Si hacés varias corridas antes de pasar al servidor, junta las líneas de todas o
vas a perder las anteriores.

### Género mal puesto en el material de origen

Es frecuente en los charts de algunos compositores. Se corrige editando el campo
`Genre` de la fila antes de aplicar; no hace falta re-importar.

---

## Reediciones: versiones alternativas de una canción que ya existe

Una reedición reusa el nombre interno original (`e_song4230`, `n_song4935`...),
que ya está ocupado. Importada tal cual, **pisaría el chart y el audio de la
canción vieja**.

El comando `reedicion` le inventa un nombre único antes de importar:

```
e_song4230  (Index nuevo 5030)  ->  e_reed5030
```

Conserva el prefijo `e_` / `n_` / `h_` porque de ahí sale la banda
(estrella / luna / sol).

```bash
python importar.py reedicion --entrada C:\musica\reediciones --base 5000
# mira el mapeo. Si está bien:
python importar.py reedicion --entrada C:\musica\reediciones --base 5000 --aplicar
```

Sin `--aplicar` no toca nada: solo te muestra qué haría y te avisa si algo choca.

---

## Cuando una canción "se siente corrida"

Las notas no caen donde suena el golpe. Para medirlo sin oído:

```bash
python audio.py desfase e_song1275:1275 e_song1606:1606
```

Compara dónde dice el chart que va cada nota contra dónde están los golpes reales
en el audio, y te da los milisegundos de diferencia.

**El número solo no dice nada.** Lo que sirve es comparar una canción que suena
bien contra la que suena mal: la *diferencia* entre los dos es la corrección.
Si la columna de confianza da menos de 2x, la medición no es concluyente (audio
muy parejo, poca percusión, o el `.bin` no corresponde a ese audio).

Para grabar lo que sale de los parlantes y tener la prueba:

```bash
python audio.py grabar
```

Necesitan `pip install numpy soundcard` y `ffmpeg` instalado.

---

## Re-masterizar el audio de una canción

Cuando el `mr.mp3` original está saturado y querés reemplazarlo por un FLAC limpio.
Es un trabajo delicado; lo que hay que saber:

- **El `mr` del juego casi nunca es la pista completa.** Suele ser una edición con
  cortes internos. Si le encajás el FLAC entero de punta a punta, se desincroniza
  a la mitad.
- Hay que mapear los tramos del `mr` contra el FLAC y encontrar dónde están los
  cortes.
- **Alineá cada corte al compás.** Eso es lo que hace que el empalme sea inaudible;
  un corte duro en cualquier lado se escucha siempre.
- Empalmá con un *crossfade* de potencia constante, sin desplazar la segunda parte.
- **Verificá la sincronía por envolvente de energía, no mirando la forma de onda.**
  A ojo dos pistas parecen iguales y están corridas 40 ms.
- Reconstruí el `.pak` con el **juego cerrado**: si está abierto, el archivo queda
  bloqueado y el reemplazo falla a medias.

---

## Volumen

No lo calibres canción por canción. Se termina con un catálogo donde cada tema
suena distinto y es peor que el problema original.
