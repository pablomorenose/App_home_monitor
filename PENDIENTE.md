# Funcionalidades pendientes de implementar

## 1. Tarjetas de monitorización del sistema

Añadir dos tarjetas nuevas en el dashboard principal:

### Tarjeta "Raspberry Pi" (sistema completo)
- CPU % total
- RAM total / usada / libre
- Temperatura (vcgencmd measure_temp)
- Disco usado / libre
- Uptime

### Tarjeta "Docker / Home Monitor" (solo el contenedor)
- CPU % que usa el contenedor
- RAM que consume
- Trafico de red entrante/saliente
- Estado (running / stopped)

### Implementación sugerida
- Nuevo endpoint en Flask: `GET /api/pi-stats`
  - Lee `/proc/meminfo` para RAM
  - Lee `vcgencmd measure_temp` para temperatura
  - Lee `/proc/stat` o `psutil` para CPU
  - Ejecuta `docker stats home-monitor --no-stream` para datos del contenedor
- Nueva tarjeta en `templates/index.html` con el mismo estilo visual que el resto
- Refresco cada 15-30 segundos junto con el resto de checks

### Notas
- Bajo consumo de recursos, lectura de archivos del sistema
- Mantener el diseño visual actual (cristal, iconos nuevos)
- La app corre en Docker en una Raspberry Pi 4 con Debian Bookworm arm64
