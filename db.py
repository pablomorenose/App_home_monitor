"""
Gestión de la base de datos PostgreSQL (Supabase).
- device_status: estado actual de cada dispositivo
- status_history: historial de cambios de estado
- devices: configuración de dispositivos (gestionada desde la app)
"""

import json
import time
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

from config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER


@contextmanager
def get_db():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD, sslmode="require",
    )
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Crea las tablas si no existen."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS device_status (
                    device_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    online INTEGER NOT NULL,
                    last_change_ts DOUBLE PRECISION NOT NULL,
                    last_check_ts DOUBLE PRECISION NOT NULL,
                    last_error TEXT,
                    response_ms INTEGER
                )
            """)
            # Estado on/off de switches de Home Assistant (NULL si no es switch)
            cur.execute("""
                ALTER TABLE device_status
                ADD COLUMN IF NOT EXISTS switch_state TEXT
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS status_history (
                    id SERIAL PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    online INTEGER NOT NULL,
                    ts DOUBLE PRECISION NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at DOUBLE PRECISION NOT NULL
                )
            """)
            # Añadir columna response_ms si no existe (migración)
            cur.execute("""
                ALTER TABLE device_status
                ADD COLUMN IF NOT EXISTS response_ms INTEGER
            """)
            cur.execute("""
                ALTER TABLE status_history
                ADD COLUMN IF NOT EXISTS response_ms INTEGER
            """)
            cur.execute("""
                ALTER TABLE devices
                ADD COLUMN IF NOT EXISTS maintenance_until DOUBLE PRECISION NOT NULL DEFAULT 0
            """)

            # ─── Phase 2 migrations: monitor config columns on devices ───
            cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS check_interval INTEGER NOT NULL DEFAULT 15")
            cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS timeout INTEGER NOT NULL DEFAULT 8")
            cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS max_retries INTEGER NOT NULL DEFAULT 3")
            cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS retry_interval INTEGER NOT NULL DEFAULT 5")
            cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS recovery_threshold INTEGER NOT NULL DEFAULT 1")
            cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS expected_status_codes TEXT NOT NULL DEFAULT '200-399'")
            cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS http_method TEXT NOT NULL DEFAULT 'GET'")
            cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS http_headers TEXT NOT NULL DEFAULT '{}'")
            cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS http_body TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS verify_keyword TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS tls_warn_days INTEGER NOT NULL DEFAULT 14")
            cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS depends_on TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS tags TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS follow_redirects INTEGER NOT NULL DEFAULT 1")
            cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS latency_threshold INTEGER NOT NULL DEFAULT 0")

            # ─── Phase 2 migrations: state machine columns on device_status ───
            cur.execute("ALTER TABLE device_status ADD COLUMN IF NOT EXISTS state TEXT NOT NULL DEFAULT 'pending'")
            cur.execute("ALTER TABLE device_status ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE device_status ADD COLUMN IF NOT EXISTS consecutive_successes INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE device_status ADD COLUMN IF NOT EXISTS last_notification_ts DOUBLE PRECISION NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE device_status ADD COLUMN IF NOT EXISTS incident_id TEXT")


# -----------------------------------------------------------------------
# Gestión de dispositivos (tabla devices)
# -----------------------------------------------------------------------

def get_all_devices() -> list[dict]:
    """Devuelve todos los dispositivos configurados."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, name, type, config_json, enabled, created_at, COALESCE(maintenance_until, 0) as maintenance_until FROM devices WHERE enabled = 1 ORDER BY created_at")
            rows = cur.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d.update(json.loads(d.pop("config_json")))
                result.append(d)
            return result


