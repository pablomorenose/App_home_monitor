# Notas para la próxima IA

## Contexto
Esta app corre en una Raspberry Pi 4 (Debian Bookworm arm64) dentro de un contenedor Docker.
El contenedor se arranca con acceso al socket de Docker:
  -v /var/run/docker.sock:/var/run/docker.sock

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
- La RAM del Docker muestra "n/a" hasta que se reinicie la Pi (se han habilitado
  los cgroups de memoria en /boot/firmware/cmdline.txt, necesita reboot para aplicar)

---

## Lo que hay que hacer cuando actualices al diseño nuevo

La versión en fly.io (https://home-monitor-sandra.fly.dev) tiene un diseño nuevo
con aspecto cristal e iconos nuevos que NO está en GitHub todavía.

Cuando el usuario suba esa versión nueva a GitHub, hay que:

1. Hacer git pull para tener la versión nueva
2. Portar el endpoint /api/pi-stats de app.py (está completo en la rama main)
3. Añadir las dos tarjetas en el nuevo index.html con el nuevo diseño visual
   manteniendo la misma funcionalidad: LED, colapsar, arrastrar, barras de progreso
4. El JS necesario está en la función loadPiStats() y toggleSysCard() del index.html actual
5. El contenedor necesita el flag: -v /var/run/docker.sock:/var/run/docker.sock

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
