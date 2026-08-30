"""Tests del formateo de duraciones."""

from utils import humanize_duration


def test_segundos():
    assert humanize_duration(0) == "0s"
    assert humanize_duration(42) == "42s"
    assert humanize_duration(59.9) == "59s"


def test_minutos():
    assert humanize_duration(60) == "1min"
    assert humanize_duration(3599) == "59min"


def test_horas_y_minutos():
    assert humanize_duration(3600) == "1h"
    assert humanize_duration(3660) == "1h 1min"


def test_dias_omiten_los_minutos():
    """Con días por delante, los minutos son ruido."""
    assert humanize_duration(86400) == "1d"
    assert humanize_duration(90061) == "1d 1h"
