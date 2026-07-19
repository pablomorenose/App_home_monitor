"""
Configuración del Home Monitor.
Los valores sensibles se leen exclusivamente desde variables de entorno (.env).
Nunca se deben poner secretos en este archivo ni en Git.
"""

import os
import sys
import logging

from dotenv import load_dotenv

# Carga .env si existe (desarrollo local). En producción las vars se
# definen en el entorno del contenedor.
load_dotenv()

# ────────────────────────────────────────────────────────────────────
# ENTORNO DE APLICACIÓN
# ────────────────────────────────────────────────────────────────────
APP_ENV = os.getenv("APP_ENV", "production")  # production | development
APP_VERSION = "2.0.0"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
TZ = os.getenv("TZ", "Europe/Madrid")

# Configurar logging global
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("config")

# ────────────────────────────────────────────────────────────────────
# SECRETOS Y AUTENTICACIÓN
# ────────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "")
ACCESS_PASSWORD = os.getenv("ACCESS_PASSWORD", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")

# Si es true, permite arrancar sin ACCESS_PASSWORD (solo desarrollo)
ALLOW_INSECURE_NO_AUTH = os.getenv("ALLOW_INSECURE_NO_AUTH", "false").lower() == "true"

# ────────────────────────────────────────────────────────────────────
# BASE DE DATOS
# ────────────────────────────────────────────────────────────────────
DB_HOST = os.getenv("DB_HOST", "")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# ────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN GENERAL
# ────────────────────────────────────────────────────────────────────
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "15"))
MAX_CHECK_WORKERS = int(os.getenv("MAX_CHECK_WORKERS", "20"))

# ────────────────────────────────────────────────────────────────────
# VAPID — notificaciones push (opcionales si no se usan)
# ────────────────────────────────────────────────────────────────────
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_CLAIMS_EMAIL = os.getenv("VAPID_CLAIMS_EMAIL", "")
PUSH_ENABLED = bool(VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY and VAPID_CLAIMS_EMAIL)

# ────────────────────────────────────────────────────────────────────
# HOME ASSISTANT (opcionales si no hay monitores HA)
# ────────────────────────────────────────────────────────────────────
HOME_ASSISTANT_URL = os.getenv("HOME_ASSISTANT_URL", "")
HOME_ASSISTANT_TOKEN = os.getenv("HOME_ASSISTANT_TOKEN", "")
HA_ENABLED = bool(HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN)

# ────────────────────────────────────────────────────────────────────
# STATUS PAGE (public, no auth required)
# ────────────────────────────────────────────────────────────────────
STATUS_PAGE_ENABLED = os.getenv("STATUS_PAGE_ENABLED", "true").lower() == "true"

# ────────────────────────────────────────────────────────────────────
# DOCKER METRICS
# ────────────────────────────────────────────────────────────────────
DOCKER_METRICS_ENABLED = os.getenv("DOCKER_METRICS_ENABLED", "true").lower() == "true"

# ────────────────────────────────────────────────────────────────────
# DISPOSITIVOS INICIALES (seed) — se migran a BD en el primer arranque
# ────────────────────────────────────────────────────────────────────
DEVICES = [
    {
        "id": "raspberry_pi",
        "name": "Raspberry Pi",
        "type": "system",
        "timeout": 5,
    },
    {
        "id": "video_scanner",
        "name": "Video Scanner",
        "type": "http",
        "url": "http://localhost:9090",
        "timeout": 8,
    },
    {
        "id": "home_assistant",
        "name": "Home Assistant",
        "type": "http",
        "url": HOME_ASSISTANT_URL,
        "timeout": 8,
    },
    {
        "id": "nas_synology",
        "name": "NAS Synology",
        "type": "http",
        "url": os.getenv("NAS_URL", "https://nas.local:5001"),
        "timeout": 8,
    },
    {
        "id": "camara_sonoff1",
        "name": "Cámara Sonoff 1",
        "type": "ha_entity",
        "entity_id": "camera.sonoff1",
        "timeout": 8,
    },
    {
        "id": "camara_sonoff2",
        "name": "Cámara Sonoff 2",
        "type": "ha_entity",
        "entity_id": "camera.sonoff2",
        "timeout": 8,
    },
    {
        "id": "camara_sonoff3",
        "name": "Cámara Sonoff 3",
        "type": "ha_entity",
        "entity_id": "camera.sonoff3",
        "timeout": 8,
    },
    {
        "id": "camara_sonoff4",
        "name": "Cámara Sonoff 4",
        "type": "ha_entity",
        "entity_id": "camera.sonoff4",
        "timeout": 8,
    },
    {
        "id": "adguard",
        "name": "AdGuard Home",
        "type": "ha_switch",
        "entity_id": "switch.adguard_home_proteccion",
        "timeout": 8,
    },
] if HA_ENABLED else [
    {
        "id": "raspberry_pi",
        "name": "Raspberry Pi",
        "type": "system",
        "timeout": 5,
    },
    {
        "id": "video_scanner",
        "name": "Video Scanner",
        "type": "http",
        "url": "http://localhost:9090",
        "timeout": 8,
    },
]


