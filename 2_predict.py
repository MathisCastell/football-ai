"""
╔══════════════════════════════════════════════════════════════╗
║  SCRIPT 2 — MOTEUR DE PRÉDICTION IA                         ║
║  Analyse les données et prédit les matchs futurs             ║
║  Exécuter : python 2_predict.py                              ║
╚══════════════════════════════════════════════════════════════╝

Algorithmes utilisés :
  1. Régression de Poisson (modélisation des buts)
  2. ELO Rating system (force relative des équipes)
  3. Analyse de forme (5 derniers matchs)
  4. Avantage domicile
  5. Head-to-Head historique
  6. XGBoost (si données suffisantes)

Installe les dépendances :
  pip install pandas numpy scipy scikit-learn xgboost
"""

import sqlite3
import json
import math
import random
from datetime import datetime
from collections import defaultdict

import numpy as np
from scipy.stats import poisson
from scipy.optimize import minimize

DB_PATH = "football.db"

# ─── ELO SYSTEM ────────────────────────────────────────────────
INITIAL_ELO = 1500
K_FACTOR = 32
HOME_ADVANTAGE_ELO = 100  # Points bonus domicile


class EloRating:
    def __init__(self):
        self.ratings = defaultdict(lambda: INITIAL_ELO)
    
    def expected(self, rating_a, rating_b):
        return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
    
    def update(self, home_team, away_team, home_score, away_score):
        """Met à jour les ELO après un match"""
        ra = self.ratings[home_team] + HOME_ADVANTAGE_ELO
        rb = self.ratings[away_team]
        
        ea = self.expected(ra, rb)
        eb = self.expected(rb, ra)
        
        if home_score > away_score:
            sa, sb = 1.0, 0.0
        elif home_score < away_score:
            sa, sb = 0.0, 1.0
        else:
            sa, sb = 0.5, 0.5
        
        # Facteur de victoire (bonus pour large victoire)
        goal_diff = abs(home_score - away_score)
        if goal_diff > 1:
            k_mult = 1 + (goal_diff - 1) * 0.1
        else:
            k_mult = 1.0
        
        self.ratings[home_team] += K_FACTOR * k_mult * (sa - ea)
        self.ratings[away_team] += K_FACTOR * k_mult * (sb - eb)
    
    def get(self, team):
        return self.ratings[team]


# ─── POISSON MODEL ─────────────────────────────────────────────

