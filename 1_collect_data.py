"""
╔══════════════════════════════════════════════════════════════╗
║  SCRIPT 1 — COLLECTE DES DONNÉES                            ║
║  Récupère les matchs depuis l'API football-data.org          ║
║  Exécuter : python 1_collect_data.py                         ║
╚══════════════════════════════════════════════════════════════╝

API GRATUITE : https://www.football-data.org/
Inscris-toi pour avoir une clé API gratuite (10 req/min).
Remplace API_KEY ci-dessous par ta clé.

Compétitions disponibles :
  PL  = Premier League
  PD  = La Liga
  FL1 = Ligue 1
  BL1 = Bundesliga
  SA  = Serie A
  CL  = Champions League
"""

import requests
import sqlite3
import time
import json
import os
from datetime import datetime, timedelta

# ─── CONFIG ────────────────────────────────────────────────────

def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

API_KEY = os.environ.get("FOOTBALL_API_KEY", "VOTRE_CLE_API_ICI")
DB_PATH = "football.db"
COMPETITIONS = {
    "PL":  "Premier League",
    "FL1": "Ligue 1",
    "PD":  "La Liga",
    "BL1": "Bundesliga",
    "SA":  "Serie A",
}
SEASON = 2024
# ───────────────────────────────────────────────────────────────

HEADERS = {
    "X-Auth-Token": API_KEY
}

BASE_URL = "https://api.football-data.org/v4"


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
            updated_at TEXT
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY,
            name TEXT,
            short_name TEXT,
            competition TEXT
        )
    """)
    
    conn.commit()
    conn.close()
    print("✅ Base de données initialisée")


def fetch_matches(competition_code):
    url = f"{BASE_URL}/competitions/{competition_code}/matches"
    params = {"season": SEASON}
    
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        
        if r.status_code == 429:
            print("⏳ Rate limit atteint, attente 60s...")
            time.sleep(60)
            return fetch_matches(competition_code)
        
        if r.status_code == 403:
            print(f"❌ Clé API invalide ou compétition non autorisée: {competition_code}")
            return []
        
        r.raise_for_status()
        data = r.json()
        return data.get("matches", [])
    
    except requests.exceptions.RequestException as e:
        print(f"❌ Erreur réseau pour {competition_code}: {e}")
        return []


def parse_and_save(matches, competition_name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    saved = 0
    
    for m in matches:
        match_id = m["id"]
        date = m.get("utcDate", "")[:10]
        matchday = m.get("matchday", 0)
        status = m.get("status", "")
        
        home_team = m["homeTeam"]["name"]
        away_team = m["awayTeam"]["name"]
        
        score = m.get("score", {})
        full_time = score.get("fullTime", {})
        home_score = full_time.get("home")
        away_score = full_time.get("away")
        
        # Stats (disponibles selon le plan API)
        stats = m.get("statistics", {})
        home_xg = stats.get("home", {}).get("xg")
        away_xg = stats.get("away", {}).get("xg")
        
        c.execute("""
            INSERT OR REPLACE INTO matches 
            (id, competition, matchday, date, home_team, away_team, 
             home_score, away_score, status, home_xg, away_xg, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            match_id, competition_name, matchday, date,
            home_team, away_team, home_score, away_score,
            status, home_xg, away_xg,
            datetime.now().isoformat()
        ))
        saved += 1
    
    conn.commit()
    conn.close()
    return saved


