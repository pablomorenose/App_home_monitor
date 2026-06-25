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


# -----------------------------------------------------------------------
# Gestión de dispositivos (tabla devices)
# -----------------------------------------------------------------------

def get_all_devices() -> list[dict]:
    """Devuelve todos los dispositivos configurados."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM devices WHERE enabled = 1 ORDER BY created_at")
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
                  error: str | None = None, response_ms: int | None = None):
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
                    (device_id, name, online, last_change_ts, last_check_ts, last_error, response_ms)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (device_id, name, int(online), now, now, error, response_ms))
                cur.execute(
                    "INSERT INTO status_history (device_id, online, ts) VALUES (%s, %s, %s)",
                    (device_id, int(online), now),
                )
                return

            changed = bool(row["online"]) != online
            last_change_ts = now if changed else row["last_change_ts"]

            cur.execute("""
                UPDATE device_status
                SET name = %s, online = %s, last_change_ts = %s,
                    last_check_ts = %s, last_error = %s, response_ms = %s
                WHERE device_id = %s
            """, (name, int(online), last_change_ts, now, error, response_ms, device_id))

            if changed:
                cur.execute(
                    "INSERT INTO status_history (device_id, online, ts) VALUES (%s, %s, %s)",
                    (device_id, int(online), now),
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
    """Devuelve los últimos N valores de latencia registrados."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT response_ms, last_check_ts as ts
                FROM device_status
                WHERE device_id = %s AND response_ms IS NOT NULL
            """, (device_id,))
            # Solo tenemos el valor actual en device_status.
            # Para sparkline usamos status_history con response_ms si existe,
            # o simplemente devolvemos el valor actual como punto único.
            row = cur.fetchone()
            if row:
                return [{"ms": row["response_ms"], "ts": row["ts"]}]
            return []


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
