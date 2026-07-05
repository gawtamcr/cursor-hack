import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ros_ws" / "src" / "raise_core"))

from raise_core.steering_classifier import classify_steering  # noqa: E402


DT = 0.05  # 20 Hz feedback
WINDOW = 80  # 4 s


def make_cmd(n: int = WINDOW) -> np.ndarray:
    """Lane-follower-like command: gentle weave plus pixel-quantization jitter."""
    rng = np.random.default_rng(7)
    t = np.arange(n) * DT
    return 0.03 * np.sin(2 * np.pi * 0.4 * t) + 0.004 * rng.standard_normal(n)


def classify(cmd, actual, **kwargs):
    kwargs.setdefault("dt", DT)
    kwargs.setdefault("pairs_seen", len(cmd))
    return classify_steering(cmd, actual, **kwargs)


def test_healthy_tracking_is_ok():
    cmd = make_cmd()
    snapshot = classify(cmd, cmd.copy())
    assert (snapshot.status, snapshot.symptom) == ("OK", "ok")


def test_constant_offset_is_steering_pull():
    cmd = make_cmd()
    snapshot = classify(cmd, cmd + 0.06)
    assert (snapshot.status, snapshot.symptom) == ("DEGRADED", "steering_pull")
    assert abs(snapshot.angle_error_mean - 0.06) < 0.005


def test_flatlined_actual_is_unresponsive():
    cmd = make_cmd()
    snapshot = classify(cmd, np.full(WINDOW, 0.01))
    assert (snapshot.status, snapshot.symptom) == ("FAILED", "unresponsive")


def test_inverted_actual_is_reversed_response():
    cmd = make_cmd()
    snapshot = classify(cmd, -cmd)
    assert (snapshot.status, snapshot.symptom) == ("FAILED", "reversed_response")
    assert snapshot.cmd_actual_corr < -0.9


def test_freshly_inverted_actual_fails_fast():
    # Healthy history with only the most recent ~25 samples reversed: the
    # short sub-window rule must fire before the full-window corr drops.
    cmd = make_cmd()
    actual = cmd.copy()
    actual[-25:] = -cmd[-25:]
    snapshot = classify(cmd, actual)
    assert (snapshot.status, snapshot.symptom) == ("FAILED", "reversed_response")


def test_added_noise_is_oscillation():
    cmd = make_cmd()
    rng = np.random.default_rng(11)
    snapshot = classify(cmd, cmd + rng.normal(0.0, 0.04, WINDOW))
    assert (snapshot.status, snapshot.symptom) == ("DEGRADED", "oscillation")


def test_delayed_actual_is_sluggish_response():
    shift = 12  # 0.6 s at 20 Hz
    cmd_long = make_cmd(WINDOW + shift)
    cmd = cmd_long[shift:]
    actual = cmd_long[:-shift]  # actual trails cmd by 0.6 s
    snapshot = classify(cmd, actual)
    assert (snapshot.status, snapshot.symptom) == ("DEGRADED", "sluggish_response")
    assert abs(snapshot.response_lag_s - 0.6) <= 0.1


def test_stale_feedback_is_no_feedback():
    cmd = make_cmd()
    snapshot = classify(cmd, cmd.copy(), seconds_since_last_feedback=5.0)
    assert (snapshot.status, snapshot.symptom) == ("FAILED", "no_feedback")


def test_no_signal_does_not_false_positive():
    zeros = np.zeros(WINDOW)
    snapshot = classify(zeros, zeros.copy())
    assert (snapshot.status, snapshot.symptom) == ("OK", "ok")


def test_short_window_is_still_warming_up():
    cmd = make_cmd(20)
    snapshot = classify(cmd, -cmd)  # even a reversed signal must not fire yet
    assert (snapshot.status, snapshot.symptom) == ("OK", "ok")
