# r2pak.exe

Herramienta de terceros para leer y crear los archivos `.pak` de R2Beat.
El servidor la necesita para sacar las tablas del juego (`face.txt`, `wing.txt`,
`shop_lt.txt`, `canciones.txt`) con `herramientas/extraer_datos.py`.

**No es parte de este proyecto.** Se incluye únicamente porque el servidor no
puede funcionar sin ella, y para que quien clone el repositorio no tenga que
buscarla por su cuenta.

| Dato | Valor |
|---|---|
| Versión | 1.10.0.113 |
| Autor / copyright | Linda Zhang — ZX Inc. (C) 2008–2021 |
| Tamaño | 67.584 bytes |
| SHA-256 | `0BDAF651B3F8BDFF7165E3E0E08D74592806BB3817DED20E7A85B52D0D496C89` |

## Licencia

No se encontró una licencia explícita en la distribución original. **Este
binario no está cubierto por la AGPL-3.0 del resto del repositorio.** Los
derechos son de sus autores. Si el autor pide que se retire, se retira.

## Uso

```bash
# descomprimir un .pak en la carpeta actual
r2pak.exe -d archivo.pak

# crear un .pak a partir de una carpeta
r2pak.exe -c carpeta [-m 1|3] [-l 0|1]
```

- `-m 1` = LZSS (el que usa R2Beat oficial, por defecto)
- `-m 3` = LZSS + XOR
- `-l 0` = más rápido y peor compresión (por defecto)
- `-l 1` = más lento y mejor compresión

`herramientas/extraer_datos.py` ya llama a `r2pak.exe -d` por vos; no hace falta
ejecutarlo a mano salvo que quieras inspeccionar un `.pak`.

## Verificar la copia

```bash
sha256sum herramientas/r2pak/r2pak.exe
```

En Windows:

```powershell
Get-FileHash herramientas\r2pak\r2pak.exe -Algorithm SHA256
```

Debe coincidir con el hash de la tabla de arriba.
