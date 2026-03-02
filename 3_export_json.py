"""
╔══════════════════════════════════════════════════════════════╗
║  SCRIPT 3 — EXPORT JSON                                     ║
║  Exporte toutes les données en JSON pour le site web         ║
║  Exécuter : python 3_export_json.py                          ║
╚══════════════════════════════════════════════════════════════╝
"""

import sqlite3
import json
from datetime import datetime
from collections import defaultdict

DB_PATH = "football.db"
OUTPUT_FILE = "data.json"


def get_standings(matches):
    """Calcule le classement depuis les matchs terminés"""
    table = defaultdict(lambda: {
        "played": 0, "wins": 0, "draws": 0, "losses": 0,
        "goals_for": 0, "goals_against": 0, "points": 0
    })
    
    for m in matches:
        if m['status'] != 'FINISHED' or m['home_score'] is None:
            continue
        
        ht = m['home_team']
        at = m['away_team']
        hg = m['home_score']
        ag = m['away_score']
        
        table[ht]['played'] += 1
        table[ht]['goals_for'] += hg
        table[ht]['goals_against'] += ag
        
        table[at]['played'] += 1
        table[at]['goals_for'] += ag
        table[at]['goals_against'] += hg
        
        if hg > ag:
            table[ht]['wins'] += 1
            table[ht]['points'] += 3
            table[at]['losses'] += 1
        elif hg < ag:
            table[at]['wins'] += 1
            table[at]['points'] += 3
            table[ht]['losses'] += 1
        else:
            table[ht]['draws'] += 1
            table[ht]['points'] += 1
            table[at]['draws'] += 1
            table[at]['points'] += 1
    
    result = []
    for team, stats in table.items():
        gd = stats['goals_for'] - stats['goals_against']
        result.append({
            "team": team,
            "played": stats['played'],
            "wins": stats['wins'],
            "draws": stats['draws'],
            "losses": stats['losses'],
            "goals_for": stats['goals_for'],
            "goals_against": stats['goals_against'],
            "goal_diff": gd,
            "points": stats['points']
        })
    
    result.sort(key=lambda x: (-x['points'], -x['goal_diff'], -x['goals_for']))
    for i, team in enumerate(result, 1):
        team['rank'] = i
    
    return result


def export():
    print("=" * 60)
    print("  FOOTBALL PREDICTOR — EXPORT JSON")
    print("=" * 60)
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # ── Matchs terminés ───────────────────────────────────────
    c.execute("""
        SELECT * FROM matches 
        WHERE status = 'FINISHED' AND home_score IS NOT NULL
        ORDER BY date DESC
        LIMIT 200
    """)
    raw_finished = [dict(row) for row in c.fetchall()]
    
    finished_matches = []
    for m in raw_finished:
        result = "D"
        if m['home_score'] > m['away_score']:
            result = "H"
        elif m['home_score'] < m['away_score']:
            result = "A"
        
        finished_matches.append({
            "id": m['id'],
            "date": m['date'],
            "matchday": m['matchday'],
            "competition": m['competition'],
            "home_team": m['home_team'],
            "away_team": m['away_team'],
            "home_score": m['home_score'],
            "away_score": m['away_score'],
            "result": result,
            "home_xg": m['home_xg'],
            "away_xg": m['away_xg'],
            "home_possession": m['home_possession'],
            "away_possession": m['away_possession'],
            "home_shots": m['home_shots'],
            "away_shots": m['away_shots'],
        })
    
    # ── Prédictions ───────────────────────────────────────────
    c.execute("""
        SELECT p.*, m.date, m.matchday, m.competition,
               m.home_team, m.away_team
        FROM predictions p
        JOIN matches m ON p.match_id = m.id
        ORDER BY m.date ASC
        LIMIT 100
    """)
    raw_preds = [dict(row) for row in c.fetchall()]
    
    predictions = []
    for row in raw_preds:
        pred = json.loads(row['prediction_json'])
        predictions.append(pred)
    
    # ── ELO Rankings ──────────────────────────────────────────
    c.execute("SELECT * FROM elo_ratings ORDER BY rating DESC")
    elo_data = [dict(row) for row in c.fetchall()]
    
    # ── Classement ────────────────────────────────────────────
    c.execute("SELECT * FROM matches WHERE status = 'FINISHED'")
    all_finished = [dict(row) for row in c.fetchall()]
    standings = get_standings(all_finished)
    
    # ── Stats globales ────────────────────────────────────────
    total_goals = sum(
        (m['home_score'] or 0) + (m['away_score'] or 0) 
        for m in all_finished 
        if m['home_score'] is not None
    )
    n_finished = len(all_finished)
    
    stats = {
        "total_matches_played": n_finished,
        "total_matches_predicted": len(predictions),
        "total_goals": total_goals,
        "avg_goals_per_match": round(total_goals / n_finished, 2) if n_finished > 0 else 0,
        "generated_at": datetime.now().isoformat(),
        "season": 2024
    }
    
    # ── Assembler ─────────────────────────────────────────────
    output = {
        "meta": stats,
        "standings": standings,
        "elo_rankings": elo_data,
        "recent_results": finished_matches,
        "predictions": predictions
    }
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Export réussi → {OUTPUT_FILE}")
    print(f"   Matchs terminés : {len(finished_matches)}")
    print(f"   Prédictions : {len(predictions)}")
    print(f"   Équipes classées : {len(standings)}")
    print(f"\n🌐 Ouvre maintenant index.html dans ton navigateur !")
    
    conn.close()


if __name__ == "__main__":
    export()
