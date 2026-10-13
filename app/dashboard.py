from flask import Blueprint, render_template

from app.directory import dashboard_counts

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def index():
    return render_template("dashboard/index.html", counts=dashboard_counts())
