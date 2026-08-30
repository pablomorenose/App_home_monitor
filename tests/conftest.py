"""Entorno mínimo para poder importar la app en los tests.

Se fija antes de que config.py lea el entorno. load_dotenv() no pisa
variables ya presentes, así que un .env local no altera los tests.
"""

import os

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("ACCESS_PASSWORD", "test-password")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PASSWORD", "test-password")
os.environ.setdefault("STATUS_PAGE_ENABLED", "true")
