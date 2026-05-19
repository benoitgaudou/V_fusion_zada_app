from __future__ import annotations

from flask import Blueprint

main_bp = Blueprint('main', __name__)

from . import api, home, nlp, upload  # noqa: F401
