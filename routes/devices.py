"""API de dispositivos: estado, historial corto y acciones sobre uno."""

import json
import threading
import time
from urllib.parse import urlparse

import requests
from flask import Blueprint, jsonify, request

from csrf import csrf_protect
from db import (delete_device, get_all_devices, get_all_statuses,
                get_heartbeat_buckets, get_incidents, get_latency_history,
                get_uptime_percentage, upsert_device)
from monitor_worker import run_monitor_cycle
from routes.common import require_auth
from utils import humanize_duration

bp = Blueprint("devices", __name__)


# ─── Caché de métricas, refrescada fuera de la petición ───
# Recolectar métricas cuesta segundos: los stats de Docker rondan los 2s
# incluso en paralelo, y un agente remoto apagado agota su timeout de 8s.
# /api/status se pide cada 15s desde cada pestaña abierta, así que hacerlo
# dentro de la petición dejaba el dashboard esperando en CADA refresco.
# Aquí se sirve siempre el último valor conocido y se refresca en un hilo.
_cache: dict = {}                 # clave -> {"value": ..., "ts": float}
_cache_lock = threading.Lock()
_refreshing: set = set()          # claves con un refresco ya en marcha

_SYSTEM_TTL = 10
_DOCKER_TTL = 20
_REMOTE_TTL = 15


def _refresh_entry(key, producer):
    value = None
    try:
        value = producer()
    except Exception:
        pass
    with _cache_lock:
        entry = _cache.setdefault(key, {"value": None, "ts": 0})
        if value is not None:
            entry["value"] = value
        # Se marca el ts aunque falle, para no reintentar en cada petición.
        entry["ts"] = time.time()
        _refreshing.discard(key)


def _cached(key, ttl, producer, default):
    """Devuelve el valor cacheado al instante y lo refresca en segundo plano."""
    now = time.time()
    with _cache_lock:
        entry = _cache.get(key)
        stale = entry is None or (now - entry["ts"]) >= ttl
        launch = stale and key not in _refreshing
        if launch:
            _refreshing.add(key)
        value = entry["value"] if entry and entry["value"] is not None else default
    if launch:
        threading.Thread(target=_refresh_entry, args=(key, producer),
                         daemon=True).start()
    return value


_METRIC_KEYS = ("cpu_pct", "ram_pct", "ram_total_mb", "ram_used_mb", "temp_c",
                "disk_pct", "disk_total_gb", "disk_used_gb", "uptime")


def _get_cached_system_metrics():
    def produce():
        from checks import check_system
        details = check_system(timeout=5).get("details", {})
        return {k: details.get(k) for k in _METRIC_KEYS}
    return _cached("system", _SYSTEM_TTL, produce, {})


def _get_cached_docker_containers():
    def produce():
        from checks import get_all_docker_containers
        return get_all_docker_containers(timeout=5).get("containers", [])
    return _cached("docker", _DOCKER_TTL, produce, [])


def _get_cached_remote_system_metrics(device_id: str, url: str, timeout: int = 8) -> dict:
    """Métricas de una máquina remota vía el endpoint HTTP de metrics_agent."""
    def produce():
        from checks import check_remote_system
        details = check_remote_system(url=url, timeout=timeout).get("details", {})
        return {k: details.get(k) for k in _METRIC_KEYS}
    return _cached(f"remote:{device_id}", _REMOTE_TTL, produce, {})


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
            # Para el diagrama de dependencias (/diagrama)
            "depends_on": cfg.get("depends_on", "") or "",
        }

        # Include system metrics inline for 'system' type monitors (cached)
        if cfg.get("type") == "system":
            try:
                entry.update(_get_cached_system_metrics())
            except Exception:
                pass

        # Include remote system metrics inline for 'remote_system' type monitors
        if cfg.get("type") == "remote_system":
            try:
                url = cfg.get("url", "")
                if not url:
                    url = json.loads(cfg.get("config_json") or "{}").get("url", "")
                if url:
                    entry.update(_get_cached_remote_system_metrics(
                        device_id=cfg["id"],
                        url=url,
                        timeout=int(cfg.get("timeout", 8))
                    ))
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


@bp.route("/api/devices/<device_id>/poweroff", methods=["POST"])
@csrf_protect
def poweroff_device(device_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    devices = {d["id"]: d for d in get_all_devices()}
    device = devices.get(device_id)
    if not device:
        return jsonify({"error": "Dispositivo no encontrado"}), 404
    if device.get("type") != "remote_system":
        return jsonify({"error": "Solo se puede apagar dispositivos de tipo remote_system"}), 400
    url = device.get("url", "")
    if not url:
        return jsonify({"error": "URL del agente no configurada"}), 400
    # Extraer host del agente
    parsed = urlparse(url)
    agent_base = f"{parsed.scheme}://{parsed.netloc}"
    try:
        resp = requests.post(f"{agent_base}/poweroff", timeout=5)
        if resp.status_code == 200:
            return jsonify({"ok": True})
        return jsonify({"error": f"El agente respondió {resp.status_code}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/devices/<device_id>", methods=["DELETE"])
@csrf_protect
def remove_device(device_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    delete_device(device_id)
    return jsonify({"ok": True})
