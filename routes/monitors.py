"""API de monitores: CRUD, heartbeat, operaciones en lote y export/import."""

import hashlib
import hmac
import time

from flask import Blueprint, jsonify, request

from config import SECRET_KEY
from csrf import csrf_protect, secure_eq
from db import (delete_device, get_all_monitors, get_monitor,
                get_monitor_statuses, update_heartbeat_ts, upsert_monitor)
from routes.common import require_auth
from validators import validate_monitor

bp = Blueprint("monitors", __name__)


@bp.route("/api/monitors", methods=["GET"])
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


@bp.route("/api/monitors/<monitor_id>", methods=["GET"])
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


@bp.route("/api/monitors", methods=["POST"])
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


@bp.route("/api/monitors/<monitor_id>", methods=["PUT"])
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


@bp.route("/api/monitors/<monitor_id>", methods=["DELETE"])
@csrf_protect
def remove_monitor(monitor_id):
    if require_auth(): return jsonify({"error": "No autorizado"}), 401
    delete_device(monitor_id)
    return jsonify({"ok": True})


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


@bp.route("/api/heartbeat/<monitor_id>", methods=["POST"])
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


@bp.route("/api/monitors/<monitor_id>/heartbeat-url")
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


@bp.route("/api/monitors/bulk-pause", methods=["POST"])
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


@bp.route("/api/monitors/bulk-resume", methods=["POST"])
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


@bp.route("/api/monitors/bulk-delete", methods=["POST"])
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


@bp.route("/api/groups")
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


@bp.route("/api/export")
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


@bp.route("/api/import", methods=["POST"])
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
