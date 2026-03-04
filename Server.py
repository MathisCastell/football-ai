"""
╔══════════════════════════════════════════════════════════════╗
║  FOOTBALL.AI — SERVEUR                                       ║
║                                                              ║
║  1. Installe :                                               ║
║     pip install flask flask-cors numpy scipy scikit-learn    ║
║                                                              ║
║  2. Lance :                                                  ║
║     python server.py                                         ║
║                                                              ║
║  3. Ouvre : http://localhost:5000                            ║
╚══════════════════════════════════════════════════════════════╝
"""

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import sqlite3, json, threading, time, math, random, os
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np
from scipy.stats import poisson

app = Flask(__name__, static_folder=".")
CORS(app)

DB_PATH = "football.db"
last_update = None
is_running = False

# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════

def _load_env():
    """Charge les variables depuis .env si le fichier existe."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

# Clé API football-data.org (gratuit : https://www.football-data.org/)
#   → Variable d'environnement FOOTBALL_API_KEY
#   → ou fichier .env avec FOOTBALL_API_KEY=ta_cle
#   → ou remplace directement ci-dessous
API_KEY = os.environ.get("FOOTBALL_API_KEY", "VOTRE_CLE_API_ICI")

# Auto-détection saison courante (août → mai)
_now = datetime.now()
CURRENT_SEASON = _now.year if _now.month >= 8 else _now.year - 1

# ═══════════════════════════════════════════════════════════════
#  BASE DE DONNÉES
# ═══════════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY,
            competition TEXT,
            matchday INTEGER,
            date TEXT,
            home_team TEXT,
            away_team TEXT,
            home_score INTEGER,
            away_score INTEGER,
            status TEXT,
            home_xg REAL,
            away_xg REAL,
            home_shots INTEGER,
            away_shots INTEGER,
            home_shots_on_target INTEGER,
            away_shots_on_target INTEGER,
            home_possession REAL,
            away_possession REAL,
            home_corners INTEGER,
            away_corners INTEGER,
            home_fouls INTEGER,
            away_fouls INTEGER,
            home_yellow_cards INTEGER,
            away_yellow_cards INTEGER,
            home_red_cards INTEGER,
            away_red_cards INTEGER,
            home_formation TEXT,
            away_formation TEXT,
            updated_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            match_id INTEGER PRIMARY KEY,
            prediction_json TEXT,
            generated_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS elo_ratings (
            team TEXT PRIMARY KEY,
            rating INTEGER,
            updated_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS lineups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER,
            team TEXT,
            player_name TEXT,
            position TEXT,
            is_starter INTEGER DEFAULT 1,
            FOREIGN KEY (match_id) REFERENCES matches(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS injuries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team TEXT,
            player_name TEXT,
            injury_type TEXT,
            detail TEXT,
            competition TEXT,
            updated_at TEXT
        )
    """)
    # Migration : ajout colonnes manquantes pour anciennes BDD
    for col, coltype in [
        ("home_corners", "INTEGER"), ("away_corners", "INTEGER"),
        ("home_fouls", "INTEGER"), ("away_fouls", "INTEGER"),
        ("home_yellow_cards", "INTEGER"), ("away_yellow_cards", "INTEGER"),
        ("home_red_cards", "INTEGER"), ("away_red_cards", "INTEGER"),
        ("home_formation", "TEXT"), ("away_formation", "TEXT"),
    ]:
        try:
            c.execute(f"ALTER TABLE matches ADD COLUMN {col} {coltype}")
        except Exception:
            pass
    conn.commit()
    conn.close()

# ═══════════════════════════════════════════════════════════════
#  COLLECTE DES DONNÉES
# ═══════════════════════════════════════════════════════════════

def collect_data(force=False):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM matches")
    count = c.fetchone()[0]

    # Vérifier si les données sont périmées (matchs futurs dans le passé)
    if count > 0 and not force:
        c.execute("SELECT MAX(date) FROM matches WHERE status IN ('SCHEDULED','TIMED')")
        row = c.fetchone()
        max_date = row[0] if row else None
        if max_date and max_date < datetime.now().strftime("%Y-%m-%d"):
            print(f"[DB] Données périmées (dernier match prévu : {max_date}). Régénération...")
            force = True
        else:
            print(f"[DB] {count} matchs déjà en base.")
            conn.close()
            return

    conn.close()

    if API_KEY != "VOTRE_CLE_API_ICI":
        _collect_from_api()
    else:
        _collect_from_thesportsdb()


def _parse_lineup_string(raw):
    """Parse une chaîne de lineup TheSportsDB en liste de noms."""
    if not raw:
        return []
    return [n.strip() for n in raw.replace("|", ";").split(";") if n.strip()]


