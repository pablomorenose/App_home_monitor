"""Tests de los helpers puros de db.py (no tocan la base de datos)."""

import time

from db import _bucket_state, _incidents_from_rows


# ── _bucket_state ──────────────────────────────────────────────────

def test_bucket_usa_el_state_cuando_es_informativo():
    assert _bucket_state(1, "degraded") == "degraded"
    assert _bucket_state(0, "maintenance") == "maintenance"


def test_bucket_cae_a_online_si_el_state_no_sirve():
    assert _bucket_state(1, None) == "up"
    assert _bucket_state(0, "") == "down"
    assert _bucket_state(0, "vete-a-saber") == "down"


# ── _incidents_from_rows ───────────────────────────────────────────

def row(ts, state, message=""):
    return {"ts": ts, "state": state, "message": message,
            "online": 0 if state == "down" else 1}


def test_sin_caidas_no_hay_incidencias():
    rows = [row(t, "up") for t in (100, 200, 300)]
    assert _incidents_from_rows(rows) == []


def test_incidencia_cerrada_mide_su_duracion():
    rows = [row(100, "up"), row(200, "down", "timeout"), row(500, "up")]
    incidents = _incidents_from_rows(rows)
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc["start_ts"] == 200
    assert inc["end_ts"] == 500
    assert inc["duration_seconds"] == 300
    assert inc["message"] == "timeout"


def test_varios_checks_caidos_son_una_sola_incidencia():
    rows = [row(100, "up")] + [row(t, "down") for t in (200, 215, 230)] + [row(300, "up")]
    incidents = _incidents_from_rows(rows)
    assert len(incidents) == 1
    assert incidents[0]["start_ts"] == 200


def test_incidencia_abierta_no_tiene_fin():
    now = time.time()
    rows = [row(now - 300, "up"), row(now - 100, "down", "sin respuesta")]
    inc = _incidents_from_rows(rows)[0]
    assert inc["end_ts"] is None
    assert 95 < inc["duration_seconds"] < 200


def test_devuelve_la_mas_reciente_primero():
    rows = [row(100, "down"), row(200, "up"), row(300, "down"), row(400, "up")]
    incidents = _incidents_from_rows(rows)
    assert [i["start_ts"] for i in incidents] == [300, 100]


def test_degraded_cuenta_como_recuperacion():
    """Solo 'down' abre incidencia; degraded no es una caída."""
    rows = [row(100, "down"), row(200, "degraded")]
    incidents = _incidents_from_rows(rows)
    assert len(incidents) == 1
    assert incidents[0]["end_ts"] == 200
