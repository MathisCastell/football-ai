from app.prediction.poisson import build_poisson, predict_match_advanced

NEUTRAL_ADV = dict(
    xg_per90=0, data_completeness=0, pressing_intensity=0, defensive_solidity=0, rest_days=None
)


def _finished(n_matches=20):
    matches = []
    for i in range(n_matches):
        matches.append({
            "home_team": "A", "away_team": "B",
            "home_score": 2, "away_score": 1, "date": f"2024-01-{i+1:02d}",
        })
    return matches


def test_build_poisson_returns_attack_defense_avg_and_home_adv():
    attack, defense, avg, home_adv = build_poisson(_finished())
    assert "A" in attack and "B" in attack
    assert avg > 0
    assert home_adv > 0


def test_build_poisson_empty_history_uses_defaults():
    attack, defense, avg, home_adv = build_poisson([])
    assert attack == {}
    assert avg == 1.3


def test_predict_probabilities_sum_to_one():
    attack, defense, avg, home_adv = build_poisson(_finished())
    pred = predict_match_advanced("A", "B", attack, defense, avg, home_adv, NEUTRAL_ADV, NEUTRAL_ADV)
    total = pred["home_win"] + pred["draw"] + pred["away_win"]
    assert abs(total - 1.0) < 1e-6


def test_stronger_attack_increases_expected_goals():
    attack = {"Strong": 2.0, "Weak": 0.5}
    defense = {"Strong": 1.0, "Weak": 1.0}
    pred = predict_match_advanced("Strong", "Weak", attack, defense, 1.3, 1.3, NEUTRAL_ADV, NEUTRAL_ADV)
    assert pred["expected_home_goals"] > pred["expected_away_goals"]