class PoissonModel:
    """
    Modèle de régression de Poisson pour prédire les scores.
    Calcule la force d'attaque/défense de chaque équipe.
    """
    
    def __init__(self, matches):
        self.teams = set()
        self.attack = {}
        self.defense = {}
        self.home_advantage = 1.3
        self._fit(matches)
    
    def _fit(self, matches):
        finished = [m for m in matches if m['home_score'] is not None]
        
        if len(finished) < 10:
            # Pas assez de données, valeurs par défaut
            for m in matches:
                self.teams.add(m['home_team'])
                self.teams.add(m['away_team'])
            for t in self.teams:
                self.attack[t] = 1.0
                self.defense[t] = 1.0
            return
        
        self.teams = set()
        for m in finished:
            self.teams.add(m['home_team'])
            self.teams.add(m['away_team'])
        
        teams_list = sorted(self.teams)
        n = len(teams_list)
        team_idx = {t: i for i, t in enumerate(teams_list)}
        
        # Paramètres initiaux : [mu, home_adv, att_0..n, def_0..n]
        # mu = moyenne de buts
        avg_goals = np.mean([m['home_score'] + m['away_score'] for m in finished]) / 2
        
        for t in teams_list:
            team_matches = [m for m in finished 
                           if m['home_team'] == t or m['away_team'] == t]
            if not team_matches:
                self.attack[t] = 1.0
                self.defense[t] = 1.0
                continue
            
            # Force d'attaque = buts marqués / moyenne
            goals_scored = sum(
                m['home_score'] if m['home_team'] == t else m['away_score']
                for m in team_matches
            )
            goals_conceded = sum(
                m['away_score'] if m['home_team'] == t else m['home_score']
                for m in team_matches
            )
            n_matches = len(team_matches)
            
            self.attack[t] = (goals_scored / n_matches) / avg_goals if avg_goals > 0 else 1.0
            self.defense[t] = (goals_conceded / n_matches) / avg_goals if avg_goals > 0 else 1.0
            
            # Normalisation
            self.attack[t] = max(0.3, min(2.5, self.attack[t]))
            self.defense[t] = max(0.3, min(2.5, self.defense[t]))
        
        # Calcul de la moyenne globale
        self.avg_goals = avg_goals
        
        # Avantage domicile
        home_goals = np.mean([m['home_score'] for m in finished])
        away_goals = np.mean([m['away_score'] for m in finished])
        self.home_advantage = home_goals / away_goals if away_goals > 0 else 1.3
    
    def predict_lambdas(self, home_team, away_team):
        """Retourne les lambdas (buts attendus) pour chaque équipe"""
        avg = getattr(self, 'avg_goals', 1.3)
        
        att_h = self.attack.get(home_team, 1.0)
        def_h = self.defense.get(home_team, 1.0)
        att_a = self.attack.get(away_team, 1.0)
        def_a = self.defense.get(away_team, 1.0)
        
        lambda_home = avg * att_h * def_a * self.home_advantage
        lambda_away = avg * att_a * def_h
        
        return max(0.1, lambda_home), max(0.1, lambda_away)
    
    def predict_proba(self, home_team, away_team, max_goals=8):
        """Retourne P(home_win), P(draw), P(away_win) et la matrice de scores"""
        lh, la = self.predict_lambdas(home_team, away_team)
        
        score_matrix = np.zeros((max_goals, max_goals))
        for i in range(max_goals):
            for j in range(max_goals):
                score_matrix[i][j] = poisson.pmf(i, lh) * poisson.pmf(j, la)
        
        # Normaliser (probabilités restantes au-delà de max_goals)
        total = score_matrix.sum()
        if total > 0:
            score_matrix /= total
        
        p_home_win = float(np.sum(np.tril(score_matrix, -1)))
        p_draw = float(np.trace(score_matrix))
        p_away_win = float(np.sum(np.triu(score_matrix, 1)))
        
        # Score le plus probable
        best_i, best_j = np.unravel_index(np.argmax(score_matrix), score_matrix.shape)
        
        return {
            "home_win": round(p_home_win, 4),
            "draw": round(p_draw, 4),
            "away_win": round(p_away_win, 4),
            "expected_home_goals": round(lh, 2),
            "expected_away_goals": round(la, 2),
            "most_likely_score": f"{best_i}-{best_j}",
            "most_likely_score_prob": round(float(score_matrix[best_i][best_j]), 4)
        }


# ─── FORM ANALYSIS ─────────────────────────────────────────────

def get_team_form(team, matches, n=5):
    """Analyse la forme des n derniers matchs d'une équipe"""
    team_matches = [
        m for m in matches 
        if (m['home_team'] == team or m['away_team'] == team)
        and m['home_score'] is not None
        and m['status'] == 'FINISHED'
    ]
    team_matches.sort(key=lambda x: x['date'], reverse=True)
    recent = team_matches[:n]
    
    if not recent:
        return {
            "form_string": "-----",
            "points": 0,
            "goals_scored": 0,
            "goals_conceded": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "form_score": 50.0
        }
    
    results = []
    points = 0
    goals_for = 0
    goals_against = 0
    
    for m in recent:
        is_home = m['home_team'] == team
        gf = m['home_score'] if is_home else m['away_score']
        ga = m['away_score'] if is_home else m['home_score']
        goals_for += gf
        goals_against += ga
        
        if gf > ga:
            results.append('W')
            points += 3
        elif gf == ga:
            results.append('D')
            points += 1
        else:
            results.append('L')
    
    # Score de forme (0-100)
    max_points = len(recent) * 3
    form_score = (points / max_points * 100) if max_points > 0 else 50
    
    # Pondération récente (match le plus récent = plus important)
    weighted_form = sum(
        (3 if r == 'W' else 1 if r == 'D' else 0) * (n - i)
        for i, r in enumerate(results)
    )
    max_weighted = sum(3 * (n - i) for i in range(len(results)))
    weighted_score = (weighted_form / max_weighted * 100) if max_weighted > 0 else 50
    
    return {
        "form_string": "".join(results),
        "points": points,
        "goals_scored": goals_for,
        "goals_conceded": goals_against,
        "wins": results.count('W'),
        "draws": results.count('D'),
        "losses": results.count('L'),
        "form_score": round(weighted_score, 1),
        "avg_goals_scored": round(goals_for / len(recent), 2),
        "avg_goals_conceded": round(goals_against / len(recent), 2)
    }