def generate_demo_data():
    """
    Génère des données de démonstration si pas de clé API.
    Simule une saison réaliste de Premier League.
    """
    import random
    
    teams = [
        "Manchester City", "Arsenal", "Liverpool", "Chelsea",
        "Tottenham", "Manchester United", "Newcastle", "Brighton",
        "Aston Villa", "West Ham", "Brentford", "Crystal Palace",
        "Fulham", "Wolves", "Everton", "Nottm Forest",
        "Bournemouth", "Luton", "Burnley", "Sheffield United"
    ]
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    match_id = 1
    # Génère 25 journées de matchs
    for matchday in range(1, 26):
        base_date = datetime(2024, 8, 10) + timedelta(weeks=matchday - 1)
        shuffled = teams.copy()
        random.shuffle(shuffled)
        
        for i in range(0, len(shuffled), 2):
            home = shuffled[i]
            away = shuffled[i + 1]
            
            # Simulation réaliste avec avantage domicile
            home_strength = random.uniform(0.8, 1.5)
            away_strength = random.uniform(0.6, 1.2)
            
            home_goals = int(random.gauss(home_strength * 1.3, 0.9))
            away_goals = int(random.gauss(away_strength * 1.0, 0.8))
            home_goals = max(0, min(home_goals, 7))
            away_goals = max(0, min(away_goals, 6))
            
            home_xg = round(home_goals + random.uniform(-0.3, 0.6), 2)
            away_xg = round(away_goals + random.uniform(-0.3, 0.5), 2)
            home_xg = max(0.1, home_xg)
            away_xg = max(0.1, away_xg)
            
            home_poss = round(random.uniform(38, 65), 1)
            away_poss = round(100 - home_poss, 1)
            
            home_shots = random.randint(6, 20)
            away_shots = random.randint(4, 16)
            home_sot = random.randint(2, min(home_shots, 10))
            away_sot = random.randint(1, min(away_shots, 8))
            
            match_date = (base_date + timedelta(days=random.randint(0, 2))).strftime("%Y-%m-%d")
            
            c.execute("""
                INSERT OR REPLACE INTO matches 
                (id, competition, matchday, date, home_team, away_team,
                 home_score, away_score, status, home_xg, away_xg,
                 home_shots, away_shots, home_shots_on_target, away_shots_on_target,
                 home_possession, away_possession, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                match_id, "Premier League", matchday, match_date,
                home, away, home_goals, away_goals, "FINISHED",
                home_xg, away_xg, home_shots, away_shots,
                home_sot, away_sot, home_poss, away_poss,
                datetime.now().isoformat()
            ))
            match_id += 1
    
    # Journées futures (à prédire)
    for matchday in range(26, 39):
        base_date = datetime(2025, 2, 1) + timedelta(weeks=matchday - 26)
        shuffled = teams.copy()
        random.shuffle(shuffled)
        
        for i in range(0, len(shuffled), 2):
            home = shuffled[i]
            away = shuffled[i + 1]
            match_date = (base_date + timedelta(days=random.randint(0, 2))).strftime("%Y-%m-%d")
            
            c.execute("""
                INSERT OR REPLACE INTO matches 
                (id, competition, matchday, date, home_team, away_team,
                 home_score, away_score, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                match_id, "Premier League", matchday, match_date,
                home, away, None, None, "SCHEDULED",
                datetime.now().isoformat()
            ))
            match_id += 1
    
    conn.commit()
    conn.close()
    print(f"✅ {match_id - 1} matchs de démonstration générés")


def main():
    print("=" * 60)
    print("  FOOTBALL PREDICTOR — COLLECTE DES DONNÉES")
    print("=" * 60)
    
    init_db()
    
    if API_KEY == "VOTRE_CLE_API_ICI":
        print("\n⚠️  Pas de clé API → Génération de données de démonstration")
        print("   (Inscris-toi sur football-data.org pour de vraies données)\n")
        generate_demo_data()
        return
    
    total = 0
    for code, name in COMPETITIONS.items():
        print(f"\n📡 Récupération {name} ({code})...")
        matches = fetch_matches(code)
        
        if matches:
            saved = parse_and_save(matches, name)
            total += saved
            print(f"   ✅ {saved} matchs sauvegardés")
        
        time.sleep(6)  # Respect rate limit (10 req/min)
    
    print(f"\n🎉 Total : {total} matchs en base")


if __name__ == "__main__":
    main()
