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

import time
from collections import defaultdict
import time as _time

from flask import Flask, jsonify, render_template, request, session, redirect, url_for

from config import (
    SECRET_KEY, ACCESS_PASSWORD, VAPID_PUBLIC_KEY, APP_ENV, PUSH_ENABLED,
    DOCKER_METRICS_ENABLED, STATUS_PAGE_ENABLED, APP_VERSION, validate_config,
)
from csrf import get_csrf_token, csrf_protect
from db import (delete_device, get_all_devices, get_all_statuses, get_history,
                init_db, seed_devices_from_config, upsert_device, get_latency_history,
                get_incidents, get_all_monitors, get_monitor, upsert_monitor,
                get_monitor_statuses, update_heartbeat_ts,
                get_uptime_percentage, get_avg_latency, get_incidents_for_monitor,
                get_history_timeseries)
from monitor_worker import run_checks_once, start_background_monitor
from notifications import delete_subscription, init_push_table, save_subscription
from validators import validate_monitor

# Validar configuración antes de arrancar
validate_config()

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=APP_ENV == 'production',
    PERMANENT_SESSION_LIFETIME=86400,  # 24h
)


def humanize_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:   parts.append(f"{days}d")
    if hours:  parts.append(f"{hours}h")
    if minutes and not days: parts.append(f"{minutes}min")
    if not parts: parts.append(f"{secs}s")
    return " ".join(parts)


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
        if not _check_login_rate(request.remote_addr):
            return render_template("login.html", error="Demasiados intentos. Espera 5 minutos."), 429
        if request.form.get("password") == ACCESS_PASSWORD:
            session.clear()
            session["authenticated"] = True
            get_csrf_token()  # regenerate CSRF token on login
            return redirect(url_for("index"))
        error = "Contraseña incorrecta"
    return render_template("login.html", error=error)


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
    from monitor_worker import run_checks_once
    import threading
    threading.Thread(target=run_checks_once, daemon=True).start()
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


