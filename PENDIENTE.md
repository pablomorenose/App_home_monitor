# Notas para la próxima IA

## Infraestructura actual — YA NO usamos fly.io

La app se ha migrado completamente a una Raspberry Pi 4 local.
fly.io queda abandonado, no hay que tocarlo ni desplegarlo ahí.

### Acceso a la app
- Red local:     http://192.168.1.119:8088
- Internet:      https://raspberrypi.tail08d292.ts.net  (Tailscale Funnel, gratuito, HTTPS automático)

### La Raspberry Pi
- Hostname:      raspberrypi
- Usuario:       admin
- OS:            Debian Bookworm arm64
- IP local:      192.168.1.119
- Tailscale IP:  100.112.43.5

### Docker
- La app corre en un contenedor llamado "home-monitor"
- Puerto: 8088
- El contenedor tiene acceso al socket de Docker para monitorización:
    -v /var/run/docker.sock:/var/run/docker.sock
- Tiene --restart unless-stopped (arranca solo al reiniciar la Pi)
- Las variables de entorno están en /home/admin/Home_Monitor/.env (NO está en GitHub)

### Auto-update
- Hay un cron cada 5 minutos que hace git pull y reconstruye la imagen si hay cambios
- Script: /home/admin/Home_Monitor/auto-update.sh
- Log: /home/admin/home-monitor-update.log
- Por tanto: cuando hagas git push, en menos de 5 minutos la Pi se actualiza sola

### Variables de entorno necesarias (.env)
- DATABASE_URL       — Supabase PostgreSQL (pooler)
- DB_HOST            — aws-1-eu-central-1.pooler.supabase.com
- DB_PORT            — 6543
- DB_NAME            — postgres
- DB_USER            — postgres.fjyckjfrbpsneyhkrvsj
- DB_PASSWORD        — (en el .env de la Pi)
- HOME_ASSISTANT_URL — https://antediluvian.tplinkdns.com
- HOME_ASSISTANT_TOKEN — (token largo de Home Assistant)
- CHECK_INTERVAL_SECONDS — 15
- VAPID_PRIVATE_KEY  — (generada en la Pi)
- VAPID_PUBLIC_KEY   — (generada en la Pi)
- VAPID_CLAIMS_EMAIL — admin@home-monitor.local

---

## Lo que ya está implementado en esta versión (rama main)

### Tarjeta "Raspberry Pi"
- Muestra CPU%, RAM%, temperatura, disco y uptime en tiempo real
- Se refresca cada 15 segundos
- Mismo aspecto visual que el resto de tarjetas: LED verde, se puede colapsar y arrastrar
- Los datos vienen del endpoint GET /api/pi-stats (en app.py)

### Tarjeta "Docker / Home Monitor"
- Muestra estado del contenedor (running/stopped), CPU%, RAM y tráfico de red
- LED rojo si el contenedor está caído
- Mismo aspecto visual que el resto de tarjetas: LED, colapsar, arrastrar
- Los datos también vienen de /api/pi-stats, sección "docker"
- La RAM del Docker muestra "n/a" hasta que se reinicie la Pi (cgroups de memoria
  habilitados en /boot/firmware/cmdline.txt, necesita reboot para aplicar)

---

## Lo que hay que hacer cuando actualices al diseño nuevo

La versión con diseño nuevo (aspecto cristal, iconos nuevos) NO está en GitHub todavía.
El usuario la subirá desde su otro ordenador.

Cuando llegue ese push, hay que:
1. Portar el endpoint /api/pi-stats de app.py (está completo en rama main)
2. Añadir las dos tarjetas en el nuevo index.html con el nuevo diseño visual
   manteniendo: LED, colapsar, arrastrar, barras de progreso
3. El JS necesario está en loadPiStats() y toggleSysCard() del index.html actual
4. Asegurarse de que el contenedor sigue arrancando con:
   -v /var/run/docker.sock:/var/run/docker.sock

---

## Estructura del endpoint /api/pi-stats

Devuelve:
{
  "cpu_pct": 15.7,
  "ram_total_mb": 3795,
  "ram_used_mb": 2150,
  "ram_avail_mb": 1645,
  "ram_pct": 56.8,
  "temp_c": 51.1,
  "disk_total_gb": 117.5,
  "disk_used_gb": 27.8,
  "disk_free_gb": 83.7,
  "disk_pct": 23.7,
  "uptime": "6d 8h 18min",
  "docker": {
    "status": "running",
    "cpu": "0.5%",
    "mem": "25.3MB",
    "net": "↓243.6kB ↑129.8kB"
  }
}
