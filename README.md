# Home Monitor v2.0.0

A comprehensive monitoring tool for homelab infrastructure. Monitors HTTP services, network devices, Home Assistant entities, Docker containers, DNS, TLS certificates, and more — with a state machine, alerting, and a public status page API.

Designed to run on a Raspberry Pi or any Docker host in your local network.

## Features

### Check Types
- **HTTP** — URL reachability with status code validation, keyword verification, redirect following
- **Ping** — ICMP ping with latency measurement
- **Port** — TCP port open check
- **Home Assistant Entity** — Monitor any HA entity state (cameras, sensors, etc.)
- **Home Assistant Switch** — Monitor and toggle HA switches
- **DNS** — DNS resolution check
- **TLS** — Certificate expiration monitoring with configurable warning days
- **Docker** — Container status via Docker socket
- **Heartbeat** — Passive monitoring (external services ping an endpoint)

### State Machine
Each monitor transitions through: `pending` → `up` / `down` / `degraded` / `maintenance`
- Configurable retries before marking down
- Recovery threshold before marking up again
- Latency threshold for degraded state
- Dependency-aware (can depend on other monitors)

### Alerting
- **Web Push** — Browser push notifications via VAPID/Web Push
- **Telegram** — Bot notifications (planned)
- **Webhook** — POST to any URL on state change
- Rate limiting to prevent alert storms

### History & Stats
- State + message recorded in history
- Uptime percentage calculation (24h, 7d)
- Average latency tracking
- Time-series data for charting
- Automatic history aggregation (detailed → hourly after 7 days)
- Configurable retention period

### UX & Operations
- Public status page API (no auth required)
- Bulk operations (pause/resume/delete multiple monitors)
- Monitor groups/tags
- Export/Import for backup and migration
- Health check endpoint for orchestrators
- Docker metrics dashboard (CPU, RAM, network per container)

### Security
- CSRF protection on all mutations
- Secure session cookies (HttpOnly, SameSite, Secure in production)
- Security headers (CSP, HSTS, X-Frame-Options, etc.)
- Rate-limited login
- Docker hardening (read-only FS, no-new-privileges, cap-drop ALL)
- Non-root container user

## Quick Start with Docker Compose

1. Clone the repository:
```bash
git clone https://github.com/your-user/App_home_monitor.git
cd App_home_monitor
```

2. Create your `.env` file:
```bash
cp .env.example .env
# Edit .env with your values (see Environment Variables below)
```

3. Generate a secret key:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

4. Start the app:
```bash
docker compose up -d
```

5. Access at `http://localhost:8088`

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `APP_ENV` | No | `production` | `production` or `development` |
| `SECRET_KEY` | **Yes** | — | Session signing key (min 16 chars) |
| `ACCESS_PASSWORD` | **Yes** | — | Login password |
| `DB_HOST` | **Yes** | — | PostgreSQL host |
| `DB_PORT` | No | `5432` | PostgreSQL port |
| `DB_NAME` | No | `postgres` | Database name |
| `DB_USER` | No | `postgres` | Database user |
| `DB_PASSWORD` | **Yes** | — | Database password |
| `CHECK_INTERVAL_SECONDS` | No | `15` | Default check interval (min 5) |
| `MAX_CHECK_WORKERS` | No | `20` | Max concurrent check threads |
| `HOME_ASSISTANT_URL` | No | — | HA base URL (enables HA monitors) |
| `HOME_ASSISTANT_TOKEN` | No | — | HA long-lived access token |
| `VAPID_PRIVATE_KEY` | No | — | Web Push private key |
| `VAPID_PUBLIC_KEY` | No | — | Web Push public key |
| `VAPID_CLAIMS_EMAIL` | No | — | Web Push contact email |
| `DOCKER_METRICS_ENABLED` | No | `true` | Enable Docker stats collection |
| `STATUS_PAGE_ENABLED` | No | `true` | Enable public status page API |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `TZ` | No | `Europe/Madrid` | Timezone |
| `ALLOW_INSECURE_NO_AUTH` | No | `false` | Dev only: allow no password |
| `HISTORY_RETENTION_DAYS` | No | `30` | Days to retain history data |

## API Endpoints

### Public (no auth)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check (status, DB, uptime, version) |
| `GET` | `/api/status-page` | Public status page data |
| `POST` | `/api/heartbeat/<id>` | Receive heartbeat ping |

