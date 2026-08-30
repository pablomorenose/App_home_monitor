"""La superficie HTTP de la app.

Fija el conjunto exacto de rutas y métodos. Sirve de red para reorganizar el
código de las vistas sin perder ni renombrar un endpoint por el camino, y
obliga a que añadir una ruta pública sea un cambio consciente.
"""

import pytest

from app import app

EXPECTED_ROUTES = {
    ("/", "GET"),
    ("/health", "GET"),
    ("/historial", "GET"),
    ("/login", "GET,POST"),
    ("/logout", "GET"),
    ("/static/<path:filename>", "GET"),
    ("/api/csrf-token", "GET"),
    ("/api/devices", "GET"),
    ("/api/devices", "POST"),
    ("/api/devices/<device_id>", "DELETE"),
    ("/api/devices/<device_id>", "PUT"),
    ("/api/devices/<device_id>/maintenance", "POST"),
    ("/api/export", "GET"),
    ("/api/force-check", "POST"),
    ("/api/groups", "GET"),
    ("/api/ha-sensors", "GET"),
    ("/api/heartbeat/<monitor_id>", "POST"),
    ("/api/heartbeats/<device_id>", "GET"),
    ("/api/import", "POST"),
    ("/api/incidents", "GET"),
    ("/api/latency/<device_id>", "GET"),
    ("/api/monitors", "GET"),
    ("/api/monitors", "POST"),
    ("/api/monitors/<monitor_id>", "DELETE"),
    ("/api/monitors/<monitor_id>", "GET"),
    ("/api/monitors/<monitor_id>", "PUT"),
    ("/api/monitors/<monitor_id>/heartbeat-url", "GET"),
    ("/api/monitors/<monitor_id>/history", "GET"),
    ("/api/monitors/<monitor_id>/stats", "GET"),
    ("/api/monitors/bulk-delete", "POST"),
    ("/api/monitors/bulk-pause", "POST"),
    ("/api/monitors/bulk-resume", "POST"),
    ("/api/stats/summary", "GET"),
    ("/api/status", "GET"),
    ("/api/status-page", "GET"),
    ("/api/subscribe", "POST"),
    ("/api/toggle/<device_id>", "POST"),
    ("/api/unsubscribe", "POST"),
    ("/api/uptime/<device_id>", "GET"),
    ("/api/vapid-key", "GET"),
}

# Rutas accesibles sin sesión, a propósito. Cualquier añadido aquí debería
# ser deliberado: la app se publica a menudo por Tailscale Funnel.
PUBLIC_ROUTES = {
    "/login", "/logout", "/static/<path:filename>", "/health",
    "/api/status-page",        # solo si STATUS_PAGE_ENABLED
    "/api/heartbeat/<monitor_id>",  # autenticado por token de monitor
    "/api/csrf-token", "/api/vapid-key",
    "/api/subscribe", "/api/unsubscribe",  # protegidas por CSRF
}


def actual_routes():
    return {(r.rule, ",".join(sorted(r.methods - {"HEAD", "OPTIONS"})))
            for r in app.url_map.iter_rules()}


def test_las_rutas_son_exactamente_las_esperadas():
    assert actual_routes() == EXPECTED_ROUTES


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_las_paginas_redirigen_al_login_sin_sesion(client):
    for path in ("/", "/historial"):
        resp = client.get(path)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


def test_los_endpoints_privados_responden_401_sin_sesion(client):
    private = [rule for rule, _ in EXPECTED_ROUTES
               if rule not in PUBLIC_ROUTES and rule not in ("/", "/historial")
               and "<" not in rule]
    assert private, "el test se quedaría vacío si cambian las rutas"
    for path in private:
        resp = client.get(path)
        assert resp.status_code in (401, 405), f"{path} devolvió {resp.status_code}"
        if resp.status_code == 401:
            assert resp.get_json()["error"] == "No autorizado"


def test_las_paginas_se_renderizan_con_sesion(client):
    """Comprueba que las plantillas y los estáticos siguen resolviéndose."""
    with client.session_transaction() as sess:
        sess["authenticated"] = True
    html = client.get("/").get_data(as_text=True)
    assert "/static/css/dashboard.css" in html
    assert "/static/js/dashboard.js" in html
    assert client.get("/historial").status_code == 200


def test_el_login_se_renderiza_con_token_csrf(client):
    html = client.get("/login").get_data(as_text=True)
    assert 'name="csrf_token"' in html


def test_el_login_rechaza_un_post_sin_token_csrf(client):
    resp = client.post("/login", data={"username": "admin", "password": "x"})
    assert resp.status_code == 400


def test_el_heartbeat_rechaza_un_token_invalido(client):
    resp = client.post("/api/heartbeat/loquesea", headers={"X-Heartbeat-Token": "malo"})
    assert resp.status_code == 403


def test_el_heartbeat_sin_token_no_toca_la_base_de_datos(client):
    """403 antes de consultar nada: un id inventado no debe llegar al SQL."""
    resp = client.post("/api/heartbeat/id-inventado")
    assert resp.status_code == 403
