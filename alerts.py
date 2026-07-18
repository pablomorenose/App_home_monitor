"""
Sistema de alertas multicanal para Home Monitor.

Canales soportados:
- Web Push (via pywebpush, migrado desde notifications.py)
- Webhook (POST JSON a URL configurable con HMAC signature)
- Telegram (via Bot API)

Cada canal se auto-activa cuando sus variables de entorno están configuradas.
Rate limiting: máximo 1 alerta por monitor cada 5 minutos.
"""

import hashlib
import hmac
import json
import logging
import os
import time
from abc import ABC, abstractmethod

import requests

from config import PUSH_ENABLED

logger = logging.getLogger("alerts")

# ────────────────────────────────────────────────────────────────────
# Configuración de canales (todas las vars son opcionales)
# ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# Rate limiting: max 1 alert per monitor per 5 minutes
_RATE_LIMIT_SECONDS = 300
_rate_limit_cache: dict[str, float] = {}

# ────────────────────────────────────────────────────────────────────
# Alert templates
# ────────────────────────────────────────────────────────────────────
ALERT_TEMPLATES = {
    "down": "🔴 {name} is DOWN — {message}",
    "recovery": "🟢 {name} recovered — was down for {duration}",
    "degraded": "🟡 {name} degraded — {message}",
    "tls_expiring": "🟡 {name} TLS cert expires in {days}d",
}


def _format_alert(event_type: str, monitor: dict, details: dict) -> str:
    """Format alert message based on event type and template."""
    name = monitor.get("name", "Unknown")
    template = ALERT_TEMPLATES.get(event_type, "⚪ {name} — {message}")

    if event_type == "down":
        message = details.get("message", "No response")
        return template.format(name=name, message=message)
    elif event_type == "recovery":
        duration = details.get("duration", "unknown")
        return template.format(name=name, duration=duration)
    elif event_type == "degraded":
        message = details.get("message", "High latency")
        return template.format(name=name, message=message)
    elif event_type == "tls_expiring":
        days = details.get("days", "?")
        return template.format(name=name, days=days)
    else:
        return f"⚪ {name} — {details.get('message', event_type)}"


# ────────────────────────────────────────────────────────────────────
# Base interface
# ────────────────────────────────────────────────────────────────────

class NotificationChannel(ABC):
    """Base interface for notification channels."""

    @abstractmethod
    def is_enabled(self) -> bool:
        """Returns True if the channel is properly configured and active."""
        ...

    @abstractmethod
    def send(self, event_type: str, monitor: dict, details: dict, message: str) -> bool:
        """
        Send a notification through this channel.

        Args:
            event_type: 'down', 'recovery', 'degraded', 'tls_expiring'
            monitor: Monitor dict with at least 'id' and 'name'
            details: Event details dict (message, duration, days, etc.)
            message: Pre-formatted alert message string

        Returns:
            True if sent successfully, False otherwise.
        """
        ...


# ────────────────────────────────────────────────────────────────────
# Web Push channel
# ────────────────────────────────────────────────────────────────────

class WebPushChannel(NotificationChannel):
    """Web Push notifications via pywebpush (wraps notifications.py)."""

    def is_enabled(self) -> bool:
        return PUSH_ENABLED

    def send(self, event_type: str, monitor: dict, details: dict, message: str) -> bool:
        try:
            from notifications import send_push
            title = self._get_title(event_type)
            send_push(title=title, body=message)
            return True
        except Exception as e:
            logger.error("WebPush error: %s", e)
            return False

    def _get_title(self, event_type: str) -> str:
        titles = {
            "down": "🔴 Monitor Down",
            "recovery": "🟢 Monitor Recovered",
            "degraded": "🟡 Monitor Degraded",
            "tls_expiring": "🟡 TLS Certificate Expiring",
        }
        return titles.get(event_type, "⚪ Monitor Alert")


# ────────────────────────────────────────────────────────────────────
# Webhook channel
# ────────────────────────────────────────────────────────────────────

