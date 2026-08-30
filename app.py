"""
Servidor Flask del Home Monitor.

Endpoints:
  GET  /                    -> dashboard HTML
  GET  /historial           -> página de historial de incidencias
  GET  /api/status          -> estado actual de todos los dispositivos
  GET  /api/uptime/<id>     -> segmentos uptime 24h
  GET  /api/latency/<id>    -> historial de latencia (sparkline)
  GET  /api/incidents       -> historial de incidencias global
  GET  /api/vapid-key       -> clave pública VAPID
  POST /api/subscribe       -> guarda suscripción push
  POST /api/unsubscribe     -> elimina suscripción push
  GET  /api/devices         -> lista de dispositivos configurados
  POST /api/devices         -> añadir dispositivo
  PUT  /api/devices/<id>    -> editar dispositivo
  DELETE /api/devices/<id>  -> borrar dispositivo
"""

import hashlib
import hmac
import time
from collections import defaultdict
import time as _time

from flask import Flask, jsonify, render_template, request, session, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from config import (
    SECRET_KEY, ACCESS_PASSWORD, ADMIN_USERNAME, VAPID_PUBLIC_KEY, APP_ENV, PUSH_ENABLED,
    DOCKER_METRICS_ENABLED, STATUS_PAGE_ENABLED, SESSION_COOKIE_SECURE, TRUSTED_PROXIES,
    APP_VERSION, validate_config,
)
from csrf import get_csrf_token, csrf_protect, secure_eq
from db import (delete_device, get_all_devices, get_all_statuses,
                init_db, seed_devices_from_config, upsert_device, get_latency_history,
                get_incidents, get_all_monitors, get_monitor, upsert_monitor,
                get_monitor_statuses, update_heartbeat_ts,
                get_uptime_percentage, get_avg_latency, get_incidents_for_monitor,
                get_history_timeseries, get_heartbeat_buckets,
                get_uptime_and_latency_bulk, get_incidents_bulk)
from monitor_worker import run_monitor_cycle, start_background_monitor
from notifications import delete_subscription, init_push_table, save_subscription
from utils import humanize_duration
from validators import validate_monitor

# Validar configuración antes de arrancar
validate_config()

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,  # ver config.py
    PERMANENT_SESSION_LIFETIME=30 * 24 * 3600,  # 30 days
)

# Detrás de nginx o Tailscale Funnel, request.remote_addr es la IP del proxy y
# el rate limit de login se aplicaría a todo el mundo a la vez. Solo se confía
# en X-Forwarded-For si TRUSTED_PROXIES > 0: activarlo sin un proxy delante
# permitiría falsificar la cabecera y saltarse el límite.
if TRUSTED_PROXIES > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=TRUSTED_PROXIES,
                            x_proto=TRUSTED_PROXIES, x_host=TRUSTED_PROXIES)


# ─── Metrics cache (avoid slow calls on every /api/status request) ───
_cache = {"system": {}, "system_ts": 0, "docker": [], "docker_ts": 0}
_CACHE_TTL = 10  # seconds

def _get_cached_system_metrics():
    now = time.time()
    if now - _cache["system_ts"] > _CACHE_TTL:
        from checks import check_system
        result = check_system(timeout=5)
        details = result.get("details", {})
        _cache["system"] = {
            "cpu_pct": details.get("cpu_pct"),
            "ram_pct": details.get("ram_pct"),
            "ram_total_mb": details.get("ram_total_mb"),
            "ram_used_mb": details.get("ram_used_mb"),
            "temp_c": details.get("temp_c"),
            "disk_pct": details.get("disk_pct"),
            "disk_total_gb": details.get("disk_total_gb"),
            "disk_used_gb": details.get("disk_used_gb"),
            "uptime": details.get("uptime"),
        }
        _cache["system_ts"] = now
    return _cache["system"]

def _get_cached_docker_containers():
    now = time.time()
    if now - _cache["docker_ts"] > _CACHE_TTL:
        from checks import get_all_docker_containers
        result = get_all_docker_containers(timeout=5)
        _cache["docker"] = result.get("containers", [])
        _cache["docker_ts"] = now
    return _cache["docker"]


