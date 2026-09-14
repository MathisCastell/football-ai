from app.prediction.elo import EloRating


def test_initial_rating_is_1500():
    elo = EloRating()
    assert elo.get("Team A") == 1500


def test_winner_gains_points_loser_loses_points():
    elo = EloRating()
    before_home = elo.get("Home")
    before_away = elo.get("Away")
    elo.update("Home", "Away", 2, 0)
    assert elo.get("Home") > before_home
    assert elo.get("Away") < before_away


def test_draw_between_equal_teams_keeps_ratings_close():
    elo = EloRating()
    elo.update("Home", "Away", 1, 1)
    # L'avantage domicile fait légèrement baisser l'équipe à domicile sur un nul
    assert elo.get("Home") < 1500
    assert elo.get("Away") > 1500


def test_larger_margin_moves_rating_more():
    elo_small = EloRating()
    elo_small.update("Home", "Away", 1, 0)
    small_gain = elo_small.get("Home") - 1500

    elo_big = EloRating()
    elo_big.update("Home", "Away", 5, 0)
    big_gain = elo_big.get("Home") - 1500

    assert big_gain > small_gain
