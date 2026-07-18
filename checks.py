"""
Funciones que comprueban si un dispositivo/monitor está vivo según su tipo.

Phase 2: Todas las funciones devuelven un dict normalizado:
{
    "state": "up" | "down" | "degraded",
    "message": "Human readable, safe",
    "latency_ms": 123,  # or None
    "details": {},  # extra info
}

Backward compat: check_device() sigue devolviendo (online, error, response_ms).
"""

import json
import re
import socket
import ssl
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests


# ───────────────────────────────────────────────────────────────────
# Utilities
# ───────────────────────────────────────────────────────────────────

def _parse_status_codes(spec: str) -> set[int]:
    """Parse '200-399' or '200,201,301' into a set of ints."""
    codes = set()
    if not spec or not spec.strip():
        # Default: 200-399
        return set(range(200, 400))
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            low, high = part.split("-", 1)
            codes.update(range(int(low), int(high) + 1))
        else:
            codes.add(int(part))
    return codes


def _safe_host(host: str) -> str | None:
    """Validate host to prevent shell injection. Returns cleaned host or None."""
    if not host:
        return None
    # Only allow alphanumeric, dots, hyphens, colons (IPv6), underscores
    if not re.match(r'^[a-zA-Z0-9.\-_:]+$', host):
        return None
    return host


def _make_result(state: str, message: str, latency_ms=None, **details) -> dict:
    """Build a normalized check result dict."""
    return {
        "state": state,
        "message": message,
        "latency_ms": latency_ms,
        "details": details,
    }


# ───────────────────────────────────────────────────────────────────
# HTTP Check
# ───────────────────────────────────────────────────────────────────

def check_http(url: str, timeout: int = 8, method: str = "GET",
               headers: dict | str = None, body: str = "",
               follow_redirects: bool = True,
               expected_status_codes: str = "200-399",
               verify_keyword: str = "",
               latency_threshold: int = 0) -> dict:
    """Enhanced HTTP check with method, headers, body, keyword verification."""
    if headers and isinstance(headers, str):
        try:
            headers = json.loads(headers)
        except (json.JSONDecodeError, TypeError):
            headers = {}
    if not headers:
        headers = {}

    valid_codes = _parse_status_codes(expected_status_codes)
    method = (method or "GET").upper()

    try:
        start = time.monotonic()
        resp = requests.request(
            method=method,
            url=url,
            timeout=timeout,
            headers=headers,
            data=body if body else None,
            allow_redirects=follow_redirects,
        )
        ms = round((time.monotonic() - start) * 1000)
    except requests.exceptions.ConnectTimeout:
        return _make_result("down", "Sin respuesta (timeout)")
    except requests.exceptions.ConnectionError:
        return _make_result("down", "Conexión rechazada")
    except requests.exceptions.RequestException as e:
        return _make_result("down", type(e).__name__)

    status_code = resp.status_code

    # Check status code
    if status_code not in valid_codes:
        return _make_result("down", f"HTTP {status_code} (esperado: {expected_status_codes})",
                            latency_ms=ms, http_status=status_code)

    # Check keyword/regex
    if verify_keyword:
        text = resp.text
        # Try regex first
        try:
            if not re.search(verify_keyword, text):
                return _make_result("down",
                                    f"Keyword '{verify_keyword}' no encontrado en respuesta",
                                    latency_ms=ms, http_status=status_code)
        except re.error:
            # If not valid regex, do plain text search
            if verify_keyword not in text:
                return _make_result("down",
                                    f"Keyword '{verify_keyword}' no encontrado en respuesta",
                                    latency_ms=ms, http_status=status_code)

    # Check latency threshold for degraded
    if latency_threshold and latency_threshold > 0 and ms > latency_threshold:
        return _make_result("degraded",
                            f"Latencia alta: {ms}ms (umbral: {latency_threshold}ms)",
                            latency_ms=ms, http_status=status_code)

    return _make_result("up", f"HTTP {status_code} OK", latency_ms=ms,
                        http_status=status_code)


# ───────────────────────────────────────────────────────────────────
# Ping Check
# ───────────────────────────────────────────────────────────────────

