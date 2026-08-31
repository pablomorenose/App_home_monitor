"""La caché de métricas nunca debe hacer esperar a /api/status.

Recoger stats de Docker o hablar con un agente remoto cuesta segundos; el
dashboard pide /api/status cada 15s. Estos tests fijan la garantía de que
la petición se sirve al instante y el trabajo caro ocurre en otro hilo.
"""

import time

from routes.devices import _cache, _cached, _refreshing


def clear():
    _cache.clear()
    _refreshing.clear()


def wait_until(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_la_primera_llamada_devuelve_el_default_sin_esperar():
    clear()
    started = time.monotonic()
    value = _cached("k", 10, lambda: (time.sleep(1.0), "tarde")[1], "default")
    elapsed = time.monotonic() - started
    assert value == "default"
    assert elapsed < 0.2, f"la petición esperó {elapsed:.2f}s al productor"


def test_el_valor_aparece_cuando_termina_el_refresco():
    clear()
    _cached("k", 10, lambda: "fresco", None)
    assert wait_until(lambda: _cached("k", 10, lambda: "fresco", None) == "fresco")


def test_el_valor_cacheado_se_sirve_sin_relanzar_el_productor():
    clear()
    calls = []
    producer = lambda: (calls.append(1), "v")[1]
    _cached("k", 10, producer, None)
    assert wait_until(lambda: len(calls) == 1)
    for _ in range(5):
        assert _cached("k", 10, producer, None) == "v"
    assert len(calls) == 1, "un valor fresco no debe reproducirse"


def test_al_caducar_se_sirve_el_valor_viejo_mientras_se_refresca():
    clear()
    _cached("k", 10, lambda: "viejo", None)
    assert wait_until(lambda: _cache.get("k", {}).get("value") == "viejo")
    _cache["k"]["ts"] = 0  # forzar caducidad

    started = time.monotonic()
    value = _cached("k", 10, lambda: (time.sleep(0.8), "nuevo")[1], None)
    elapsed = time.monotonic() - started
    assert value == "viejo", "debe servir lo anterior, no bloquear ni devolver vacío"
    assert elapsed < 0.2
    assert wait_until(lambda: _cache["k"]["value"] == "nuevo")


def test_un_productor_que_falla_no_borra_el_valor_ni_reintenta_en_bucle():
    clear()
    _cached("k", 10, lambda: "bueno", None)
    assert wait_until(lambda: _cache.get("k", {}).get("value") == "bueno")
    _cache["k"]["ts"] = 0

    def explota():
        raise RuntimeError("agente caído")

    assert _cached("k", 10, explota, None) == "bueno"
    assert wait_until(lambda: "k" not in _refreshing)
    # el ts se refresca aunque falle: la siguiente petición no relanza nada
    assert _cache["k"]["value"] == "bueno"
    assert _cache["k"]["ts"] > 0


def test_no_se_solapan_refrescos_de_la_misma_clave():
    clear()
    running = []

    def lento():
        running.append(1)
        time.sleep(0.4)
        return "v"

    for _ in range(10):
        _cached("k", 10, lento, None)
    assert wait_until(lambda: "k" not in _refreshing, timeout=4)
    assert len(running) == 1, f"se lanzaron {len(running)} refrescos a la vez"


def test_claves_distintas_se_refrescan_por_separado():
    clear()
    _cached("a", 10, lambda: "va", None)
    _cached("b", 10, lambda: "vb", None)
    assert wait_until(lambda: _cache.get("a", {}).get("value") == "va")
    assert wait_until(lambda: _cache.get("b", {}).get("value") == "vb")
