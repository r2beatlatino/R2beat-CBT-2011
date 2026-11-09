# La carpeta `datos/`

Esta carpeta viene **vacía a propósito**, y se llena sola.

## Por qué está vacía

Acá van las tablas del juego: qué hay en la tienda, qué caras y alas existen, la
lista de canciones y las notas de cada una. Todo eso es de Softnyx, no nuestro, y
no lo podemos repartir. Ver [AVISO-LEGAL.md](../AVISO-LEGAL.md).

Pero no hace falta que te lo demos: **ya lo tenés**, adentro del juego que
instalaste. Sólo hay que sacarlo de ahí.

## Cómo se llena

```bash
python herramientas/extraer_datos.py  "C:\ruta\al\juego"  "C:\ruta\a\r2pak.exe"
```

Eso deja acá:

| archivo | qué es | de dónde sale |
|---|---|---|
| `shop_lt.txt` | catálogo de la tienda: qué se vende y a cuánto | `rnr_script.pak` del cliente |
| `face.txt` | las caras | `rnr_script.pak` |
| `wing.txt` | las alas | `rnr_script.pak` |
| `canciones.txt` | lista del selector, `número\|nombre` por línea | se arma del `music_lt.txt` |
| `notas/*.bin` | dónde cae cada nota de cada canción | ver abajo |

## Las notas son aparte

`extraer_datos.py` **no** genera las notas. Son las que decís vos: un archivo por
canción, con el momento exacto de cada flecha.

Se generan a partir de los charts del cliente con el importador de música:
[herramientas/importador/LEEME.md](../herramientas/importador/LEEME.md).

Sin ellas el juego anda igual y las canciones suenan, pero **no cae ninguna nota**:
patinás sobre una pista vacía.

## Lo que también aparece acá y no se sube a GitHub

| archivo | qué es |
|---|---|
| `cuentas.db` | la base de datos: cuentas, personajes, inventario. **Es lo único irreemplazable de tu servidor.** Hacele copias. |
| `estado.json` | quién está online ahora mismo. Se regenera solo. |

Los dos están en `.gitignore`. `cuentas.db` tiene las contraseñas de tu gente:
no lo subas nunca a ningún lado.