def require_auth():
    """Devuelve True si hay que autenticar y no está autenticado."""
    if not ACCESS_PASSWORD:
        return False
    return not session.get("authenticated")


# -----------------------------------------------------------------------
# Rate limiting (login)
# -----------------------------------------------------------------------

_login_attempts = defaultdict(list)
LOGIN_RATE_LIMIT = 5  # max attempts
LOGIN_RATE_WINDOW = 300  # 5 minutes


def _check_login_rate(ip):
    now = _time.time()
    # Purgar IPs cuya ventana ya expiró: si no, el dict crece sin límite.
    for other in [k for k, v in _login_attempts.items()
                  if k != ip and (not v or now - v[-1] >= LOGIN_RATE_WINDOW)]:
        del _login_attempts[other]

    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < LOGIN_RATE_WINDOW]
    if len(_login_attempts[ip]) >= LOGIN_RATE_LIMIT:
        return False
    _login_attempts[ip].append(now)
    return True


# -----------------------------------------------------------------------
# Auth
# -----------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        # CSRF también en el login: sin esto, un tercero puede forzar una sesión
        # conocida por él en el navegador de la víctima (login CSRF).
        form_token = request.form.get("csrf_token", "")
        if not form_token or not secure_eq(form_token, session.get("_csrf_token", "")):
            return render_template(
                "login.html", csrf_token=get_csrf_token(),
                error="La sesión ha caducado. Inténtalo de nuevo."), 400

        if not _check_login_rate(request.remote_addr):
            return render_template(
                "login.html", csrf_token=get_csrf_token(),
                error="Demasiados intentos. Espera 5 minutos."), 429

        # compare_digest evita filtrar la contraseña por tiempo de respuesta.
        # Se evalúan siempre las dos comparaciones para no distinguir "usuario
        # incorrecto" de "contraseña incorrecta" por la duración del check.
        user_ok = secure_eq(request.form.get("username", ""), ADMIN_USERNAME)
        pass_ok = secure_eq(request.form.get("password", ""), ACCESS_PASSWORD)
        if user_ok and pass_ok:
            session.clear()
            session.permanent = True
            session["authenticated"] = True
            get_csrf_token()  # regenerate CSRF token on login
            return redirect(url_for("index"))
        error = "Usuario o contraseña incorrectos"
    return render_template("login.html", csrf_token=get_csrf_token(), error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/csrf-token")
def api_csrf_token():
    return jsonify({"token": get_csrf_token()})


# -----------------------------------------------------------------------
# Dashboard
# -----------------------------------------------------------------------

@app.route("/")
def index():
    if require_auth():
        return redirect(url_for("login"))
    return render_template("index.html")


@app.route("/historial")
def historial():
    if require_auth():
        return redirect(url_for("login"))
    return render_template("historial.html")


@app.route("/api/force-check", methods=["POST"])
@csrf_protect
def api_force_check():
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    import threading
    threading.Thread(target=run_monitor_cycle, kwargs={"force": True},
                     daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/devices/<device_id>/maintenance", methods=["POST"])
@csrf_protect
def api_maintenance(device_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    hours = float(request.json.get("hours", 0))
    from db import set_maintenance
    set_maintenance(device_id, hours)
    return jsonify({"ok": True})


@app.route("/api/toggle/<device_id>", methods=["POST"])
@csrf_protect
def api_toggle(device_id):
    """Activa o desactiva un switch de Home Assistant."""
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    data = request.get_json() or {}
    action = data.get("action")
    if action not in ("turn_on", "turn_off", "toggle"):
        return jsonify({"ok": False, "message": "Acción inválida (turn_on/turn_off/toggle)"}), 400

    devices = {d["id"]: d for d in get_all_devices()}
    device = devices.get(device_id)
    if not device:
        return jsonify({"ok": False, "message": "Dispositivo no encontrado"}), 404
    if device["type"] != "ha_switch":
        return jsonify({"ok": False, "message": "El dispositivo no es un switch"}), 400

    from checks import toggle_ha_switch
    ok, msg = toggle_ha_switch(device["entity_id"], action, timeout=8)
    if not ok:
        return jsonify({"ok": False, "message": msg})
    return jsonify({"ok": True})


@app.route("/api/ha-sensors")
def api_ha_sensors():
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    from config import HOME_ASSISTANT_TOKEN, HOME_ASSISTANT_URL
    import requests as req
    sensors = [
        "sensor.system_monitor_temperatura_del_procesador",
        "sensor.system_monitor_uso_de_memoria_2",
        "sensor.adguard_home_consultas_dns",
        "sensor.adguard_home_proporcion_de_consultas_dns_bloqueadas",
    ]
    result = {}
    headers = {"Authorization": f"Bearer {HOME_ASSISTANT_TOKEN}"}
    for entity_id in sensors:
        try:
            r = req.get(f"{HOME_ASSISTANT_URL.rstrip('/')}/api/states/{entity_id}",
                        headers=headers, timeout=5)
            if r.status_code == 200:
                data = r.json()
                result[entity_id] = {
                    "state": data.get("state"),
                    "unit": data.get("attributes", {}).get("unit_of_measurement", ""),
                }
        except Exception:
            pass
    return jsonify(result)


@app.route("/api/status")
def api_status():
    if require_auth():
        return jsonify({"error": "No autorizado"}), 401
    now = time.time()
    statuses = get_all_statuses()
    devices_cfg = {d["id"]: d for d in get_all_devices()}
    result = []
    for s in statuses:
        since_seconds = now - s["last_change_ts"]
        cfg = devices_cfg.get(s["device_id"], {})
        maintenance_until = cfg.get("maintenance_until", 0)
        in_maintenance = maintenance_until > now
        # Derive effective state: maintenance overrides DB state
        db_state = s.get("state", "pending")
        if in_maintenance:
            effective_state = "maintenance"
        elif db_state == "pending":
            effective_state = "up" if s["online"] else "down"
        else:
            effective_state = db_state
        entry = {
            "id": s["device_id"],
            "name": s["name"],
            "type": cfg.get("type"),
            "online": bool(s["online"]),
            "state": effective_state,
            "since_seconds": since_seconds,
            "since_human": humanize_duration(since_seconds),
            "last_check_seconds_ago": now - s["last_check_ts"],
            "last_error": s["last_error"],
            "response_ms": s.get("response_ms"),
            "maintenance_until": maintenance_until,
            "in_maintenance": in_maintenance,
            "switch_state": s.get("switch_state"),
        }

        # Include system metrics inline for 'system' type monitors (cached)
        if cfg.get("type") == "system":
            try:
                entry.update(_get_cached_system_metrics())
            except Exception:
                pass

        # Include docker container info inline for 'docker' type monitors (cached)
        if cfg.get("type") == "docker":
            try:
                entry["containers"] = _get_cached_docker_containers()
            except Exception:
                entry["containers"] = []

        result.append(entry)
    result.sort(key=lambda d: d["name"])
    return jsonify({"server_time": now, "devices": result})


# -----------------------------------------------------------------------
# Gestión de dispositivos
# -----------------------------------------------------------------------

@app.route("/api/uptime/<device_id>")
def api_uptime(device_id):
    """Uptime de las últimas 24h: porcentaje y segmentos de estado.

    El porcentaje viene de get_uptime_percentage(), que cuenta los checks
    reales de toda la ventana. Los segmentos se derivan de los mismos buckets
    de 15 min que alimentan las barras de heartbeat, así ambos coinciden.

    (Antes esto reconstruía segmentos a mano asumiendo que status_history solo
    guardaba cambios de estado. Desde que se registra un check por ciclo, esa
    suposición era falsa y el porcentaje salía absurdamente bajo.)
    """
    if require_auth(): return jsonify({"error": "No autorizado"}), 401

    hours = 24
    span = hours * 3600.0
    uptime_pct = get_uptime_percentage(device_id, hours=hours)
    buckets = get_heartbeat_buckets(device_id, hours=hours, bucket_minutes=15)

    # Fusionar buckets consecutivos del mismo estado en segmentos.
    segments = []
    for bk in buckets:
        if bk["state"] == "unknown":
            continue
        online = bk["state"] in ("up", "degraded")
        prev = segments[-1] if segments else None
        if prev and prev["online"] == online and prev["end"] == bk["start"]:
            prev["end"] = bk["end"]
        else:
            segments.append({"start": bk["start"], "end": bk["end"], "online": online})

    for seg in segments:
        seg["pct"] = (seg["end"] - seg["start"]) / span * 100

    return jsonify({"segments": segments, "uptime_pct": uptime_pct})


@app.route("/api/latency/<device_id>")
def api_latency(device_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    points = get_latency_history(device_id, limit=60)
    return jsonify({"points": points})


@app.route("/api/heartbeats/<device_id>")
def api_heartbeats(device_id):
    """Historial 12h agrupado en buckets de 15 min (estilo Uptime Kuma).
    Devuelve siempre 48 buckets; los vacíos salen como 'unknown' (gris)."""
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    buckets = get_heartbeat_buckets(device_id, hours=12, bucket_minutes=15)
    return jsonify({
        "hours": 12,
        "bucket_minutes": 15,
        "points": [
            {"start": bk["start"], "end": bk["end"], "state": bk["state"], "n": bk["n"]}
            for bk in buckets
        ],
    })


@app.route("/api/incidents")
def api_incidents():
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    incidents = get_incidents(limit=100)
    now = time.time()
    result = []
    for i in incidents:
        duration = None
        if not i["online"] and i.get("recovered_ts"):
            duration = humanize_duration(i["recovered_ts"] - i["ts"])
        result.append({
            "device_id": i["device_id"],
            "name": i["name"],
            "online": bool(i["online"]),
            "ts": i["ts"],
            "ts_human": time.strftime("%d/%m %H:%M", time.localtime(i["ts"])),
            "duration": duration,
        })
    return jsonify({"incidents": result})


@app.route("/api/devices", methods=["GET"])
def list_devices():
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    return jsonify(get_all_devices())


@app.route("/api/devices", methods=["POST"])
@csrf_protect
def add_device():
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    data = request.get_json()
    if not data or not all(k in data for k in ("id", "name", "type")):
        return jsonify({"error": "Faltan campos obligatorios (id, name, type)"}), 400
    upsert_device(data)
    return jsonify({"ok": True}), 201


@app.route("/api/devices/<device_id>", methods=["PUT"])
@csrf_protect
def edit_device(device_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    data = request.get_json()
    if not data:
        return jsonify({"error": "Sin datos"}), 400
    data["id"] = device_id
    upsert_device(data)
    return jsonify({"ok": True})


@app.route("/api/devices/<device_id>", methods=["DELETE"])
@csrf_protect
def remove_device(device_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    delete_device(device_id)
    return jsonify({"ok": True})


# -----------------------------------------------------------------------
# API de Monitores (Phase 2 — campos extendidos)
# -----------------------------------------------------------------------

@app.route("/api/monitors", methods=["GET"])
def list_monitors():
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    monitors = get_all_monitors()
    statuses = {s["device_id"]: s for s in get_monitor_statuses()}
    now = time.time()
    result = []
    for m in monitors:
        status = statuses.get(m["id"], {})
        result.append({
            **m,
            "state": status.get("state", "pending"),
            "online": bool(status.get("online", 0)),
            "last_check_ts": status.get("last_check_ts"),
            "last_error": status.get("last_error"),
            "response_ms": status.get("response_ms"),
            "consecutive_failures": status.get("consecutive_failures", 0),
            "consecutive_successes": status.get("consecutive_successes", 0),
            "incident_id": status.get("incident_id"),
            "in_maintenance": m.get("maintenance_until", 0) > now,
        })
    return jsonify({"monitors": result})


@app.route("/api/monitors/<monitor_id>", methods=["GET"])
def get_monitor_detail(monitor_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    m = get_monitor(monitor_id)
    if not m:
        return jsonify({"error": "Monitor no encontrado"}), 404
    statuses = {s["device_id"]: s for s in get_monitor_statuses()}
    status = statuses.get(monitor_id, {})
    now = time.time()
    result = {
        **m,
        "state": status.get("state", "pending"),
        "online": bool(status.get("online", 0)),
        "last_check_ts": status.get("last_check_ts"),
        "last_error": status.get("last_error"),
        "response_ms": status.get("response_ms"),
        "consecutive_failures": status.get("consecutive_failures", 0),
        "consecutive_successes": status.get("consecutive_successes", 0),
        "incident_id": status.get("incident_id"),
        "in_maintenance": m.get("maintenance_until", 0) > now,
    }
    return jsonify(result)


@app.route("/api/monitors", methods=["POST"])
@csrf_protect
def add_monitor():
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    data = request.get_json()
    if not data:
        return jsonify({"error": "Sin datos"}), 400
    errors = validate_monitor(data)
    if errors:
        return jsonify({"error": "Validación fallida", "details": errors}), 400
    upsert_monitor(data)
    return jsonify({"ok": True}), 201


@app.route("/api/monitors/<monitor_id>", methods=["PUT"])
@csrf_protect
def edit_monitor(monitor_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    data = request.get_json()
    if not data:
        return jsonify({"error": "Sin datos"}), 400
    data["id"] = monitor_id
    errors = validate_monitor(data)
    if errors:
        return jsonify({"error": "Validación fallida", "details": errors}), 400
    upsert_monitor(data)
    return jsonify({"ok": True})


@app.route("/api/monitors/<monitor_id>", methods=["DELETE"])
@csrf_protect
def remove_monitor(monitor_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    delete_device(monitor_id)
    return jsonify({"ok": True})


# -----------------------------------------------------------------------
# Heartbeat endpoint (Phase 2)
# -----------------------------------------------------------------------

def heartbeat_token(monitor_id: str) -> str:
    """Token estable por monitor, derivado de SECRET_KEY.

    No necesita almacenamiento ni migración: se recalcula igual en cada
    arranque. Rotar SECRET_KEY invalida todos los tokens de heartbeat.
    """
    return hmac.new(
        SECRET_KEY.encode("utf-8"),
        f"heartbeat:{monitor_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]


@app.route("/api/heartbeat/<monitor_id>", methods=["POST"])
def api_heartbeat(monitor_id):
    """Recibe un ping de un servicio externo.

    Sin sesión (lo llaman cron jobs y scripts), pero autenticado con el token
    del monitor: por cabecera X-Heartbeat-Token (preferido, no acaba en los
    logs del proxy) o por ?token= para clientes simples tipo curl.
    """
    token = request.headers.get("X-Heartbeat-Token") or request.args.get("token", "")
    if not secure_eq(token, heartbeat_token(monitor_id)):
        return jsonify({"error": "Token inválido"}), 403
    if not update_heartbeat_ts(monitor_id):
        return jsonify({"error": "Monitor no encontrado"}), 404
    return jsonify({"ok": True})


@app.route("/api/monitors/<monitor_id>/heartbeat-url")
def api_heartbeat_url(monitor_id):
    """Devuelve la URL y el token que debe usar el servicio externo."""
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    m = get_monitor(monitor_id)
    if not m:
        return jsonify({"error": "Monitor no encontrado"}), 404
    token = heartbeat_token(monitor_id)
    return jsonify({
        "url": f"{request.host_url.rstrip('/')}/api/heartbeat/{monitor_id}",
        "token": token,
        "header": "X-Heartbeat-Token",
        "curl": (f"curl -fsS -X POST -H 'X-Heartbeat-Token: {token}' "
                 f"{request.host_url.rstrip('/')}/api/heartbeat/{monitor_id}"),
    })


# -----------------------------------------------------------------------
# Phase 4: Stats & History endpoints
# -----------------------------------------------------------------------

@app.route("/api/monitors/<monitor_id>/stats")
def api_monitor_stats(monitor_id):
    """Returns stats for a specific monitor: uptime %, avg latency, incidents count."""
    if require_auth():
        return jsonify({"error": "No autorizado"}), 401

    m = get_monitor(monitor_id)
    if not m:
        return jsonify({"error": "Monitor no encontrado"}), 404

    statuses = {s["device_id"]: s for s in get_monitor_statuses()}
    status = statuses.get(monitor_id, {})

    uptime_24h = get_uptime_percentage(monitor_id, hours=24)
    uptime_7d = get_uptime_percentage(monitor_id, hours=168)
    avg_latency_24h = get_avg_latency(monitor_id, hours=24)
    incidents = get_incidents_for_monitor(monitor_id, limit=100)
    # Count incidents in last 24h
    now = time.time()
    cutoff_24h = now - 86400
    incidents_24h = sum(1 for i in incidents if i["start_ts"] >= cutoff_24h)

    return jsonify({
        "monitor_id": monitor_id,
        "uptime_pct_24h": uptime_24h,
        "uptime_pct_7d": uptime_7d,
        "avg_latency_24h": avg_latency_24h,
        "incidents_count_24h": incidents_24h,
        "current_state": status.get("state", "pending"),
    })


@app.route("/api/monitors/<monitor_id>/history")
def api_monitor_history(monitor_id):
    """Returns time-series history data for charting."""
    if require_auth():
        return jsonify({"error": "No autorizado"}), 401

    m = get_monitor(monitor_id)
    if not m:
        return jsonify({"error": "Monitor no encontrado"}), 404

    hours = request.args.get("hours", 24, type=int)
    # Cap at 720 hours (30 days)
    hours = min(hours, 720)
    data = get_history_timeseries(monitor_id, hours=hours)
    return jsonify({"monitor_id": monitor_id, "hours": hours, "data": data})


@app.route("/api/stats/summary")
def api_stats_summary():
    """Global summary: total monitors, up/down/degraded counts, avg uptime."""
    if require_auth():
        return jsonify({"error": "No autorizado"}), 401

    statuses = get_monitor_statuses()
    total = len(statuses)
    up_count = sum(1 for s in statuses if s.get("state") == "up")
    down_count = sum(1 for s in statuses if s.get("state") == "down")
    degraded_count = sum(1 for s in statuses if s.get("state") == "degraded")

    # Uptime medio de todos los monitores, en una sola consulta
    bulk = get_uptime_and_latency_bulk(hours=24)
    uptimes = [bulk.get(s["device_id"], {}).get("uptime_pct", 100.0) for s in statuses]
    avg_uptime = round(sum(uptimes) / len(uptimes), 2) if uptimes else 100.0

    return jsonify({
        "total_monitors": total,
        "up": up_count,
        "down": down_count,
        "degraded": degraded_count,
        "avg_uptime_24h": avg_uptime,
    })


# -----------------------------------------------------------------------
# Phase 5: Status Page, Bulk Operations, Groups, Export/Import
# -----------------------------------------------------------------------

@app.route("/api/status-page")
def api_status_page():
    """Public status page endpoint — no auth required if enabled."""
    if not STATUS_PAGE_ENABLED:
        return jsonify({"error": "Status page disabled"}), 404

    now = time.time()
    cutoff_24h = now - 86400
    monitors = get_all_monitors()
    statuses = {s["device_id"]: s for s in get_monitor_statuses()}
    # Dos consultas para todos los monitores, en vez de tres por monitor.
    bulk = get_uptime_and_latency_bulk(hours=24)
    incidents_by_monitor = get_incidents_bulk(hours=24)

    monitor_list = []
    down_count = 0
    degraded_count = 0

    for m in monitors:
        status = statuses.get(m["id"], {})
        state = status.get("state", "pending")
        if state == "down":
            down_count += 1
        elif state == "degraded":
            degraded_count += 1

        stats = bulk.get(m["id"], {})
        uptime_24h = stats.get("uptime_pct", 100.0)
        avg_latency = stats.get("avg_latency_ms")

        monitor_list.append({
            "id": m["id"],
            "name": m["name"],
            "state": state,
            "uptime_24h": uptime_24h,
            "latency_ms": avg_latency,
            "last_check": status.get("last_check_ts"),
            "group": m.get("tags", "") or "Ungrouped",
        })

    # Determine overall status
    total = len(monitors)
    if down_count > 0 and down_count >= total * 0.5:
        overall_status = "major_outage"
    elif down_count > 0 or degraded_count > 0:
        overall_status = "degraded"
    else:
        overall_status = "operational"

    # Incidents in last 24h
    incidents_24h = []
    for m in monitors:
        for inc in incidents_by_monitor.get(m["id"], []):
            if inc["start_ts"] >= cutoff_24h:
                incidents_24h.append({
                    "monitor_id": m["id"],
                    "monitor_name": m["name"],
                    "start_ts": inc["start_ts"],
                    "end_ts": inc["end_ts"],
                    "duration_seconds": inc["duration_seconds"],
                    "message": inc.get("message", ""),
                })

    incidents_24h.sort(key=lambda x: x["start_ts"], reverse=True)

    return jsonify({
        "overall_status": overall_status,
        "monitors": monitor_list,
        "incidents_24h": incidents_24h,
        "last_updated": now,
    })


@app.route("/api/monitors/bulk-pause", methods=["POST"])
@csrf_protect
def api_bulk_pause():
    """Put multiple monitors in maintenance mode."""
    if require_auth():
        return jsonify({"error": "No autorizado"}), 401
    data = request.get_json()
    if not data or "ids" not in data:
        return jsonify({"error": "Campo 'ids' obligatorio"}), 400
    ids = data["ids"]
    if not isinstance(ids, list):
        return jsonify({"error": "'ids' debe ser una lista"}), 400
    hours = data.get("hours", 24)
    from db import set_maintenance
    count = 0
    for monitor_id in ids:
        if isinstance(monitor_id, str):
            set_maintenance(monitor_id, hours)
            count += 1
    return jsonify({"ok": True, "paused": count})


@app.route("/api/monitors/bulk-resume", methods=["POST"])
@csrf_protect
def api_bulk_resume():
    """End maintenance for multiple monitors."""
    if require_auth():
        return jsonify({"error": "No autorizado"}), 401
    data = request.get_json()
    if not data or "ids" not in data:
        return jsonify({"error": "Campo 'ids' obligatorio"}), 400
    ids = data["ids"]
    if not isinstance(ids, list):
        return jsonify({"error": "'ids' debe ser una lista"}), 400
    from db import set_maintenance
    count = 0
    for monitor_id in ids:
        if isinstance(monitor_id, str):
            set_maintenance(monitor_id, 0)
            count += 1
    return jsonify({"ok": True, "resumed": count})


@app.route("/api/monitors/bulk-delete", methods=["POST"])
@csrf_protect
def api_bulk_delete():
    """Delete multiple monitors."""
    if require_auth():
        return jsonify({"error": "No autorizado"}), 401
    data = request.get_json()
    if not data or "ids" not in data:
        return jsonify({"error": "Campo 'ids' obligatorio"}), 400
    ids = data["ids"]
    if not isinstance(ids, list):
        return jsonify({"error": "'ids' debe ser una lista"}), 400
    count = 0
    for monitor_id in ids:
        if isinstance(monitor_id, str):
            delete_device(monitor_id)
            count += 1
    return jsonify({"ok": True, "deleted": count})


@app.route("/api/groups")
def api_groups():
    """Returns monitors grouped by their tags field."""
    if require_auth():
        return jsonify({"error": "No autorizado"}), 401
    monitors = get_all_monitors()
    statuses = {s["device_id"]: s for s in get_monitor_statuses()}
    now = time.time()

    groups = {}
    for m in monitors:
        status = statuses.get(m["id"], {})
        monitor_data = {
            "id": m["id"],
            "name": m["name"],
            "type": m["type"],
            "state": status.get("state", "pending"),
            "online": bool(status.get("online", 0)),
            "response_ms": status.get("response_ms"),
            "in_maintenance": m.get("maintenance_until", 0) > now,
        }
        tags = m.get("tags", "").strip()
        group_name = tags if tags else "Ungrouped"
        if group_name not in groups:
            groups[group_name] = []
        groups[group_name].append(monitor_data)

    return jsonify({"groups": groups})


@app.route("/api/export")
def api_export():
    """Export all monitors configuration as JSON (backup/migration)."""
    if require_auth():
        return jsonify({"error": "No autorizado"}), 401
    monitors = get_all_monitors()
    # Remove internal fields that shouldn't be exported
    export_data = []
    for m in monitors:
        export_item = {k: v for k, v in m.items() if k not in ("created_at", "enabled")}
        export_data.append(export_item)
    return jsonify({
        "version": "2.0.0",
        "exported_at": time.time(),
        "monitors": export_data,
    })


@app.route("/api/import", methods=["POST"])
@csrf_protect
def api_import():
    """Import monitors from JSON. Validates each, skips duplicates."""
    if require_auth():
        return jsonify({"error": "No autorizado"}), 401
    data = request.get_json()
    if not data or "monitors" not in data:
        return jsonify({"error": "Campo 'monitors' obligatorio"}), 400

    monitors_data = data["monitors"]
    if not isinstance(monitors_data, list):
        return jsonify({"error": "'monitors' debe ser una lista"}), 400

    existing_monitors = {m["id"] for m in get_all_monitors()}
    imported = 0
    skipped = 0
    errors_list = []

    for i, monitor in enumerate(monitors_data):
        if not isinstance(monitor, dict):
            errors_list.append(f"Item {i}: no es un objeto válido")
            continue

        # Skip duplicates
        monitor_id = monitor.get("id", "")
        if monitor_id in existing_monitors:
            skipped += 1
            continue

        # Validate
        validation_errors = validate_monitor(monitor)
        if validation_errors:
            errors_list.append(f"Item {i} ({monitor_id}): {'; '.join(validation_errors)}")
            continue

        upsert_monitor(monitor)
        existing_monitors.add(monitor_id)
        imported += 1

    return jsonify({
        "ok": True,
        "imported": imported,
        "skipped": skipped,
        "errors": errors_list,
    })


# -----------------------------------------------------------------------
# Push notifications
# -----------------------------------------------------------------------

@app.route("/api/vapid-key")
def vapid_key():
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})


@app.route("/api/subscribe", methods=["POST"])
@csrf_protect
def subscribe():
    sub = request.get_json()
    if not sub or "endpoint" not in sub:
        return jsonify({"error": "Suscripción inválida"}), 400
    save_subscription(sub)
    return jsonify({"ok": True}), 201


@app.route("/api/unsubscribe", methods=["POST"])
@csrf_protect
def unsubscribe():
    data = request.get_json()
    if data and "endpoint" in data:
        delete_subscription(data["endpoint"])
    return jsonify({"ok": True})


# -----------------------------------------------------------------------
# Security headers
# -----------------------------------------------------------------------

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    if APP_ENV == 'production':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    # CSP: sin scripts inline — todo el JS vive en /static y los handlers van
    # por data-action. style-src mantiene 'unsafe-inline' porque el marcado usa
    # atributos style= (incluidos los que genera el JS al pintar las filas).
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "frame-ancestors 'self'"
    )
    return response


# -----------------------------------------------------------------------
# Phase 6: Health endpoint
# -----------------------------------------------------------------------

_app_start_time = time.time()


@app.route("/health")
def health_check():
    """Health check endpoint — no auth required."""
    uptime_seconds = int(time.time() - _app_start_time)

    # Check DB connectivity
    try:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM devices")
                monitors_count = cur.fetchone()[0]
        return jsonify({
            "status": "ok",
            "db": "connected",
            "uptime_seconds": uptime_seconds,
            "monitors_count": monitors_count,
            "version": APP_VERSION,
        })
    except Exception:
        return jsonify({
            "status": "degraded",
            "db": "disconnected",
            "uptime_seconds": uptime_seconds,
            "monitors_count": 0,
            "version": APP_VERSION,
        }), 503


# -----------------------------------------------------------------------
# Arranque
# -----------------------------------------------------------------------

def bootstrap():
    """Inicialización única del proceso: esquema, seed y monitor de fondo.

    La llama wsgi.py (gunicorn en producción) y el __main__ de abajo (dev).
    No fuerza un ciclo inicial: start_background_monitor() ya comprueba de
    inmediato todos los monitores que tengan el intervalo vencido, que al
    arrancar son todos.
    """
    init_db()
    init_push_table()
    seed_devices_from_config()
    start_background_monitor()


if __name__ == "__main__":
    # Solo para desarrollo. En producción se sirve con gunicorn (ver Dockerfile).
    bootstrap()
    app.run(host="0.0.0.0", port=8088, debug=False)