### Authenticated
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/status` | All device statuses |
| `GET` | `/api/monitors` | List all monitors with state |
| `GET` | `/api/monitors/<id>` | Single monitor detail |
| `POST` | `/api/monitors` | Create monitor |
| `PUT` | `/api/monitors/<id>` | Update monitor |
| `DELETE` | `/api/monitors/<id>` | Delete monitor |
| `GET` | `/api/monitors/<id>/stats` | Uptime %, latency, incidents |
| `GET` | `/api/monitors/<id>/history` | Time-series history |
| `GET` | `/api/stats/summary` | Global summary stats |
| `GET` | `/api/groups` | Monitors grouped by tags |
| `GET` | `/api/export` | Export all monitors as JSON |
| `POST` | `/api/import` | Import monitors from JSON |
| `POST` | `/api/monitors/bulk-pause` | Bulk maintenance mode |
| `POST` | `/api/monitors/bulk-resume` | Bulk end maintenance |
| `POST` | `/api/monitors/bulk-delete` | Bulk delete monitors |
| `GET` | `/api/uptime/<id>` | 24h uptime segments |
| `GET` | `/api/latency/<id>` | Latency sparkline data |
| `GET` | `/api/incidents` | Global incident history |
| `GET` | `/api/devices` | List devices (legacy) |
| `POST` | `/api/devices` | Add device (legacy) |
| `GET` | `/api/pi-stats` | Raspberry Pi system stats |
| `POST` | `/api/force-check` | Trigger immediate check |

### Auth endpoints
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/login` | Login with password |
| `GET` | `/logout` | Clear session |
| `GET` | `/api/csrf-token` | Get CSRF token |

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                   Docker Container                    │
│                                                      │
│  ┌──────────┐    ┌───────────────┐    ┌──────────┐ │
│  │  Flask   │    │ Monitor Worker│    │  Alerts  │ │
│  │  (app.py)│    │ (background)  │    │  Engine  │ │
│  └────┬─────┘    └───────┬───────┘    └────┬─────┘ │
│       │                  │                  │       │
│       └──────────┬───────┘──────────────────┘       │
│                  │                                   │
│           ┌──────┴──────┐                           │
│           │   db.py     │                           │
│           │ (PostgreSQL)│                           │
│           └──────┬──────┘                           │
└──────────────────┼───────────────────────────────────┘
                   │
            ┌──────┴──────┐
            │  PostgreSQL  │
            │  (Supabase)  │
            └─────────────┘
```

- **app.py** — Flask web server, REST API, authentication, security headers
- **monitor_worker.py** — Background thread running checks at configured intervals
- **checks.py** — Check implementations (HTTP, Ping, Port, DNS, TLS, HA, Docker, Heartbeat)
- **state_machine.py** — State transitions with retries and recovery thresholds
- **alerts.py** — Alert dispatch (Web Push, Telegram, Webhook) with rate limiting
- **db.py** — PostgreSQL data layer (statuses, history, config)
- **config.py** — Environment-based configuration with validation
- **validators.py** — Input validation for monitor data
- **csrf.py** — CSRF token generation and verification

## Security Notes

- All secrets are loaded from environment variables — never hardcoded
- CSRF tokens required on all POST/PUT/DELETE endpoints
- Session cookies: HttpOnly, SameSite=Lax, Secure (production)
- Security headers: CSP, HSTS, X-Frame-Options, X-Content-Type-Options
- Login rate limiting (5 attempts per 5 minutes per IP)
- Docker: read-only filesystem, no-new-privileges, all capabilities dropped
- Non-root user inside container
- DB connections use SSL (`sslmode=require`)
- Input validation on all monitor creation/update

## Migration from v1

v1 used a simpler device model with just HTTP/ping/HA checks and SQLite. v2 brings:

1. **PostgreSQL** — Replace SQLite with PostgreSQL (Supabase) for reliability
2. **Extended monitor model** — Retries, intervals, dependencies, tags, state machine
3. **New check types** — DNS, TLS, Docker, Heartbeat added
4. **State machine** — Proper pending/up/down/degraded/maintenance states
5. **Alerting** — Web Push, Telegram, Webhook with rate limiting
6. **History** — State+message in history, uptime %, avg latency
7. **Public status page** — No-auth API for external status displays
8. **Bulk operations** — Manage multiple monitors at once
9. **Export/Import** — Backup and migrate monitor configurations
10. **Health endpoint** — For Docker/K8s health checks

### Steps to migrate:
1. Set up a PostgreSQL database (Supabase free tier works well)
2. Update `.env` with new DB credentials
3. The app will auto-create tables on first run
4. Re-create your monitors via the API or UI (old SQLite data is not auto-migrated)

## License

Private project — not for redistribution.
