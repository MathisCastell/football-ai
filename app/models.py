"""Modèles SQLAlchemy — fonctionnent à l'identique sur PostgreSQL (production)
et SQLite (développement local)."""

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True)
    competition = Column(String, index=True)
    matchday = Column(Integer)
    date = Column(String, index=True)
    home_team = Column(String, index=True)
    away_team = Column(String, index=True)
    home_score = Column(Integer, nullable=True)
    away_score = Column(Integer, nullable=True)
    status = Column(String, index=True)

    home_xg = Column(Float, nullable=True)
    away_xg = Column(Float, nullable=True)
    home_shots = Column(Integer, nullable=True)
    away_shots = Column(Integer, nullable=True)
    home_shots_on_target = Column(Integer, nullable=True)
    away_shots_on_target = Column(Integer, nullable=True)
    home_possession = Column(Float, nullable=True)
    away_possession = Column(Float, nullable=True)
    home_corners = Column(Integer, nullable=True)
    away_corners = Column(Integer, nullable=True)
    home_fouls = Column(Integer, nullable=True)
    away_fouls = Column(Integer, nullable=True)
    home_yellow_cards = Column(Integer, nullable=True)
    away_yellow_cards = Column(Integer, nullable=True)
    home_red_cards = Column(Integer, nullable=True)
    away_red_cards = Column(Integer, nullable=True)
    home_formation = Column(String, nullable=True)
    away_formation = Column(String, nullable=True)

    updated_at = Column(String)


class Lineup(Base):
    __tablename__ = "lineups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id"), index=True)
    team = Column(String, index=True)
    player_name = Column(String)
    position = Column(String)
    is_starter = Column(Boolean, default=True)


class Injury(Base):
    __tablename__ = "injuries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    team = Column(String, index=True)
    player_name = Column(String)
    injury_type = Column(String)
    detail = Column(String)
    competition = Column(String)
    updated_at = Column(String)


class Prediction(Base):
    """Dernière prédiction générée pour un match. Figée dès que le match passe
    au statut FINISHED (le pipeline ne régénère plus de prédiction pour un match joué)."""
    __tablename__ = "predictions"

    match_id = Column(Integer, ForeignKey("matches.id"), primary_key=True)
    prediction_json = Column(Text)
    generated_at = Column(String)


class PredictionScore(Base):
    """Notation immuable d'une prédiction une fois le match terminé — c'est la mémoire
    d'erreur qui alimente le recalibrage automatique du modèle."""
    __tablename__ = "prediction_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id"), unique=True, index=True)
    competition = Column(String, index=True)
    evaluated_at = Column(String)
    brier_score = Column(Float)
    log_loss = Column(Float)
    favorite_correct = Column(Boolean)
    expected_goals_mae = Column(Float)
    predicted_home_win = Column(Float)
    predicted_draw = Column(Float)
    predicted_away_win = Column(Float)
    actual_result = Column(String)  # "H" | "D" | "A"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    is_premium = Column(Boolean, default=False)
    created_at = Column(String)
    last_login_at = Column(String, nullable=True)


class SupportMessage(Base):
    """Messages du formulaire de contact — consultables dans l'espace admin."""
    __tablename__ = "support_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    name = Column(String)
    email = Column(String)
    message = Column(Text)
    created_at = Column(String)
    status = Column(String, default="open")  # "open" | "resolved"


class EloRating(Base):
    __tablename__ = "elo_ratings"

    team = Column(String, primary_key=True)
    rating = Column(Integer)
    updated_at = Column(String)


class ModelParams(Base):
    """Hyperparamètres du moteur de prédiction, par compétition ('GLOBAL' par défaut).
    Ajustés automatiquement par app/learning/calibrate.py à partir de l'historique
    des erreurs de prédiction — c'est ici que le modèle 'retient' ce qu'il a appris."""
    __tablename__ = "model_params"

    competition = Column(String, primary_key=True)
    k_factor = Column(Float, default=32.0)
    home_advantage_elo = Column(Float, default=100.0)
    poisson_home_adv = Column(Float, default=1.3)

    # Calibration Platt : probabilité calibrée = sigmoid(a * logit(p_brute) + b)
    calib_home_a = Column(Float, default=1.0)
    calib_home_b = Column(Float, default=0.0)
    calib_draw_a = Column(Float, default=1.0)
    calib_draw_b = Column(Float, default=0.0)
    calib_away_a = Column(Float, default=1.0)
    calib_away_b = Column(Float, default=0.0)

    sample_size = Column(Integer, default=0)
    last_log_loss = Column(Float, nullable=True)
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


DEFAULT_PARAMS = dict(
    k_factor=32.0,
    home_advantage_elo=100.0,
    poisson_home_adv=1.3,
    calib_home_a=1.0, calib_home_b=0.0,
    calib_draw_a=1.0, calib_draw_b=0.0,
    calib_away_a=1.0, calib_away_b=0.0,
    sample_size=0,
)
