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


@app.route("/api/status")
def api_status():
    if require_auth():
        return jsonify({"error": "No autorizado"}), 401
    now = time.time()
    statuses = get_all_statuses()
    result = []
    for s in statuses:
        since_seconds = now - s["last_change_ts"]
        result.append({
            "id": s["device_id"],
            "name": s["name"],
            "online": bool(s["online"]),
            "since_seconds": since_seconds,
            "since_human": humanize_duration(since_seconds),
            "last_check_seconds_ago": now - s["last_check_ts"],
            "last_error": s["last_error"],
            "response_ms": s.get("response_ms"),
        })
    result.sort(key=lambda d: d["name"])
    return jsonify({"server_time": now, "devices": result})


# -----------------------------------------------------------------------
# Gestión de dispositivos
# -----------------------------------------------------------------------

@app.route("/api/uptime/<device_id>")
def api_uptime(device_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    Devuelve segmentos de estado para las últimas 24h.
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
