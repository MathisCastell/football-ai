"""Orchestration complète : collecte -> prédiction -> évaluation -> recalibrage.
C'est le cœur du cycle d'auto-apprentissage : chaque exécution mesure l'erreur
des prédictions passées et ajuste les hyperparamètres avant de générer les
nouvelles prédictions."""

import json
import threading
from datetime import datetime

from app import config
from app.collectors import thesportsdb
from app.db import session_scope
from app.learning.calibrate import apply_calibration, get_params
from app.learning.evaluate import evaluate_pending
from app.learning import calibrate as calibrate_mod
from app.models import EloRating as EloRatingModel
from app.models import Match, Prediction
from app.prediction.elo import EloRating
from app.prediction.features import (
    compute_advanced_metrics, compute_confidence, get_form, get_h2h, get_rest_days,
    get_team_injuries, get_team_lineups,
)
from app.prediction.poisson import build_poisson, predict_match_advanced

_lock = threading.Lock()
_state = {"is_running": False, "last_update": None, "last_error": None}


def get_status():
    return dict(_state)


def run(force=False):
    if not _lock.acquire(blocking=False):
        print("[PIPELINE] Déjà en cours, appel ignoré.")
        return
    _state["is_running"] = True
    _state["last_error"] = None
    try:
        print(f"\n[PIPELINE] Démarrage — {datetime.now().isoformat()}")
        with session_scope() as session:
            thesportsdb.collect(session, force=force)

        with session_scope() as session:
            _generate_predictions(session)

        with session_scope() as session:
            n_scored = evaluate_pending(session)
            print(f"[PIPELINE] {n_scored} prédictions notées (comparées au résultat réel).")

            all_matches = [_row_to_dict(m) for m in session.query(Match).all()]
            finished = [m for m in all_matches if m["status"] == "FINISHED" and m["home_score"] is not None]
            competitions = sorted({m["competition"] for m in finished if m["competition"]})
            finished_by_competition = {c: [m for m in finished if m["competition"] == c] for c in competitions}
            calibrate_mod.recalibrate(session, finished_by_competition, competitions)
            print(f"[PIPELINE] Recalibrage effectué pour {len(competitions)} compétitions.")

        _state["last_update"] = datetime.now().isoformat()
        print(f"[PIPELINE] Terminé — {_state['last_update']}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        _state["last_error"] = str(e)
    finally:
        _state["is_running"] = False
        _lock.release()


def _row_to_dict(match: Match):
    return {c.name: getattr(match, c.name) for c in match.__table__.columns}


def _generate_predictions(session):
    all_matches = [_row_to_dict(m) for m in session.query(Match).all()]
    finished = [m for m in all_matches if m["status"] == "FINISHED" and m["home_score"] is not None]
    scheduled = [m for m in all_matches if m["status"] in ("SCHEDULED", "TIMED")]
    print(f"[PIPELINE] {len(finished)} terminés / {len(scheduled)} à prédire")

    if not finished:
        return

    competitions = sorted({m["competition"] for m in finished if m["competition"]})
    finished_by_competition = {c: [m for m in finished if m["competition"] == c] for c in competitions}

    elo_by_competition, poisson_by_competition, params_by_competition = {}, {}, {}
    for comp in competitions:
        params = get_params(session, comp)
        params_by_competition[comp] = params

        elo = EloRating(k_factor=params.k_factor, home_advantage=params.home_advantage_elo)
        for m in sorted(finished_by_competition[comp], key=lambda x: x["date"]):
            elo.update(m["home_team"], m["away_team"], m["home_score"], m["away_score"])
        elo_by_competition[comp] = elo

        poisson_by_competition[comp] = build_poisson(finished_by_competition[comp], home_adv=params.poisson_home_adv)

    all_teams = set(m["home_team"] for m in all_matches) | set(m["away_team"] for m in all_matches)
    teams_adv = {t: compute_advanced_metrics(t, finished) for t in all_teams}

    # Une requête base par équipe (pas par match) : avec ~100 équipes vs. ~1500 matchs
    # programmés, ça évite des milliers d'allers-retours réseau vers Postgres en prod.
    lineups_by_team = {t: get_team_lineups(t, session) for t in all_teams}
    injuries_by_team = {t: get_team_injuries(t, session) for t in all_teams}

    # Même logique pour les prédictions : une requête pour toutes plutôt qu'un
    # session.get() par match (qui vaudrait ~1500 allers-retours réseau).
    scheduled_ids = [m["id"] for m in scheduled]
    existing_predictions = {
        p.match_id: p
        for p in session.query(Prediction).filter(Prediction.match_id.in_(scheduled_ids)).all()
    } if scheduled_ids else {}

    for m in scheduled:
        comp = m["competition"]
        home, away = m["home_team"], m["away_team"]

        if comp not in elo_by_competition:
            continue
        elo = elo_by_competition[comp]
        attack, defense, avg, home_adv = poisson_by_competition[comp]
        params = params_by_competition[comp]
        comp_finished = finished_by_competition[comp]

        adv_h = dict(teams_adv.get(home) or compute_advanced_metrics(home, finished))
        adv_a = dict(teams_adv.get(away) or compute_advanced_metrics(away, finished))
        adv_h["rest_days"] = get_rest_days(home, finished, m["date"])
        adv_a["rest_days"] = get_rest_days(away, finished, m["date"])

        pp = predict_match_advanced(home, away, attack, defense, avg, home_adv, adv_h, adv_a)

        calibrated = apply_calibration((pp["home_win"], pp["draw"], pp["away_win"]), params)
        pp["home_win"], pp["draw"], pp["away_win"] = (round(v, 4) for v in calibrated)

        fh_global = get_form(home, comp_finished)
        fa_global = get_form(away, comp_finished)
        fh_home = get_form(home, comp_finished, venue="home")
        fa_away = get_form(away, comp_finished, venue="away")
        h2h = get_h2h(home, away, comp_finished)

        lineup_h = lineups_by_team.get(home, {"available": False, "starters": [], "subs": [], "formation": None, "date": None})
        lineup_a = lineups_by_team.get(away, {"available": False, "starters": [], "subs": [], "formation": None, "date": None})
        injuries_h = injuries_by_team.get(home, [])
        injuries_a = injuries_by_team.get(away, [])

        conf = compute_confidence(pp, elo.get(home), elo.get(away), fh_global, fa_global, h2h, adv_h, adv_a)
        probs = {"home": pp["home_win"], "draw": pp["draw"], "away": pp["away_win"]}
        favorite = max(probs, key=probs.get)

        tips = _betting_tips(pp, home, away)

        pred = {
            "match_id": m["id"], "date": m["date"], "matchday": m["matchday"], "competition": comp,
            "home_team": home, "away_team": away,
            **pp,
            "elo_home": round(elo.get(home)), "elo_away": round(elo.get(away)),
            "form_home": fh_global, "form_away": fa_global,
            "form_home_venue": fh_home, "form_away_venue": fa_away,
            "h2h": h2h,
            "advanced_home": adv_h, "advanced_away": adv_a,
            "lineup_home": lineup_h, "lineup_away": lineup_a,
            "injuries_home": injuries_h, "injuries_away": injuries_a,
            "confidence": conf, "favorite": favorite, "betting_tips": tips,
            "model_version": params.updated_at,
            "generated_at": datetime.now().isoformat(),
        }

        row = existing_predictions.get(m["id"])
        if row is None:
            row = Prediction(match_id=m["id"])
            session.add(row)
            existing_predictions[m["id"]] = row
        row.prediction_json = json.dumps(pred, ensure_ascii=False)
        row.generated_at = pred["generated_at"]

    all_elo_teams = {team for elo in elo_by_competition.values() for team in elo.ratings}
    existing_elo = {
        e.team: e
        for e in session.query(EloRatingModel).filter(EloRatingModel.team.in_(all_elo_teams)).all()
    } if all_elo_teams else {}

    for comp, elo in elo_by_competition.items():
        for team, rating in elo.ratings.items():
            row = existing_elo.get(team)
            if row is None:
                row = EloRatingModel(team=team)
                session.add(row)
                existing_elo[team] = row
            row.rating = round(rating)
            row.updated_at = datetime.now().isoformat()


def _betting_tips(pp, home, away):
    tips = []
    if pp["home_win"] > 0.55:
        tips.append({"type": "1", "label": f"Victoire {home}", "odds": pp["odds_1"], "prob": pp["home_win"]})
    elif pp["away_win"] > 0.55:
        tips.append({"type": "2", "label": f"Victoire {away}", "odds": pp["odds_2"], "prob": pp["away_win"]})
    elif pp["double_chance_1x"] > 0.72:
        tips.append({"type": "1X", "label": f"{home} ou Nul", "odds": round(1 / max(0.01, pp["double_chance_1x"]), 2), "prob": pp["double_chance_1x"]})
    elif pp["double_chance_x2"] > 0.72:
        tips.append({"type": "X2", "label": f"Nul ou {away}", "odds": round(1 / max(0.01, pp["double_chance_x2"]), 2), "prob": pp["double_chance_x2"]})
    if pp["over_25"] > 0.58:
        tips.append({"type": "O2.5", "label": "Plus de 2.5 buts", "odds": round(1 / max(0.01, pp["over_25"]), 2), "prob": pp["over_25"]})
    elif pp["over_25"] < 0.42:
        tips.append({"type": "U2.5", "label": "Moins de 2.5 buts", "odds": round(1 / max(0.01, 1 - pp["over_25"]), 2), "prob": round(1 - pp["over_25"], 4)})
    if pp["btts_yes"] > 0.55:
        tips.append({"type": "BTTS Oui", "label": "Les 2 marquent", "odds": round(1 / max(0.01, pp["btts_yes"]), 2), "prob": pp["btts_yes"]})
    return tips
