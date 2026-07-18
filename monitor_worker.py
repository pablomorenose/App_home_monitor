"""
Bucle en background que comprueba todos los monitores.

Phase 2:
- Uses MAX_CHECK_WORKERS from config (caps ThreadPoolExecutor)
- Cycle timeout: warns and skips if cycle > 2x CHECK_INTERVAL_SECONDS
- Overlap prevention: Lock prevents concurrent cycles
- Per-monitor check_interval: groups monitors by interval
- State machine integration: process_check_result for each monitor
- Heartbeat support: POST /api/heartbeat/<id> updates last_ping_ts

Backward compatible: run_checks_once() still works for the old flow.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from checks import check_device, check_monitor
from config import CHECK_INTERVAL_SECONDS, MAX_CHECK_WORKERS, PUSH_ENABLED
from db import (
    get_all_devices,
    get_all_statuses,
    update_status,
    cleanup_old_history,
    get_all_monitors,
    get_monitor_statuses,
    update_monitor_status,
)
from state_machine import process_check_result

logger = logging.getLogger("monitor_worker")

# Lock to prevent overlapping cycles
_cycle_lock = threading.Lock()
_last_cleanup_ts = 0.0
# Track last cycle time per interval group
_last_cycle_by_interval: dict[int, float] = {}


def run_checks_once():
    """
    Backward-compatible: check all devices using old tuple-based flow.
    Used by /api/force-check and initial startup.
    """
    devices = get_all_devices()
    prev = {s["device_id"]: bool(s["online"]) for s in get_all_statuses()}

    def _check(device):
        online, info, response_ms = check_device(device)
        if device["type"] == "ha_switch":
            if online:
                update_status(device["id"], device["name"], True, None,
                              response_ms, switch_state=info)
                return device, True, None
            update_status(device["id"], device["name"], False, info,
                          response_ms, switch_state=None)
            return device, False, info
        update_status(device["id"], device["name"], online, info, response_ms)
        return device, online, info

    workers = min(MAX_CHECK_WORKERS, len(devices) or 1)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_check, d): d for d in devices}
        for future in as_completed(futures):
            try:
                device, online, error = future.result()
                in_maintenance = device.get("maintenance_until", 0) > time.time()
                if device["id"] in prev and prev[device["id"]] != online and not in_maintenance:
                    _notify_change(device["name"], online, error)
            except Exception as e:
                logger.error("Error comprobando dispositivo: %s", e)


def run_monitor_cycle():
    """
    Phase 2 monitor cycle with state machine integration.
    Groups monitors by check_interval and runs the appropriate ones each cycle.
    """
    if not _cycle_lock.acquire(blocking=False):
        logger.warning("Ciclo anterior todavía en ejecución, saltando.")
        return

    try:
        cycle_start = time.time()
        monitors = get_all_monitors()
        statuses = {s["device_id"]: s for s in get_monitor_statuses()}

        # Filter monitors that are due for a check based on their check_interval
        now = time.time()
        due_monitors = []
        for m in monitors:
            if not m.get("enabled", 1):
                continue
            interval = int(m.get("check_interval", CHECK_INTERVAL_SECONDS))
            monitor_id = m["id"]
            status = statuses.get(monitor_id, {})
            last_check = float(status.get("last_check_ts", 0))
            if (now - last_check) >= interval:
                due_monitors.append(m)

        if not due_monitors:
            return

        # Build lookup for parent status (for depends_on)
        status_by_id = statuses

        def _check_one(monitor):
            monitor_id = monitor["id"]
            current_status = status_by_id.get(monitor_id, {})

            # For heartbeat type, inject last_check_ts so the check knows when
            # the last ping was received
            if monitor.get("type") == "heartbeat":
                monitor["_last_check_ts"] = float(current_status.get("last_check_ts", 0))

            # Run the actual check
            result = check_monitor(monitor)

            # Check dependency: if depends_on is set, check parent state
            depends_on = monitor.get("depends_on", "")
            if depends_on:
                parent_status = status_by_id.get(depends_on, {})
                if parent_status.get("state") == "down":
                    result["details"]["_parent_down"] = True

            # Process through state machine
            new_state_info = process_check_result(monitor, result, current_status)

            # Determine notification timestamp
            notification_ts = float(current_status.get("last_notification_ts", 0))
            if new_state_info["should_notify_down"] or new_state_info["should_notify_recovery"]:
                notification_ts = time.time()

            # Determine switch_state for HA switches
            switch_state = None
            if monitor.get("type") == "ha_switch":
                switch_state = result.get("details", {}).get("switch_state")

            # Update DB
            online = result["state"] in ("up", "degraded")
            error_msg = None if online else result["message"]
            update_monitor_status(
                device_id=monitor_id,
                name=monitor["name"],
                online=online,
                error=error_msg,
                response_ms=result["latency_ms"],
                switch_state=switch_state,
                state=new_state_info["state"],
                consecutive_failures=new_state_info["consecutive_failures"],
                consecutive_successes=new_state_info["consecutive_successes"],
                last_notification_ts=notification_ts,
                incident_id=new_state_info["incident_id"],
            )

            return monitor, new_state_info, result

        # Execute checks in parallel
        workers = min(MAX_CHECK_WORKERS, len(due_monitors))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_check_one, m): m for m in due_monitors}
            for future in as_completed(futures):
                try:
                    monitor, state_info, result = future.result()
                    # Send notifications if needed
                    if PUSH_ENABLED:
                        if state_info["should_notify_down"]:
                            reason = result.get("message", "")
                            _notify_change(monitor["name"], False, reason)
                        elif state_info["should_notify_recovery"]:
                            _notify_change(monitor["name"], True, None)
                except Exception as e:
                    logger.error("Error procesando monitor: %s", e)

        # Check cycle duration
        cycle_duration = time.time() - cycle_start
        max_duration = CHECK_INTERVAL_SECONDS * 2
        if cycle_duration > max_duration:
            logger.warning(
                "Ciclo de monitoreo tardó %.1fs (máximo esperado: %ds). "
                "Considere aumentar MAX_CHECK_WORKERS o CHECK_INTERVAL_SECONDS.",
                cycle_duration, max_duration
            )
    finally:
        _cycle_lock.release()


def _notify_change(name: str, online: bool, error: str | None):
    """Send push notification for state change."""
    try:
        from notifications import send_push
        if online:
            send_push(
                title="✅ Dispositivo recuperado",
                body=f"{name} vuelve a estar en línea",
            )
        else:
            reason = f" — {error}" if error else ""
            send_push(
                title="🔴 Dispositivo caído",
                body=f"{name} no responde{reason}",
            )
    except Exception as e:
        logger.error("No se pudo enviar notificación: %s", e)


def _loop():
    """Main monitoring loop."""
    global _last_cleanup_ts
    while True:
        try:
            run_monitor_cycle()
            # Daily cleanup
            if time.time() - _last_cleanup_ts > 86400:
                cleanup_old_history(days=30)
                _last_cleanup_ts = time.time()
        except Exception as e:
            logger.error("Error en el ciclo de monitoreo: %s", e)
        time.sleep(CHECK_INTERVAL_SECONDS)


def start_background_monitor():
    """Start the background monitoring thread."""
    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    logger.info("Monitor background thread started (interval=%ds, workers=%d)",
                CHECK_INTERVAL_SECONDS, MAX_CHECK_WORKERS)
    return thread
