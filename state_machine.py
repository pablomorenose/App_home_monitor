"""
Máquina de estados para monitores.

Procesa resultados de checks y determina transiciones de estado,
generando señales de notificación según las reglas configuradas.

Estados: pending, up, down, degraded, maintenance
"""

import time
import uuid


# Default cooldown between notifications (seconds)
NOTIFICATION_COOLDOWN = 300


def process_check_result(monitor: dict, check_result: dict,
                         current_status: dict) -> dict:
    """
    Given a monitor config, a check result, and the current persisted status,
    returns the new state and whether to notify.

    Args:
        monitor: Monitor configuration dict (from DB, includes max_retries,
                 recovery_threshold, maintenance_until, depends_on, etc.)
        check_result: Normalized check result dict with state, message, latency_ms, details
        current_status: Current persisted status dict from device_status table
                       (state, consecutive_failures, consecutive_successes,
                        last_notification_ts, incident_id)

    Returns: {
        'state': 'pending'|'up'|'down'|'degraded'|'maintenance',
        'should_notify_down': bool,
        'should_notify_recovery': bool,
        'consecutive_failures': int,
        'consecutive_successes': int,
        'incident_id': str|None,
    }
    """
    now = time.time()

    # Extract monitor config
    max_retries = int(monitor.get("max_retries", 3))
    recovery_threshold = int(monitor.get("recovery_threshold", 1))
    maintenance_until = float(monitor.get("maintenance_until", 0))
    depends_on = monitor.get("depends_on", "")

    # Extract current state
    prev_state = current_status.get("state", "pending")
    prev_failures = int(current_status.get("consecutive_failures", 0))
    prev_successes = int(current_status.get("consecutive_successes", 0))
    prev_notification_ts = float(current_status.get("last_notification_ts", 0))
    prev_incident_id = current_status.get("incident_id")

    # Check result state
    result_state = check_result.get("state", "down")  # up, down, degraded

    # ─── Maintenance mode ───
    if maintenance_until > now:
        return {
            "state": "maintenance",
            "should_notify_down": False,
            "should_notify_recovery": False,
            "consecutive_failures": prev_failures if result_state == "down" else 0,
            "consecutive_successes": prev_successes if result_state == "up" else 0,
            "incident_id": prev_incident_id,
        }

    # ─── Dependency check ───
    # If depends_on is set and parent is DOWN, suppress notifications
    # The actual parent status check is done by the caller (worker),
    # which sets _parent_down=True in check_result.details if applicable
    parent_down = check_result.get("details", {}).get("_parent_down", False)
    if depends_on and parent_down:
        return {
            "state": prev_state if prev_state != "maintenance" else "pending",
            "should_notify_down": False,
            "should_notify_recovery": False,
            "consecutive_failures": prev_failures + (1 if result_state == "down" else 0),
            "consecutive_successes": prev_successes + (1 if result_state == "up" else 0),
            "incident_id": prev_incident_id,
        }

    # ─── Process result ───
    should_notify_down = False
    should_notify_recovery = False
    new_state = prev_state
    new_failures = prev_failures
    new_successes = prev_successes
    incident_id = prev_incident_id

    if result_state == "down":
        new_failures = prev_failures + 1
        new_successes = 0

        # Transition to DOWN only after max_retries consecutive failures
        if new_failures >= max_retries:
            if prev_state != "down":
                new_state = "down"
                incident_id = str(uuid.uuid4())
                # Check cooldown before notifying
                if _can_notify(prev_notification_ts, now):
                    should_notify_down = True
            else:
                # Already down - re-notify only after cooldown
                new_state = "down"
        else:
            # Not enough failures yet — stay in current state or pending
            if prev_state == "pending":
                new_state = "pending"
            # If previously up/degraded, stay there until threshold hit

    elif result_state == "up":
        new_successes = prev_successes + 1
        new_failures = 0

        if prev_state in ("down", "pending", "degraded"):
            # Recovery: need consecutive_successes >= recovery_threshold
            if new_successes >= recovery_threshold:
                was_down = prev_state == "down"
                new_state = "up"
                if was_down and _can_notify(prev_notification_ts, now):
                    should_notify_recovery = True
                # Clear incident on recovery
                incident_id = None
            else:
                # Not enough successes yet
                new_state = prev_state
        else:
            new_state = "up"

    elif result_state == "degraded":
        new_successes = 0
        new_failures = 0
        new_state = "degraded"
        # Don't generate incident for degraded, but notify if transitioning
        if prev_state == "up" and _can_notify(prev_notification_ts, now):
            should_notify_down = True
        # Keep incident_id if was down before
        if prev_state != "down":
            incident_id = prev_incident_id

    return {
        "state": new_state,
        "should_notify_down": should_notify_down,
        "should_notify_recovery": should_notify_recovery,
        "consecutive_failures": new_failures,
        "consecutive_successes": new_successes,
        "incident_id": incident_id,
    }


def _can_notify(last_notification_ts: float, now: float) -> bool:
    """Check if enough time has passed since last notification (cooldown)."""
    if last_notification_ts == 0:
        return True
    return (now - last_notification_ts) >= NOTIFICATION_COOLDOWN
