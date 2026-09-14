"""Authentification par session Flask (cookie signé, pas de JWT — le plus simple
et le plus sûr pour une appli mono-domaine comme celle-ci) + décorateurs de contrôle
d'accès réutilisés par les routes API (app/api.py)."""

import re
from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from app import config
from app.db import session_scope
from app.models import User

bp = Blueprint("auth", __name__, url_prefix="/auth")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def bootstrap_admin():
    """Crée (ou promeut) le compte admin défini par ADMIN_EMAIL/ADMIN_PASSWORD
    au démarrage, s'il n'existe pas déjà. Pattern standard pour une appli
    auto-hébergée sans système d'invitation."""
    if not config.ADMIN_EMAIL or not config.ADMIN_PASSWORD:
        return
    with session_scope() as session_:
        user = session_.query(User).filter(User.email == config.ADMIN_EMAIL).first()
        if user is None:
            session_.add(User(
                email=config.ADMIN_EMAIL,
                password_hash=generate_password_hash(config.ADMIN_PASSWORD),
                is_admin=True, is_premium=True,
                created_at=datetime.now().isoformat(),
            ))
        elif not user.is_admin:
            user.is_admin = True


def current_user(session_):
    user_id = session.get("user_id")
    if not user_id:
        return None
    return session_.get(User, user_id)


def user_to_dict(user):
    if user is None:
        return None
    return {
        "id": user.id, "email": user.email,
        "is_admin": user.is_admin, "is_premium": user.is_premium,
    }


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Connexion requise."}), 401
        return fn(*args, **kwargs)
    return wrapper


def premium_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with session_scope() as session_:
            user = current_user(session_)
            if user is None:
                return jsonify({"error": "Connexion requise."}), 401
            if not (user.is_premium or user.is_admin):
                return jsonify({"error": "Fonctionnalité réservée aux comptes premium."}), 403
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with session_scope() as session_:
            user = current_user(session_)
            if user is None or not user.is_admin:
                return jsonify({"error": "Accès administrateur requis."}), 403
        return fn(*args, **kwargs)
    return wrapper


@bp.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not EMAIL_RE.match(email):
        return jsonify({"error": "Adresse email invalide."}), 400
    if len(password) < 8:
        return jsonify({"error": "Le mot de passe doit contenir au moins 8 caractères."}), 400

    with session_scope() as session_:
        if session_.query(User).filter(User.email == email).first():
            return jsonify({"error": "Un compte existe déjà avec cet email."}), 409

        user = User(
            email=email, password_hash=generate_password_hash(password),
            is_admin=False, is_premium=False,
            created_at=datetime.now().isoformat(),
            last_login_at=datetime.now().isoformat(),
        )
        session_.add(user)
        session_.flush()
        session["user_id"] = user.id
        return jsonify({"ok": True, "user": user_to_dict(user)})


@bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    with session_scope() as session_:
        user = session_.query(User).filter(User.email == email).first()
        if user is None or not check_password_hash(user.password_hash, password):
            return jsonify({"error": "Email ou mot de passe incorrect."}), 401

        user.last_login_at = datetime.now().isoformat()
        session["user_id"] = user.id
        return jsonify({"ok": True, "user": user_to_dict(user)})


@bp.route("/logout", methods=["POST"])
def logout():
    session.pop("user_id", None)
    return jsonify({"ok": True})