# ────────────────────────────────────────────────────────────────────
# VALIDACIÓN DE CONFIGURACIÓN
# ────────────────────────────────────────────────────────────────────

_INSECURE_SECRET_KEYS = {
    "",
    "homem-dev-secret-change-me",
    "change-me",
    "secret",
    "dev",
    "test",
}


def validate_config():
    """
    Valida la configuración al arranque.
    En producción falla si faltan configuraciones críticas.
    En desarrollo emite advertencias pero permite arrancar.
    Nunca imprime valores de secretos en los mensajes de error.
    """
    errors = []
    warnings = []

    is_production = APP_ENV == "production"

    # --- SECRET_KEY ---
    if SECRET_KEY.lower() in _INSECURE_SECRET_KEYS:
        msg = "SECRET_KEY no está configurada o usa un valor inseguro por defecto."
        if is_production:
            errors.append(msg)
        else:
            warnings.append(msg + " (aceptable solo en desarrollo)")

    if SECRET_KEY and len(SECRET_KEY) < 16:
        msg = "SECRET_KEY debe tener al menos 16 caracteres."
        if is_production:
            errors.append(msg)
        else:
            warnings.append(msg)

    # --- ACCESS_PASSWORD ---
    if not ACCESS_PASSWORD and not ALLOW_INSECURE_NO_AUTH:
        msg = (
            "ACCESS_PASSWORD no está configurada. "
            "La app no arrancará sin autenticación en producción. "
            "Para desarrollo sin auth, usa ALLOW_INSECURE_NO_AUTH=true."
        )
        if is_production:
            errors.append(msg)
        else:
            warnings.append(msg)

    # --- BASE DE DATOS ---
    if not DB_HOST:
        errors.append("DB_HOST es obligatorio.")
    if not DB_PASSWORD:
        errors.append("DB_PASSWORD es obligatorio.")

    # --- CHECK_INTERVAL ---
    if CHECK_INTERVAL_SECONDS < 5:
        errors.append("CHECK_INTERVAL_SECONDS debe ser >= 5 para evitar sobrecarga.")

    # --- VAPID (solo si push habilitado) ---
    if PUSH_ENABLED:
        if not VAPID_PRIVATE_KEY:
            errors.append("VAPID_PRIVATE_KEY es obligatorio cuando Push está habilitado.")
        if not VAPID_PUBLIC_KEY:
            errors.append("VAPID_PUBLIC_KEY es obligatorio cuando Push está habilitado.")
        if not VAPID_CLAIMS_EMAIL:
            errors.append("VAPID_CLAIMS_EMAIL es obligatorio cuando Push está habilitado.")

    # --- HOME ASSISTANT (solo si hay monitores HA) ---
    # Se valida si HA_ENABLED está activo
    if HA_ENABLED:
        if not HOME_ASSISTANT_URL.startswith(("http://", "https://")):
            errors.append("HOME_ASSISTANT_URL debe ser una URL válida (http:// o https://).")
        if not HOME_ASSISTANT_TOKEN:
            errors.append("HOME_ASSISTANT_TOKEN es obligatorio cuando hay monitores Home Assistant.")

    # --- Emitir resultados ---
    for w in warnings:
        logger.warning("⚠️  CONFIG: %s", w)

    if errors:
        logger.error("=" * 60)
        logger.error("CONFIGURACIÓN INVÁLIDA — La app no puede arrancar:")
        logger.error("=" * 60)
        for e in errors:
            logger.error("  ✗ %s", e)
        logger.error("=" * 60)
        logger.error("Revisa tus variables de entorno (.env o Docker environment).")
        sys.exit(1)

    logger.info("✓ Configuración validada (%s)", APP_ENV)
