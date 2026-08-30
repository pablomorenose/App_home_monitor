"""
Protección CSRF basada en tokens.
No usa Flask-WTF para evitar dependencias de formularios.
Compatible con fetch() / XHR enviando el header X-CSRF-Token.
"""

import hmac
import secrets
import functools
from flask import session, request, jsonify, abort


def secure_eq(a: str, b: str) -> bool:
    """Comparación en tiempo constante tolerante a texto no-ASCII.

    hmac.compare_digest lanza TypeError con str no-ASCII, y estos valores
    vienen del usuario (contraseña con tildes, token manipulado a mano).
    """
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


def get_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']


def csrf_protect(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if request.method in ('POST', 'PUT', 'DELETE'):
            token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
            if not token or not secure_eq(token, session.get('_csrf_token')):
                abort(403)
        return f(*args, **kwargs)
    return decorated
