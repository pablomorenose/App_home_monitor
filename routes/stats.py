"""Estadísticas, status page pública y health check."""

import time

from flask import Blueprint, jsonify, request

from config import APP_VERSION, STATUS_PAGE_ENABLED
from db import (get_all_monitors, get_avg_latency, get_history_timeseries,
                get_incidents_bulk, get_incidents_for_monitor, get_monitor,
                get_monitor_statuses, get_uptime_and_latency_bulk,
                get_uptime_percentage)
from routes.common import require_auth

bp = Blueprint("stats", __name__)


@bp.route("/api/monitors/<monitor_id>/stats")
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


@bp.route("/api/monitors/<monitor_id>/history")
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


@bp.route("/api/stats/summary")
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


@bp.route("/api/status-page")
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


_app_start_time = time.time()


@bp.route("/health")
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
