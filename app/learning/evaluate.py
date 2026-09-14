"""Note chaque prédiction une fois le match terminé — c'est la mémoire d'erreur
brute que app/learning/calibrate.py utilise ensuite pour s'auto-corriger."""

import json
import math
from datetime import datetime

from app.models import Match, Prediction, PredictionScore


def _outcome_vector(result):
    return {"H": (1, 0, 0), "D": (0, 1, 0), "A": (0, 0, 1)}[result]


def evaluate_pending(session):
    """Note toutes les prédictions dont le match est terminé mais pas encore évalué."""
    already_scored = {r[0] for r in session.query(PredictionScore.match_id).all()}

    finished_ids = {
        m.id for m in session.query(Match.id)
        .filter(Match.status == "FINISHED")
        .filter(Match.home_score.isnot(None))
        .all()
    }
    to_score = finished_ids - already_scored
    if not to_score:
        return 0

    scored = 0
    for match_id in to_score:
        pred_row = session.get(Prediction, match_id)
        match = session.get(Match, match_id)
        if not pred_row or not match:
            continue

        pred = json.loads(pred_row.prediction_json)
        p_home, p_draw, p_away = pred["home_win"], pred["draw"], pred["away_win"]

        if match.home_score > match.away_score:
            result = "H"
        elif match.home_score < match.away_score:
            result = "A"
        else:
            result = "D"

        actual = _outcome_vector(result)
        probs = (p_home, p_draw, p_away)

        brier = sum((p - a) ** 2 for p, a in zip(probs, actual))
        p_actual = probs[actual.index(1)]
        log_loss = -math.log(max(p_actual, 1e-9))

        favorite = max(("H", "D", "A"), key=lambda k: {"H": p_home, "D": p_draw, "A": p_away}[k])
        favorite_correct = favorite == result

        eg_mae = (
            abs(pred.get("expected_home_goals", 0) - match.home_score)
            + abs(pred.get("expected_away_goals", 0) - match.away_score)
        ) / 2

        session.add(PredictionScore(
            match_id=match_id, competition=match.competition,
            evaluated_at=datetime.now().isoformat(),
            brier_score=round(brier, 4), log_loss=round(log_loss, 4),
            favorite_correct=favorite_correct, expected_goals_mae=round(eg_mae, 2),
            predicted_home_win=p_home, predicted_draw=p_draw, predicted_away_win=p_away,
            actual_result=result,
        ))
        scored += 1

    return scored
