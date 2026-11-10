# R2Beat — servidor abierto

Servidor para **R2Beat**, el juego de ritmo de Softnyx (2011), escrito desde cero
en Python. El servicio oficial global cerró el 23 de abril de 2026; esto permite
volver a jugarlo en tu propia computadora o en un servidor.

- **Guía completa:** [LEEME.md](LEEME.md)
- **Paso a paso desde cero:** [EMPEZAR.md](EMPEZAR.md)
- **Cómo funciona por dentro:** [docs/COMO-FUNCIONA.md](docs/COMO-FUNCIONA.md)
- **Protocolo:** [docs/PROTOCOLO.md](docs/PROTOCOLO.md)
- **Aviso legal:** [AVISO-LEGAL.md](AVISO-LEGAL.md)
- **Licencia:** [AGPL-3.0](LICENSE)

## El cliente no está acá

Este repositorio contiene **sólo el servidor**. El cliente, los `.pak`, la
música y los gráficos son de Softnyx/VALOFE y no se distribuyen acá.

Para preservación, el instalador del CBT 2011-03-21 está en Internet Archive:

**[Softnyx R2Beat CBT 20110321 — install version](https://archive.org/details/softnyx-r-2-beat-cbt-20110321-install-version)**

Es un `.exe` de unos 430 MB. Descargalo, instalá el juego y después seguí
[EMPEZAR.md](EMPEZAR.md). Ese archivo no es nuestro: el enlace es externo y
puede dejar de estar disponible.

## Resumen rápido

```bash
python herramientas/extraer_datos.py "C:\ruta\al\juego" "herramientas\r2pak\r2pak.exe"
python herramientas/parchar_cliente.py "C:\ruta\al\juego" 192.168.1.50
python correr_servidor.py
python jugar.py
```

Requiere **Python 3.10 o más nuevo**. El servidor no necesita dependencias
externas: alcanza con lo que trae Python.
