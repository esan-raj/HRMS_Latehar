from flask import Blueprint, jsonify

from app.database import ping_database
from app.extensions import csrf

bp = Blueprint("health", __name__)


@bp.route("/health", methods=["GET"])
@csrf.exempt
def health():
    if ping_database():
        return jsonify(status="ok")
    return jsonify(status="unavailable"), 503
