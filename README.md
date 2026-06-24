# Home Monitor

Panel de estado para Home Assistant, cámaras IP y NAS Synology.
Comprueba periódicamente cada dispositivo y muestra un LED verde/rojo, junto
con el tiempo que lleva en ese estado ("caído desde hace 2h 15min").

Diseñado para correr en una Raspberry Pi 4, dentro de tu red local.

## 1. Instalación

Copia esta carpeta a tu Raspberry Pi (por ejemplo a `/home/pi/home-monitor`)
y desde dentro de la carpeta:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2. Configuración

**Importante sobre esta instalación:** la Raspberry está en la oficina, y
Home Assistant, el NAS y las cámaras están en tu casa, en una red distinta.
Por eso `config.py` usa URLs públicas (DDNS, QuickConnect) en vez de IPs
locales — la Raspberry necesita salir a internet para llegar a casa, igual
que harías tú desde el móvil.

Abre `config.py` y rellena:

### Home Assistant

```python
HOME_ASSISTANT_URL = "https://tu-ddns-de-casa.duckdns.org"
HOME_ASSISTANT_TOKEN = "PEGA_AQUI_TU_TOKEN_LARGO"
```

- `HOME_ASSISTANT_URL`: la URL pública por la que ya accedes a HA desde
  fuera de casa (el DDNS + puerto que tienes abierto en el router de casa).
- `HOME_ASSISTANT_TOKEN`: un token de acceso de larga duración. Se genera
  así:
  1. Entra en Home Assistant con tu navegador
  2. Click en tu perfil (abajo a la izquierda, tu nombre/avatar)
  3. Pestaña **Seguridad** → sección **Tokens de acceso de larga duración**
  4. **Crear token**, ponle un nombre (ej. "monitor-oficina") y copia el
     token completo — solo se muestra una vez

Este mismo token es el que se usa también para comprobar las cámaras
(siguiente apartado), así que solo hace falta generarlo una vez.

### NAS Synology

```python
"url": "https://tu-id.quickconnect.to",
```

Pon aquí tu URL real de QuickConnect. Ten en cuenta que QuickConnect pasa
por los servidores de Synology como intermediario, así que esta
comprobación depende tanto de tu NAS como de que el servicio de
QuickConnect esté operativo — es la mejor aproximación posible sin abrir tú
mismo un puerto en el router de casa hacia el NAS (lo cual no se
recomienda, ya que la interfaz DSM es un objetivo común de ataques).

### Cámaras

Como tus cámaras están conectadas directamente a Home Assistant (no tienen
IP propia accesible desde fuera), se comprueban consultando su estado
dentro de Home Assistant, usando el mismo token de arriba:

```python
{
    "id": "camara_entrada",
    "name": "Cámara Entrada",
    "type": "ha_entity",
    "entity_id": "camera.entrada",   # <-- el entity_id real en tu HA
    "timeout": 8,
},
```

Para encontrar el `entity_id` exacto de cada cámara en tu Home Assistant:

- Ajustes → Dispositivos y servicios → Entidades → busca "cámara" o el
  nombre que le hayas puesto, o
- Herramientas de desarrollador → Estados → filtra por `camera.`

Copia el texto exacto que empieza por `camera.` (por ejemplo
`camera.entrada_principal`) y pégalo en `entity_id`.

Una cámara se considera "caída" cuando Home Assistant reporta su estado
como `unavailable` o `unknown` — es decir, cuando HA ha perdido la
conexión con ella, que es la misma información que verías tú en el panel
de HA.

## 3. Probarlo manualmente

```bash
source venv/bin/activate
python3 app.py
```

Abre en el navegador (desde cualquier dispositivo de tu red):

```
http://IP_DE_TU_RASPBERRY:8088
```

Si todo va bien, deberías ver los LEDs en verde para los dispositivos que
están encendidos y accesibles en tu red.

## 4. Arranque automático al encender la Raspberry Pi (systemd)

Para que el monitor se inicie solo cada vez que arranque la Raspberry y se
reinicie si falla, crea un servicio de systemd.

Crea el archivo `/etc/systemd/system/home-monitor.service` con este
contenido (ajusta las rutas y el usuario si no usas `pi`):

```ini
[Unit]
Description=Home Monitor Dashboard
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/home-monitor
ExecStart=/home/pi/home-monitor/venv/bin/python3 app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Luego activa y arranca el servicio:

```bash
sudo systemctl daemon-reload
sudo systemctl enable home-monitor
sudo systemctl start home-monitor
```

Comandos útiles:

```bash
sudo systemctl status home-monitor   # ver si está corriendo
sudo systemctl restart home-monitor  # reiniciarlo
journalctl -u home-monitor -f        # ver logs en vivo
```

## 5. Acceder desde fuera de casa/oficina (DDNS + HTTPS + contraseña)

Si quieres ver el panel desde el móvil estando fuera de la red donde está
la Raspberry (por ejemplo, usando tu DDNS `antediluvian.tplinkdns.com`,
igual que ya haces con Home Assistant), sigue la guía completa en
[`deploy/README_DEPLOY.md`](deploy/README_DEPLOY.md).

Esa guía monta Nginx delante de Flask para añadir:

- **HTTPS real** con certificado de Let's Encrypt (gratis, se renueva solo)
- **Usuario y contraseña**, ya que el dashboard no trae login de por sí

Importante: `app.py` está configurado para escuchar solo en
`127.0.0.1` (no en `0.0.0.0`), precisamente para que **no se pueda acceder
directamente a Flask desde fuera de la Raspberry** — todo el tráfico
externo tiene que pasar por Nginx, con su HTTPS y su contraseña. Si en algún
momento quieres volver a probar el dashboard en tu red local sin pasar por
Nginx, puedes cambiar temporalmente esa línea a `0.0.0.0`, pero no lo dejes
así si vas a exponer el puerto a internet.

### Alternativa sin tocar el router: Tailscale

Si más adelante prefieres no depender de abrir puertos en el router de la
oficina, [Tailscale](https://tailscale.com) es una alternativa sin
necesidad de DDNS ni Nginx ni certificados — crea una red privada entre tus
dispositivos. Es más sencilla de mantener, aunque significa instalar una
app adicional en el móvil.

## 6. Estructura del proyecto

```
config.py          → IPs y dispositivos a monitorizar (lo único que sueles tocar)
checks.py           → Lógica de comprobación (http / ping / port)
db.py               → Guarda el histórico en SQLite (monitor.db, se crea solo)
monitor_worker.py   → Bucle en background que comprueba todo cada X segundos
app.py              → Servidor Flask: sirve el dashboard y la API /api/status
templates/index.html → El dashboard visual (LEDs, tiempos, etc.)
```

## 7. Personalización rápida

- **Frecuencia de comprobación**: cambia `CHECK_INTERVAL_SECONDS` en `config.py`.
- **Frecuencia de refresco del navegador**: cambia `REFRESH_MS` en
  `templates/index.html`.
- **Tipos de comprobación disponibles**:
  - `http`: para servicios con interfaz web accesible por URL (Home
    Assistant, QuickConnect del NAS)
  - `ha_entity`: consulta el estado de una entidad dentro de Home Assistant
    vía su API (usado para las cámaras, que no tienen IP propia accesible)
  - `port`: para comprobar que un puerto TCP concreto responde, si algún
    día tienes un dispositivo con IP/puerto propio accesible desde fuera
  - `ping`: ping ICMP normal — ojo, no funciona contra la mayoría de
    servicios detrás de DDNS/proxy, que suelen bloquear ICMP