def _collect_from_thesportsdb():
    """Collecte de VRAIS matchs via TheSportsDB (gratuit, sans inscription)."""
    import requests as req

    LEAGUES = {
        4328: ("Premier League", 38),
        4334: ("Ligue 1", 34),
        4335: ("La Liga", 38),
        4332: ("Serie A", 38),
        4331: ("Bundesliga", 34),
    }
    season_str = f"{CURRENT_SEASON}-{CURRENT_SEASON + 1}"
    print(f"[API] Collecte depuis TheSportsDB (saison {season_str})...")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM matches")
    c.execute("DELETE FROM predictions")
    c.execute("DELETE FROM elo_ratings")
    c.execute("DELETE FROM lineups")
    c.execute("DELETE FROM injuries")

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
                    print(f"[API] Rate limit → pause 5s...")
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
                        status = "FINISHED"
                        hs, aws = int(hs), int(aws)
                    else:
                        status = "SCHEDULED"
                        hs = aws = None

                    # Extraire les stats avancées de l'événement
                    _int = lambda k: int(e[k]) if e.get(k) not in (None, "") else None
                    _str = lambda k: e.get(k) or None

                    match_id = int(e["idEvent"])
                    home_team = e.get("strHomeTeam", "")
                    away_team = e.get("strAwayTeam", "")

                    c.execute("""
                        INSERT OR REPLACE INTO matches
                        (id, competition, matchday, date, home_team, away_team,
                         home_score, away_score, status,
                         home_shots, away_shots,
                         home_yellow_cards, away_yellow_cards,
                         home_red_cards, away_red_cards,
                         home_formation, away_formation,
                         updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        match_id, league_name, round_num,
                        e.get("dateEvent", ""),
                        home_team, away_team,
                        hs, aws, status,
                        _int("intHomeShots"), _int("intAwayShots"),
                        _int("intHomeYellowCards"), _int("intAwayYellowCards"),
                        _int("intHomeRedCards"), _int("intAwayRedCards"),
                        _str("strHomeFormation"), _str("strAwayFormation"),
                        datetime.now().isoformat(),
                    ))

                    # Extraire les compositions (lineups)
                    for side, team in [("Home", home_team), ("Away", away_team)]:
                        for field, pos in [
                            (f"str{side}LineupGoalkeeper", "GK"),
                            (f"str{side}LineupDefense", "DEF"),
                            (f"str{side}LineupMidfield", "MID"),
                            (f"str{side}LineupForward", "FWD"),
                        ]:
                            for name in _parse_lineup_string(e.get(field)):
                                c.execute(
                                    "INSERT INTO lineups (match_id,team,player_name,position,is_starter) VALUES (?,?,?,?,1)",
                                    (match_id, team, name, pos),
                                )
                        # Remplaçants
                        for name in _parse_lineup_string(e.get(f"str{side}LineupSubstitutes")):
                            c.execute(
                                "INSERT INTO lineups (match_id,team,player_name,position,is_starter) VALUES (?,?,?,?,0)",
                                (match_id, team, name, "SUB"),
                            )

                    if status == "FINISHED":
                        finished_ids.append(match_id)

                    league_total += 1

                time.sleep(1)
            except Exception as ex:
                print(f"[API] Erreur {league_name} J{round_num}: {ex}")

        print(f"[API] {league_name} → {league_total} matchs")
        total += league_total

    # Récupérer les statistiques détaillées pour les matchs récents
    _collect_event_statistics(conn, finished_ids[-100:])  # 100 derniers matchs

    # Détecter suspensions (cartons rouges / accumulation jaunes)
    _detect_suspensions(conn)

    conn.commit()
    conn.close()
    print(f"[API] Total : {total} matchs réels récupérés (dont {len(finished_ids)} terminés).")


def _collect_event_statistics(conn, match_ids):
    """Récupère les stats détaillées (possession, corners, tirs cadrés, fautes) pour les matchs terminés."""
    import requests as req
    c = conn.cursor()
    updated = 0

    # Traiter les matchs les plus récents en priorité
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
                stat_name = (s.get("strStat") or "").strip()
                h_val = s.get("intHome")
                a_val = s.get("intAway")
                if stat_name:
                    stat_map[stat_name] = (h_val, a_val)

            updates = {}
            # Mapping des noms de stats TheSportsDB
            STAT_MAP = {
                "Ball Possession":     ("home_possession", "away_possession"),
                "Possession":          ("home_possession", "away_possession"),
                "Shots on Goal":       ("home_shots_on_target", "away_shots_on_target"),
                "Shots On Target":     ("home_shots_on_target", "away_shots_on_target"),
                "Corner Kicks":        ("home_corners", "away_corners"),
                "Corners":             ("home_corners", "away_corners"),
                "Fouls":               ("home_fouls", "away_fouls"),
                "Fouls Committed":     ("home_fouls", "away_fouls"),
            }
            for stat_name, (h_col, a_col) in STAT_MAP.items():
                if stat_name in stat_map:
                    hv, av = stat_map[stat_name]
                    if hv is not None:
                        try:
                            updates[h_col] = float(str(hv).replace("%", ""))
                        except (ValueError, TypeError):
                            pass
                    if av is not None:
                        try:
                            updates[a_col] = float(str(av).replace("%", ""))
                        except (ValueError, TypeError):
                            pass

            if updates:
                set_clause = ", ".join(f"{k}=?" for k in updates)
                c.execute(f"UPDATE matches SET {set_clause} WHERE id=?",
                          list(updates.values()) + [mid])
                updated += 1

            time.sleep(0.8)
        except Exception as ex:
            print(f"[STATS] Erreur match {mid}: {ex}")

    print(f"[STATS] {updated} matchs enrichis avec stats détaillées.")


def _detect_suspensions(conn):
    """Détecte les joueurs suspendus (carton rouge ou accumulation de jaunes)."""
    c = conn.cursor()

    # Récupérer les cartons rouges des derniers matchs
    c.execute("""
        SELECT m.home_team, m.away_team, m.competition,
               m.home_red_cards, m.away_red_cards, m.date,
               m.id
        FROM matches m
        WHERE m.status = 'FINISHED'
          AND (m.home_red_cards > 0 OR m.away_red_cards > 0)
        ORDER BY m.date DESC
        LIMIT 50
    """)
    red_card_matches = c.fetchall()

    # Pour chaque match avec carton rouge, chercher les joueurs expulsés dans les lineups
    for ht, at, comp, hrc, arc, date, mid in red_card_matches:
        if hrc and hrc > 0:
            # On ne connaît pas exactement qui a reçu le rouge,
            # mais on flag l'équipe comme ayant un suspendu potentiel
            c.execute("""
                INSERT OR REPLACE INTO injuries (team, player_name, injury_type, detail, competition, updated_at)
                VALUES (?, ?, 'suspension', ?, ?, ?)
            """, (ht, f"Joueur suspendu ({hrc} rouge(s))", f"Carton rouge le {date}", comp, datetime.now().isoformat()))
        if arc and arc > 0:
            c.execute("""
                INSERT OR REPLACE INTO injuries (team, player_name, injury_type, detail, competition, updated_at)
                VALUES (?, ?, 'suspension', ?, ?, ?)
            """, (at, f"Joueur suspendu ({arc} rouge(s))", f"Carton rouge le {date}", comp, datetime.now().isoformat()))


def _collect_from_api():
    import requests as req
    print(f"[API] Collecte depuis football-data.org (saison {CURRENT_SEASON})...")
    headers = {"X-Auth-Token": API_KEY}
    competitions = {
        "PL": "Premier League",
        "FL1": "Ligue 1",
        "PD": "La Liga",
        "BL1": "Bundesliga",
        "SA": "Serie A",
    }
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    total = 0

    for code, name in competitions.items():
        try:
            r = req.get(
                f"https://api.football-data.org/v4/competitions/{code}/matches",
                headers=headers, params={"season": CURRENT_SEASON}, timeout=15
            )
            if r.status_code == 403:
                print(f"[API] {code} → accès refusé (plan gratuit = PL seulement)")
                continue
            if r.status_code == 429:
                print("[API] Rate limit → attente 60s...")
                time.sleep(60)
                continue
            if r.status_code != 200:
                print(f"[API] {code} → HTTP {r.status_code}")
                continue
            matches = r.json().get("matches", [])
            for m in matches:
                ft = m.get("score", {}).get("fullTime", {})
                c.execute("""
                    INSERT OR REPLACE INTO matches
                    (id, competition, matchday, date, home_team, away_team,
                     home_score, away_score, status, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    m["id"], name, m.get("matchday"),
                    m.get("utcDate", "")[:10],
                    m["homeTeam"]["name"], m["awayTeam"]["name"],
                    ft.get("home"), ft.get("away"),
                    m.get("status"),
                    datetime.now().isoformat()
                ))
            total += len(matches)
            print(f"[API] {name} → {len(matches)} matchs")
            time.sleep(6)
        except Exception as e:
            print(f"[API] Erreur {code}: {e}")

    conn.commit()
    conn.close()
    print(f"[API] Total : {total} matchs récupérés.")