@app.route("/api/pi-stats")
def api_pi_stats():
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    import subprocess, shutil

    # --- CPU % (media 1s) ---
    try:
        with open("/proc/stat") as f:
            line1 = f.readline().split()
        time.sleep(0.5)
        with open("/proc/stat") as f:
            line2 = f.readline().split()
        idle1 = int(line1[4]); total1 = sum(int(x) for x in line1[1:])
        idle2 = int(line2[4]); total2 = sum(int(x) for x in line2[1:])
        cpu_pct = round(100 * (1 - (idle2 - idle1) / (total2 - total1)), 1)
    except Exception:
        cpu_pct = None

    # --- RAM ---
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":"); mem[k.strip()] = int(v.split()[0])
        ram_total_mb = mem["MemTotal"] // 1024
        ram_avail_mb = mem["MemAvailable"] // 1024
        ram_used_mb  = ram_total_mb - ram_avail_mb
        ram_pct      = round(100 * ram_used_mb / ram_total_mb, 1)
    except Exception:
        ram_total_mb = ram_used_mb = ram_avail_mb = ram_pct = None

    # --- Temperatura ---
    try:
        result = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=2)
        temp_str = result.stdout.strip().replace("temp=", "").replace("'C", "")
        temp_c = float(temp_str)
    except Exception:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                temp_c = round(int(f.read().strip()) / 1000, 1)
        except Exception:
            temp_c = None

    # --- Disco ---
    try:
        disk = shutil.disk_usage("/")
        disk_total_gb = round(disk.total / 1e9, 1)
        disk_used_gb  = round(disk.used  / 1e9, 1)
        disk_free_gb  = round(disk.free  / 1e9, 1)
        disk_pct      = round(100 * disk.used / disk.total, 1)
    except Exception:
        disk_total_gb = disk_used_gb = disk_free_gb = disk_pct = None

    # --- Uptime ---
    try:
        with open("/proc/uptime") as f:
            uptime_secs = int(float(f.read().split()[0]))
        days, rem = divmod(uptime_secs, 86400)
        hours, rem = divmod(rem, 3600)
        mins = rem // 60
        uptime_human = (f"{days}d " if days else "") + f"{hours}h {mins}min"
    except Exception:
        uptime_human = None

    # --- Docker stats (todos los contenedores) ---
    docker_stats = None
    try:
        import socket as sock
        import json as _json

        def docker_get(path):
            s = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
            s.connect("/var/run/docker.sock")
            req = f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n"
            s.sendall(req.encode())
            resp = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                resp += chunk
            s.close()
            body = resp.split(b"\r\n\r\n", 1)[1]
            return _json.loads(body)

        def fmt_bytes(b):
            return f"{round(b/1048576,1)}MB" if b >= 1048576 else f"{round(b/1024,1)}kB"

        def get_container_stats(cid, pid):
            try:
                # Dos muestras separadas para CPU precisa
                st1 = docker_get(f"/containers/{cid}/stats?stream=false")
                time.sleep(0.3)
                st2 = docker_get(f"/containers/{cid}/stats?stream=false")

                cpu_delta = st2["cpu_stats"]["cpu_usage"]["total_usage"] - st1["cpu_stats"]["cpu_usage"]["total_usage"]
                sys_delta = st2["cpu_stats"].get("system_cpu_usage", 0) - st1["cpu_stats"].get("system_cpu_usage", 0)
                if sys_delta > 0 and cpu_delta >= 0:
                    cpu_pct = round((cpu_delta / sys_delta) * 100, 1)
                    cpu_pct = min(cpu_pct, 100.0)
                else:
                    cpu_pct = 0.0

                # RAM
                mem_usage_raw = st2["memory_stats"].get("usage")
                if mem_usage_raw:
                    mem_usage = mem_usage_raw - st2["memory_stats"].get("stats", {}).get("cache", 0)
                    mem_str   = f"{round(mem_usage/1048576,1)}MB"
                else:
                    try:
                        with open(f"/proc/{pid}/status") as fh:
                            for line in fh:
                                if line.startswith("VmRSS:"):
                                    mem_str = f"{round(int(line.split()[1])/1024,1)}MB"
                                    break
                            else:
                                mem_str = "n/a"
                    except Exception:
                        mem_str = "n/a"

                # Red
                net_rx = sum(v["rx_bytes"] for v in st2.get("networks", {}).values())
                net_tx = sum(v["tx_bytes"] for v in st2.get("networks", {}).values())

                # Disco (blkio)
                blk_read = blk_write = 0
                for item in st2.get("blkio_stats", {}).get("io_service_bytes_recursive", []) or []:
                    if item["op"] == "read":  blk_read  = item["value"]
                    if item["op"] == "write": blk_write = item["value"]

                return {
                    "cpu":       f"{cpu_pct}%",
                    "mem":       mem_str,
                    "net_rx":    fmt_bytes(net_rx),
                    "net_tx":    fmt_bytes(net_tx),
                    "blk_read":  fmt_bytes(blk_read),
                    "blk_write": fmt_bytes(blk_write),
                }
            except Exception:
                return {"cpu": "n/a", "mem": "n/a", "net_rx": "n/a", "net_tx": "n/a", "blk_read": "n/a", "blk_write": "n/a"}

        # Listar todos los contenedores (running y stopped)
        all_containers = docker_get("/containers/json?all=true")
        containers = []
        running_count = 0
        for c in all_containers:
            cid    = c["Id"]
            name   = c["Names"][0].lstrip("/") if c.get("Names") else cid[:12]
            status = c.get("State", "unknown")
            pid    = 0
            restart_count = 0
            started_at    = ""
            image_size    = ""

            # Inspect para info extra
            try:
                info = docker_get(f"/containers/{cid}/json")
                pid           = info.get("State", {}).get("Pid", 0)
                restart_count = info.get("RestartCount", 0)
                raw_start     = info.get("State", {}).get("StartedAt", "")[:19]
                if raw_start and raw_start != "0001-01-01T00:00:00":
                    started_at = raw_start.replace("T", " ")
                # Tamano imagen
                img_name = info.get("Config", {}).get("Image", "")
                try:
                    img_info   = docker_get(f"/images/{img_name}/json")
                    img_bytes  = img_info.get("Size", 0)
                    image_size = fmt_bytes(img_bytes)
                except Exception:
                    image_size = "n/a"
            except Exception:
                pass

            if status == "running":
                running_count += 1
                stats = get_container_stats(cid, pid)
            else:
                stats = {"cpu": "—", "mem": "—", "net_rx": "—", "net_tx": "—", "blk_read": "—", "blk_write": "—"}

            containers.append({
                "name":          name,
                "status":        status,
                "cpu":           stats["cpu"],
                "mem":           stats["mem"],
                "net_rx":        stats["net_rx"],
                "net_tx":        stats["net_tx"],
                "blk_read":      stats["blk_read"],
                "blk_write":     stats["blk_write"],
                "image_size":    image_size,
                "started_at":    started_at,
                "restart_count": restart_count,
            })

        # Ordenar: portainer primero, luego running, luego alfabetico
        def sort_key(x):
            name_lower = x["name"].lower()
            is_portainer = 0 if "portainer" in name_lower else 1
            is_running   = 0 if x["status"] == "running" else 1
            return (is_portainer, is_running, name_lower)
        containers.sort(key=sort_key)

        # Info global del sistema Docker
        docker_version = "n/a"
        disk_images_mb = disk_volumes_mb = 0
        try:
            info = docker_get("/info")
            docker_version = info.get("ServerVersion", "n/a")
        except Exception:
            pass
        try:
            df = docker_get("/system/df")
            disk_images_mb  = round(sum(i.get("Size", 0) for i in df.get("Images", [])) / 1048576, 1)
            disk_volumes_mb = round(sum(v.get("UsageData", {}).get("Size", 0) for v in df.get("Volumes", [])) / 1048576, 1)
        except Exception:
            pass

        # CPU y RAM totales sumadas de todos los contenedores
        total_cpu_str = "n/a"
        total_mem_mb  = 0
        try:
            cpu_vals = [float(c["cpu"].rstrip("%")) for c in containers if c["cpu"] not in ("n/a", "—")]
            mem_vals = [float(c["mem"].rstrip("MB")) for c in containers if c["mem"] not in ("n/a", "—") and c["mem"].endswith("MB")]
            total_cpu_str = f"{round(sum(cpu_vals), 1)}%"
            total_mem_mb  = round(sum(mem_vals), 1)
        except Exception:
            pass

        docker_stats = {
            "status":          "running" if running_count > 0 else "stopped",
            "total":           len(containers),
            "running":         running_count,
            "version":         docker_version,
            "disk_images_mb":  disk_images_mb,
            "disk_volumes_mb": disk_volumes_mb,
            "total_cpu":       total_cpu_str,
            "total_mem_mb":    total_mem_mb,
            "containers":      containers,
        }
    except Exception:
        docker_stats = {"status": "unknown", "total": 0, "running": 0, "containers": []}

    return jsonify({
        "cpu_pct": cpu_pct,
        "ram_total_mb": ram_total_mb,
        "ram_used_mb": ram_used_mb,
        "ram_avail_mb": ram_avail_mb,
        "ram_pct": ram_pct,
        "temp_c": temp_c,
        "disk_total_gb": disk_total_gb,
        "disk_used_gb": disk_used_gb,
        "disk_free_gb": disk_free_gb,
        "disk_pct": disk_pct,
        "uptime": uptime_human,
        "docker": docker_stats,
    })


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

        # Include system metrics inline for 'system' type monitors
        if cfg.get("type") == "system":
            try:
                from checks import check_system
                sys_result = check_system(timeout=int(cfg.get("timeout", 5)))
                details = sys_result.get("details", {})
                entry["cpu_pct"] = details.get("cpu_pct")
                entry["ram_pct"] = details.get("ram_pct")
                entry["ram_total_mb"] = details.get("ram_total_mb")
                entry["ram_used_mb"] = details.get("ram_used_mb")
                entry["temp_c"] = details.get("temp_c")
                entry["disk_pct"] = details.get("disk_pct")
                entry["disk_total_gb"] = details.get("disk_total_gb")
                entry["disk_used_gb"] = details.get("disk_used_gb")
                entry["uptime"] = details.get("uptime")
            except Exception:
                pass

        # Include docker container info inline for 'docker' type monitors
        if cfg.get("type") == "docker":
            try:
                import socket as _sock
                import json as _json
                container_name = cfg.get("container_name", cfg.get("id", ""))

                def _docker_get(path):
                    s = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
                    s.settimeout(3)
                    s.connect("/var/run/docker.sock")
                    req = f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n"
                    s.sendall(req.encode())
                    resp = b""
                    while True:
                        chunk = s.recv(65536)
                        if not chunk:
                            break
                        resp += chunk
                    s.close()
                    body = resp.split(b"\r\n\r\n", 1)[1]
                    return _json.loads(body)

                info = _docker_get(f"/containers/{container_name}/json")
                state_info = info.get("State", {})
                container_status = state_info.get("Status", "unknown")
                running = state_info.get("Running", False)

                docker_entry = {
                    "container_name": container_name,
                    "container_status": container_status,
                    "running": running,
                }

                if running:
                    try:
                        stats = _docker_get(f"/containers/{container_name}/stats?stream=false")
                        # CPU
                        cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - stats["precpu_stats"]["cpu_usage"]["total_usage"]
                        sys_delta = stats["cpu_stats"].get("system_cpu_usage", 0) - stats["precpu_stats"].get("system_cpu_usage", 0)
                        if sys_delta > 0:
                            docker_entry["cpu"] = f"{min(round((cpu_delta / sys_delta) * 100, 1), 100.0)}%"
                        else:
                            docker_entry["cpu"] = "0%"
                        # Memory
                        mem_usage = stats["memory_stats"].get("usage", 0) - stats["memory_stats"].get("stats", {}).get("cache", 0)
                        docker_entry["mem"] = f"{round(mem_usage / 1048576, 1)}MB"
                        # Network
                        net_rx = sum(v["rx_bytes"] for v in stats.get("networks", {}).values())
                        net_tx = sum(v["tx_bytes"] for v in stats.get("networks", {}).values())
                        docker_entry["net"] = f"↓{round(net_rx/1048576,1)}MB ↑{round(net_tx/1048576,1)}MB"
                    except Exception:
                        docker_entry["cpu"] = "n/a"
                        docker_entry["mem"] = "n/a"
                        docker_entry["net"] = "n/a"

                entry["docker_info"] = docker_entry
            except Exception:
                pass

        result.append(entry)
    result.sort(key=lambda d: d["name"])
    return jsonify({"server_time": now, "devices": result})


