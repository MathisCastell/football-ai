"""Collecte de matchs réels via TheSportsDB (gratuit, sans inscription) ou
football-data.org si une clé API est fournie.

Contrairement à l'ancienne version (Server.py), la collecte est INCRÉMENTALE :
elle met à jour les matchs existants (upsert) au lieu de tout supprimer puis
tout reconstruire. C'est indispensable pour que l'historique des prédictions
et leur notation (app/learning) survive aux cycles de collecte successifs.
"""

import time
from datetime import datetime

from app import config
from app.models import Injury, Lineup, Match

LEAGUES = {
    4328: ("Premier League", 38),
    4334: ("Ligue 1", 34),
    4335: ("La Liga", 38),
    4332: ("Serie A", 38),
    4331: ("Bundesliga", 34),
}


def _parse_lineup_string(raw):
    if not raw:
        return []
    return [n.strip() for n in raw.replace("|", ";").split(";") if n.strip()]


def _upsert_match(session, values: dict):
    match = session.get(Match, values["id"])
    if match is None:
        match = Match(id=values["id"])
        session.add(match)
    for k, v in values.items():
        setattr(match, k, v)
    return match


def collect(session, force=False):
    """Point d'entrée : choisit la source de données et met à jour la base en place."""
    existing = session.query(Match).count()
    if existing > 0 and not force:
        stale = (
            session.query(Match)
            .filter(Match.status.in_(["SCHEDULED", "TIMED"]))
            .order_by(Match.date.desc())
            .first()
        )
        if stale and stale.date and stale.date < datetime.now().strftime("%Y-%m-%d"):
            print(f"[COLLECT] Données périmées (dernier match prévu : {stale.date}). Rafraîchissement...")
        else:
            print(f"[COLLECT] {existing} matchs déjà en base, pas de rafraîchissement complet nécessaire.")
            return

    if config.FOOTBALL_API_KEY:
        _collect_from_football_data(session)
    else:
        _collect_from_thesportsdb(session)


def _collect_from_thesportsdb(session):
    import requests as req

    season_str = f"{config.CURRENT_SEASON}-{config.CURRENT_SEASON + 1}"
    print(f"[COLLECT] TheSportsDB, saison {season_str}...")

    total = 0
    finished_ids = []

    for league_id, (league_name, max_rounds) in LEAGUES.items():
        league_total = 0
        for round_num in range(1, max_rounds + 1):
            try:
                r = req.get(
                    "https://www.thesportsdb.com/api/v1/json/3/eventsround.php",
                    params={"id": league_id, "r": round_num, "s": season_str},
                    timeout=10,
                )
                if r.status_code == 429:
                    time.sleep(5)
                    r = req.get(
                        "https://www.thesportsdb.com/api/v1/json/3/eventsround.php",
                        params={"id": league_id, "r": round_num, "s": season_str},
                        timeout=10,
                    )
                if r.status_code != 200:
                    continue

                events = r.json().get("events") or []
                for e in events:
                    hs = e.get("intHomeScore")
                    aws = e.get("intAwayScore")
                    if hs is not None and aws is not None:
                        status, hs, aws = "FINISHED", int(hs), int(aws)
                    else:
                        status, hs, aws = "SCHEDULED", None, None

                    _int = lambda k: int(e[k]) if e.get(k) not in (None, "") else None
                    _str = lambda k: e.get(k) or None

                    match_id = int(e["idEvent"])
                    home_team = e.get("strHomeTeam", "")
                    away_team = e.get("strAwayTeam", "")

                    _upsert_match(session, dict(
                        id=match_id, competition=league_name, matchday=round_num,
                        date=e.get("dateEvent", ""), home_team=home_team, away_team=away_team,
                        home_score=hs, away_score=aws, status=status,
                        home_shots=_int("intHomeShots"), away_shots=_int("intAwayShots"),
                        home_yellow_cards=_int("intHomeYellowCards"), away_yellow_cards=_int("intAwayYellowCards"),
                        home_red_cards=_int("intHomeRedCards"), away_red_cards=_int("intAwayRedCards"),
                        home_formation=_str("strHomeFormation"), away_formation=_str("strAwayFormation"),
                        updated_at=datetime.now().isoformat(),
                    ))

                    session.query(Lineup).filter(Lineup.match_id == match_id).delete()
                    for side, team in [("Home", home_team), ("Away", away_team)]:
                        for field, pos in [
                            (f"str{side}LineupGoalkeeper", "GK"),
                            (f"str{side}LineupDefense", "DEF"),
                            (f"str{side}LineupMidfield", "MID"),
                            (f"str{side}LineupForward", "FWD"),
                        ]:
                            for name in _parse_lineup_string(e.get(field)):
                                session.add(Lineup(match_id=match_id, team=team, player_name=name,
                                                    position=pos, is_starter=True))
                        for name in _parse_lineup_string(e.get(f"str{side}LineupSubstitutes")):
                            session.add(Lineup(match_id=match_id, team=team, player_name=name,
                                                position="SUB", is_starter=False))

                    if status == "FINISHED":
                        finished_ids.append(match_id)
                    league_total += 1

                session.flush()
                time.sleep(1)
            except Exception as ex:
                print(f"[COLLECT] Erreur {league_name} J{round_num}: {ex}")

        print(f"[COLLECT] {league_name} -> {league_total} matchs")
        total += league_total

    _collect_event_statistics(session, finished_ids[-100:])
    _detect_suspensions(session)
    print(f"[COLLECT] Total : {total} matchs (dont {len(finished_ids)} terminés).")