# ═══════════════════════════════════════════════════════════════
#  MODÈLES IA
# ═══════════════════════════════════════════════════════════════

class EloRating:
    def __init__(self):
        self.ratings = defaultdict(lambda: 1500)

    def update(self, home, away, hs, as_):
        ra = self.ratings[home] + 100  # avantage domicile
        rb = self.ratings[away]
        ea = 1 / (1 + 10 ** ((rb - ra) / 400))
        sa = 1.0 if hs > as_ else 0.0 if hs < as_ else 0.5
        gd = abs(hs - as_)
        k = 32 * (1 + (gd - 1) * 0.1 if gd > 1 else 1)
        self.ratings[home] += k * (sa - ea)
        self.ratings[away] += k * ((1 - sa) - (1 - ea))

    def get(self, t):
        return self.ratings[t]


def build_poisson(finished):
    teams = set(m["home_team"] for m in finished) | set(m["away_team"] for m in finished)
    if not finished:
        return {t: 1.0 for t in teams}, {t: 1.0 for t in teams}, 1.3, 1.3

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
        attack[t]  = max(0.3, min(2.5, (gf / n) / avg))
        defense[t] = max(0.3, min(2.5, (ga / n) / avg))

    home_avg = np.mean([m["home_score"] for m in finished])
    away_avg = np.mean([m["away_score"] for m in finished])
    home_adv = home_avg / away_avg if away_avg > 0 else 1.3

    return attack, defense, float(avg), float(home_adv)


