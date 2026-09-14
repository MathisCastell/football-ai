"""Un compte gratuit ne doit voir que les championnats libres et aucun champ
premium ; un compte premium (ou admin) doit tout recevoir."""

from datetime import datetime

from app import config
from app.db import init_db, session_scope
from app.models import Match, Prediction, User
from app.server import create_app
from werkzeug.security import generate_password_hash


def _seed():
    with session_scope() as s:
        s.add(Match(
            id=1, competition=config.FREE_COMPETITIONS[0], matchday=1, date="2024-01-01",
            home_team="Free Home", away_team="Free Away", status="SCHEDULED",
            updated_at=datetime.now().isoformat(),
        ))
        s.add(Match(
            id=2, competition="Serie A", matchday=1, date="2024-01-01",
            home_team="Paid Home", away_team="Paid Away", status="SCHEDULED",
            updated_at=datetime.now().isoformat(),
        ))
        comp_by_match = {1: config.FREE_COMPETITIONS[0], 2: "Serie A"}
        for mid, comp in comp_by_match.items():
            s.add(Prediction(match_id=mid, prediction_json=(
                '{"match_id": %d, "competition": "%s", "home_win": 0.5, "draw": 0.3, "away_win": 0.2, '
                '"betting_tips": [{"label": "test"}], "advanced_home": {"xg_per90": 1.2}, '
                '"advanced_away": {"xg_per90": 0.9}}' % (mid, comp)
            ), generated_at=datetime.now().isoformat()))

        s.add(User(email="free@example.com", password_hash=generate_password_hash("hunter22"),
                    is_admin=False, is_premium=False, created_at=datetime.now().isoformat()))
        s.add(User(email="premium@example.com", password_hash=generate_password_hash("hunter22"),
                    is_admin=False, is_premium=True, created_at=datetime.now().isoformat()))


def _client():
    init_db()
    _seed()
    app = create_app()
    app.testing = True
    return app.test_client()


def test_free_user_only_sees_free_competition_and_no_premium_fields():
    c = _client()
    c.post("/auth/login", json={"email": "free@example.com", "password": "hunter22"})
    data = c.get("/api/data").get_json()

    assert data["access"]["full_access"] is False
    comps = {p["competition"] for p in data["predictions"]}
    assert comps == {config.FREE_COMPETITIONS[0]}
    for p in data["predictions"]:
        assert "betting_tips" not in p
        assert "advanced_home" not in p


def test_premium_user_sees_everything():
    c = _client()
    c.post("/auth/login", json={"email": "premium@example.com", "password": "hunter22"})
    data = c.get("/api/data").get_json()

    assert data["access"]["full_access"] is True
    comps = {p["competition"] for p in data["predictions"]}
    assert comps == {config.FREE_COMPETITIONS[0], "Serie A"}
    assert any("betting_tips" in p for p in data["predictions"])
