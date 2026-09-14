"""Force un DATABASE_URL SQLite temporaire avant que le reste de la suite
n'importe app.db (qui crée son engine SQLAlchemy à l'import), et repart d'une
base vide avant chaque test — tous les fichiers de test partagent le même
engine (créé une fois à l'import), donc sans ça les ids insérés par un test
entreraient en collision avec ceux d'un autre."""

import os
import tempfile

import pytest

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db.name}"


@pytest.fixture(autouse=True)
def _reset_db():
    from app.db import engine
    from app.models import Base
    Base.metadata.drop_all(engine)
    yield
