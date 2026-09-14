"""Modèle de régression de Poisson pour prédire les scores, enrichi par les
métriques avancées (xG estimé, pressing, efficacité, solidité défensive)."""

import numpy as np
from scipy.stats import poisson


def build_poisson(finished, home_adv=None):
    """Calcule force d'attaque/défense de chaque équipe depuis l'historique.
    `home_adv` : si fourni (valeur apprise dans ModelParams), remplace le calcul
    naïf goals_domicile/goals_exterieur — c'est le point d'entrée du recalibrage."""
    teams = set(m["home_team"] for m in finished) | set(m["away_team"] for m in finished)
    if not finished:
        return {t: 1.0 for t in teams}, {t: 1.0 for t in teams}, 1.3, (home_adv or 1.3)

    avg = np.mean([m["home_score"] + m["away_score"] for m in finished]) / 2
    attack, defense = {}, {}

    for t in teams:
        tm = [m for m in finished if m["home_team"] == t or m["away_team"] == t]
        if not tm:
            attack[t] = defense[t] = 1.0
            continue
        gf = sum(m["home_score"] if m["home_team"] == t else m["away_score"] for m in tm)
        ga = sum(m["away_score"] if m["home_team"] == t else m["home_score"] for m in tm)
        n = len(tm)
        attack[t] = max(0.3, min(2.5, (gf / n) / avg)) if avg > 0 else 1.0
        defense[t] = max(0.3, min(2.5, (ga / n) / avg)) if avg > 0 else 1.0

    if home_adv is None:
        home_avg = np.mean([m["home_score"] for m in finished])
        away_avg = np.mean([m["away_score"] for m in finished])
        home_adv = home_avg / away_avg if away_avg > 0 else 1.3

    return attack, defense, float(avg), float(home_adv)


def predict_match_advanced(home, away, attack, defense, avg, home_adv, adv_h, adv_a):
    """Prédiction Poisson enrichie par xG réel, pressing et solidité défensive."""
    lh = avg * attack.get(home, 1.0) * defense.get(away, 1.0) * home_adv
    la = avg * attack.get(away, 1.0) * defense.get(home, 1.0)

    if adv_h["xg_per90"] > 0 and adv_h["data_completeness"] >= 2:
        w = min(0.4, adv_h["data_completeness"] * 0.08)
        lh = lh * (1 - w) + adv_h["xg_per90"] * w
    if adv_a["xg_per90"] > 0 and adv_a["data_completeness"] >= 2:
        w = min(0.4, adv_a["data_completeness"] * 0.08)
        la = la * (1 - w) + adv_a["xg_per90"] * w

    if adv_h["pressing_intensity"] > 0 and adv_a["pressing_intensity"] > 0:
        press_diff = (adv_h["pressing_intensity"] - adv_a["pressing_intensity"]) / 200
        lh *= (1 + press_diff * 0.15)
        la *= (1 - press_diff * 0.10)

    if adv_a["defensive_solidity"] > 60:
        lh *= 0.95
    if adv_h["defensive_solidity"] > 60:
        la *= 0.95

    # Fatigue : pénalise légèrement les buts attendus si l'équipe joue avec peu de repos
    if adv_h.get("rest_days") is not None and adv_h["rest_days"] < 4:
        lh *= 0.97
    if adv_a.get("rest_days") is not None and adv_a["rest_days"] < 4:
        la *= 0.97

    lh, la = max(0.1, lh), max(0.1, la)

    mat = np.array([[poisson.pmf(i, lh) * poisson.pmf(j, la) for j in range(9)] for i in range(9)])
    mat /= mat.sum()

    bi, bj = np.unravel_index(np.argmax(mat), mat.shape)
    p_home = round(float(np.sum(np.tril(mat, -1))), 4)
    p_draw = round(float(np.trace(mat)), 4)
    p_away = round(float(np.sum(np.triu(mat, 1))), 4)

    over_15 = round(float(sum(mat[i][j] for i in range(9) for j in range(9) if i + j >= 2)), 4)
    over_25 = round(float(sum(mat[i][j] for i in range(9) for j in range(9) if i + j >= 3)), 4)
    over_35 = round(float(sum(mat[i][j] for i in range(9) for j in range(9) if i + j >= 4)), 4)
    btts_yes = round(float(sum(mat[i][j] for i in range(1, 9) for j in range(1, 9))), 4)

    flat = [(float(mat[i][j]), f"{i}-{j}") for i in range(9) for j in range(9)]
    flat.sort(reverse=True)
    top_scores = [{"score": s, "prob": round(p, 4)} for p, s in flat[:5]]

    return {
        "home_win": p_home, "draw": p_draw, "away_win": p_away,
        "expected_home_goals": round(lh, 2), "expected_away_goals": round(la, 2),
        "most_likely_score": f"{bi}-{bj}",
        "most_likely_score_prob": round(float(mat[bi][bj]), 4),
        "over_15": over_15, "over_25": over_25, "over_35": over_35,
        "btts_yes": btts_yes, "btts_no": round(1 - btts_yes, 4),
        "odds_1": round(1 / max(0.01, p_home), 2),
        "odds_x": round(1 / max(0.01, p_draw), 2),
        "odds_2": round(1 / max(0.01, p_away), 2),
        "double_chance_1x": round(p_home + p_draw, 4),
        "double_chance_x2": round(p_draw + p_away, 4),
        "double_chance_12": round(p_home + p_away, 4),
        "top_scores": top_scores,
    }
