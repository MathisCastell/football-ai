"""Moteur SQLAlchemy partagé — PostgreSQL en production, SQLite en local."""

from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app import config
from app.models import Base

_connect_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(config.DATABASE_URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)


def init_db():
    Base.metadata.create_all(engine)
    _run_column_migrations()


def _run_column_migrations():
    """Ajoute les colonnes manquantes sur une base existante (pas d'Alembic pour ce
    projet, on reste sur un garde simple comme dans l'ancien Server.py)."""
    inspector = inspect(engine)
    if "matches" not in inspector.get_table_names():
        return
    existing_cols = {c["name"] for c in inspector.get_columns("matches")}
    wanted = {c.name: c.type for c in Base.metadata.tables["matches"].columns}
    missing = [name for name in wanted if name not in existing_cols]
    if not missing:
        return
    with engine.begin() as conn:
        for name in missing:
            col_type = wanted[name]
            sql_type = "INTEGER" if "Integer" in col_type.__class__.__name__ else \
                       "FLOAT" if "Float" in col_type.__class__.__name__ else "TEXT"
            try:
                conn.execute(text(f"ALTER TABLE matches ADD COLUMN {name} {sql_type}"))
            except Exception:
                pass


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