def upsert_device(device: dict):
    """Crea o actualiza un dispositivo."""
    # Separar campos de la tabla de los campos de configuración
    base_fields = {"id", "name", "type", "enabled", "created_at"}
    config = {k: v for k, v in device.items() if k not in base_fields}
    now = time.time()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO devices (id, name, type, config_json, enabled, created_at)
                VALUES (%s, %s, %s, %s, 1, %s)
                ON CONFLICT (id) DO UPDATE
                SET name = EXCLUDED.name,
                    type = EXCLUDED.type,
                    config_json = EXCLUDED.config_json
            """, (
                device["id"],
                device["name"],
                device["type"],
                json.dumps(config),
                device.get("created_at", now),
            ))


def delete_device(device_id: str):
    """Elimina un dispositivo y su estado."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM devices WHERE id = %s", (device_id,))
            cur.execute("DELETE FROM device_status WHERE device_id = %s", (device_id,))
            cur.execute("DELETE FROM status_history WHERE device_id = %s", (device_id,))


def seed_devices_from_config():
    """Migra los dispositivos de config.py a la BD si la tabla está vacía."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM devices")
            count = cur.fetchone()[0]
    if count == 0:
        from config import DEVICES
        for d in DEVICES:
            upsert_device(d)


# -----------------------------------------------------------------------
# Estado de dispositivos
# -----------------------------------------------------------------------

def update_status(device_id: str, name: str, online: bool,
                  error: str | None = None, response_ms: int | None = None,
                  switch_state: str | None = None):
    now = time.time()
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT online, last_change_ts FROM device_status WHERE device_id = %s",
                (device_id,),
            )
            row = cur.fetchone()

            if row is None:
                cur.execute("""
                    INSERT INTO device_status
                    (device_id, name, online, last_change_ts, last_check_ts, last_error, response_ms, switch_state)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (device_id, name, int(online), now, now, error, response_ms, switch_state))
                cur.execute(
                    "INSERT INTO status_history (device_id, online, ts, response_ms) VALUES (%s, %s, %s, %s)",
                    (device_id, int(online), now, response_ms),
                )
                return

            changed = bool(row["online"]) != online
            last_change_ts = now if changed else row["last_change_ts"]

            cur.execute("""
                UPDATE device_status
                SET name = %s, online = %s, last_change_ts = %s,
                    last_check_ts = %s, last_error = %s, response_ms = %s,
                    switch_state = %s
                WHERE device_id = %s
            """, (name, int(online), last_change_ts, now, error, response_ms, switch_state, device_id))

            # Guardar siempre la latencia para sparkline, solo guardar cambio de estado si cambio
            cur.execute(
                "INSERT INTO status_history (device_id, online, ts, response_ms) VALUES (%s, %s, %s, %s)",
                (device_id, int(online), now, response_ms),
            )


def get_all_statuses() -> list[dict]:
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM device_status")
            return [dict(row) for row in cur.fetchall()]


def get_history(device_id: str, limit: int = 50) -> list[dict]:
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT online, ts FROM status_history WHERE device_id = %s ORDER BY ts DESC LIMIT %s",
                (device_id, limit),
            )
            return [dict(row) for row in cur.fetchall()]


def get_latency_history(device_id: str, limit: int = 60) -> list[dict]:
    """Devuelve los ultimos N valores de latencia registrados."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT response_ms as ms, ts
                FROM status_history
                WHERE device_id = %s AND response_ms IS NOT NULL
                ORDER BY ts DESC LIMIT %s
            """, (device_id, limit))
            rows = cur.fetchall()
            return [dict(r) for r in reversed(rows)]


def set_maintenance(device_id: str, hours: float):
    """Activa modo mantenimiento durante `hours` horas (0 = desactivar)."""
    until = time.time() + hours * 3600 if hours > 0 else 0
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE devices SET maintenance_until = %s WHERE id = %s",
                (until, device_id),
            )


def cleanup_old_history(days: int = 30):
    """Borra registros de status_history con mas de `days` dias de antiguedad."""
    cutoff = time.time() - days * 86400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM status_history WHERE ts < %s", (cutoff,))


# -----------------------------------------------------------------------
# Gestión de monitores (Phase 2 — vista extendida de devices)
# -----------------------------------------------------------------------

