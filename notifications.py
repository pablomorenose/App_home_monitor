"""
Gestión de suscripciones push y envío de notificaciones Web Push.

DEPRECATED: Este módulo se mantiene por compatibilidad.
Use alerts.py para enviar notificaciones multi-canal (send_alert).
Las funciones de gestión de suscripciones (save_subscription, delete_subscription, etc.)
siguen siendo necesarias y se usan desde alerts.py y app.py.
"""

import json
import time
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from pywebpush import webpush, WebPushException

from config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER
from config import VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY, VAPID_CLAIMS_EMAIL


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


def init_push_table():
    """Crea la tabla de suscripciones si no existe."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    id SERIAL PRIMARY KEY,
                    endpoint TEXT UNIQUE NOT NULL,
                    subscription_json TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL
                )
            """)


def save_subscription(subscription: dict):
    """Guarda o actualiza una suscripción push."""
    endpoint = subscription["endpoint"]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO push_subscriptions (endpoint, subscription_json, created_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (endpoint) DO UPDATE
                SET subscription_json = EXCLUDED.subscription_json
            """, (endpoint, json.dumps(subscription), time.time()))


def delete_subscription(endpoint: str):
    """Elimina una suscripción (p.ej. cuando el navegador la revoca)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM push_subscriptions WHERE endpoint = %s", (endpoint,))


def get_all_subscriptions() -> list[dict]:
    """Devuelve todas las suscripciones activas."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT subscription_json FROM push_subscriptions")
            return [json.loads(row["subscription_json"]) for row in cur.fetchall()]


def send_push(title: str, body: str, icon: str = "/static/icon-192.png"):
    """Envía una notificación push a todos los suscriptores."""
    payload = json.dumps({"title": title, "body": body, "icon": icon})
    dead_endpoints = []

    for sub in get_all_subscriptions():
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{VAPID_CLAIMS_EMAIL}"},
            )
        except WebPushException as e:
            status = e.response.status_code if e.response else None
            # 404/410 significa que la suscripción ya no existe
            if status in (404, 410):
                dead_endpoints.append(sub["endpoint"])
            else:
                print(f"[push] Error enviando notificación: {e}")

    for ep in dead_endpoints:
        delete_subscription(ep)