def _collect_event_statistics(session, match_ids):
    import requests as req

    STAT_MAP = {
        "Ball Possession": ("home_possession", "away_possession"),
        "Possession": ("home_possession", "away_possession"),
        "Shots on Goal": ("home_shots_on_target", "away_shots_on_target"),
        "Shots On Target": ("home_shots_on_target", "away_shots_on_target"),
        "Corner Kicks": ("home_corners", "away_corners"),
        "Corners": ("home_corners", "away_corners"),
        "Fouls": ("home_fouls", "away_fouls"),
        "Fouls Committed": ("home_fouls", "away_fouls"),
    }
    updated = 0
    for mid in reversed(match_ids[-60:]):
        try:
            r = req.get(
                "https://www.thesportsdb.com/api/v1/json/3/lookupeventstatistics.php",
                params={"id": mid}, timeout=10,
            )
            if r.status_code == 429:
                time.sleep(5)
                continue
            if r.status_code != 200:
                continue
            raw = r.json().get("eventstats") or []
            if not raw:
                time.sleep(0.5)
                continue

            stat_map = {}
            for s in raw:
                name = (s.get("strStat") or "").strip()
                if name:
                    stat_map[name] = (s.get("intHome"), s.get("intAway"))

            match = session.get(Match, mid)
            if not match:
                continue
            for stat_name, (h_col, a_col) in STAT_MAP.items():
                if stat_name not in stat_map:
                    continue
                hv, av = stat_map[stat_name]
                if hv is not None:
                    try:
                        setattr(match, h_col, float(str(hv).replace("%", "")))
                    except (ValueError, TypeError):
                        pass
                if av is not None:
                    try:
                        setattr(match, a_col, float(str(av).replace("%", "")))
                    except (ValueError, TypeError):
                        pass
            updated += 1
            time.sleep(0.8)
        except Exception as ex:
            print(f"[COLLECT] Erreur stats match {mid}: {ex}")

    print(f"[COLLECT] {updated} matchs enrichis avec stats détaillées.")


def _detect_suspensions(session):
    """Détecte les suspensions potentielles (carton rouge récent). Ces lignes sont
    entièrement redérivées à chaque cycle (transitoire, contrairement à l'historique
    des prédictions) donc on les recrée proprement plutôt que de les accumuler."""
    session.query(Injury).filter(Injury.injury_type == "suspension").delete()

    red_card_matches = (
        session.query(Match)
        .filter(Match.status == "FINISHED")
        .filter((Match.home_red_cards > 0) | (Match.away_red_cards > 0))
        .order_by(Match.date.desc())
        .limit(50)
        .all()
    )
    for m in red_card_matches:
        if m.home_red_cards and m.home_red_cards > 0:
            session.add(Injury(
                team=m.home_team, player_name=f"Joueur suspendu ({m.home_red_cards} rouge(s))",
                injury_type="suspension", detail=f"Carton rouge le {m.date}",
                competition=m.competition, updated_at=datetime.now().isoformat(),
            ))
        if m.away_red_cards and m.away_red_cards > 0:
            session.add(Injury(
                team=m.away_team, player_name=f"Joueur suspendu ({m.away_red_cards} rouge(s))",
                injury_type="suspension", detail=f"Carton rouge le {m.date}",
                competition=m.competition, updated_at=datetime.now().isoformat(),
            ))


def _collect_from_football_data(session):
    import requests as req

    print(f"[COLLECT] football-data.org, saison {config.CURRENT_SEASON}...")
    headers = {"X-Auth-Token": config.FOOTBALL_API_KEY}
    competitions = {"PL": "Premier League", "FL1": "Ligue 1", "PD": "La Liga",
                     "BL1": "Bundesliga", "SA": "Serie A"}
    total = 0
    for code, name in competitions.items():
        try:
            r = req.get(
                f"https://api.football-data.org/v4/competitions/{code}/matches",
                headers=headers, params={"season": config.CURRENT_SEASON}, timeout=15,
            )
            if r.status_code == 429:
                time.sleep(60)
                continue
            if r.status_code != 200:
                print(f"[COLLECT] {code} -> HTTP {r.status_code}")
                continue
            matches = r.json().get("matches", [])
            for m in matches:
                ft = m.get("score", {}).get("fullTime", {})
                _upsert_match(session, dict(
                    id=m["id"], competition=name, matchday=m.get("matchday"),
                    date=m.get("utcDate", "")[:10],
                    home_team=m["homeTeam"]["name"], away_team=m["awayTeam"]["name"],
                    home_score=ft.get("home"), away_score=ft.get("away"),
                    status=m.get("status"), updated_at=datetime.now().isoformat(),
                ))
            session.flush()
            total += len(matches)
            print(f"[COLLECT] {name} -> {len(matches)} matchs")
            time.sleep(6)
        except Exception as e:
            print(f"[COLLECT] Erreur {code}: {e}")
    print(f"[COLLECT] Total : {total} matchs récupérés.")
