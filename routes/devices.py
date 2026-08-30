"""API de dispositivos: estado, historial corto y acciones sobre uno."""

import time

from flask import Blueprint, jsonify, request

from csrf import csrf_protect
from db import (delete_device, get_all_devices, get_all_statuses,
                get_heartbeat_buckets, get_incidents, get_latency_history,
                get_uptime_percentage, upsert_device)
from monitor_worker import run_monitor_cycle
from routes.common import require_auth
from utils import humanize_duration

bp = Blueprint("devices", __name__)


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


@bp.route("/api/force-check", methods=["POST"])
@csrf_protect
def api_force_check():
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    import threading
    threading.Thread(target=run_monitor_cycle, kwargs={"force": True},
                     daemon=True).start()
    return jsonify({"ok": True})


@bp.route("/api/devices/<device_id>/maintenance", methods=["POST"])
@csrf_protect
def api_maintenance(device_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    hours = float(request.json.get("hours", 0))
    from db import set_maintenance
    set_maintenance(device_id, hours)
    return jsonify({"ok": True})


@bp.route("/api/toggle/<device_id>", methods=["POST"])
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


@bp.route("/api/ha-sensors")
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


@bp.route("/api/status")
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


@bp.route("/api/uptime/<device_id>")
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


@bp.route("/api/latency/<device_id>")
def api_latency(device_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    points = get_latency_history(device_id, limit=60)
    return jsonify({"points": points})


@bp.route("/api/heartbeats/<device_id>")
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


@bp.route("/api/incidents")
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


@bp.route("/api/devices", methods=["GET"])
def list_devices():
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    return jsonify(get_all_devices())


@bp.route("/api/devices", methods=["POST"])
@csrf_protect
def add_device():
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    data = request.get_json()
    if not data or not all(k in data for k in ("id", "name", "type")):
        return jsonify({"error": "Faltan campos obligatorios (id, name, type)"}), 400
    upsert_device(data)
    return jsonify({"ok": True}), 201


@bp.route("/api/devices/<device_id>", methods=["PUT"])
@csrf_protect
def edit_device(device_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    data = request.get_json()
    if not data:
        return jsonify({"error": "Sin datos"}), 400
    data["id"] = device_id
    upsert_device(data)
    return jsonify({"ok": True})


@bp.route("/api/devices/<device_id>", methods=["DELETE"])
@csrf_protect
def remove_device(device_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    delete_device(device_id)
    return jsonify({"ok": True})
