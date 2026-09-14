from app.learning.calibrate import apply_calibration, _logit, _sigmoid
from app.models import ModelParams


def test_logit_sigmoid_are_inverse():
    for p in (0.1, 0.3, 0.5, 0.7, 0.9):
        assert abs(_sigmoid(_logit(p)) - p) < 1e-6


def test_identity_calibration_leaves_probabilities_unchanged():
    params = ModelParams(competition="TEST")  # a=1, b=0 par défaut (colonnes SQLAlchemy)
    params.calib_home_a, params.calib_home_b = 1.0, 0.0
    params.calib_draw_a, params.calib_draw_b = 1.0, 0.0
    params.calib_away_a, params.calib_away_b = 1.0, 0.0

    probs = (0.5, 0.3, 0.2)
    calibrated = apply_calibration(probs, params)
    for a, b in zip(probs, calibrated):
        assert abs(a - b) < 1e-6


def test_calibration_output_sums_to_one():
    params = ModelParams(competition="TEST")
    params.calib_home_a, params.calib_home_b = 1.4, 0.2
    params.calib_draw_a, params.calib_draw_b = 0.8, -0.1
    params.calib_away_a, params.calib_away_b = 1.1, 0.0

    calibrated = apply_calibration((0.5, 0.3, 0.2), params)
    assert abs(sum(calibrated) - 1.0) < 1e-6
