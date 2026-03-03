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
        c.execute("SELECT MAX(date) FROM matches WHERE status='SCHEDULED'")
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
        _generate_demo()


def _generate_demo():
    print("[DEMO] Génération des données (dates relatives à aujourd'hui)...")
    teams = [
        "Manchester City", "Arsenal", "Liverpool", "Chelsea",
        "Tottenham", "Manchester United", "Newcastle", "Brighton",
        "Aston Villa", "West Ham", "Brentford", "Crystal Palace",
        "Fulham", "Wolves", "Everton", "Nottm Forest",
        "Bournemouth", "Luton", "Burnley", "Sheffield United"
    ]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM matches")
    c.execute("DELETE FROM predictions")
    c.execute("DELETE FROM elo_ratings")
    match_id = 1
    today = datetime.now()

    # 25 journées passées (résultats connus) — derniers ~6 mois
    for matchday in range(1, 26):
        base_date = today - timedelta(weeks=26 - matchday)
        shuffled = teams.copy()
        random.shuffle(shuffled)
        for i in range(0, len(shuffled), 2):
            home, away = shuffled[i], shuffled[i + 1]
            hg = max(0, min(7, int(random.gauss(1.5, 1.1))))
            ag = max(0, min(6, int(random.gauss(1.1, 1.0))))
            hxg = round(max(0.1, hg + random.uniform(-0.4, 0.7)), 2)
            axg = round(max(0.1, ag + random.uniform(-0.4, 0.6)), 2)
            hp  = round(random.uniform(38, 65), 1)
            c.execute("""
                INSERT OR REPLACE INTO matches
                (id, competition, matchday, date, home_team, away_team,
                 home_score, away_score, status, home_xg, away_xg,
                 home_shots, away_shots, home_shots_on_target, away_shots_on_target,
                 home_possession, away_possession, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                match_id, "Premier League", matchday,
                (base_date + timedelta(days=random.randint(0, 2))).strftime("%Y-%m-%d"),
                home, away, hg, ag, "FINISHED",
                hxg, axg,
                random.randint(6, 20), random.randint(4, 16),
                random.randint(2, 8), random.randint(1, 7),
                hp, round(100 - hp, 1),
                datetime.now().isoformat()
            ))
            match_id += 1

    # 13 journées futures (à prédire) — prochaines semaines
    for matchday in range(26, 39):
        base_date = today + timedelta(weeks=matchday - 25)
        shuffled = teams.copy()
        random.shuffle(shuffled)
        for i in range(0, len(shuffled), 2):
            home, away = shuffled[i], shuffled[i + 1]
            c.execute("""
                INSERT OR REPLACE INTO matches
                (id, competition, matchday, date, home_team, away_team, status, updated_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                match_id, "Premier League", matchday,
                (base_date + timedelta(days=random.randint(0, 2))).strftime("%Y-%m-%d"),
                home, away, "SCHEDULED",
                datetime.now().isoformat()
            ))
            match_id += 1

    conn.commit()
    conn.close()
    print(f"[DEMO] {match_id - 1} matchs générés.")


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


def compute_confidence(pp, elo_home, elo_away, fh, fa, h2h):
    probs = [pp["home_win"], pp["draw"], pp["away_win"]]
    entropy = -sum(p * math.log(p + 1e-9) for p in probs) / math.log(3)
    certainty = (1 - entropy) * 50
    elo_ok = 20 if (elo_home > elo_away + 50) == (pp["home_win"] > pp["away_win"]) else 0
    form_bonus = min(15, abs(fh["form_score"] - fa["form_score"]) * 0.15)
    h2h_bonus = 10 if h2h["total_games"] >= 3 else 0
    return round(min(95, max(30, certainty + elo_ok + form_bonus + h2h_bonus)), 1)

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
        scheduled = [m for m in all_m if m["status"] == "SCHEDULED"]
        print(f"[PIPELINE] {len(finished)} terminés / {len(scheduled)} à prédire")

        # Entraîner les modèles
        elo = EloRating()
        for m in finished:
            elo.update(m["home_team"], m["away_team"], m["home_score"], m["away_score"])

        attack, defense, avg, home_adv = build_poisson(finished)

        # Générer les prédictions
        for m in scheduled:
            home, away = m["home_team"], m["away_team"]
            pp = predict_match(home, away, attack, defense, avg, home_adv)
            fh = get_form(home, finished)
            fa = get_form(away, finished)
            h2h = get_h2h(home, away, finished)
            conf = compute_confidence(pp, elo.get(home), elo.get(away), fh, fa, h2h)

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

    c.execute("SELECT * FROM matches WHERE status='FINISHED' AND home_score IS NOT NULL ORDER BY date DESC LIMIT 200")
    finished_raw = [dict(r) for r in c.fetchall()]

    c.execute("SELECT * FROM matches WHERE status='FINISHED' AND home_score IS NOT NULL")
    all_finished = [dict(r) for r in c.fetchall()]

    c.execute("""
        SELECT p.prediction_json FROM predictions p
        JOIN matches m ON p.match_id = m.id
        ORDER BY m.date ASC LIMIT 100
    """)
    preds_out = [json.loads(r["prediction_json"]) for r in c.fetchall()]

    c.execute("SELECT * FROM elo_ratings ORDER BY rating DESC")
    elo_raw = [dict(r) for r in c.fetchall()]

    conn.close()

    # Classement
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
        [{"team": t, "goal_diff": s["goals_for"] - s["goals_against"], **s} for t, s in table.items()],
        key=lambda x: (-x["points"], -x["goal_diff"], -x["goals_for"])
    )
    for i, t in enumerate(standings, 1):
        t["rank"] = i

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
            "home_possession": m["home_possession"], "away_possession": m["away_possession"],
            "home_shots": m["home_shots"], "away_shots": m["away_shots"],
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