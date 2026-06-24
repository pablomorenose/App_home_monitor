"""
Funciones que comprueban si un dispositivo está vivo según su tipo
(http, ping o port) y devuelven (online: bool, error: str | None, response_ms: float | None).
"""

import socket
import subprocess
import time

import requests


def check_http(url: str, timeout: int):
    try:
        start = time.monotonic()
        requests.get(url, timeout=timeout)
        ms = (time.monotonic() - start) * 1000
        return True, None, round(ms)
    except requests.exceptions.ConnectTimeout:
        return False, "Sin respuesta (timeout)", None
    except requests.exceptions.ConnectionError:
        return False, "Conexión rechazada", None
    except requests.exceptions.RequestException as e:
        return False, type(e).__name__, None


def check_ping(host: str, timeout: int):
    try:
        start = time.monotonic()
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), host],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        ms = (time.monotonic() - start) * 1000
        if result.returncode == 0:
            # Intentar extraer el tiempo real del output del ping
            output = result.stdout.decode("utf-8", errors="ignore")
            for part in output.split():
                if part.startswith("time="):
                    try:
                        return True, None, round(float(part.split("=")[1]))
                    except Exception:
                        pass
            return True, None, round(ms)
        return False, "No responde al ping", None
    except Exception as e:
        return False, str(e), None


def check_port(host: str, port: int, timeout: int):
    try:
        start = time.monotonic()
        with socket.create_connection((host, port), timeout=timeout):
            ms = (time.monotonic() - start) * 1000
            return True, None, round(ms)
    except socket.timeout:
        return False, "Sin respuesta (timeout)", None
    except ConnectionRefusedError:
        return False, "Conexión rechazada", None
    except Exception as e:
        return False, type(e).__name__, None


def check_ha_entity(entity_id: str, timeout: int):
    from config import HOME_ASSISTANT_TOKEN, HOME_ASSISTANT_URL

    url = f"{HOME_ASSISTANT_URL.rstrip('/')}/api/states/{entity_id}"
    headers = {
        "Authorization": f"Bearer {HOME_ASSISTANT_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        start = time.monotonic()
        resp = requests.get(url, headers=headers, timeout=timeout)
        ms = (time.monotonic() - start) * 1000
    except requests.exceptions.ConnectTimeout:
        return False, "Sin respuesta de Home Assistant (timeout)", None
    except requests.exceptions.ConnectionError:
        return False, "No se pudo conectar con Home Assistant", None
    except requests.exceptions.RequestException as e:
        return False, type(e).__name__, None

    if resp.status_code == 401:
        return False, "Token de Home Assistant inválido o caducado", None
    if resp.status_code == 404:
        return False, f"Entidad '{entity_id}' no existe en Home Assistant", None
    if resp.status_code != 200:
        return False, f"Home Assistant respondió HTTP {resp.status_code}", None

    data = resp.json()
    state = data.get("state")

    if state in ("unavailable", "unknown", None):
        return False, f"Entidad en estado '{state}'", None

    return True, None, round(ms)


def check_device(device: dict):
    """Despacha la comprobación según el tipo de dispositivo."""
    dtype = device["type"]

    if dtype == "http":
        return check_http(device["url"], device.get("timeout", 5))
    if dtype == "ping":
        return check_ping(device["host"], device.get("timeout", 5))
    if dtype == "port":
        return check_port(device["host"], device["port"], device.get("timeout", 5))
    if dtype == "ha_entity":
        return check_ha_entity(device["entity_id"], device.get("timeout", 5))

    return False, f"Tipo de dispositivo desconocido: {dtype}", None
