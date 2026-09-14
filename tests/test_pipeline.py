"""Smoke test bout-en-bout : collecte (sans réseau, données déjà en base),
prédiction, évaluation, recalibrage — sur une base SQLite temporaire isolée
(voir conftest.py)."""

from datetime import datetime, timedelta

from app import pipeline
from app.db import init_db, session_scope
from app.models import EloRating, Match, Prediction


def _seed(session):
    teams = ["Alpha FC", "Beta United", "Gamma City", "Delta Town"]
    match_id = 1
    for i in range(24):
        home = teams[i % 4]
        away = teams[(i + 1) % 4]
        date = (datetime(2024, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
        session.add(Match(
            id=match_id, competition="Test League", matchday=i + 1, date=date,
            home_team=home, away_team=away,
            home_score=2, away_score=1, status="FINISHED",
            updated_at=datetime.now().isoformat(),
        ))
        match_id += 1

    future_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    session.add(Match(
        id=match_id, competition="Test League", matchday=25, date=future_date,
        home_team="Alpha FC", away_team="Beta United",
        home_score=None, away_score=None, status="SCHEDULED",
        updated_at=datetime.now().isoformat(),
    ))
    return match_id


def test_full_pipeline_generates_predictions_without_network(monkeypatch):
    init_db()

    with session_scope() as session:
        scheduled_id = _seed(session)

    pipeline.run(force=False)

    status = pipeline.get_status()
    assert status["last_error"] is None
    assert status["last_update"] is not None

    with session_scope() as session:
        pred = session.get(Prediction, scheduled_id)
        assert pred is not None

        elo_rows = session.query(EloRating).all()
        assert len(elo_rows) >= 2