_MONITOR_COLUMNS = (
    "id", "name", "type", "config_json", "enabled", "created_at",
    "maintenance_until", "check_interval", "timeout", "max_retries",
    "retry_interval", "recovery_threshold", "expected_status_codes",
    "http_method", "http_headers", "http_body", "verify_keyword",
    "tls_warn_days", "depends_on", "tags", "follow_redirects", "latency_threshold",
)


def get_all_monitors() -> list[dict]:
    """Devuelve todos los monitores con campos extendidos."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT {', '.join(_MONITOR_COLUMNS)}
                FROM devices
                ORDER BY created_at
            """)
            rows = cur.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                # Merge config_json into the dict
                config = json.loads(d.pop("config_json", "{}"))
                d.update(config)
                result.append(d)
            return result


def get_monitor(monitor_id: str) -> dict | None:
    """Devuelve un monitor por su ID."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT {', '.join(_MONITOR_COLUMNS)}
                FROM devices WHERE id = %s
            """, (monitor_id,))
            row = cur.fetchone()
            if not row:
                return None
            d = dict(row)
            config = json.loads(d.pop("config_json", "{}"))
            d.update(config)
            return d


def upsert_monitor(monitor: dict):
    """Crea o actualiza un monitor con todos los campos extendidos."""
    base_fields = {
        "id", "name", "type", "enabled", "created_at",
        "check_interval", "timeout", "max_retries", "retry_interval",
        "recovery_threshold", "expected_status_codes", "http_method",
        "http_headers", "http_body", "verify_keyword", "tls_warn_days",
        "depends_on", "tags", "follow_redirects", "latency_threshold",
        "maintenance_until",
    }
    config = {k: v for k, v in monitor.items() if k not in base_fields}
    now = time.time()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO devices (
                    id, name, type, config_json, enabled, created_at,
                    check_interval, timeout, max_retries, retry_interval,
                    recovery_threshold, expected_status_codes, http_method,
                    http_headers, http_body, verify_keyword, tls_warn_days,
                    depends_on, tags, follow_redirects, latency_threshold,
                    maintenance_until
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s
                )
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    type = EXCLUDED.type,
                    config_json = EXCLUDED.config_json,
                    check_interval = EXCLUDED.check_interval,
                    timeout = EXCLUDED.timeout,
                    max_retries = EXCLUDED.max_retries,
                    retry_interval = EXCLUDED.retry_interval,
                    recovery_threshold = EXCLUDED.recovery_threshold,
                    expected_status_codes = EXCLUDED.expected_status_codes,
                    http_method = EXCLUDED.http_method,
                    http_headers = EXCLUDED.http_headers,
                    http_body = EXCLUDED.http_body,
                    verify_keyword = EXCLUDED.verify_keyword,
                    tls_warn_days = EXCLUDED.tls_warn_days,
                    depends_on = EXCLUDED.depends_on,
                    tags = EXCLUDED.tags,
                    follow_redirects = EXCLUDED.follow_redirects,
                    latency_threshold = EXCLUDED.latency_threshold,
                    maintenance_until = EXCLUDED.maintenance_until
            """, (
                monitor["id"],
                monitor["name"],
                monitor["type"],
                json.dumps(config),
                monitor.get("enabled", 1),
                monitor.get("created_at", now),
                monitor.get("check_interval", 15),
                monitor.get("timeout", 8),
                monitor.get("max_retries", 3),
                monitor.get("retry_interval", 5),
                monitor.get("recovery_threshold", 1),
                monitor.get("expected_status_codes", "200-399"),
                monitor.get("http_method", "GET"),
                monitor.get("http_headers", "{}"),
                monitor.get("http_body", ""),
                monitor.get("verify_keyword", ""),
                monitor.get("tls_warn_days", 14),
                monitor.get("depends_on", ""),
                monitor.get("tags", ""),
                monitor.get("follow_redirects", 1),
                monitor.get("latency_threshold", 0),
                monitor.get("maintenance_until", 0),
            ))


def get_monitor_statuses() -> list[dict]:
    """Devuelve todos los estados incluyendo campos de state machine."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT device_id, name, online, last_change_ts, last_check_ts,
                       last_error, response_ms, switch_state,
                       state, consecutive_failures, consecutive_successes,
                       last_notification_ts, incident_id
                FROM device_status
            """)
            return [dict(row) for row in cur.fetchall()]


def update_monitor_status(device_id: str, name: str, online: bool,
                          error: str | None = None, response_ms: int | None = None,
                          switch_state: str | None = None,
                          state: str = "pending",
                          consecutive_failures: int = 0,
                          consecutive_successes: int = 0,
                          last_notification_ts: float = 0,
                          incident_id: str | None = None):
    """Actualiza el estado del monitor con campos de state machine."""
    now = time.time()
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT online, last_change_ts FROM device_status WHERE device_id = %s",
                (device_id,),
            )
            row = cur.fetchone()

            if row is None:
                cur.execute("""
                    INSERT INTO device_status
                    (device_id, name, online, last_change_ts, last_check_ts,
                     last_error, response_ms, switch_state,
                     state, consecutive_failures, consecutive_successes,
                     last_notification_ts, incident_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (device_id, name, int(online), now, now, error, response_ms,
                      switch_state, state, consecutive_failures, consecutive_successes,
                      last_notification_ts, incident_id))
            else:
                changed = bool(row["online"]) != online
                last_change_ts = now if changed else row["last_change_ts"]
                cur.execute("""
                    UPDATE device_status
                    SET name = %s, online = %s, last_change_ts = %s,
                        last_check_ts = %s, last_error = %s, response_ms = %s,
                        switch_state = %s, state = %s,
                        consecutive_failures = %s, consecutive_successes = %s,
                        last_notification_ts = %s, incident_id = %s
                    WHERE device_id = %s
                """, (name, int(online), last_change_ts, now, error, response_ms,
                      switch_state, state, consecutive_failures, consecutive_successes,
                      last_notification_ts, incident_id, device_id))

            # Always record history
            cur.execute(
                "INSERT INTO status_history (device_id, online, ts, response_ms) VALUES (%s, %s, %s, %s)",
                (device_id, int(online), now, response_ms),
            )


