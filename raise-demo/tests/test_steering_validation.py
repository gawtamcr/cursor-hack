"""Live-graph steering validation test — the pass/fail the demo line references.

Unlike test_steering_classifier.py (a pure unit test of classify_steering),
this exercises the RUNNING system end to end: it drives faults into the live
ROS2 graph via the HTTP shim and asserts on the real /steering/health output.

Prereq: the sim container is up:
    cd GIT/raise-demo && docker compose up -d

Run:
    GIT/raise-demo/mcp-server/.venv/bin/python -m pytest \
        GIT/raise-demo/tests/test_steering_validation.py -v

REQ-STEER-001: |angle_error_mean| must stay within ±0.02 rad. This test is the
executable form of TR-0417 (docs/plm/test-reports/): healthy PASSES, an injected
steer_bias FAILS with symptom steering_pull, and a power-cycle recovers to PASS.
"""
from __future__ import annotations

import time

import httpx
import pytest

SHIM = "http://localhost:8000"
REQ_STEER_001_MAX_DEVIATION = 0.02  # rad
MONITOR_WINDOW_S = 6.0  # let the 4 s monitor window refill after a fault change


def _get(path: str) -> dict:
    return httpx.get(f"{SHIM}{path}", timeout=10.0).json()


def _post(path: str, json: dict | None = None) -> dict:
    return httpx.post(f"{SHIM}{path}", json=json, timeout=10.0).json()


def _set_fault(fault: str) -> None:
    _post("/tools/set_fault", {"fault": fault})
    time.sleep(MONITOR_WINDOW_S)


@pytest.fixture(autouse=True)
def _require_sim():
    """Skip cleanly (not error) if the container/shim isn't up."""
    try:
        assert _get("/health").get("bridge_ready")
    except Exception:
        pytest.skip("sim container not running (docker compose up -d)")


@pytest.fixture(autouse=True)
def _clean_slate():
    """Always start and end healthy so tests don't leak fault state."""
    _post("/tools/restart_steering_unit")
    time.sleep(MONITOR_WINDOW_S)
    yield
    _post("/tools/restart_steering_unit")


def test_healthy_passes_req_steer_001():
    """A healthy vehicle passes steering validation (REQ-STEER-001)."""
    h = _get("/tools/get_steering_health")
    assert h["status"] == "OK", f"expected OK, got {h['status']}/{h['symptom']}"
    assert abs(h["angle_error_mean"]) < REQ_STEER_001_MAX_DEVIATION


def test_steer_bias_fails_and_cites_the_symptom():
    """steer_bias violates REQ-STEER-001 with symptom steering_pull."""
    _set_fault("steer_bias")
    h = _get("/tools/get_steering_health")
    assert h["status"] != "OK", "steer_bias should make validation FAIL"
    assert h["symptom"] == "steering_pull"
    # The +0.06 rad injected bias is well beyond the ±0.02 rad requirement.
    assert abs(h["angle_error_mean"]) >= REQ_STEER_001_MAX_DEVIATION
    # Direction still tracks — this is a bias, not an inversion.
    assert h["cmd_actual_corr"] > 0.5


def test_diagnosis_cites_requirement_id():
    """The /diagnose endpoint returns a FAIL that cites REQ-STEER-001 + KI-2231."""
    _set_fault("steer_bias")
    d = _get("/diagnose")
    assert d["test_status"] == "FAIL"
    assert d["requirement_id"] == "REQ-STEER-001"
    assert d["known_issue"] == "KI-2231"
    assert "restart" in d["action"].lower() or "power-cycle" in d["action"].lower()


def test_restart_recovers_to_pass():
    """Power-cycling the EPS clears the fault and validation passes again."""
    _set_fault("steer_bias")
    assert _get("/tools/get_steering_health")["status"] != "OK"
    _post("/tools/restart_steering_unit")
    time.sleep(MONITOR_WINDOW_S)
    h = _get("/tools/get_steering_health")
    assert h["status"] == "OK", f"expected recovery, got {h['status']}/{h['symptom']}"
    assert abs(h["angle_error_mean"]) < REQ_STEER_001_MAX_DEVIATION