def check_ping(host: str, timeout: int = 5, latency_threshold: int = 0) -> dict:
    """Ping check with host validation to prevent shell injection."""
    safe = _safe_host(host)
    if not safe:
        return _make_result("down", f"Host inválido: '{host}'")

    try:
        start = time.monotonic()
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), safe],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        ms = round((time.monotonic() - start) * 1000)

        if result.returncode == 0:
            # Extract real ping time from output
            output = result.stdout.decode("utf-8", errors="ignore")
            for part in output.split():
                if part.startswith("time="):
                    try:
                        ms = round(float(part.split("=")[1]))
                    except Exception:
                        pass
                    break

            if latency_threshold and latency_threshold > 0 and ms > latency_threshold:
                return _make_result("degraded",
                                    f"Latencia alta: {ms}ms (umbral: {latency_threshold}ms)",
                                    latency_ms=ms)
            return _make_result("up", "Ping OK", latency_ms=ms)

        return _make_result("down", "No responde al ping")
    except Exception as e:
        return _make_result("down", str(e))


# ───────────────────────────────────────────────────────────────────
# Port Check
# ───────────────────────────────────────────────────────────────────

def check_port(host: str, port: int, timeout: int = 5,
               latency_threshold: int = 0) -> dict:
    """TCP port connectivity check."""
    safe = _safe_host(host)
    if not safe:
        return _make_result("down", f"Host inválido: '{host}'")

    try:
        start = time.monotonic()
        with socket.create_connection((safe, port), timeout=timeout):
            ms = round((time.monotonic() - start) * 1000)

        if latency_threshold and latency_threshold > 0 and ms > latency_threshold:
            return _make_result("degraded",
                                f"Latencia alta: {ms}ms (umbral: {latency_threshold}ms)",
                                latency_ms=ms, port=port)
        return _make_result("up", f"Puerto {port} abierto", latency_ms=ms, port=port)
    except socket.timeout:
        return _make_result("down", "Sin respuesta (timeout)", port=port)
    except ConnectionRefusedError:
        return _make_result("down", "Conexión rechazada", port=port)
    except Exception as e:
        return _make_result("down", type(e).__name__, port=port)


# ───────────────────────────────────────────────────────────────────
# Home Assistant Entity Check
# ───────────────────────────────────────────────────────────────────

