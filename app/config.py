"""Configuration centralisée : variables d'environnement (.env en local, vraies
variables d'environnement en production sur Render)."""

import os


def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# Base de données : Postgres en production (DATABASE_URL fourni par Render),
# repli automatique sur un fichier SQLite local si absent (aucune install requise en dev).
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    # SQLAlchemy 2.x exige le préfixe "postgresql://" (Render fournit l'ancien format historique)
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if not DATABASE_URL:
    DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "football.db")
    DATABASE_URL = f"sqlite:///{DB_FILE}"

FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY", "").strip()

# Intervalle entre deux cycles automatiques du pipeline (collecte + prédiction + apprentissage)
PIPELINE_INTERVAL_HOURS = float(os.environ.get("PIPELINE_INTERVAL_HOURS", "6"))

# Nombre minimum de prédictions notées avant d'activer la calibration Platt par compétition
MIN_SAMPLES_FOR_CALIBRATION = int(os.environ.get("MIN_SAMPLES_FOR_CALIBRATION", "40"))

PORT = int(os.environ.get("PORT", "5000"))

_now_year = __import__("datetime").datetime.now()
CURRENT_SEASON = _now_year.year if _now_year.month >= 8 else _now_year.year - 1

# ── Comptes / freemium ──────────────────────────────────────────
import secrets as _secrets

# Signe les cookies de session. En prod, Render fournit une valeur persistante
# (render.yaml, generateValue). En dev, une valeur aléatoire par process suffit
# (les sessions ne survivent pas à un redémarrage, ce qui est acceptable en local).
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip() or _secrets.token_hex(32)

# Compte admin créé automatiquement au démarrage s'il n'existe pas déjà.
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()

# Championnats accessibles aux comptes gratuits (les autres nécessitent premium).
FREE_COMPETITIONS = [
    c.strip() for c in os.environ.get("FREE_COMPETITIONS", "Premier League,Ligue 1").split(",")
    if c.strip()
]
