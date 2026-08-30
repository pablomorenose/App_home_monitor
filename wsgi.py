"""Punto de entrada WSGI para gunicorn.

    gunicorn --workers 1 --threads 8 --bind 0.0.0.0:8088 wsgi:app

Debe ejecutarse con un solo worker: el hilo de monitorización vive dentro del
proceso, así que con N workers habría N bucles comprobando los mismos
monitores y escribiendo el mismo historial.
"""

from app import app, bootstrap

bootstrap()
