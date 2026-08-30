"""Login, logout y token CSRF."""

import time
from collections import defaultdict

from flask import (Blueprint, jsonify, redirect, render_template, request,
                   session, url_for)

from config import ACCESS_PASSWORD, ADMIN_USERNAME
from csrf import get_csrf_token, secure_eq

bp = Blueprint("auth", __name__)


_login_attempts = defaultdict(list)
LOGIN_RATE_LIMIT = 5  # max attempts
LOGIN_RATE_WINDOW = 300  # 5 minutes


def _check_login_rate(ip):
    now = time.time()
    # Purgar IPs cuya ventana ya expiró: si no, el dict crece sin límite.
    for other in [k for k, v in _login_attempts.items()
                  if k != ip and (not v or now - v[-1] >= LOGIN_RATE_WINDOW)]:
        del _login_attempts[other]

    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < LOGIN_RATE_WINDOW]
    if len(_login_attempts[ip]) >= LOGIN_RATE_LIMIT:
        return False
    _login_attempts[ip].append(now)
    return True


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        # CSRF también en el login: sin esto, un tercero puede forzar una sesión
        # conocida por él en el navegador de la víctima (login CSRF).
        form_token = request.form.get("csrf_token", "")
        if not form_token or not secure_eq(form_token, session.get("_csrf_token", "")):
            return render_template(
                "login.html", csrf_token=get_csrf_token(),
                error="La sesión ha caducado. Inténtalo de nuevo."), 400

        if not _check_login_rate(request.remote_addr):
            return render_template(
                "login.html", csrf_token=get_csrf_token(),
                error="Demasiados intentos. Espera 5 minutos."), 429

        # compare_digest evita filtrar la contraseña por tiempo de respuesta.
        # Se evalúan siempre las dos comparaciones para no distinguir "usuario
        # incorrecto" de "contraseña incorrecta" por la duración del check.
        user_ok = secure_eq(request.form.get("username", ""), ADMIN_USERNAME)
        pass_ok = secure_eq(request.form.get("password", ""), ACCESS_PASSWORD)
        if user_ok and pass_ok:
            session.clear()
            session.permanent = True
            session["authenticated"] = True
            get_csrf_token()  # regenerate CSRF token on login
            return redirect(url_for("pages.index"))
        error = "Usuario o contraseña incorrectos"
    return render_template("login.html", csrf_token=get_csrf_token(), error=error)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/api/csrf-token")
def api_csrf_token():
    return jsonify({"token": get_csrf_token()})
