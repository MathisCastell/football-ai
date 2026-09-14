"""Facteurs de contexte utilisés par le moteur de prédiction : forme (globale et
domicile/extérieur), historique des confrontations, métriques avancées (xG,
pressing, efficacité), repos/fatigue, compositions et blessures/suspensions."""

import math
from datetime import datetime

from app.models import Injury, Lineup, Match


def get_form(team, finished, n=5, venue=None):
    """Forme sur les n derniers matchs. `venue='home'`/`'away'` restreint aux matchs
    joués à domicile ou à l'extérieur uniquement (un profil différent d'un autre)."""
    pool = [m for m in finished if m["home_team"] == team or m["away_team"] == team]
    if venue == "home":
        pool = [m for m in pool if m["home_team"] == team]
    elif venue == "away":
        pool = [m for m in pool if m["away_team"] == team]

    tm = sorted(pool, key=lambda x: x["date"], reverse=True)[:n]
    if not tm:
        return {"form_string": "-----", "form_score": 50.0,
                "avg_goals_scored": 0.0, "avg_goals_conceded": 0.0, "sample_size": 0}

    res, gf, ga = [], 0, 0
    for m in tm:
        h = m["home_team"] == team
        g1 = m["home_score"] if h else m["away_score"]
        g2 = m["away_score"] if h else m["home_score"]
        gf += g1
        ga += g2
        if g1 > g2:
            res.append("W")
        elif g1 == g2:
            res.append("D")
        else:
            res.append("L")

    wf = sum((3 if r == "W" else 1 if r == "D" else 0) * (n - i) for i, r in enumerate(res))
    mw = sum(3 * (n - i) for i in range(len(res)))
    return {
        "form_string": "".join(res),
        "form_score": round(wf / mw * 100, 1) if mw else 50.0,
        "avg_goals_scored": round(gf / len(tm), 2),
        "avg_goals_conceded": round(ga / len(tm), 2),
        "sample_size": len(tm),
    }


def get_h2h(home, away, finished):
    h2h = sorted(
        [m for m in finished
         if (m["home_team"] == home and m["away_team"] == away) or
            (m["home_team"] == away and m["away_team"] == home)],
        key=lambda x: x["date"], reverse=True
    )[:5]
    hw = dw = aw = 0
    for m in h2h:
        if m["home_team"] == home:
            if m["home_score"] > m["away_score"]:
                hw += 1
            elif m["home_score"] == m["away_score"]:
                dw += 1
            else:
                aw += 1
        else:
            if m["away_score"] > m["home_score"]:
                hw += 1
            elif m["away_score"] == m["home_score"]:
                dw += 1
            else:
                aw += 1
    return {
        "total_games": len(h2h), "home_wins": hw, "draws": dw, "away_wins": aw,
        "last_meetings": [
            {"date": m["date"], "home": m["home_team"], "away": m["away_team"],
             "score": f"{m['home_score']}-{m['away_score']}"}
            for m in h2h[:3]
        ]
    }


def get_rest_days(team, finished, reference_date):
    """Jours écoulés depuis le dernier match de l'équipe. None si aucun match connu."""
    played = sorted(
        [m for m in finished if (m["home_team"] == team or m["away_team"] == team) and m["date"] <= reference_date],
        key=lambda x: x["date"], reverse=True
    )
    if not played:
        return None
    try:
        last = datetime.strptime(played[0]["date"], "%Y-%m-%d")
        ref = datetime.strptime(reference_date, "%Y-%m-%d")
        return max(0, (ref - last).days)
    except ValueError:
        return None


