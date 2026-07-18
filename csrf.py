"""
Protección CSRF basada en tokens.
No usa Flask-WTF para evitar dependencias de formularios.
Compatible con fetch() / XHR enviando el header X-CSRF-Token.
"""

import secrets
import functools
from flask import session, request, jsonify, abort


def get_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']


def csrf_protect(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if request.method in ('POST', 'PUT', 'DELETE'):
            token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
            if not token or token != session.get('_csrf_token'):
                abort(403)
        return f(*args, **kwargs)
    return decorated
