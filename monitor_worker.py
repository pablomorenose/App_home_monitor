"""
Bucle en background que comprueba todos los dispositivos cada
CHECK_INTERVAL_SECONDS y guarda el resultado en la base de datos.
Lee los dispositivos de la BD en cada ciclo para recoger cambios al instante.
"""

import threading
import time

from checks import check_device
from config import CHECK_INTERVAL_SECONDS
from db import get_all_devices, get_all_statuses, update_status


def run_checks_once():
    devices = get_all_devices()
    prev = {s["device_id"]: bool(s["online"]) for s in get_all_statuses()}

    for device in devices:
        online, error, response_ms = check_device(device)
        update_status(device["id"], device["name"], online, error, response_ms)

        if device["id"] in prev and prev[device["id"]] != online:
            _notify_change(device["name"], online, error)


def _notify_change(name: str, online: bool, error: str | None):
    try:
        from notifications import send_push
        if online:
            send_push(title="✅ Dispositivo recuperado", body=f"{name} vuelve a estar en línea")
        else:
            reason = f" — {error}" if error else ""
            send_push(title="🔴 Dispositivo caído", body=f"{name} no responde{reason}")
    except Exception as e:
        print(f"[push] No se pudo enviar notificación: {e}")


def _loop():
    while True:
        try:
            run_checks_once()
        except Exception as e:
            print(f"[monitor] Error en el ciclo de comprobación: {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)


def start_background_monitor():
    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread
