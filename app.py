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

import os
import time

from flask import Flask, jsonify, render_template, request, session, redirect, url_for

from config import VAPID_PUBLIC_KEY
from db import (delete_device, get_all_devices, get_all_statuses, get_history,
                init_db, seed_devices_from_config, upsert_device, get_latency_history,
                get_incidents)
from monitor_worker import run_checks_once, start_background_monitor
from notifications import delete_subscription, init_push_table, save_subscription

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "homem-dev-secret-change-me")

# Contraseña de acceso (vacía = sin protección)
ACCESS_PASSWORD = os.environ.get("ACCESS_PASSWORD", "")


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
# Auth
# -----------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == ACCESS_PASSWORD:
            session["authenticated"] = True
            return redirect(url_for("index"))
        error = "Contraseña incorrecta"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


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
def api_force_check():
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    from monitor_worker import run_checks_once
    import threading
    threading.Thread(target=run_checks_once, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/devices/<device_id>/maintenance", methods=["POST"])
def api_maintenance(device_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    hours = float(request.json.get("hours", 0))
    from db import set_maintenance
    set_maintenance(device_id, hours)
    return jsonify({"ok": True})


@app.route("/api/toggle/<device_id>", methods=["POST"])
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

    # --- Docker container stats ---
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
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp += chunk
            s.close()
            body = resp.split(b"\r\n\r\n", 1)[1]
            return _json.loads(body)

        # Inspect container
        info = docker_get("/containers/home-monitor/json")
        status = info.get("State", {}).get("Status", "unknown")

        # Stats (no-stream via ?stream=false)
        def docker_get_stats(path):
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

        st = docker_get_stats("/containers/home-monitor/stats?stream=false")

        # CPU %
        cpu_delta = st["cpu_stats"]["cpu_usage"]["total_usage"] - st["precpu_stats"]["cpu_usage"]["total_usage"]
        sys_delta  = st["cpu_stats"]["system_cpu_usage"] - st["precpu_stats"]["system_cpu_usage"]
        num_cpus   = st["cpu_stats"].get("online_cpus", len(st["cpu_stats"]["cpu_usage"].get("percpu_usage", [1])))
        docker_cpu = round(cpu_delta / sys_delta * num_cpus * 100, 1) if sys_delta > 0 else 0.0

        # RAM — fallback a /proc si cgroups no disponibles
        mem_usage_raw = st["memory_stats"].get("usage")
        if mem_usage_raw:
            mem_usage = mem_usage_raw - st["memory_stats"].get("stats", {}).get("cache", 0)
            mem_limit = st["memory_stats"]["limit"]
            docker_mem_mb    = round(mem_usage / 1048576, 1)
            docker_mem_limit = round(mem_limit  / 1048576, 1)
            mem_str = f"{docker_mem_mb}MB / {docker_mem_limit}MB"
        else:
            # Raspberry Pi sin memory cgroup: leer desde /proc del proceso principal
            pid = info.get("State", {}).get("Pid", 0)
            try:
                with open(f"/proc/{pid}/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            rss_kb = int(line.split()[1])
                            mem_str = f"{round(rss_kb/1024,1)}MB"
                            break
                    else:
                        mem_str = "n/a"
            except Exception:
                mem_str = "n/a"

        # Network
        net_rx = sum(v["rx_bytes"] for v in st.get("networks", {}).values())
        net_tx = sum(v["tx_bytes"] for v in st.get("networks", {}).values())
        def fmt_bytes(b):
            return f"{round(b/1048576,1)}MB" if b >= 1048576 else f"{round(b/1024,1)}kB"

        docker_stats = {
            "status": status,
            "cpu": f"{docker_cpu}%",
            "mem": mem_str,
            "net": f"↓{fmt_bytes(net_rx)} ↑{fmt_bytes(net_tx)}",
        }
    except Exception:
        docker_stats = {"status": "unknown"}

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
        result.append({
            "id": s["device_id"],
            "name": s["name"],
            "type": cfg.get("type"),
            "online": bool(s["online"]),
            "since_seconds": since_seconds,
            "since_human": humanize_duration(since_seconds),
            "last_check_seconds_ago": now - s["last_check_ts"],
            "last_error": s["last_error"],
            "response_ms": s.get("response_ms"),
            "maintenance_until": maintenance_until,
            "in_maintenance": maintenance_until > now,
            "switch_state": s.get("switch_state"),
        })
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
def add_device():
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    data = request.get_json()
    if not data or not all(k in data for k in ("id", "name", "type")):
        return jsonify({"error": "Faltan campos obligatorios (id, name, type)"}), 400
    upsert_device(data)
    return jsonify({"ok": True}), 201


@app.route("/api/devices/<device_id>", methods=["PUT"])
def edit_device(device_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    data = request.get_json()
    if not data:
        return jsonify({"error": "Sin datos"}), 400
    data["id"] = device_id
    upsert_device(data)
    return jsonify({"ok": True})


@app.route("/api/devices/<device_id>", methods=["DELETE"])
def remove_device(device_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    delete_device(device_id)
    return jsonify({"ok": True})


# -----------------------------------------------------------------------
# Push notifications
# -----------------------------------------------------------------------

@app.route("/api/vapid-key")
def vapid_key():
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})


@app.route("/api/subscribe", methods=["POST"])
def subscribe():
    sub = request.get_json()
    if not sub or "endpoint" not in sub:
        return jsonify({"error": "Suscripción inválida"}), 400
    save_subscription(sub)
    return jsonify({"ok": True}), 201


@app.route("/api/unsubscribe", methods=["POST"])
def unsubscribe():
    data = request.get_json()
    if data and "endpoint" in data:
        delete_subscription(data["endpoint"])
    return jsonify({"ok": True})


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
