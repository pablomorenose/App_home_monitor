"""Suscripciones de Web Push."""

from flask import Blueprint, jsonify, request

from config import VAPID_PUBLIC_KEY
from csrf import csrf_protect
from notifications import delete_subscription, save_subscription

bp = Blueprint("push", __name__)


@bp.route("/api/vapid-key")
def vapid_key():
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})


@bp.route("/api/subscribe", methods=["POST"])
@csrf_protect
def subscribe():
    sub = request.get_json()
    if not sub or "endpoint" not in sub:
        return jsonify({"error": "Suscripción inválida"}), 400
    save_subscription(sub)
    return jsonify({"ok": True}), 201


@bp.route("/api/unsubscribe", methods=["POST"])
@csrf_protect
def unsubscribe():
    data = request.get_json()
    if data and "endpoint" in data:
        delete_subscription(data["endpoint"])
    return jsonify({"ok": True})
