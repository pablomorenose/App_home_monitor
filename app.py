"""
Servidor Flask del Home Monitor.

Aquí solo vive el armado de la aplicación: configuración, blueprints,
cabeceras de seguridad y arranque. Las vistas están en routes/:

  routes/auth.py      login, logout, token CSRF
  routes/pages.py     dashboard e historial (HTML)
  routes/devices.py   estado, acciones y CRUD de dispositivos
  routes/monitors.py  CRUD de monitores, heartbeat, lotes, export/import
  routes/stats.py     estadísticas, status page pública y /health
  routes/push.py      suscripciones Web Push
"""

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from config import (
    SECRET_KEY, APP_ENV, SESSION_COOKIE_SECURE, TRUSTED_PROXIES, validate_config,
)
from db import init_db, seed_devices_from_config
from monitor_worker import start_background_monitor
from notifications import init_push_table
from routes import BLUEPRINTS

# Validar configuración antes de arrancar
validate_config()

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,  # ver config.py
    PERMANENT_SESSION_LIFETIME=30 * 24 * 3600,  # 30 days
)

# Detrás de nginx o Tailscale Funnel, request.remote_addr es la IP del proxy y
# el rate limit de login se aplicaría a todo el mundo a la vez. Solo se confía
# en X-Forwarded-For si TRUSTED_PROXIES > 0: activarlo sin un proxy delante
# permitiría falsificar la cabecera y saltarse el límite.
if TRUSTED_PROXIES > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=TRUSTED_PROXIES,
                            x_proto=TRUSTED_PROXIES, x_host=TRUSTED_PROXIES)

for blueprint in BLUEPRINTS:
    app.register_blueprint(blueprint)


# -----------------------------------------------------------------------
# Security headers
# -----------------------------------------------------------------------

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    if APP_ENV == 'production':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    # CSP: sin scripts inline — todo el JS vive en /static y los handlers van
    # por data-action. style-src mantiene 'unsafe-inline' porque el marcado usa
    # atributos style= (incluidos los que genera el JS al pintar las filas).
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "frame-ancestors 'self'"
    )
    return response


# -----------------------------------------------------------------------
# Arranque
# -----------------------------------------------------------------------

def bootstrap():
    """Inicialización única del proceso: esquema, seed y monitor de fondo.

    La llama wsgi.py (gunicorn en producción) y el __main__ de abajo (dev).
    No fuerza un ciclo inicial: start_background_monitor() ya comprueba de
    inmediato todos los monitores que tengan el intervalo vencido, que al
    arrancar son todos.
    """
    init_db()
    init_push_table()
    seed_devices_from_config()
    start_background_monitor()


if __name__ == "__main__":
    # Solo para desarrollo. En producción se sirve con gunicorn (ver Dockerfile).
    bootstrap()
    app.run(host="0.0.0.0", port=8088, debug=False)
