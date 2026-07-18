# Home Monitor v2.0.0 — Estado actual y pendientes

## ✅ Implementado en v2.0.0

### Phase 1 — Seguridad
- [x] Protección CSRF en todas las mutaciones
- [x] Security headers (CSP, HSTS, X-Frame-Options, etc.)
- [x] Sesiones seguras (HttpOnly, SameSite, Secure)
- [x] Rate limiting en login
- [x] Docker hardening (read-only, no-new-privileges, cap-drop ALL)
- [x] Validación de configuración al arranque
- [x] Usuario no-root en contenedor

### Phase 2 — Modelo de monitores extendido
- [x] Schema con retries, intervals, dependencies, tags
- [x] Check types: HTTP, Ping, Port, HA Entity, HA Switch, DNS, TLS, Docker, Heartbeat
- [x] Validación de datos de monitores
- [x] Campos extendidos: http_method, http_headers, http_body, verify_keyword
- [x] TLS warning days, expected status codes, follow redirects
- [x] Latency threshold para estado degraded

### Phase 3 — State Machine & Alertas
- [x] Estados: pending → up / down / degraded / maintenance
- [x] Transiciones con retries (consecutive_failures/successes)
- [x] Recovery threshold
- [x] Dependency-aware checks
- [x] Web Push notifications (VAPID)
- [x] Webhook alerts
- [x] Rate limiting de alertas
- [x] Modo mantenimiento por horas

### Phase 4 — Historial y estadísticas
- [x] State + message en historial
- [x] Uptime % (24h, 7d)
- [x] Average latency
- [x] Stats API por monitor
- [x] Time-series history API
- [x] Global summary stats
- [x] Agregación automática (detalle → horario tras 7 días)
- [x] Retención configurable (HISTORY_RETENTION_DAYS)

### Phase 5 — UX Improvements (backend)
- [x] Public status page API (`GET /api/status-page`)
- [x] Bulk operations (pause/resume/delete)
- [x] Monitor groups/tags API (`GET /api/groups`)
- [x] Export/Import de monitores (`GET /api/export`, `POST /api/import`)
- [x] STATUS_PAGE_ENABLED config var

### Phase 6 — Operabilidad
- [x] Health endpoint (`GET /health`) — DB check, uptime, version
- [x] APP_VERSION = "2.0.0"
- [x] README.md completo con documentación
- [x] Referencia de variables de entorno
- [x] Referencia de API endpoints

---

## 🔮 Pendiente para futuras versiones

### v2.1 — Mejoras de alertas
- [ ] Telegram bot notifications (config: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
- [ ] Email alerts (SMTP)
- [ ] Alert escalation (notify different channels after X minutes)
- [ ] Alert acknowledgement API
- [ ] Notification preferences per monitor

### v2.2 — Dashboard frontend
- [ ] Status page frontend (HTML/JS usando /api/status-page)
- [ ] Grafana-style latency charts
- [ ] Monitor group filtering in UI
- [ ] Bulk actions UI (checkboxes + buttons)
- [ ] Export/Import UI buttons

### v2.3 — Integrations
- [ ] Slack notifications
- [ ] Discord webhook
- [ ] PagerDuty integration
- [ ] Prometheus metrics endpoint (`/metrics`)
- [ ] Grafana datasource API

### v2.4 — Multi-user
- [ ] User accounts (not just single password)
- [ ] API keys for automation
- [ ] Role-based access (admin/viewer)
- [ ] Audit log

### v2.5 — Advanced monitoring
- [ ] Multi-step checks (HTTP chain)
- [ ] Content change detection
- [ ] Performance budget tracking
- [ ] Geographic probes (check from multiple locations)
- [ ] Scheduled maintenance windows (calendar)

### Infraestructura
- [ ] Migrar a multi-container con Redis para colas
- [ ] Backup automático de BD
- [ ] Métricas internas de la app (response times, queue depth)
- [ ] Auto-scaling de workers según carga

---

## Notas de infraestructura

- La app corre en Raspberry Pi 4 con Docker
- BD: PostgreSQL en Supabase (free tier)
- Acceso: Tailscale Funnel (HTTPS automático, gratis)
- Auto-update: cron cada 5 min hace git pull + rebuild
- Docker socket montado para métricas de contenedores
