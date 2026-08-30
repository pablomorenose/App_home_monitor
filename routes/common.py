"""Helpers compartidos por los blueprints."""

from flask import session

from config import ACCESS_PASSWORD


def require_auth():
    """Devuelve True si hay que autenticar y no está autenticado."""
    if not ACCESS_PASSWORD:
        return False
    return not session.get("authenticated")
