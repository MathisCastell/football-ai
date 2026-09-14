"""Inscription, connexion, et décorateurs de contrôle d'accès — sur l'app Flask
complète (client de test), base SQLite temporaire isolée (voir conftest.py)."""

from app.db import init_db
from app.server import create_app


def _client():
    init_db()
    app = create_app()
    app.testing = True
    return app.test_client()


def test_register_then_me_returns_user():
    c = _client()
    resp = c.post("/auth/register", json={"email": "alice@example.com", "password": "hunter22"})
    assert resp.status_code == 200
    assert resp.get_json()["user"]["email"] == "alice@example.com"

    me = c.get("/api/me").get_json()
    assert me["user"]["email"] == "alice@example.com"
    assert me["user"]["is_premium"] is False


def test_register_rejects_short_password():
    c = _client()
    resp = c.post("/auth/register", json={"email": "bob@example.com", "password": "short"})
    assert resp.status_code == 400


def test_register_rejects_duplicate_email():
    c = _client()
    c.post("/auth/register", json={"email": "dup@example.com", "password": "hunter22"})
    resp = c.post("/auth/register", json={"email": "dup@example.com", "password": "hunter22"})
    assert resp.status_code == 409


def test_login_wrong_password_rejected():
    c = _client()
    c.post("/auth/register", json={"email": "carl@example.com", "password": "hunter22"})
    c.post("/auth/logout")
    resp = c.post("/auth/login", json={"email": "carl@example.com", "password": "wrongpass"})
    assert resp.status_code == 401


def test_logout_clears_session():
    c = _client()
    c.post("/auth/register", json={"email": "dana@example.com", "password": "hunter22"})
    c.post("/auth/logout")
    me = c.get("/api/me").get_json()
    assert me["user"] is None


def test_api_data_requires_login():
    c = _client()
    resp = c.get("/api/data")
    assert resp.status_code == 401


def test_admin_routes_forbidden_for_regular_user():
    c = _client()
    c.post("/auth/register", json={"email": "eve@example.com", "password": "hunter22"})
    assert c.get("/api/admin/users").status_code == 403
    assert c.get("/api/refresh").status_code == 403


def test_model_performance_requires_premium():
    c = _client()
    c.post("/auth/register", json={"email": "frank@example.com", "password": "hunter22"})
    assert c.get("/api/model-performance").status_code == 403


def _make_admin(email):
    from datetime import datetime
    from app.db import session_scope
    from app.models import User
    from werkzeug.security import generate_password_hash
    with session_scope() as s:
        s.add(User(email=email, password_hash=generate_password_hash("hunter22"),
                    is_admin=True, is_premium=True, created_at=datetime.now().isoformat()))


def test_admin_can_grant_premium_to_another_user():
    c = _client()
    _make_admin("boss@example.com")
    c.post("/auth/register", json={"email": "grace@example.com", "password": "hunter22"})
    c.post("/auth/logout")
    c.post("/auth/login", json={"email": "boss@example.com", "password": "hunter22"})

    from app.db import session_scope
    from app.models import User
    with session_scope() as s:
        target_id = s.query(User).filter(User.email == "grace@example.com").first().id

    resp = c.post(f"/api/admin/users/{target_id}", json={"is_premium": True})
    assert resp.status_code == 200
    with session_scope() as s:
        assert s.get(User, target_id).is_premium is True


def test_admin_cannot_remove_own_admin_rights():
    c = _client()
    _make_admin("solo-admin@example.com")
    c.post("/auth/login", json={"email": "solo-admin@example.com", "password": "hunter22"})

    from app.db import session_scope
    from app.models import User
    with session_scope() as s:
        self_id = s.query(User).filter(User.email == "solo-admin@example.com").first().id

    resp = c.post(f"/api/admin/users/{self_id}", json={"is_admin": False})
    assert resp.status_code == 400
    with session_scope() as s:
        assert s.get(User, self_id).is_admin is True
