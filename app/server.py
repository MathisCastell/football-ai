"""Fabrique l'application Flask (routes + auth + base de données), sans démarrer
le planificateur de pipeline ni le serveur HTTP — utilisé par main.py en
production et par les tests (qui ne veulent pas de collecte réseau en fond)."""

from flask import Flask

from app import config
from app.api import bp
from app.auth import bootstrap_admin
from app.auth import bp as auth_bp
from app.db import init_db


def create_app():
    app = Flask(__name__, static_folder=None)
    app.secret_key = config.SECRET_KEY
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    app.register_blueprint(bp)
    app.register_blueprint(auth_bp)

    init_db()
    bootstrap_admin()
    return app
