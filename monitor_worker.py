"""
Bucle en background que comprueba todos los dispositivos cada
CHECK_INTERVAL_SECONDS y guarda el resultado en la base de datos.
Lee los dispositivos de la BD en cada ciclo para recoger cambios al instante.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from checks import check_device
from config import CHECK_INTERVAL_SECONDS
from db import get_all_devices, get_all_statuses, update_status, cleanup_old_history

_last_cleanup_ts = 0.0


def run_checks_once():
    devices = get_all_devices()
    prev = {s["device_id"]: bool(s["online"]) for s in get_all_statuses()}

    def _check(device):
        online, error, response_ms = check_device(device)
        update_status(device["id"], device["name"], online, error, response_ms)
        return device, online, error

    with ThreadPoolExecutor(max_workers=len(devices) or 1) as ex:
        futures = {ex.submit(_check, d): d for d in devices}
        for future in as_completed(futures):
            try:
                device, online, error = future.result()
                in_maintenance = device.get("maintenance_until", 0) > time.time()
                if device["id"] in prev and prev[device["id"]] != online and not in_maintenance:
                    _notify_change(device["name"], online, error)
            except Exception as e:
                print(f"[monitor] Error comprobando dispositivo: {e}")


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
    global _last_cleanup_ts
    while True:
        try:
            run_checks_once()
            if time.time() - _last_cleanup_ts > 86400:
                cleanup_old_history(days=30)
                _last_cleanup_ts = time.time()
        except Exception as e:
            print(f"[monitor] Error en el ciclo de comprobacion: {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)


def start_background_monitor():
    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread
