"""Recalibrage automatique du modèle à partir de l'historique de ses erreurs
(PredictionScore). Deux mécanismes complémentaires :

1. Walk-forward tuning : rejoue l'historique avec une petite grille de valeurs pour
   K_FACTOR (ELO) et l'avantage domicile (ELO + Poisson), garde la combinaison qui
   minimise le log loss réel, et ne met à jour ModelParams que si le gain est net.
2. Calibration Platt : régression logistique entre probabilité brute et issue
   réelle, pour corriger un biais systématique de sur/sous-confiance.
"""

import math
from datetime import datetime

import numpy as np
from scipy.stats import poisson as poisson_dist

from app import config
from app.models import ModelParams, PredictionScore
from app.prediction.elo import EloRating
from app.prediction.poisson import build_poisson

K_FACTOR_GRID = [20, 24, 28, 32, 36, 40]
HOME_ADV_ELO_GRID = [60, 80, 100, 120, 140]
POISSON_HOME_ADV_GRID = [1.10, 1.20, 1.30, 1.40, 1.50]
IMPROVEMENT_THRESHOLD = 0.01  # gain minimal de log loss pour valider un changement


def get_params(session, competition):
    params = session.get(ModelParams, competition)
    if params is None:
        params = ModelParams(competition=competition)
        session.add(params)
        session.flush()
    return params


def _elo_log_loss(matches_sorted, k_factor, home_advantage):
    elo = EloRating(k_factor=k_factor, home_advantage=home_advantage)
    total, count = 0.0, 0
    for m in matches_sorted:
        home, away, hs, aws = m["home_team"], m["away_team"], m["home_score"], m["away_score"]
        ra = elo.ratings[home] + elo.home_advantage
        rb = elo.ratings[away]
        ea = elo.expected(ra, rb)
        p_draw = max(0.15, 0.36 - abs(ea - 0.5) * 0.5)
        remaining = 1 - p_draw
        p_home, p_away = remaining * ea, remaining * (1 - ea)

        if hs > aws:
            p_actual = p_home
        elif hs < aws:
            p_actual = p_away
        else:
            p_actual = p_draw
        total += -math.log(max(p_actual, 1e-9))
        count += 1
        elo.update(home, away, hs, aws)
    return total / count if count else None


def _tune_elo(matches_sorted, current_k, current_home_adv):
    baseline = _elo_log_loss(matches_sorted, current_k, current_home_adv)
    best_k, best_adv, best_loss = current_k, current_home_adv, baseline
    for k in K_FACTOR_GRID:
        for adv in HOME_ADV_ELO_GRID:
            loss = _elo_log_loss(matches_sorted, k, adv)
            if loss is not None and (best_loss is None or loss < best_loss):
                best_k, best_adv, best_loss = k, adv, loss
    return best_k, best_adv, baseline, best_loss


def _poisson_log_loss(finished, attack, defense, avg, home_adv):
    total, count = 0.0, 0
    for m in finished:
        home, away = m["home_team"], m["away_team"]
        lh = max(0.1, avg * attack.get(home, 1.0) * defense.get(away, 1.0) * home_adv)
        la = max(0.1, avg * attack.get(away, 1.0) * defense.get(home, 1.0))
        mat = np.array([[poisson_dist.pmf(i, lh) * poisson_dist.pmf(j, la) for j in range(9)] for i in range(9)])
        mat /= mat.sum()
        p_home = float(np.sum(np.tril(mat, -1)))
        p_draw = float(np.trace(mat))
        p_away = float(np.sum(np.triu(mat, 1)))
        hs, aws = m["home_score"], m["away_score"]
        p_actual = p_home if hs > aws else p_away if hs < aws else p_draw
        total += -math.log(max(p_actual, 1e-9))
        count += 1
    return total / count if count else None


def _tune_poisson_home_adv(finished, attack, defense, avg, current):
    baseline = _poisson_log_loss(finished, attack, defense, avg, current)
    best_adv, best_loss = current, baseline
    for cand in POISSON_HOME_ADV_GRID:
        loss = _poisson_log_loss(finished, attack, defense, avg, cand)
        if loss is not None and (best_loss is None or loss < best_loss):
            best_adv, best_loss = cand, loss
    return best_adv, baseline, best_loss


def _fit_platt(xs, ys):
    """Régression logistique 1D (scikit-learn) : logit(p_brute) -> issue réelle."""
    if len(set(ys)) < 2 or len(xs) < config.MIN_SAMPLES_FOR_CALIBRATION:
        return None
    from sklearn.linear_model import LogisticRegression

    X = np.array(xs).reshape(-1, 1)
    y = np.array(ys)
    clf = LogisticRegression()
    clf.fit(X, y)
    return float(clf.coef_[0][0]), float(clf.intercept_[0])


def _logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(x):
    return 1 / (1 + math.exp(-x))


def apply_calibration(probs, params):
    """Applique la calibration Platt apprise sur (home_win, draw, away_win) et renormalise."""
    p_home, p_draw, p_away = probs
    c_home = _sigmoid(params.calib_home_a * _logit(p_home) + params.calib_home_b)
    c_draw = _sigmoid(params.calib_draw_a * _logit(p_draw) + params.calib_draw_b)
    c_away = _sigmoid(params.calib_away_a * _logit(p_away) + params.calib_away_b)
    total = c_home + c_draw + c_away
    if total <= 0:
        return probs
    return c_home / total, c_draw / total, c_away / total


def recalibrate(session, finished_by_competition, competitions):
    """Point d'entrée appelé par le pipeline après chaque évaluation. Met à jour
    ModelParams par compétition à partir de l'historique des scores et des matchs."""
    for competition in competitions:
        params = get_params(session, competition)
        finished = sorted(finished_by_competition.get(competition, []), key=lambda m: m["date"])
        if len(finished) >= 15:
            best_k, best_adv, baseline_ll, best_ll = _tune_elo(finished, params.k_factor, params.home_advantage_elo)
            if baseline_ll is not None and best_ll is not None and baseline_ll - best_ll > IMPROVEMENT_THRESHOLD:
                params.k_factor = float(best_k)
                params.home_advantage_elo = float(best_adv)
                params.last_log_loss = round(best_ll, 4)

            attack, defense, avg, _ = build_poisson(finished, home_adv=params.poisson_home_adv)
            best_padv, baseline_pll, best_pll = _tune_poisson_home_adv(finished, attack, defense, avg, params.poisson_home_adv)
            if baseline_pll is not None and best_pll is not None and baseline_pll - best_pll > IMPROVEMENT_THRESHOLD:
                params.poisson_home_adv = float(best_padv)

        scores = (
            session.query(PredictionScore)
            .filter(PredictionScore.competition == competition)
            .all()
        )
        if len(scores) >= config.MIN_SAMPLES_FOR_CALIBRATION:
            for outcome, prob_attr, a_attr, b_attr in [
                ("H", "predicted_home_win", "calib_home_a", "calib_home_b"),
                ("D", "predicted_draw", "calib_draw_a", "calib_draw_b"),
                ("A", "predicted_away_win", "calib_away_a", "calib_away_b"),
            ]:
                xs = [_logit(getattr(s, prob_attr)) for s in scores]
                ys = [1 if s.actual_result == outcome else 0 for s in scores]
                fit = _fit_platt(xs, ys)
                if fit is not None:
                    setattr(params, a_attr, fit[0])
                    setattr(params, b_attr, fit[1])
            params.sample_size = len(scores)

        params.updated_at = datetime.now().isoformat()

    session.flush()
