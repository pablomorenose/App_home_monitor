"""
Configuración del Home Monitor.
Los valores sensibles se leen desde variables de entorno (archivo .env).
Para añadir o quitar dispositivos solo tienes que editar la lista DEVICES
y reiniciar la app — no hay que tocar nada más.
"""

import os

from dotenv import load_dotenv

# Carga el archivo .env si existe (en local). En producción (Oracle)
# las variables se definen directamente en el entorno del servidor.
load_dotenv()

# --------------------------------------------------------------------
# BASE DE DATOS (parámetros separados para evitar problemas con
# caracteres especiales en la contraseña)
# --------------------------------------------------------------------
DB_HOST = os.getenv("DB_HOST", "db.fjyckjfrbpsneyhkrvsj.supabase.co")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.environ["DB_PASSWORD"]

# --------------------------------------------------------------------
# CONFIGURACIÓN GENERAL
# --------------------------------------------------------------------
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "15"))

# --------------------------------------------------------------------
# VAPID — notificaciones push
# --------------------------------------------------------------------
VAPID_PRIVATE_KEY    = os.environ["VAPID_PRIVATE_KEY"]
VAPID_PUBLIC_KEY     = os.environ["VAPID_PUBLIC_KEY"]
VAPID_CLAIMS_EMAIL   = os.environ["VAPID_CLAIMS_EMAIL"]

# --------------------------------------------------------------------
# HOME ASSISTANT
# --------------------------------------------------------------------
HOME_ASSISTANT_URL = os.environ["HOME_ASSISTANT_URL"]
HOME_ASSISTANT_TOKEN = os.environ["HOME_ASSISTANT_TOKEN"]

# --------------------------------------------------------------------
# DISPOSITIVOS A MONITORIZAR
# --------------------------------------------------------------------
# Para añadir un dispositivo nuevo, copia uno de los bloques de abajo
# y ajusta los campos. Tipos disponibles:
#
#   "http"      -> comprueba que una URL responde (panel web, router...)
#   "ping"      -> ping ICMP (solo para IPs accesibles directamente)
#   "port"      -> comprueba que un puerto TCP está abierto
#   "ha_entity" -> consulta el estado de una entidad en Home Assistant
#                  (ideal para cámaras y dispositivos integrados en HA)
#
# Campos según el tipo:
#   http      -> url, timeout
#   ping      -> host, timeout
#   port      -> host, port, timeout
#   ha_entity -> entity_id, timeout

DEVICES = [
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
        "url": "https://antediluvian.synology.me:5001",
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
]
