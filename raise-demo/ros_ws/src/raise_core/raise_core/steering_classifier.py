"""Pure steering-health classification, kept free of ROS imports for unit testing.

The classifier compares commanded steering intent with the measured rack angle
over a short rolling window and reports a *symptom*, never a root cause.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"
STATUS_FAILED = "FAILED"

# Window geometry: 20 Hz feedback, 4 s window -> 80 (cmd, actual) pairs.
MIN_PAIRS = 40                # need >= 2 s of data before judging
# Generous: collision physics or rendering load can stall the sim below real
# time for over a second without the steering telemetry being actually dead.
FEEDBACK_TIMEOUT_S = 2.5

# The variance/correlation tests are meaningless without steering activity.
MIN_CMD_STD = 0.002           # rad
STUCK_ACTUAL_STD = 0.0005     # rad; measured angle flatlines while cmd moves
REVERSED_CORR = -0.5
# Reversal is dangerous at speed: also check a short recent sub-window so the
# safety stop fires seconds earlier than the full-window statistics would.
REVERSED_FAST_PAIRS = 25
REVERSED_FAST_CORR = -0.7
LAG_MIN_S = 0.3
LAG_MAX_S = 1.5
LAG_PEAK_CORR = 0.7
LAG_ZERO_CORR_MAX = 0.5
BIAS_MEAN_ERR = 0.02          # rad; injected bias is 0.06 -> 3x margin
NOISE_ERR_STD = 0.02          # rad; injected noise sigma is 0.04 -> 2x margin


@dataclass
class SteeringSnapshot:
    status: str
    symptom: str
    cmd_angle: float = 0.0
    actual_angle: float = 0.0
    angle_error_mean: float = 0.0
    angle_error_stddev: float = 0.0
    cmd_stddev: float = 0.0
    actual_stddev: float = 0.0
    cmd_actual_corr: float = 0.0
    response_lag_s: float = 0.0


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or a.std() == 0.0 or b.std() == 0.0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _peak_lag(cmd: np.ndarray, actual: np.ndarray, dt: float) -> tuple[float, float]:
    """Return (lag_seconds, correlation) of the best cmd->actual alignment.

    actual[t] responding to cmd[t - k*dt] shows up as a correlation peak when
    actual is shifted back by k samples.
    """
    max_shift = min(int(round(LAG_MAX_S / dt)), len(cmd) - 2)
    best_lag, best_corr = 0.0, -1.0
    for shift in range(0, max_shift + 1):
        a = cmd[: len(cmd) - shift] if shift else cmd
        b = actual[shift:]
        corr = _pearson(a, b)
        if corr > best_corr:
            best_corr, best_lag = corr, shift * dt
    return best_lag, best_corr


def classify_steering(
    cmd: Sequence[float],
    actual: Sequence[float],
    *,
    dt: float = 0.05,
    seconds_since_last_feedback: float = 0.0,
    pairs_seen: int = 0,
) -> SteeringSnapshot:
    if pairs_seen == 0 or seconds_since_last_feedback > FEEDBACK_TIMEOUT_S:
        return SteeringSnapshot(status=STATUS_FAILED, symptom="no_feedback")

    cmd_arr = np.asarray(cmd, dtype=np.float64)
    actual_arr = np.asarray(actual, dtype=np.float64)
    snapshot = SteeringSnapshot(
        status=STATUS_OK,
        symptom="ok",
        cmd_angle=float(cmd_arr[-1]) if len(cmd_arr) else 0.0,
        actual_angle=float(actual_arr[-1]) if len(actual_arr) else 0.0,
    )
    if len(cmd_arr) < MIN_PAIRS or len(cmd_arr) != len(actual_arr):
        return snapshot  # still warming up

    error = actual_arr - cmd_arr
    snapshot.angle_error_mean = float(error.mean())
    snapshot.angle_error_stddev = float(error.std())
    snapshot.cmd_stddev = float(cmd_arr.std())
    snapshot.actual_stddev = float(actual_arr.std())
    snapshot.cmd_actual_corr = _pearson(cmd_arr, actual_arr)

    has_cmd_signal = snapshot.cmd_stddev >= MIN_CMD_STD

    if has_cmd_signal and snapshot.cmd_actual_corr <= REVERSED_CORR:
        snapshot.status, snapshot.symptom = STATUS_FAILED, "reversed_response"
        return snapshot

    recent_cmd = cmd_arr[-REVERSED_FAST_PAIRS:]
    recent_actual = actual_arr[-REVERSED_FAST_PAIRS:]
    if (
        recent_cmd.std() >= MIN_CMD_STD
        and _pearson(recent_cmd, recent_actual) <= REVERSED_FAST_CORR
    ):
        snapshot.status, snapshot.symptom = STATUS_FAILED, "reversed_response"
        return snapshot

    if has_cmd_signal and snapshot.actual_stddev < STUCK_ACTUAL_STD:
        snapshot.status, snapshot.symptom = STATUS_FAILED, "unresponsive"
        return snapshot

    if has_cmd_signal:
        lag_s, lag_corr = _peak_lag(cmd_arr, actual_arr, dt)
        # A lagged-but-faithful response: strong correlation at a delayed
        # alignment while the zero-lag correlation is poor. Checked before the
        # noise rule because a delayed smooth signal also inflates std(error).
        if (
            lag_corr >= LAG_PEAK_CORR
            and lag_s >= LAG_MIN_S
            and snapshot.cmd_actual_corr < LAG_ZERO_CORR_MAX
        ):
            snapshot.response_lag_s = float(lag_s)
            snapshot.status, snapshot.symptom = STATUS_DEGRADED, "sluggish_response"
            return snapshot

    if abs(snapshot.angle_error_mean) >= BIAS_MEAN_ERR and snapshot.angle_error_stddev < NOISE_ERR_STD:
        snapshot.status, snapshot.symptom = STATUS_DEGRADED, "steering_pull"
        return snapshot

    if snapshot.angle_error_stddev >= NOISE_ERR_STD:
        snapshot.status, snapshot.symptom = STATUS_DEGRADED, "oscillation"
        return snapshot

    return snapshot