def predict_match(home, away, attack, defense, avg, home_adv):
    lh = avg * attack.get(home, 1.0) * defense.get(away, 1.0) * home_adv
    la = avg * attack.get(away, 1.0) * defense.get(home, 1.0)
    lh, la = max(0.1, lh), max(0.1, la)

    mat = np.array([
        [poisson.pmf(i, lh) * poisson.pmf(j, la) for j in range(9)]
        for i in range(9)
    ])
    mat /= mat.sum()

    bi, bj = np.unravel_index(np.argmax(mat), mat.shape)
    p_home = round(float(np.sum(np.tril(mat, -1))), 4)
    p_draw = round(float(np.trace(mat)), 4)
    p_away = round(float(np.sum(np.triu(mat, 1))), 4)

    # Over/Under
    over_15 = round(float(sum(mat[i][j] for i in range(9) for j in range(9) if i + j >= 2)), 4)
    over_25 = round(float(sum(mat[i][j] for i in range(9) for j in range(9) if i + j >= 3)), 4)
    over_35 = round(float(sum(mat[i][j] for i in range(9) for j in range(9) if i + j >= 4)), 4)

    # BTTS (Both Teams To Score)
    btts_yes = round(float(sum(mat[i][j] for i in range(1, 9) for j in range(1, 9))), 4)

    # Top 5 scores les plus probables
    flat = [(float(mat[i][j]), f"{i}-{j}") for i in range(9) for j in range(9)]
    flat.sort(reverse=True)
    top_scores = [{"score": s, "prob": round(p, 4)} for p, s in flat[:5]]

    # Cotes justes (fair odds = 1 / probabilité)
    odds_1 = round(1 / max(0.01, p_home), 2)
    odds_x = round(1 / max(0.01, p_draw), 2)
    odds_2 = round(1 / max(0.01, p_away), 2)

    # Double chance
    dc_1x = round(p_home + p_draw, 4)
    dc_x2 = round(p_draw + p_away, 4)
    dc_12 = round(p_home + p_away, 4)

    return {
        "home_win":               p_home,
        "draw":                   p_draw,
        "away_win":               p_away,
        "expected_home_goals":    round(lh, 2),
        "expected_away_goals":    round(la, 2),
        "most_likely_score":      f"{bi}-{bj}",
        "most_likely_score_prob": round(float(mat[bi][bj]), 4),
        "over_15":                over_15,
        "over_25":                over_25,
        "over_35":                over_35,
        "btts_yes":               btts_yes,
        "btts_no":                round(1 - btts_yes, 4),
        "odds_1":                 odds_1,
        "odds_x":                 odds_x,
        "odds_2":                 odds_2,
        "double_chance_1x":       dc_1x,
        "double_chance_x2":       dc_x2,
        "double_chance_12":       dc_12,
        "top_scores":             top_scores,
    }


