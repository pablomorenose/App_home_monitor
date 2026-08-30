"""Blueprints de la aplicación.

Cada módulo agrupa un área de la API. app.py los registra todos; ninguno
importa app.py, así que no hay ciclos.
"""

from routes import auth, devices, monitors, pages, push, stats

BLUEPRINTS = (
    auth.bp,
    pages.bp,
    devices.bp,
    monitors.bp,
    stats.bp,
    push.bp,
)
