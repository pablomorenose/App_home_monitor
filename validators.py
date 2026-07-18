"""
Validación de datos para monitores.
"""

import re
from urllib.parse import urlparse

VALID_TYPES = ("http", "ping", "port", "ha_entity", "ha_switch", "dns", "tls", "docker", "heartbeat", "system")

_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_\-]{1,50}$')
_STATUS_CODE_PATTERN = re.compile(r'^(\d{3}(-\d{3})?)(,\d{3}(-\d{3})?)*$')


def validate_monitor(data: dict) -> list[str]:
    """
    Valida los datos de un monitor.
    Devuelve una lista de errores (vacía si todo es válido).
    """
    errors = []

    # --- id ---
    mid = data.get("id", "")
    if not mid or not _ID_PATTERN.match(str(mid)):
        errors.append("id: debe ser alfanumérico (a-z, 0-9, _, -), 1-50 caracteres.")

    # --- name ---
    name = data.get("name", "")
    if not name or len(str(name)) < 1 or len(str(name)) > 100:
        errors.append("name: obligatorio, 1-100 caracteres.")

    # --- type ---
    mtype = data.get("type", "")
    if mtype not in VALID_TYPES:
        errors.append(f"type: debe ser uno de {', '.join(VALID_TYPES)}.")

    # --- url (required for http, tls) ---
    if mtype in ("http", "tls"):
        url = data.get("url", "")
        if not url:
            errors.append(f"url: obligatorio para tipo '{mtype}'.")
        else:
            parsed = urlparse(str(url))
            if parsed.scheme not in ("http", "https"):
                errors.append("url: debe ser http:// o https://.")
            if not parsed.hostname:
                errors.append("url: hostname inválido.")

    # --- host (required for ping, port, dns) ---
    if mtype in ("ping", "port", "dns"):
        host = data.get("host", "")
        if not host:
            errors.append(f"host: obligatorio para tipo '{mtype}'.")
        else:
            # Basic validation: no spaces, no shell metacharacters
            host_str = str(host)
            if not re.match(r'^[a-zA-Z0-9.\-_:]+$', host_str):
                errors.append("host: contiene caracteres inválidos.")

    # --- port (required for port type, 1-65535) ---
    if mtype == "port":
        port = data.get("port")
        if port is None:
            errors.append("port: obligatorio para tipo 'port'.")
        else:
            try:
                port_int = int(port)
                if port_int < 1 or port_int > 65535:
                    errors.append("port: debe estar entre 1 y 65535.")
            except (ValueError, TypeError):
                errors.append("port: debe ser un número entero.")

    # --- timeout (1-60) ---
    timeout = data.get("timeout")
    if timeout is not None:
        try:
            t = int(timeout)
            if t < 1 or t > 60:
                errors.append("timeout: debe estar entre 1 y 60.")
        except (ValueError, TypeError):
            errors.append("timeout: debe ser un número entero.")

    # --- check_interval (5-3600) ---
    check_interval = data.get("check_interval")
    if check_interval is not None:
        try:
            ci = int(check_interval)
            if ci < 5 or ci > 3600:
                errors.append("check_interval: debe estar entre 5 y 3600.")
        except (ValueError, TypeError):
            errors.append("check_interval: debe ser un número entero.")

    # --- max_retries (0-10) ---
    max_retries = data.get("max_retries")
    if max_retries is not None:
        try:
            mr = int(max_retries)
            if mr < 0 or mr > 10:
                errors.append("max_retries: debe estar entre 0 y 10.")
        except (ValueError, TypeError):
            errors.append("max_retries: debe ser un número entero.")

    # --- expected_status_codes ---
    codes = data.get("expected_status_codes")
    if codes is not None and str(codes).strip():
        codes_str = str(codes).strip()
        if not _STATUS_CODE_PATTERN.match(codes_str):
            errors.append("expected_status_codes: formato inválido. Use '200-399' o '200,201,301'.")
        else:
            # Validate ranges make sense
            for part in codes_str.split(","):
                if "-" in part:
                    low, high = part.split("-", 1)
                    if int(low) > int(high):
                        errors.append(f"expected_status_codes: rango inválido {low}-{high}.")
                        break

    return errors