def compute_advanced_metrics(team, finished, reference_date=None):
    """xG estimé, pressing, efficacité de tir, solidité défensive, repos."""
    team_matches = sorted(
        [m for m in finished if m["home_team"] == team or m["away_team"] == team],
        key=lambda x: x["date"], reverse=True
    )[:15]

    if not team_matches:
        return {
            "xg_per90": 0, "xga_per90": 0, "pressing_intensity": 50,
            "shot_efficiency": 0, "defensive_solidity": 50,
            "avg_possession": 50, "avg_shots": 0, "avg_sot": 0,
            "avg_corners": 0, "avg_fouls": 0, "clean_sheets": 0,
            "data_completeness": 0, "rest_days": None, "matches_analyzed": 0,
        }

    total_shots = total_sot = total_gf = total_ga = 0
    total_corners = total_possession = total_fouls = 0
    clean_sheets = has_shots = has_poss = has_corners = 0
    n = len(team_matches)

    for m in team_matches:
        is_home = m["home_team"] == team
        gf = (m["home_score"] if is_home else m["away_score"]) or 0
        ga = (m["away_score"] if is_home else m["home_score"]) or 0
        total_gf += gf
        total_ga += ga
        if ga == 0:
            clean_sheets += 1

        shots = m["home_shots"] if is_home else m["away_shots"]
        sot = m["home_shots_on_target"] if is_home else m["away_shots_on_target"]
        poss = m["home_possession"] if is_home else m["away_possession"]
        corners = m["home_corners"] if is_home else m["away_corners"]
        fouls = m["home_fouls"] if is_home else m["away_fouls"]

        if shots is not None:
            total_shots += shots
            has_shots += 1
        if sot is not None:
            total_sot += sot
        if poss is not None:
            total_possession += poss
            has_poss += 1
        if corners is not None:
            total_corners += corners
            has_corners += 1
        if fouls is not None:
            total_fouls += fouls

    avg_shots = total_shots / has_shots if has_shots else 0
    avg_sot = total_sot / has_shots if has_shots else 0
    avg_poss = total_possession / has_poss if has_poss else 50
    avg_corners = total_corners / has_corners if has_corners else 0
    avg_fouls = total_fouls / n
    avg_gf = total_gf / n
    avg_ga = total_ga / n

    xg_per90 = (avg_sot * 0.32 + (avg_shots - avg_sot) * 0.04) if (has_shots and avg_shots > 0) else avg_gf
    xga_per90 = avg_ga

    pressing = min(100, max(0, (avg_poss - 35) * 0.8 + avg_shots * 1.5 + avg_corners * 2.5 + avg_fouls * 0.3))
    shot_efficiency = (total_gf / total_shots * 100) if total_shots > 0 else 0
    defensive_solidity = max(0, min(100, 100 - avg_ga * 30 + clean_sheets * 5))
    data_completeness = sum([has_shots > 0, has_poss > 0, has_corners > 0, n >= 5, n >= 10])

    rest_days = get_rest_days(team, finished, reference_date) if reference_date else None

    return {
        "xg_per90": round(xg_per90, 2), "xga_per90": round(xga_per90, 2),
        "pressing_intensity": round(pressing, 1), "shot_efficiency": round(shot_efficiency, 1),
        "defensive_solidity": round(defensive_solidity, 1), "avg_possession": round(avg_poss, 1),
        "avg_shots": round(avg_shots, 1), "avg_sot": round(avg_sot, 1),
        "avg_corners": round(avg_corners, 1), "avg_fouls": round(avg_fouls, 1),
        "clean_sheets": clean_sheets, "data_completeness": data_completeness,
        "rest_days": rest_days, "matches_analyzed": n,
    }


def get_team_lineups(team, session):
    """Un seul aller-retour base (jointure incluant la formation) au lieu de deux —
    important quand la base est distante (Postgres en production)."""
    rows = (
        session.query(Lineup, Match.date, Match.home_team, Match.home_formation, Match.away_formation)
        .join(Match, Lineup.match_id == Match.id)
        .filter(Lineup.team == team)
        .order_by(Match.date.desc())
        .all()
    )
    if not rows:
        return {"available": False, "starters": [], "subs": [], "formation": None, "date": None}

    last_match_id = rows[0][0].match_id
    last_date = rows[0][1]
    formation = rows[0][3] if rows[0][2] == team else rows[0][4]
    starters, subs = [], []
    for lineup, date, home_team, home_formation, away_formation in rows:
        if lineup.match_id != last_match_id:
            break
        entry = {"name": lineup.player_name, "position": lineup.position}
        (starters if lineup.is_starter else subs).append(entry)

    return {"available": len(starters) > 0, "starters": starters, "subs": subs,
            "formation": formation, "date": last_date, "match_id": last_match_id}


def get_team_injuries(team, session):
    rows = (
        session.query(Injury)
        .filter(Injury.team == team)
        .order_by(Injury.updated_at.desc())
        .all()
    )
    return [{"player": r.player_name, "type": r.injury_type, "detail": r.detail,
             "updated_at": r.updated_at} for r in rows]


def compute_confidence(pp, elo_home, elo_away, fh, fa, h2h, adv_h=None, adv_a=None):
    probs = [pp["home_win"], pp["draw"], pp["away_win"]]
    entropy = -sum(p * math.log(p + 1e-9) for p in probs) / math.log(3)
    certainty = (1 - entropy) * 50
    elo_ok = 20 if (elo_home > elo_away + 50) == (pp["home_win"] > pp["away_win"]) else 0
    form_bonus = min(15, abs(fh["form_score"] - fa["form_score"]) * 0.15)
    h2h_bonus = 10 if h2h["total_games"] >= 3 else 0

    adv_bonus = 0
    if adv_h and adv_a:
        data_count = adv_h.get("data_completeness", 0) + adv_a.get("data_completeness", 0)
        adv_bonus = min(10, data_count * 2)
        if adv_h.get("xg_per90", 0) > 0 and adv_a.get("xg_per90", 0) > 0:
            xg_fav = "home" if adv_h["xg_per90"] > adv_a["xg_per90"] else "away"
            prob_fav = "home" if pp["home_win"] > pp["away_win"] else "away"
            if xg_fav == prob_fav:
                adv_bonus += 5

    return round(min(95, max(30, certainty + elo_ok + form_bonus + h2h_bonus + adv_bonus)), 1)
