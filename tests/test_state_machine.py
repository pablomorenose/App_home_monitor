"""Tests de la máquina de estados.

process_check_result() es pura (sin BD ni red), así que se puede fijar todo
el estado de entrada y comprobar la transición exacta.
"""

import time

from state_machine import NOTIFICATION_COOLDOWN, process_check_result


def monitor(**kwargs):
    base = {"id": "m1", "name": "M1", "max_retries": 3, "recovery_threshold": 1,
            "maintenance_until": 0, "depends_on": ""}
    base.update(kwargs)
    return base


def status(**kwargs):
    base = {"state": "pending", "consecutive_failures": 0,
            "consecutive_successes": 0, "last_notification_ts": 0,
            "incident_id": None}
    base.update(kwargs)
    return base


def result(state, **details):
    return {"state": state, "message": "msg", "latency_ms": 10, "details": details}


# ── Camino feliz ───────────────────────────────────────────────────

def test_pending_sube_a_up_al_primer_exito():
    out = process_check_result(monitor(), result("up"), status())
    assert out["state"] == "up"
    assert out["consecutive_successes"] == 1
    assert out["should_notify_recovery"] is False  # no venía de down


def test_up_se_mantiene_up():
    out = process_check_result(monitor(), result("up"),
                               status(state="up", consecutive_successes=5))
    assert out["state"] == "up"
    assert out["consecutive_failures"] == 0


def test_recovery_threshold_retiene_la_subida():
    m = monitor(recovery_threshold=3)
    out = process_check_result(m, result("up"), status(state="down"))
    assert out["state"] == "down", "un solo éxito no debe recuperar"
    out = process_check_result(m, result("up"),
                               status(state="down", consecutive_successes=2))
    assert out["state"] == "up"


# ── Caídas ─────────────────────────────────────────────────────────

def test_no_baja_a_down_antes_de_max_retries():
    m = monitor(max_retries=3)
    out = process_check_result(m, result("down"),
                               status(state="up", consecutive_failures=1))
    assert out["state"] == "up"
    assert out["consecutive_failures"] == 2
    assert out["should_notify_down"] is False


def test_baja_a_down_al_alcanzar_max_retries():
    m = monitor(max_retries=3)
    out = process_check_result(m, result("down"),
                               status(state="up", consecutive_failures=2))
    assert out["state"] == "down"
    assert out["should_notify_down"] is True
    assert out["incident_id"], "una caída nueva debe abrir un incidente"


def test_down_no_reenvia_aviso_dentro_del_cooldown():
    m = monitor(max_retries=1)
    out = process_check_result(
        m, result("down"),
        status(state="down", consecutive_failures=5,
               last_notification_ts=time.time() - 10, incident_id="inc-1"))
    assert out["state"] == "down"
    assert out["should_notify_down"] is False
    assert out["incident_id"] == "inc-1", "el incidente sigue siendo el mismo"


def test_caida_nueva_respeta_el_cooldown():
    m = monitor(max_retries=1)
    out = process_check_result(
        m, result("down"),
        status(state="up", last_notification_ts=time.time() - 10))
    assert out["state"] == "down"
    assert out["should_notify_down"] is False


def test_caida_nueva_notifica_pasado_el_cooldown():
    m = monitor(max_retries=1)
    out = process_check_result(
        m, result("down"),
        status(state="up", last_notification_ts=time.time() - NOTIFICATION_COOLDOWN - 1))
    assert out["should_notify_down"] is True


# ── Recuperación ───────────────────────────────────────────────────

def test_recuperacion_notifica_y_cierra_el_incidente():
    out = process_check_result(monitor(), result("up"),
                               status(state="down", incident_id="inc-1"))
    assert out["state"] == "up"
    assert out["should_notify_recovery"] is True
    assert out["incident_id"] is None


def test_recuperacion_rapida_no_se_silencia_por_el_cooldown():
    """Una caída y su recuperación en menos de 5 min: el aviso de vuelta debe
    llegar igualmente, o el usuario se queda con un DOWN sin cierre."""
    out = process_check_result(
        monitor(), result("up"),
        status(state="down", incident_id="inc-1",
               last_notification_ts=time.time() - 5))
    assert out["should_notify_recovery"] is True


def test_pending_a_up_no_cuenta_como_recuperacion():
    out = process_check_result(monitor(), result("up"), status(state="pending"))
    assert out["should_notify_recovery"] is False


# ── Degradado ──────────────────────────────────────────────────────

def test_degradado_desde_up_avisa():
    out = process_check_result(monitor(), result("degraded"), status(state="up"))
    assert out["state"] == "degraded"
    assert out["should_notify_down"] is True


def test_degradado_sostenido_no_repite_aviso():
    out = process_check_result(monitor(), result("degraded"),
                               status(state="degraded"))
    assert out["state"] == "degraded"
    assert out["should_notify_down"] is False


# ── Mantenimiento y dependencias ───────────────────────────────────

def test_mantenimiento_silencia_las_alertas():
    m = monitor(maintenance_until=time.time() + 3600, max_retries=1)
    out = process_check_result(m, result("down"), status(state="up"))
    assert out["state"] == "maintenance"
    assert out["should_notify_down"] is False
    assert out["should_notify_recovery"] is False


def test_mantenimiento_caducado_ya_no_aplica():
    m = monitor(maintenance_until=time.time() - 1, max_retries=1)
    out = process_check_result(m, result("down"), status(state="up"))
    assert out["state"] == "down"


def test_padre_caido_silencia_al_hijo():
    m = monitor(depends_on="padre", max_retries=1)
    out = process_check_result(m, result("down", _parent_down=True),
                               status(state="up"))
    assert out["should_notify_down"] is False
    assert out["state"] == "up", "no se marca down por culpa del padre"


def test_depends_on_sin_padre_caido_se_comporta_normal():
    m = monitor(depends_on="padre", max_retries=1)
    out = process_check_result(m, result("down"), status(state="up"))
    assert out["state"] == "down"