class WebhookChannel(NotificationChannel):
    """POST JSON to configurable URL with optional HMAC signature."""

    def is_enabled(self) -> bool:
        return bool(WEBHOOK_URL)

    def send(self, event_type: str, monitor: dict, details: dict, message: str) -> bool:
        try:
            payload = {
                "event_type": event_type,
                "monitor_id": monitor.get("id", ""),
                "monitor_name": monitor.get("name", ""),
                "state": details.get("state", event_type),
                "message": message,
                "details": details,
                "timestamp": time.time(),
            }
            body = json.dumps(payload, ensure_ascii=False)
            headers = {"Content-Type": "application/json"}

            # Add HMAC signature if secret is configured
            if WEBHOOK_SECRET:
                signature = hmac.HMAC(
                    WEBHOOK_SECRET.encode("utf-8"),
                    body.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                headers["X-Signature-256"] = f"sha256={signature}"

            resp = requests.post(
                WEBHOOK_URL,
                data=body,
                headers=headers,
                timeout=10,
            )
            if resp.status_code >= 400:
                logger.warning("Webhook returned %d: %s", resp.status_code, resp.text[:200])
                return False
            return True
        except Exception as e:
            logger.error("Webhook error: %s", e)
            return False


# ────────────────────────────────────────────────────────────────────
# Telegram channel
# ────────────────────────────────────────────────────────────────────

class TelegramChannel(NotificationChannel):
    """Send alerts via Telegram Bot API."""

    def is_enabled(self) -> bool:
        return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

    def send(self, event_type: str, monitor: dict, details: dict, message: str) -> bool:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.warning("Telegram API returned %d: %s", resp.status_code, resp.text[:200])
                return False
            return True
        except Exception as e:
            logger.error("Telegram error: %s", e)
            return False


# ────────────────────────────────────────────────────────────────────
# Channel registry
# ────────────────────────────────────────────────────────────────────

_channels: list[NotificationChannel] = [
    WebPushChannel(),
    WebhookChannel(),
    TelegramChannel(),
]


def get_enabled_channels() -> list[NotificationChannel]:
    """Returns list of currently enabled notification channels."""
    return [ch for ch in _channels if ch.is_enabled()]


# ────────────────────────────────────────────────────────────────────
# Rate limiting
# ────────────────────────────────────────────────────────────────────

def _is_rate_limited(monitor_id: str) -> bool:
    """Check if a monitor is rate-limited (max 1 alert per 5 min)."""
    now = time.time()
    last_sent = _rate_limit_cache.get(monitor_id, 0)
    if (now - last_sent) < _RATE_LIMIT_SECONDS:
        return True
    return False


def _update_rate_limit(monitor_id: str):
    """Record that an alert was sent for this monitor."""
    _rate_limit_cache[monitor_id] = time.time()


# ────────────────────────────────────────────────────────────────────
# Main public API
# ────────────────────────────────────────────────────────────────────

def send_alert(event_type: str, monitor: dict, details: dict) -> bool:
    """
    Broadcast alert to all enabled notification channels.

    Args:
        event_type: 'down', 'recovery', 'degraded', 'tls_expiring'
        monitor: Monitor dict (must have 'id' and 'name')
        details: Event details dict. Keys depend on event_type:
            - down: message, state
            - recovery: duration, state
            - degraded: message, state
            - tls_expiring: days, state

    Returns:
        True if at least one channel sent successfully, False otherwise.
    """
    monitor_id = monitor.get("id", "unknown")

    # Rate limiting check
    if _is_rate_limited(monitor_id):
        logger.debug("Alert rate-limited for monitor %s", monitor_id)
        return False

    # Get enabled channels
    enabled = get_enabled_channels()
    if not enabled:
        logger.debug("No notification channels enabled")
        return False

    # Format alert message
    message = _format_alert(event_type, monitor, details)

    # Broadcast to all enabled channels
    any_success = False
    for channel in enabled:
        try:
            success = channel.send(event_type, monitor, details, message)
            if success:
                any_success = True
        except Exception as e:
            logger.error("Error sending via %s: %s", channel.__class__.__name__, e)

    # Update rate limit only if at least one channel succeeded
    if any_success:
        _update_rate_limit(monitor_id)

    return any_success
