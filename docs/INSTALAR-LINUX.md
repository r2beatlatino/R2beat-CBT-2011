# Dejarlo corriendo en un servidor de verdad

Para que el juego funcione las 24 horas y entre gente desde internet, no desde tu
red de casa. Va sobre Linux (Ubuntu o Debian).

Nada de esto hace falta para jugar entre amigos en la misma casa: para eso alcanza
[EMPEZAR.md](../EMPEZAR.md).

---

## 1. Los cinco puertos

Esto es lo primero que sale mal, así que va primero.

| puerto | para qué |
|---|---|
| 48004 | login |
| 48005 | canal Estrella |
| 48015 | canal Luna |
| 48025 | canal Sol |
| 48035 | canal Libre |

**Hay que abrir los cinco.** Si abrís sólo 48004 y 48005, se puede entrar a
Estrella y a ningún otro canal, sin ningún mensaje de error: el jugador hace clic
y no pasa nada.

```bash
sudo iptables -A INPUT -p tcp -m multiport --dports 48004,48005,48015,48025,48035 -j ACCEPT
```

Si estás en un servicio de nube (Oracle, AWS, Google), **además** hay que abrirlos
en el panel del proveedor. Tienen su propio firewall aparte del de la máquina, y
esa es la segunda causa de "no conecta y no entiendo por qué".

---

## 2. Instalar

```bash
sudo apt update && sudo apt install -y python3 git
sudo useradd -r -m -d /opt/r2beat r2beat
sudo -u r2beat git clone <la-url-de-tu-repo> /opt/r2beat/servidor
```

Las tablas del juego se sacan de un cliente, igual que en Windows. Lo más práctico
es correr `extraer_datos.py` en tu PC y subir la carpeta `datos/` ya armada:

```bash
scp -r datos/ usuario@tu-servidor:/tmp/datos
sudo cp -r /tmp/datos/* /opt/r2beat/servidor/datos/
sudo chown -R r2beat:r2beat /opt/r2beat/servidor/datos
```

---

## 3. Que arranque solo

Creá `/etc/systemd/system/r2beat.service`:

```ini
[Unit]
Description=Servidor de R2Beat
After=network.target

[Service]
Type=simple
User=r2beat
WorkingDirectory=/opt/r2beat/servidor
ExecStart=/usr/bin/python3 correr_servidor.py
Restart=on-failure
RestartSec=5

# La IP publica que el servidor le ANUNCIA a los clientes. Tiene que ser la que
# ellos ven desde afuera. Si no la ponés, se autodetecta, y en un servidor de nube
# la autodetección casi siempre devuelve la IP interna: los jugadores entran al
# login y después no llegan a ningún canal.
Environment=R2_PUBLIC_IP=203.0.113.10

# Apagar el modo desarrollo: saca la sala de prueba y los comandos de debug.
Environment=R2_DEV=0

# Quiénes son administradores, por número de usuario, separados por coma.
Environment=R2_ADMIN_UIDS=1

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now r2beat
sudo systemctl status r2beat
```

---

## 4. Copias de la base de datos

`datos/cuentas.db` es **lo único irreemplazable**. Todo lo demás se vuelve a bajar
o a generar; las cuentas y el progreso de tu gente, no.

SQLite no se copia con `cp` mientras el servidor escribe: te podés llevar un
archivo a medio escribir. Hay que usar el respaldo en caliente que trae:

```bash
sudo -u r2beat python3 - <<'PY'
import sqlite3, time
origen = sqlite3.connect("/opt/r2beat/servidor/datos/cuentas.db")
destino = sqlite3.connect("/opt/r2beat/copias/cuentas-%s.db" % time.strftime("%Y%m%d"))
origen.backup(destino)
destino.close(); origen.close()
PY
```

Ponelo en un cron diario y borrá las copias de más de dos semanas. Es aburrido
hasta el día que lo necesitás.

---

## 5. Las actualizaciones automáticas te van a reiniciar el servidor

Ubuntu instala parches de seguridad solo, y por defecto **reinicia los servicios
afectados**. Cuando actualiza Python o SQLite, se lleva puesto el servidor de juego
en el medio de una partida.

Para que instale los parches pero no reinicie nada:

```bash
echo "\$nrconf{restart} = 'l';" | sudo tee /etc/needrestart/conf.d/zzz-r2beat.conf
echo 'Unattended-Upgrade::Automatic-Reboot "false";' | sudo tee /etc/apt/apt.conf.d/52-r2beat
```

Después reiniciás vos, cuando no haya nadie jugando:

```bash
sudo systemctl restart r2beat
```

---

## 6. El disco se llena

El servidor loguea muchísimo: cada mensaje que entra y sale queda escrito. Es
justo lo que querés mientras estás desarrollando, y es un problema cuando lleva
meses corriendo. Puede ser varios GB por día.

Opciones, de menos a más drástica:

- **Bajar el detalle:** poné `LOG_HEXBYTES = 0` en `servidor/registro.py`.
- **Rotar los logs:** un `logrotate` sobre `logs/` y `paquetes/`.
- **Capar el journal:** `SystemMaxUse=200M` en `/etc/systemd/journald.conf`.

> ⚠ `logs/servidor.log` se abre de nuevo en cada arranque, no en modo agregar. Si
> lo truncás con el servidor prendido, el archivo queda con un agujero y ocupa lo
> mismo. Rotalo cuando el servicio esté parado, o simplemente reiniciá el servicio.

---

## 7. Antes de abrirlo al público

- [ ] **Borrá las cuentas de demo.** Los usuarios `estrella`, `luna` y `sol`
      tienen la contraseña `1234a`, publicada en este repositorio.
- [ ] `R2_DEV=0` en el servicio.
- [ ] Los cinco puertos abiertos, en la máquina **y** en el panel del proveedor.
- [ ] `R2_PUBLIC_IP` puesta a la IP que se ve desde afuera.
- [ ] Copias de la base andando, y probá **restaurar una**. Una copia que nunca
      restauraste no es una copia, es una suposición.
- [ ] Publicá tu código. Si el servidor está online, la AGPL te obliga: tus
      jugadores tienen derecho a tu versión.

---

## Cuando algo no anda

| síntoma | causa casi segura |
|---|---|
| entran al login pero no llegan a ningún canal | `R2_PUBLIC_IP` mal, o falta |
| entran a Estrella y a ningún otro canal | faltan puertos abiertos (48015/25/35) |
| clic en "cambiar de canal" y no pasa nada | el servidor no está cerrando el socket de login — ver [COMO-FUNCIONA.md](COMO-FUNCIONA.md) |
| la tienda aparece vacía | falta `datos/shop_lt.txt` |
| las canciones suenan pero no cae ninguna nota | falta `datos/notas/*.bin` |
| se cortó solo de madrugada | actualizaciones automáticas — ver punto 5 |

Los logs, siempre:

```bash
sudo journalctl -u r2beat -f
tail -f /opt/r2beat/servidor/logs/servidor.log
```