def get_form(team, finished, n=5):
    tm = sorted(
        [m for m in finished if m["home_team"] == team or m["away_team"] == team],
        key=lambda x: x["date"], reverse=True
    )[:n]
    if not tm:
        return {"form_string": "-----", "form_score": 50.0,
                "avg_goals_scored": 0.0, "avg_goals_conceded": 0.0}
    res, pts, gf, ga = [], 0, 0, 0
    for m in tm:
        h = m["home_team"] == team
        g1 = m["home_score"] if h else m["away_score"]
        g2 = m["away_score"] if h else m["home_score"]
        gf += g1; ga += g2
        if g1 > g2:   res.append("W"); pts += 3
        elif g1 == g2: res.append("D"); pts += 1
        else:          res.append("L")
    wf = sum((3 if r == "W" else 1 if r == "D" else 0) * (n - i) for i, r in enumerate(res))
    mw = sum(3 * (n - i) for i in range(len(res)))
    return {
        "form_string":       "".join(res),
        "form_score":        round(wf / mw * 100, 1) if mw else 50.0,
        "avg_goals_scored":  round(gf / len(tm), 2),
        "avg_goals_conceded":round(ga / len(tm), 2),
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
            if m["home_score"] > m["away_score"]: hw += 1
            elif m["home_score"] == m["away_score"]: dw += 1
            else: aw += 1
        else:
            if m["away_score"] > m["home_score"]: hw += 1
            elif m["away_score"] == m["home_score"]: dw += 1
            else: aw += 1
    return {
        "total_games": len(h2h),
        "home_wins": hw, "draws": dw, "away_wins": aw,
        "last_meetings": [
            {"date": m["date"], "home": m["home_team"],
             "away": m["away_team"],
             "score": f"{m['home_score']}-{m['away_score']}"}
            for m in h2h[:3]
        ]
    }


def compute_confidence(pp, elo_home, elo_away, fh, fa, h2h, adv_h=None, adv_a=None):
    probs = [pp["home_win"], pp["draw"], pp["away_win"]]
    entropy = -sum(p * math.log(p + 1e-9) for p in probs) / math.log(3)
    certainty = (1 - entropy) * 50
    elo_ok = 20 if (elo_home > elo_away + 50) == (pp["home_win"] > pp["away_win"]) else 0
    form_bonus = min(15, abs(fh["form_score"] - fa["form_score"]) * 0.15)
    h2h_bonus = 10 if h2h["total_games"] >= 3 else 0

    # Bonus données avancées
    adv_bonus = 0
    if adv_h and adv_a:
        # Plus de données = plus de confiance
        data_count = adv_h.get("data_completeness", 0) + adv_a.get("data_completeness", 0)
        adv_bonus = min(10, data_count * 2)
        # Si xG et pressing concordent avec le pronostic → bonus
        if adv_h.get("xg_per90", 0) > 0 and adv_a.get("xg_per90", 0) > 0:
            xg_fav = "home" if adv_h["xg_per90"] > adv_a["xg_per90"] else "away"
            prob_fav = "home" if pp["home_win"] > pp["away_win"] else "away"
            if xg_fav == prob_fav:
                adv_bonus += 5

    return round(min(95, max(30, certainty + elo_ok + form_bonus + h2h_bonus + adv_bonus)), 1)


# ═══════════════════════════════════════════════════════════════
#  MOTEUR ANALYTIQUE AVANCÉ
# ═══════════════════════════════════════════════════════════════

def compute_advanced_metrics(team, finished):
    """Calcule les métriques avancées : xG, pressing, efficacité tirs, solidité défensive."""
    team_matches = sorted(
        [m for m in finished if m["home_team"] == team or m["away_team"] == team],
        key=lambda x: x["date"], reverse=True
    )[:15]  # 15 derniers matchs

    if not team_matches:
        return {
            "xg_per90": 0, "xga_per90": 0, "pressing_intensity": 50,
            "shot_efficiency": 0, "defensive_solidity": 50,
            "avg_possession": 50, "avg_shots": 0, "avg_sot": 0,
            "avg_corners": 0, "avg_fouls": 0, "clean_sheets": 0,
            "data_completeness": 0,
        }

    total_shots = 0
    total_sot = 0
    total_goals_for = 0
    total_goals_against = 0
    total_corners = 0
    total_possession = 0
    total_fouls = 0
    clean_sheets = 0
    has_shots = 0
    has_poss = 0
    has_corners = 0
    n = len(team_matches)

    for m in team_matches:
        is_home = m["home_team"] == team
        gf = (m["home_score"] if is_home else m["away_score"]) or 0
        ga = (m["away_score"] if is_home else m["home_score"]) or 0
        total_goals_for += gf
        total_goals_against += ga
        if ga == 0:
            clean_sheets += 1

        shots = (m["home_shots"] if is_home else m["away_shots"])
        sot = (m["home_shots_on_target"] if is_home else m["away_shots_on_target"])
        poss = (m["home_possession"] if is_home else m["away_possession"])
        corners = (m["home_corners"] if is_home else m["away_corners"])
        fouls = (m["home_fouls"] if is_home else m["away_fouls"])

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
    avg_gf = total_goals_for / n
    avg_ga = total_goals_against / n

    # Modèle xG basé sur les tirs
    # xG par tir ≈ 0.10, par tir cadré ≈ 0.32 (données moyennes top 5 ligues)
    if has_shots and avg_shots > 0:
        xg_per90 = avg_sot * 0.32 + (avg_shots - avg_sot) * 0.04
    else:
        xg_per90 = avg_gf  # Fallback sur les buts réels

    # xGA (expected goals against) — estimation défensive
    # On utilise les buts encaissés pondérés par la solidité défensive
    xga_per90 = avg_ga

    # Intensité de pressing (0-100)
    # Haute possession + beaucoup de tirs + corners + fautes = pressing haut
    pressing = min(100, max(0,
        (avg_poss - 35) * 0.8 +
        avg_shots * 1.5 +
        avg_corners * 2.5 +
        avg_fouls * 0.3
    ))

    # Efficacité des tirs (% de conversion)
    shot_efficiency = (total_goals_for / total_shots * 100) if total_shots > 0 else 0

    # Solidité défensive (0-100, inversement lié aux buts encaissés)
    defensive_solidity = max(0, min(100, 100 - avg_ga * 30 + clean_sheets * 5))

    # Complétude des données (0-5 = combien de métriques sont disponibles)
    data_completeness = sum([has_shots > 0, has_poss > 0, has_corners > 0, n >= 5, n >= 10])

    return {
        "xg_per90":             round(xg_per90, 2),
        "xga_per90":            round(xga_per90, 2),
        "pressing_intensity":   round(pressing, 1),
        "shot_efficiency":      round(shot_efficiency, 1),
        "defensive_solidity":   round(defensive_solidity, 1),
        "avg_possession":       round(avg_poss, 1),
        "avg_shots":            round(avg_shots, 1),
        "avg_sot":              round(avg_sot, 1),
        "avg_corners":          round(avg_corners, 1),
        "avg_fouls":            round(avg_fouls, 1),
        "clean_sheets":         clean_sheets,
        "data_completeness":    data_completeness,
        "matches_analyzed":     n,
    }


def get_team_lineups(team, conn):
    """Récupère la dernière composition connue d'une équipe."""
    c = conn.cursor()
    c.execute("""
        SELECT l.match_id, l.player_name, l.position, l.is_starter, m.date
        FROM lineups l
        JOIN matches m ON l.match_id = m.id
        WHERE l.team = ?
        ORDER BY m.date DESC
    """, (team,))
    rows = c.fetchall()
    if not rows:
        return {"available": False, "starters": [], "subs": [], "formation": None, "date": None}

    last_match_id = rows[0][0]
    last_date = rows[0][4]
    starters = []
    subs = []
    for mid, name, pos, is_starter, date in rows:
        if mid != last_match_id:
            break
        if is_starter:
            starters.append({"name": name, "position": pos})
        else:
            subs.append({"name": name, "position": pos})

    # Récupérer la formation
    c.execute("SELECT home_team, home_formation, away_formation FROM matches WHERE id=?", (last_match_id,))
    mrow = c.fetchone()
    formation = None
    if mrow:
        formation = mrow[1] if mrow[0] == team else mrow[2]

    return {
        "available": len(starters) > 0,
        "starters": starters,
        "subs": subs,
        "formation": formation,
        "date": last_date,
        "match_id": last_match_id,
    }


def get_team_injuries(team, conn):
    """Récupère les blessures/suspensions d'une équipe."""
    c = conn.cursor()
    c.execute("""
        SELECT player_name, injury_type, detail, updated_at
        FROM injuries
        WHERE team = ?
        ORDER BY updated_at DESC
    """, (team,))
    return [
        {"player": r[0], "type": r[1], "detail": r[2], "updated_at": r[3]}
        for r in c.fetchall()
    ]


def predict_match_advanced(home, away, attack, defense, avg, home_adv, adv_h, adv_a):
    """Prédiction Poisson enrichie par les métriques avancées (xG, pressing, efficacité)."""
    lh = avg * attack.get(home, 1.0) * defense.get(away, 1.0) * home_adv
    la = avg * attack.get(away, 1.0) * defense.get(home, 1.0)

    # Ajustement xG : pondérer avec le xG réel quand disponible
    if adv_h["xg_per90"] > 0 and adv_h["data_completeness"] >= 2:
        xg_weight = min(0.4, adv_h["data_completeness"] * 0.08)
        lh = lh * (1 - xg_weight) + adv_h["xg_per90"] * xg_weight
    if adv_a["xg_per90"] > 0 and adv_a["data_completeness"] >= 2:
        xg_weight = min(0.4, adv_a["data_completeness"] * 0.08)
        la = la * (1 - xg_weight) + adv_a["xg_per90"] * xg_weight

    # Ajustement pressing : équipe avec pressing haut génère plus d'occasions
    if adv_h["pressing_intensity"] > 0 and adv_a["pressing_intensity"] > 0:
        press_diff = (adv_h["pressing_intensity"] - adv_a["pressing_intensity"]) / 200
        lh *= (1 + press_diff * 0.15)
        la *= (1 - press_diff * 0.10)

    # Ajustement solidité défensive
    if adv_a["defensive_solidity"] > 60:
        lh *= 0.95  # Défense solide réduit les buts attendus
    if adv_h["defensive_solidity"] > 60:
        la *= 0.95

    lh, la = max(0.1, lh), max(0.1, la)

    mat = np.array([
        [poisson.pmf(i, lh) * poisson.pmf(j, la) for j in range(9)]
        for i in range(9)
    ])
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

    odds_1 = round(1 / max(0.01, p_home), 2)
    odds_x = round(1 / max(0.01, p_draw), 2)
    odds_2 = round(1 / max(0.01, p_away), 2)

    dc_1x = round(p_home + p_draw, 4)
    dc_x2 = round(p_draw + p_away, 4)
    dc_12 = round(p_home + p_away, 4)

    return {
        "home_win":               p_home,
        "draw":                   p_draw,
        "away_win":               p_away,
        "expected_home_goals":    round(lh, 2),
        "expected_away_goals":    round(la, 2),
        "most_likely_score":      f"{bi}-{bj}",
        "most_likely_score_prob": round(float(mat[bi][bj]), 4),
        "over_15":                over_15,
        "over_25":                over_25,
        "over_35":                over_35,
        "btts_yes":               btts_yes,
        "btts_no":                round(1 - btts_yes, 4),
        "odds_1":                 odds_1,
        "odds_x":                 odds_x,
        "odds_2":                 odds_2,
        "double_chance_1x":       dc_1x,
        "double_chance_x2":       dc_x2,
        "double_chance_12":       dc_12,
        "top_scores":             top_scores,
    }

# ═══════════════════════════════════════════════════════════════
#  PIPELINE COMPLET
# ═══════════════════════════════════════════════════════════════

def run_pipeline(force=False):
    global last_update, is_running
    if is_running:
        return
    is_running = True
    print("\n[PIPELINE] Démarrage...")

    try:
        collect_data(force=force)

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM matches ORDER BY date")
        all_m = [dict(r) for r in c.fetchall()]

        finished  = [m for m in all_m if m["status"] == "FINISHED" and m["home_score"] is not None]
        scheduled = [m for m in all_m if m["status"] in ("SCHEDULED", "TIMED")]
        print(f"[PIPELINE] {len(finished)} terminés / {len(scheduled)} à prédire")

        # Entraîner les modèles
        elo = EloRating()
        for m in finished:
            elo.update(m["home_team"], m["away_team"], m["home_score"], m["away_score"])

        attack, defense, avg, home_adv = build_poisson(finished)

        # Calculer les métriques avancées pour toutes les équipes
        print("[PIPELINE] Calcul des métriques avancées (xG, pressing, efficacité)...")
        teams_adv = {}
        all_teams = set(m["home_team"] for m in all_m) | set(m["away_team"] for m in all_m)
        for t in all_teams:
            teams_adv[t] = compute_advanced_metrics(t, finished)

        # Générer les prédictions avec le modèle avancé
        for m in scheduled:
            home, away = m["home_team"], m["away_team"]
            adv_h = teams_adv.get(home, compute_advanced_metrics(home, finished))
            adv_a = teams_adv.get(away, compute_advanced_metrics(away, finished))

            pp = predict_match_advanced(home, away, attack, defense, avg, home_adv, adv_h, adv_a)
            fh = get_form(home, finished)
            fa = get_form(away, finished)
            h2h = get_h2h(home, away, finished)

            # Compositions et blessures
            lineup_h = get_team_lineups(home, conn)
            lineup_a = get_team_lineups(away, conn)
            injuries_h = get_team_injuries(home, conn)
            injuries_a = get_team_injuries(away, conn)

            conf = compute_confidence(pp, elo.get(home), elo.get(away), fh, fa, h2h, adv_h, adv_a)

            probs = {"home": pp["home_win"], "draw": pp["draw"], "away": pp["away_win"]}
            favorite = max(probs, key=probs.get)

            # Conseils paris
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

            pred = {
                "match_id":   m["id"],
                "date":       m["date"],
                "matchday":   m["matchday"],
                "competition":m["competition"],
                "home_team":  home,
                "away_team":  away,
                **pp,
                "elo_home":   round(elo.get(home)),
                "elo_away":   round(elo.get(away)),
                "form_home":  fh,
                "form_away":  fa,
                "h2h":        h2h,
                "advanced_home": adv_h,
                "advanced_away": adv_a,
                "lineup_home":   lineup_h,
                "lineup_away":   lineup_a,
                "injuries_home": injuries_h,
                "injuries_away": injuries_a,
                "confidence": conf,
                "favorite":   favorite,
                "betting_tips": tips,
                "generated_at": datetime.now().isoformat()
            }
            c.execute(
                "INSERT OR REPLACE INTO predictions VALUES (?,?,?)",
                (m["id"], json.dumps(pred, ensure_ascii=False), pred["generated_at"])
            )

        # Sauvegarder les ELO
        for team, rating in elo.ratings.items():
            c.execute(
                "INSERT OR REPLACE INTO elo_ratings VALUES (?,?,?)",
                (team, round(rating), datetime.now().isoformat())
            )

        conn.commit()
        conn.close()
        last_update = datetime.now().isoformat()
        print(f"[PIPELINE] Terminé — {last_update}")

    except Exception as e:
        import traceback
        print(f"[PIPELINE] ERREUR: {e}")
        traceback.print_exc()
    finally:
        is_running = False

# ═══════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(".", filename)

@app.route("/api/status")
def api_status():
    return jsonify({
        "status": "running" if is_running else "idle",
        "last_update": last_update
    })

@app.route("/api/refresh")
def api_refresh():
    if is_running:
        return jsonify({"ok": False, "message": "Pipeline déjà en cours..."})
    threading.Thread(target=lambda: run_pipeline(force=True), daemon=True).start()
    return jsonify({"ok": True, "message": "Pipeline relancé !"})

@app.route("/api/data")
def api_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("SELECT * FROM matches WHERE status='FINISHED' AND home_score IS NOT NULL ORDER BY date DESC")
    finished_raw = [dict(r) for r in c.fetchall()]

    c.execute("SELECT * FROM matches WHERE status='FINISHED' AND home_score IS NOT NULL")
    all_finished = [dict(r) for r in c.fetchall()]

    c.execute("""
        SELECT p.prediction_json FROM predictions p
        JOIN matches m ON p.match_id = m.id
        ORDER BY m.date ASC
    """)
    preds_out = [json.loads(r["prediction_json"]) for r in c.fetchall()]

    c.execute("SELECT * FROM elo_ratings ORDER BY rating DESC")
    elo_raw = [dict(r) for r in c.fetchall()]

    conn.close()

    # Classement par compétition
    team_comp = {}
    for m in all_finished:
        team_comp[m["home_team"]] = m["competition"]
        team_comp[m["away_team"]] = m["competition"]

    table = defaultdict(lambda: {"played": 0, "wins": 0, "draws": 0, "losses": 0,
                                  "goals_for": 0, "goals_against": 0, "points": 0})
    for m in all_finished:
        ht, at, hg, ag = m["home_team"], m["away_team"], m["home_score"], m["away_score"]
        table[ht]["played"] += 1; table[at]["played"] += 1
        table[ht]["goals_for"] += hg; table[ht]["goals_against"] += ag
        table[at]["goals_for"] += ag; table[at]["goals_against"] += hg
        if hg > ag:
            table[ht]["wins"] += 1; table[ht]["points"] += 3; table[at]["losses"] += 1
        elif hg < ag:
            table[at]["wins"] += 1; table[at]["points"] += 3; table[ht]["losses"] += 1
        else:
            table[ht]["draws"] += 1; table[ht]["points"] += 1
            table[at]["draws"] += 1; table[at]["points"] += 1

    standings = sorted(
        [{"team": t, "competition": team_comp.get(t, ""), "goal_diff": s["goals_for"] - s["goals_against"], **s} for t, s in table.items()],
        key=lambda x: (x["competition"], -x["points"], -x["goal_diff"], -x["goals_for"])
    )
    # Rang par compétition
    current_comp = None
    rank = 0
    for t in standings:
        if t["competition"] != current_comp:
            current_comp = t["competition"]
            rank = 1
        else:
            rank += 1
        t["rank"] = rank

    finished_out = []
    for m in finished_raw:
        res = "H" if m["home_score"] > m["away_score"] else "A" if m["home_score"] < m["away_score"] else "D"
        finished_out.append({
            "id": m["id"], "date": m["date"], "matchday": m["matchday"],
            "competition": m["competition"],
            "home_team": m["home_team"], "away_team": m["away_team"],
            "home_score": m["home_score"], "away_score": m["away_score"],
            "result": res,
            "home_xg": m["home_xg"], "away_xg": m["away_xg"],
            "home_possession": m.get("home_possession"), "away_possession": m.get("away_possession"),
            "home_shots": m.get("home_shots"), "away_shots": m.get("away_shots"),
            "home_shots_on_target": m.get("home_shots_on_target"),
            "away_shots_on_target": m.get("away_shots_on_target"),
            "home_corners": m.get("home_corners"), "away_corners": m.get("away_corners"),
            "home_fouls": m.get("home_fouls"), "away_fouls": m.get("away_fouls"),
            "home_yellow_cards": m.get("home_yellow_cards"), "away_yellow_cards": m.get("away_yellow_cards"),
            "home_red_cards": m.get("home_red_cards"), "away_red_cards": m.get("away_red_cards"),
            "home_formation": m.get("home_formation"), "away_formation": m.get("away_formation"),
        })

    n = len(all_finished)
    total_goals = sum((m["home_score"] or 0) + (m["away_score"] or 0) for m in all_finished)

    return jsonify({
        "meta": {
            "total_matches_played":    n,
            "total_matches_predicted": len(preds_out),
            "total_goals":             total_goals,
            "avg_goals_per_match":     round(total_goals / n, 2) if n else 0,
            "generated_at":            last_update or datetime.now().isoformat(),
            "season":                  CURRENT_SEASON
        },
        "standings":    standings,
        "elo_rankings": elo_raw,
        "recent_results": finished_out,
        "predictions":  preds_out
    })

# ═══════════════════════════════════════════════════════════════
#  LANCEMENT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "=" * 55)
    print("  ⚽  FOOTBALL.AI — Serveur local")
    print("=" * 55)
    print("  → http://localhost:5000")
    print("  Ctrl+C pour arrêter\n")
    init_db()
    threading.Thread(target=run_pipeline, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)