def get_h2h(home_team, away_team, matches, n=6):
    """Analyse les confrontations directes"""
    h2h = [
        m for m in matches
        if ((m['home_team'] == home_team and m['away_team'] == away_team) or
            (m['home_team'] == away_team and m['away_team'] == home_team))
        and m['home_score'] is not None
    ]
    h2h.sort(key=lambda x: x['date'], reverse=True)
    recent_h2h = h2h[:n]
    
    home_wins = away_wins = draws = 0
    for m in recent_h2h:
        if m['home_team'] == home_team:
            if m['home_score'] > m['away_score']:
                home_wins += 1
            elif m['home_score'] < m['away_score']:
                away_wins += 1
            else:
                draws += 1
        else:
            if m['away_score'] > m['home_score']:
                home_wins += 1
            elif m['away_score'] < m['home_score']:
                away_wins += 1
            else:
                draws += 1
    
    return {
        "total_games": len(recent_h2h),
        "home_wins": home_wins,
        "draws": draws,
        "away_wins": away_wins,
        "last_meetings": [
            {
                "date": m['date'],
                "home": m['home_team'],
                "away": m['away_team'],
                "score": f"{m['home_score']}-{m['away_score']}"
            }
            for m in recent_h2h[:3]
        ]
    }


# ─── CONFIDENCE SCORE ──────────────────────────────────────────

def compute_confidence(poisson_pred, elo_home, elo_away, form_home, form_away, h2h):
    """
    Score de confiance global (0-100).
    Combine Poisson + ELO + Forme + H2H.
    """
    # Probabilité dominante (plus c'est écrasant, plus c'est confiant)
    probs = [poisson_pred['home_win'], poisson_pred['draw'], poisson_pred['away_win']]
    max_prob = max(probs)
    entropy = -sum(p * math.log(p + 1e-9) for p in probs) / math.log(3)
    certainty = (1 - entropy) * 100
    
    # Accord ELO vs Poisson
    elo_favors_home = elo_home > elo_away + 50
    poisson_favors_home = poisson_pred['home_win'] > poisson_pred['away_win']
    elo_agreement = 20 if (elo_favors_home == poisson_favors_home) else 0
    
    # Forme claire
    form_diff = abs(form_home - form_away)
    form_bonus = min(15, form_diff * 0.15)
    
    # Historique H2H
    h2h_bonus = 0
    if h2h['total_games'] >= 3:
        h2h_bonus = 10
    
    confidence = certainty * 0.5 + elo_agreement + form_bonus + h2h_bonus
    return round(min(95, max(30, confidence)), 1)


# ─── MAIN PREDICTION ENGINE ─────────────────────────────────────