# -----------------------------------------------------------------------
# Gestión de dispositivos
# -----------------------------------------------------------------------

@app.route("/api/uptime/<device_id>")
def api_uptime(device_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    """Devuelve segmentos de estado para las últimas 24h.
    Cada segmento tiene: start, end, online (bool).
    """
    now = time.time()
    since = now - 86400  # 24 horas

    history = get_history(device_id, limit=500)
    # history viene ordenado DESC (más reciente primero)

    # Construir segmentos de tiempo
    segments = []
    # Añadir el momento actual como punto de corte
    events = [{"ts": now, "online": None}]  # sentinel
    for h in history:
        if h["ts"] >= since:
            events.append({"ts": h["ts"], "online": bool(h["online"])})
    # Añadir inicio del periodo
    events.append({"ts": since, "online": None})

    # Ordenar ASC
    events.sort(key=lambda x: x["ts"])

    # Obtener estado actual del dispositivo
    statuses = get_all_statuses()
    current = next((s for s in statuses if s["device_id"] == device_id), None)
    current_online = bool(current["online"]) if current else True

    # Reconstruir segmentos
    # Empezamos desde since con el estado más antiguo conocido
    seg_start = since
    # El estado al inicio del periodo es el estado actual si no hay cambios,
    # o el primer evento más antiguo
    if len(events) <= 2:
        # Sin cambios en 24h
        segments.append({"start": since, "end": now, "online": current_online, "pct": 100})
    else:
        # Recorremos los eventos en orden ASC ignorando sentinels
        real_events = [e for e in events if e["online"] is not None]
        # El estado inicial es el opuesto del primer cambio registrado
        # (porque history guarda el estado TRAS el cambio)
        if real_events:
            state = not real_events[0]["online"]  # estado antes del primer cambio
            seg_start = since
            for ev in real_events:
                if ev["ts"] > since:
                    segments.append({
                        "start": seg_start, "end": ev["ts"],
                        "online": state,
                        "pct": (ev["ts"] - seg_start) / 864
                    })
                    seg_start = ev["ts"]
                state = ev["online"]
            # Último segmento hasta ahora
            segments.append({
                "start": seg_start, "end": now,
                "online": state,
                "pct": (now - seg_start) / 864
            })

    # Calcular % uptime total
    online_secs = sum((s["end"] - s["start"]) for s in segments if s["online"])
    uptime_pct = round(online_secs / 864, 1)

    return jsonify({"segments": segments, "uptime_pct": uptime_pct})


@app.route("/api/latency/<device_id>")
def api_latency(device_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    points = get_latency_history(device_id, limit=60)
    return jsonify({"points": points})


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

@app.route("/api/heartbeat/<monitor_id>", methods=["POST"])
def api_heartbeat(monitor_id):
    """Receives a heartbeat ping from an external service."""
    update_heartbeat_ts(monitor_id)
    return jsonify({"ok": True})


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

    # Calculate average uptime across all monitors
    uptimes = []
    for s in statuses:
        device_id = s["device_id"]
        uptime = get_uptime_percentage(device_id, hours=24)
        uptimes.append(uptime)
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

        uptime_24h = get_uptime_percentage(m["id"], hours=24)
        avg_latency = get_avg_latency(m["id"], hours=24)

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
        incidents = get_incidents_for_monitor(m["id"], limit=50)
        for inc in incidents:
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
    # CSP: allow inline styles/scripts (existing app uses them), self for everything else
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
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

if __name__ == "__main__":
    init_db()
    init_push_table()
    seed_devices_from_config()
    run_checks_once()
    start_background_monitor()
    app.run(host="0.0.0.0", port=8088, debug=False)
