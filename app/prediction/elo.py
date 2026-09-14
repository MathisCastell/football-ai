"""Système de notation ELO. Les paramètres (K_FACTOR, avantage domicile) ne sont
plus codés en dur : ils sont lus depuis ModelParams et ajustés par
app/learning/calibrate.py au fil du temps."""

from collections import defaultdict

INITIAL_ELO = 1500


class EloRating:
    def __init__(self, k_factor=32.0, home_advantage=100.0):
        self.ratings = defaultdict(lambda: INITIAL_ELO)
        self.k_factor = k_factor
        self.home_advantage = home_advantage

    def expected(self, rating_a, rating_b):
        return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))

    def update(self, home_team, away_team, home_score, away_score):
        ra = self.ratings[home_team] + self.home_advantage
        rb = self.ratings[away_team]
        ea = self.expected(ra, rb)
        eb = self.expected(rb, ra)

        if home_score > away_score:
            sa, sb = 1.0, 0.0
        elif home_score < away_score:
            sa, sb = 0.0, 1.0
        else:
            sa, sb = 0.5, 0.5

        goal_diff = abs(home_score - away_score)
        k_mult = 1 + (goal_diff - 1) * 0.1 if goal_diff > 1 else 1.0

        self.ratings[home_team] += self.k_factor * k_mult * (sa - ea)
        self.ratings[away_team] += self.k_factor * k_mult * (sb - eb)

    def get(self, team):
        return self.ratings[team]