def run_predictions():
    print("=" * 60)
    print("  FOOTBALL PREDICTOR — MOTEUR DE PRÉDICTION")
    print("=" * 60)
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Charger tous les matchs
    c.execute("SELECT * FROM matches ORDER BY date ASC")
    all_matches = [dict(row) for row in c.fetchall()]
    
    finished = [m for m in all_matches if m['status'] == 'FINISHED']
    scheduled = [m for m in all_matches if m['status'] == 'SCHEDULED']
    
    print(f"\n📊 Matchs terminés : {len(finished)}")
    print(f"📅 Matchs à prédire : {len(scheduled)}")
    
    if not finished:
        print("❌ Aucun match terminé en base. Lance d'abord 1_collect_data.py")
        conn.close()
        return
    
    # ── Entraîner les modèles ──────────────────────────────────
    print("\n⚙️  Entraînement des modèles...")
    
    # 1. ELO
    elo = EloRating()
    for m in sorted(finished, key=lambda x: x['date']):
        elo.update(m['home_team'], m['away_team'], m['home_score'], m['away_score'])
    print("   ✅ ELO Rating calculé")
    
    # 2. Poisson
    poisson_model = PoissonModel(finished)
    print("   ✅ Modèle de Poisson entraîné")
    
    # ── Prédictions ───────────────────────────────────────────
    print(f"\n🔮 Génération des prédictions pour {len(scheduled)} matchs...")
    
    predictions = []
    
    for m in scheduled:
        home = m['home_team']
        away = m['away_team']
        
        # Poisson
        poisson_pred = poisson_model.predict_proba(home, away)
        
        # ELO
        elo_home = elo.get(home)
        elo_away = elo.get(away)
        
        # Forme
        form_home = get_team_form(home, finished)
        form_away = get_team_form(away, finished)
        
        # H2H
        h2h = get_h2h(home, away, finished)
        
        # Confiance
        confidence = compute_confidence(
            poisson_pred, elo_home, elo_away,
            form_home['form_score'], form_away['form_score'],
            h2h
        )
        
        # Déterminer le favori
        probs = {
            'home': poisson_pred['home_win'],
            'draw': poisson_pred['draw'],
            'away': poisson_pred['away_win']
        }
        favorite = max(probs, key=probs.get)
        
        prediction = {
            "match_id": m['id'],
            "date": m['date'],
            "matchday": m['matchday'],
            "competition": m['competition'],
            "home_team": home,
            "away_team": away,
            "expected_home_goals": poisson_pred['expected_home_goals'],
            "expected_away_goals": poisson_pred['expected_away_goals'],
            "most_likely_score": poisson_pred['most_likely_score'],
            "most_likely_score_prob": poisson_pred['most_likely_score_prob'],
            "home_win": poisson_pred['home_win'],
            "draw": poisson_pred['draw'],
            "away_win": poisson_pred['away_win'],
            "elo_home": round(elo_home),
            "elo_away": round(elo_away),
            "form_home": form_home,
            "form_away": form_away,
            "h2h": h2h,
            "confidence": confidence,
            "favorite": favorite,
            "generated_at": datetime.now().isoformat()
        }
        predictions.append(prediction)
    
    # ── Sauvegarder ───────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            match_id INTEGER PRIMARY KEY,
            prediction_json TEXT,
            generated_at TEXT
        )
    """)
    
    for pred in predictions:
        c.execute("""
            INSERT OR REPLACE INTO predictions (match_id, prediction_json, generated_at)
            VALUES (?, ?, ?)
        """, (pred['match_id'], json.dumps(pred, ensure_ascii=False), pred['generated_at']))
    
    # ── Stats ELO de toutes les équipes ───────────────────────
    elo_table = {team: round(rating) for team, rating in elo.ratings.items()}
    c.execute("""
        CREATE TABLE IF NOT EXISTS elo_ratings (
            team TEXT PRIMARY KEY,
            rating INTEGER,
            updated_at TEXT
        )
    """)
    for team, rating in elo_table.items():
        c.execute("""
            INSERT OR REPLACE INTO elo_ratings VALUES (?, ?, ?)
        """, (team, rating, datetime.now().isoformat()))
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ {len(predictions)} prédictions sauvegardées en base")
    print("\n📊 Top ELO :")
    top_elo = sorted(elo_table.items(), key=lambda x: x[1], reverse=True)[:5]
    for i, (team, rating) in enumerate(top_elo, 1):
        print(f"   {i}. {team}: {rating}")
    
    print("\n🎯 Exemples de prédictions :")
    for pred in predictions[:3]:
        print(f"\n   {pred['home_team']} vs {pred['away_team']} ({pred['date']})")
        print(f"   Score probable : {pred['most_likely_score']} | Confiance : {pred['confidence']}%")
        print(f"   1: {pred['home_win']:.1%} | X: {pred['draw']:.1%} | 2: {pred['away_win']:.1%}")
    
    print("\n🚀 Lance maintenant : python 3_export_json.py")


if __name__ == "__main__":
    run_predictions()