def update_heartbeat_ts(monitor_id: str):
    """Actualiza last_check_ts para un monitor de tipo heartbeat."""
    now = time.time()
    with get_db() as conn:
        with conn.cursor() as cur:
            # Use last_check_ts as the heartbeat ping timestamp
            cur.execute("""
                UPDATE device_status
                SET last_check_ts = %s
                WHERE device_id = %s
            """, (now, monitor_id))
            # If no row exists yet, create one
            if cur.rowcount == 0:
                cur.execute("""
                    INSERT INTO device_status
                    (device_id, name, online, last_change_ts, last_check_ts,
                     last_error, response_ms, switch_state, state,
                     consecutive_failures, consecutive_successes,
                     last_notification_ts, incident_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (monitor_id, monitor_id, 1, now, now, None, None, None,
                      'up', 0, 1, 0, None))


def get_incidents(limit: int = 100) -> list[dict]:
    """
    Devuelve los últimos eventos de caída/recuperación de todos los dispositivos,
    incluyendo el nombre del dispositivo y cuánto tardó en recuperarse.
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Join con device_status para obtener el nombre
            cur.execute("""
                SELECT h.id, h.device_id, h.online, h.ts,
                       COALESCE(ds.name, h.device_id) as name
                FROM status_history h
                LEFT JOIN device_status ds ON ds.device_id = h.device_id
                ORDER BY h.ts DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()

        # Calcular tiempo de recuperación: para cada caída buscar la siguiente
        # recuperación del mismo dispositivo
        result = []
        rows_list = [dict(r) for r in rows]
        for i, row in enumerate(rows_list):
            recovered_ts = None
            if not row["online"]:
                # Buscar la recuperación más cercana posterior (en la lista ordenada DESC)
                for j in range(i - 1, -1, -1):
                    if rows_list[j]["device_id"] == row["device_id"] and rows_list[j]["online"]:
                        recovered_ts = rows_list[j]["ts"]
                        break
            row["recovered_ts"] = recovered_ts
            result.append(row)
        return result
