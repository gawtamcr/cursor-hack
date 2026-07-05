#!/usr/bin/env python3
"""PLM diagnosis mapping — the same symptom -> requirement -> root-cause -> fix
knowledge that lives in .cursor/rules/plm-steering.mdc and docs/plm/, expressed
as data so the shim can produce a cited diagnosis without an LLM.

This mirrors the Cursor-agent path (Path B) behaviourally: read the live
/steering/health symptom, correlate against the PLM requirements, and return a
root cause that cites a REQ-STEER-### id plus a recommended action. When the
@cursor/sdk agent is wired in later, it replaces THIS function while the output
contract stays the same, so the web panel does not change.
"""
from __future__ import annotations

from typing import Any

# symptom -> PLM knowledge (kept in lockstep with .cursor/rules/plm-steering.mdc)
PLM_BY_SYMPTOM: dict[str, dict[str, str]] = {
    "ok": {
        "requirement_id": "REQ-STEER-001",
        "title": "Steering within tolerance",
        "root_cause": "No fault. Commanded and measured steering angle agree within the ±0.02 rad deviation budget.",
        "action": "No action required. Steering validation passes.",
        "known_issue": "",
    },
    "steering_pull": {
        "requirement_id": "REQ-STEER-001",
        "title": "Commanded-vs-measured deviation exceeds ±0.02 rad",
        "root_cause": "EPS-770 zero-point calibration drift (datasheet §4.2): the actuator applies a constant angular bias (~+0.06 rad) while still tracking command direction (high cmd_actual_corr).",
        "action": "Power-cycle the steering ECU to re-run §4.1 zero calibration (restart_steering_unit). If it recurs after re-cal, flag EPS calibration (KI-2231).",
        "known_issue": "KI-2231",
    },
    "reversed_response": {
        "requirement_id": "REQ-STEER-002",
        "title": "Steering direction inverted (cmd_actual_corr < 0)",
        "root_cause": "EPS harness/firmware polarity inverted after an actuator swap — measured angle moves opposite to command.",
        "action": "Do NOT drive. Escalate to hardware; verify EPS harness orientation (KI-2244).",
        "known_issue": "KI-2244",
    },
    "unresponsive": {
        "requirement_id": "REQ-STEER-003",
        "title": "Actuator stuck — measured angle flatlines while command moves",
        "root_cause": "EPS-770 rack seizure: actual_stddev collapses though the command is still varying.",
        "action": "Stop the vehicle. Power-cycle EPS; if still stuck, actuator replacement (KI-2251).",
        "known_issue": "KI-2251",
    },
    "sluggish_response": {
        "requirement_id": "REQ-STEER-004",
        "title": "Actuator response lag exceeds 0.3 s",
        "root_cause": "Excess control-loop / assist latency (~0.6 s): measured angle correlates with command only at a delayed alignment.",
        "action": "Reduce speed; power-cycle EPS. If lag persists, investigate control-loop scheduling (KI-2263).",
        "known_issue": "KI-2263",
    },
    "oscillation": {
        "requirement_id": "REQ-STEER-005",
        "title": "Steering noise exceeds 0.02 rad",
        "root_cause": "Rack-angle sensor noise (σ~0.04 rad) inflating the command-vs-measured error variance.",
        "action": "Check the rack-angle sensor connector / EMI shielding; power-cycle to clear a transient (KI-2277).",
        "known_issue": "KI-2277",
    },
    "no_feedback": {
        "requirement_id": "REQ-STEER-006",
        "title": "Steering feedback dropout / stale",
        "root_cause": "chassis_interface is not publishing /vehicle/steering/feedback at the required rate.",
        "action": "Confirm the chassis interface node is alive (get_node_list); restart the feedback path (KI-2288).",
        "known_issue": "KI-2288",
    },
}


def diagnose(health: dict[str, Any]) -> dict[str, Any]:
    """Turn a live /steering/health snapshot into a cited PLM diagnosis.

    Output contract (stable across the LLM upgrade):
      {
        test_status: "PASS" | "FAIL",
        status, symptom,                       # from live health
        requirement_id, title, root_cause, action, known_issue,
        summary,                               # one-line narrative
        evidence: { cmd_angle, actual_angle, angle_error_mean, ... },
        claims: [ {label, value} ]             # the numbers behind the call
      }
    """
    symptom = health.get("symptom", "no_data")
    status = health.get("status", "UNKNOWN")
    plm = PLM_BY_SYMPTOM.get(symptom, {
        "requirement_id": "—",
        "title": "Unrecognised steering state",
        "root_cause": f"No PLM mapping for symptom '{symptom}'.",
        "action": "Inspect live telemetry manually.",
        "known_issue": "",
    })

    passing = status == "OK"
    if passing:
        summary = (
            f"Steering validation PASSES. Commanded and measured angle agree "
            f"(error {health.get('angle_error_mean', 0):+.3f} rad), within "
            f"{plm['requirement_id']} (±0.02 rad)."
        )
    else:
        summary = (
            f"Steering validation FAILS ({status}). Symptom '{symptom}' violates "
            f"{plm['requirement_id']}: {plm['title']}. Root cause: {plm['root_cause']} "
            f"Recommended: {plm['action']}"
        )

    return {
        "test_status": "PASS" if passing else "FAIL",
        "status": status,
        "symptom": symptom,
        "requirement_id": plm["requirement_id"],
        "title": plm["title"],
        "root_cause": plm["root_cause"],
        "action": plm["action"],
        "known_issue": plm["known_issue"],
        "summary": summary,
        "evidence": {
            "cmd_angle": health.get("cmd_angle"),
            "actual_angle": health.get("actual_angle"),
            "angle_error_mean": health.get("angle_error_mean"),
            "cmd_actual_corr": health.get("cmd_actual_corr"),
            "response_lag_s": health.get("response_lag_s"),
            "feedback_rate_hz": health.get("feedback_rate_hz"),
        },
        "claims": [
            {"label": "Commanded angle", "value": f"{health.get('cmd_angle', 0):+.3f} rad"},
            {"label": "Measured angle", "value": f"{health.get('actual_angle', 0):+.3f} rad"},
            {"label": "Mean deviation", "value": f"{health.get('angle_error_mean', 0):+.3f} rad"},
            {"label": "Direction corr.", "value": f"{health.get('cmd_actual_corr', 0):.2f}"},
            {"label": "Response lag", "value": f"{health.get('response_lag_s', 0):.2f} s"},
            {"label": "Feedback rate", "value": f"{health.get('feedback_rate_hz', 0):.1f} Hz"},
        ],
    }