def check_ha_entity(entity_id: str, timeout: int = 8) -> dict:
    """Check Home Assistant entity availability."""
    from config import HOME_ASSISTANT_TOKEN, HOME_ASSISTANT_URL

    url = f"{HOME_ASSISTANT_URL.rstrip('/')}/api/states/{entity_id}"
    headers = {
        "Authorization": f"Bearer {HOME_ASSISTANT_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        start = time.monotonic()
        resp = requests.get(url, headers=headers, timeout=timeout)
        ms = round((time.monotonic() - start) * 1000)
    except requests.exceptions.ConnectTimeout:
        return _make_result("down", "Sin respuesta de Home Assistant (timeout)")
    except requests.exceptions.ConnectionError:
        return _make_result("down", "No se pudo conectar con Home Assistant")
    except requests.exceptions.RequestException as e:
        return _make_result("down", type(e).__name__)

    if resp.status_code == 401:
        return _make_result("down", "Token de Home Assistant inválido o caducado",
                            latency_ms=ms)
    if resp.status_code == 404:
        return _make_result("down", f"Entidad '{entity_id}' no existe en Home Assistant",
                            latency_ms=ms)
    if resp.status_code != 200:
        return _make_result("down", f"Home Assistant respondió HTTP {resp.status_code}",
                            latency_ms=ms)

    data = resp.json()
    state = data.get("state")

    if state in ("unavailable", "unknown", None):
        return _make_result("down", f"Entidad en estado '{state}'", latency_ms=ms,
                            entity_state=state)

    return _make_result("up", f"Entidad OK (state={state})", latency_ms=ms,
                        entity_state=state)


# ───────────────────────────────────────────────────────────────────
# Home Assistant Switch Check
# ───────────────────────────────────────────────────────────────────

def check_ha_switch(entity_id: str, timeout: int = 8) -> dict:
    """Check Home Assistant switch availability. Returns switch_state in details."""
    from config import HOME_ASSISTANT_TOKEN, HOME_ASSISTANT_URL

    url = f"{HOME_ASSISTANT_URL.rstrip('/')}/api/states/{entity_id}"
    headers = {
        "Authorization": f"Bearer {HOME_ASSISTANT_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        start = time.monotonic()
        resp = requests.get(url, headers=headers, timeout=timeout)
        ms = round((time.monotonic() - start) * 1000)
    except requests.exceptions.ConnectTimeout:
        return _make_result("down", "Sin respuesta de Home Assistant (timeout)")
    except requests.exceptions.ConnectionError:
        return _make_result("down", "No se pudo conectar con Home Assistant")
    except requests.exceptions.RequestException as e:
        return _make_result("down", type(e).__name__)

    if resp.status_code == 401:
        return _make_result("down", "Token inválido o caducado", latency_ms=ms)
    if resp.status_code == 404:
        return _make_result("down", f"Entidad '{entity_id}' no existe", latency_ms=ms)
    if resp.status_code != 200:
        return _make_result("down", f"HA respondió HTTP {resp.status_code}", latency_ms=ms)

    data = resp.json()
    state = data.get("state")

    if state in ("unavailable", "unknown", None):
        return _make_result("down", f"Switch en estado '{state}'", latency_ms=ms,
                            switch_state=None)

    # Switch is available (on or off)
    return _make_result("up", f"Switch {state}", latency_ms=ms, switch_state=state)


def toggle_ha_switch(entity_id: str, action: str, timeout: int = 5) -> tuple[bool, str]:
    """
    Activa o desactiva un switch de Home Assistant.
    action: 'turn_on' | 'turn_off' | 'toggle'
    Devuelve (éxito, mensaje).
    """
    from config import HOME_ASSISTANT_TOKEN, HOME_ASSISTANT_URL

    url = f"{HOME_ASSISTANT_URL.rstrip('/')}/api/services/switch/{action}"
    headers = {
        "Authorization": f"Bearer {HOME_ASSISTANT_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"entity_id": entity_id}

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if resp.status_code in (200, 201):
            return True, "ok"
        return False, f"HA respondió HTTP {resp.status_code}"
    except Exception as e:
        return False, str(e)


# ───────────────────────────────────────────────────────────────────
# DNS Check (new)
# ───────────────────────────────────────────────────────────────────

def check_dns(host: str, timeout: int = 5, expected_value: str = "",
              latency_threshold: int = 0) -> dict:
    """Resolve A record using socket.getaddrinfo."""
    safe = _safe_host(host)
    if not safe:
        return _make_result("down", f"Host inválido: '{host}'")

    original_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        start = time.monotonic()
        results = socket.getaddrinfo(safe, None, socket.AF_INET, socket.SOCK_STREAM)
        ms = round((time.monotonic() - start) * 1000)

        if not results:
            return _make_result("down", "No se resolvió ningún registro A",
                                resolved_ips=[])

        ips = list(set(r[4][0] for r in results))

        # Check expected value if set
        if expected_value and expected_value.strip():
            expected = expected_value.strip()
            if expected not in ips:
                return _make_result("down",
                                    f"IP esperada '{expected}' no encontrada (got: {', '.join(ips)})",
                                    latency_ms=ms, resolved_ips=ips)

        if latency_threshold and latency_threshold > 0 and ms > latency_threshold:
            return _make_result("degraded",
                                f"DNS lento: {ms}ms (umbral: {latency_threshold}ms)",
                                latency_ms=ms, resolved_ips=ips)

        return _make_result("up", f"DNS OK ({', '.join(ips)})",
                            latency_ms=ms, resolved_ips=ips)
    except socket.gaierror as e:
        return _make_result("down", f"Error DNS: {e}")
    except socket.timeout:
        return _make_result("down", "DNS timeout")
    except Exception as e:
        return _make_result("down", str(e))
    finally:
        socket.setdefaulttimeout(original_timeout)


# ───────────────────────────────────────────────────────────────────
# TLS Certificate Check (new)
# ───────────────────────────────────────────────────────────────────

def check_tls(url: str, timeout: int = 8, tls_warn_days: int = 14) -> dict:
    """Connect with SSL, check certificate expiry."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port or 443
    except Exception:
        return _make_result("down", "URL inválida para TLS check")

    if not hostname:
        return _make_result("down", "Hostname inválido")

    try:
        start = time.monotonic()
        context = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                ms = round((time.monotonic() - start) * 1000)
                cert = ssock.getpeercert()

        if not cert:
            return _make_result("down", "No se pudo obtener el certificado",
                                latency_ms=ms)

        # Parse expiry
        not_after = cert.get("notAfter")
        if not not_after:
            return _make_result("down", "Certificado sin fecha de expiración",
                                latency_ms=ms)

        # Parse date: 'Sep 15 12:00:00 2025 GMT'
        expire_dt = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
        expire_dt = expire_dt.replace(tzinfo=timezone.utc)
        now_dt = datetime.now(timezone.utc)
        days_left = (expire_dt - now_dt).days

        if days_left < 0:
            return _make_result("down", f"Certificado expirado hace {-days_left} días",
                                latency_ms=ms, tls_days_left=days_left,
                                tls_expires=not_after)

        if days_left < tls_warn_days:
            return _make_result("degraded",
                                f"Certificado expira en {days_left} días (umbral: {tls_warn_days})",
                                latency_ms=ms, tls_days_left=days_left,
                                tls_expires=not_after)

        return _make_result("up", f"TLS OK ({days_left} días restantes)",
                            latency_ms=ms, tls_days_left=days_left,
                            tls_expires=not_after)

    except ssl.SSLError as e:
        return _make_result("down", f"Error SSL: {e}")
    except socket.timeout:
        return _make_result("down", "TLS timeout")
    except ConnectionRefusedError:
        return _make_result("down", "Conexión rechazada")
    except Exception as e:
        return _make_result("down", f"Error TLS: {type(e).__name__}: {e}")


# ───────────────────────────────────────────────────────────────────
# Docker Container Check (new)
# ───────────────────────────────────────────────────────────────────

def check_docker(container_name: str, timeout: int = 5) -> dict:
    """Check Docker container status via /var/run/docker.sock."""
    try:
        start = time.monotonic()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect("/var/run/docker.sock")

        # Query container by name
        req = (
            f"GET /containers/{container_name}/json HTTP/1.0\r\n"
            f"Host: localhost\r\n\r\n"
        )
        sock.sendall(req.encode())

        resp = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            resp += chunk
        sock.close()
        ms = round((time.monotonic() - start) * 1000)

        # Parse HTTP response
        parts = resp.split(b"\r\n\r\n", 1)
        if len(parts) < 2:
            return _make_result("down", "Respuesta inválida del socket Docker",
                                latency_ms=ms)

        status_line = parts[0].split(b"\r\n")[0].decode()
        if "404" in status_line:
            return _make_result("down", f"Container '{container_name}' no encontrado",
                                latency_ms=ms)
        if "200" not in status_line:
            return _make_result("down", f"Docker respondió: {status_line}",
                                latency_ms=ms)

        body = json.loads(parts[1])
        state = body.get("State", {})
        container_status = state.get("Status", "unknown")
        running = state.get("Running", False)
        health = state.get("Health", {}).get("Status", "")

        if not running:
            return _make_result("down", f"Container parado (status={container_status})",
                                latency_ms=ms, container_status=container_status)

        if health == "unhealthy":
            return _make_result("degraded", "Container running pero unhealthy",
                                latency_ms=ms, container_status=container_status,
                                health_status=health)

        return _make_result("up", f"Container running ({health or 'no healthcheck'})",
                            latency_ms=ms, container_status=container_status,
                            health_status=health)

    except FileNotFoundError:
        return _make_result("down", "Docker socket no encontrado (/var/run/docker.sock)")
    except PermissionError:
        return _make_result("down", "Sin permisos para acceder al socket Docker")
    except socket.timeout:
        return _make_result("down", "Docker socket timeout")
    except Exception as e:
        return _make_result("down", f"Error Docker: {type(e).__name__}: {e}")


# ───────────────────────────────────────────────────────────────────
# Heartbeat Check (new)
# ───────────────────────────────────────────────────────────────────

def check_heartbeat(monitor_id: str, check_interval: int = 15,
                    last_check_ts: float = 0) -> dict:
    """
    Check if last heartbeat ping is within expected interval.
    The heartbeat is considered overdue if last_check_ts is older than
    2x check_interval seconds.
    """
    now = time.time()
    if last_check_ts == 0:
        return _make_result("down", "Nunca se recibió un heartbeat",
                            last_ping_ago=None)

    seconds_ago = now - last_check_ts
    max_allowed = check_interval * 2  # Allow 2x the interval before marking down

    if seconds_ago > max_allowed:
        return _make_result("down",
                            f"Heartbeat vencido ({round(seconds_ago)}s, máximo: {max_allowed}s)",
                            last_ping_ago=round(seconds_ago))

    return _make_result("up", f"Heartbeat recibido hace {round(seconds_ago)}s",
                        last_ping_ago=round(seconds_ago))


# ───────────────────────────────────────────────────────────────────
# System Check (local host metrics)
# ───────────────────────────────────────────────────────────────────

def check_system(timeout: int = 5) -> dict:
    """
    Check local system metrics: CPU, RAM, temperature, disk, uptime.
    Returns normalized result with extra details for the dashboard.
    """
    import shutil

    start = time.monotonic()
    cpu_pct = None
    ram_pct = None
    ram_total_mb = None
    ram_used_mb = None
    ram_avail_mb = None
    temp_c = None
    disk_pct = None
    disk_total_gb = None
    disk_used_gb = None
    disk_free_gb = None
    uptime_human = None

    # --- CPU % (quick sample) ---
    try:
        with open("/proc/stat") as f:
            line1 = f.readline().split()
        time.sleep(0.3)
        with open("/proc/stat") as f:
            line2 = f.readline().split()
        idle1 = int(line1[4]); total1 = sum(int(x) for x in line1[1:])
        idle2 = int(line2[4]); total2 = sum(int(x) for x in line2[1:])
        cpu_pct = round(100 * (1 - (idle2 - idle1) / (total2 - total1)), 1)
    except Exception:
        pass

    # --- RAM ---
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":"); mem[k.strip()] = int(v.split()[0])
        ram_total_mb = mem["MemTotal"] // 1024
        ram_avail_mb = mem["MemAvailable"] // 1024
        ram_used_mb = ram_total_mb - ram_avail_mb
        ram_pct = round(100 * ram_used_mb / ram_total_mb, 1)
    except Exception:
        pass

    # --- Temperature ---
    try:
        result = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=2)
        temp_str = result.stdout.strip().replace("temp=", "").replace("'C", "")
        temp_c = float(temp_str)
    except Exception:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                temp_c = round(int(f.read().strip()) / 1000, 1)
        except Exception:
            pass

    # --- Disk ---
    try:
        disk = shutil.disk_usage("/")
        disk_total_gb = round(disk.total / 1e9, 1)
        disk_used_gb = round(disk.used / 1e9, 1)
        disk_free_gb = round(disk.free / 1e9, 1)
        disk_pct = round(100 * disk.used / disk.total, 1)
    except Exception:
        pass

    # --- Uptime ---
    try:
        with open("/proc/uptime") as f:
            uptime_secs = int(float(f.read().split()[0]))
        days, rem = divmod(uptime_secs, 86400)
        hours, rem = divmod(rem, 3600)
        mins = rem // 60
        uptime_human = (f"{days}d " if days else "") + f"{hours}h {mins}min"
    except Exception:
        pass

    ms = round((time.monotonic() - start) * 1000)

    # Determine state based on thresholds
    state = "up"
    message = "System OK"
    if cpu_pct is not None and cpu_pct > 95:
        state = "degraded"
        message = f"CPU alta: {cpu_pct}%"
    elif ram_pct is not None and ram_pct > 95:
        state = "degraded"
        message = f"RAM alta: {ram_pct}%"
    elif disk_pct is not None and disk_pct > 95:
        state = "degraded"
        message = f"Disco lleno: {disk_pct}%"
    elif temp_c is not None and temp_c > 80:
        state = "degraded"
        message = f"Temperatura alta: {temp_c}°C"

    return _make_result(
        state, message, latency_ms=ms,
        cpu_pct=cpu_pct,
        ram_pct=ram_pct,
        ram_total_mb=ram_total_mb,
        ram_used_mb=ram_used_mb,
        ram_avail_mb=ram_avail_mb,
        temp_c=temp_c,
        disk_pct=disk_pct,
        disk_total_gb=disk_total_gb,
        disk_used_gb=disk_used_gb,
        disk_free_gb=disk_free_gb,
        uptime=uptime_human,
    )


# ───────────────────────────────────────────────────────────────────
# Dispatcher
# ───────────────────────────────────────────────────────────────────

def check_monitor(monitor: dict) -> dict:
    """
    Dispatch check based on monitor type. Returns normalized result dict.
    
    The monitor dict should contain all config fields (merged from config_json
    and the new columns).
    """
    mtype = monitor.get("type", "")
    timeout = int(monitor.get("timeout", 8))
    latency_threshold = int(monitor.get("latency_threshold", 0))

    if mtype == "http":
        return check_http(
            url=monitor.get("url", ""),
            timeout=timeout,
            method=monitor.get("http_method", "GET"),
            headers=monitor.get("http_headers", "{}"),
            body=monitor.get("http_body", ""),
            follow_redirects=bool(monitor.get("follow_redirects", 1)),
            expected_status_codes=monitor.get("expected_status_codes", "200-399"),
            verify_keyword=monitor.get("verify_keyword", ""),
            latency_threshold=latency_threshold,
        )

    if mtype == "ping":
        return check_ping(
            host=monitor.get("host", ""),
            timeout=timeout,
            latency_threshold=latency_threshold,
        )

    if mtype == "port":
        return check_port(
            host=monitor.get("host", ""),
            port=int(monitor.get("port", 80)),
            timeout=timeout,
            latency_threshold=latency_threshold,
        )

    if mtype == "ha_entity":
        return check_ha_entity(
            entity_id=monitor.get("entity_id", ""),
            timeout=timeout,
        )

    if mtype == "ha_switch":
        return check_ha_switch(
            entity_id=monitor.get("entity_id", ""),
            timeout=timeout,
        )

    if mtype == "dns":
        return check_dns(
            host=monitor.get("host", ""),
            timeout=timeout,
            expected_value=monitor.get("expected_value", ""),
            latency_threshold=latency_threshold,
        )

    if mtype == "tls":
        return check_tls(
            url=monitor.get("url", ""),
            timeout=timeout,
            tls_warn_days=int(monitor.get("tls_warn_days", 14)),
        )

    if mtype == "docker":
        return check_docker(
            container_name=monitor.get("container_name", monitor.get("id", "")),
            timeout=timeout,
        )

    if mtype == "system":
        return check_system(timeout=timeout)

    if mtype == "heartbeat":
        return check_heartbeat(
            monitor_id=monitor.get("id", ""),
            check_interval=int(monitor.get("check_interval", 15)),
            last_check_ts=float(monitor.get("_last_check_ts", 0)),
        )

    return _make_result("down", f"Tipo de monitor desconocido: {mtype}")


def check_device(device: dict):
    """
    Backward-compatible wrapper. Calls check_monitor and converts
    the normalized result to the old (online, error, response_ms) tuple.
    
    For ha_switch type, returns (online, switch_state_or_error, response_ms).
    """
    result = check_monitor(device)
    online = result["state"] in ("up", "degraded")
    ms = result["latency_ms"]

    if device.get("type") == "ha_switch":
        if online:
            # Return the switch state as the info field (on/off)
            switch_state = result["details"].get("switch_state", "on")
            return online, switch_state, ms
        else:
            return online, result["message"], ms

    error = None if online else result["message"]
    return online, error, ms
