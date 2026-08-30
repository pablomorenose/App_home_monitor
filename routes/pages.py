"""Páginas HTML del dashboard."""

from flask import Blueprint, redirect, render_template, url_for

from routes.common import require_auth

bp = Blueprint("pages", __name__)


@bp.route("/")
def index():
    if require_auth():
        return redirect(url_for("auth.login"))
    return render_template("index.html")


@bp.route("/historial")
def historial():
    if require_auth():
        return redirect(url_for("auth.login"))
    return render_template("historial.html")
