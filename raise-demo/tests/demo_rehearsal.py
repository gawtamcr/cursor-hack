"""End-to-end demo rehearsal — runs the 60-second demo line against the live sim.

Prereq: docker compose up -d.  Run:
    GIT/raise-demo/mcp-server/.venv/bin/python GIT/raise-demo/tests/demo_rehearsal.py
"""
import time

import httpx

SHIM = "http://localhost:8000"


def get(p):
    return httpx.get(f"{SHIM}{p}", timeout=10).json()


def post(p, j=None):
    return httpx.post(f"{SHIM}{p}", json=j, timeout=10).json()


print("=" * 56)
print("  DEMO REHEARSAL — 60-second run order")
print("=" * 56)

print("\n[1] BASELINE — vehicle healthy, validation passing")
post("/tools/restart_steering_unit")
time.sleep(6)
d = get("/diagnose")
print(f"    test={d['test_status']}  status={d['status']}  symptom={d['symptom']}")

print("\n[2] DEFECT INJECTED (off-camera) — steering starts failing")
r = post("/tools/set_fault", {"fault": "steer_bias"})
print(f"    {r['message']}")
time.sleep(6)

print('\n[3] ENGINEER: "the steering validation test is failing, diagnose it"')
print("    -> agent queries live ROS2 + PLM:")
d = get("/diagnose")
ev = d["evidence"]
print(f"    TEST {d['test_status']} / {d['status']}")
print(f"    Requirement: {d['requirement_id']}  ({d['known_issue']})")
print(f"    Commanded vs measured: {ev['cmd_angle']:+.3f} vs {ev['actual_angle']:+.3f} rad")
print(f"    Deviation: {ev['angle_error_mean']:+.3f} rad  (limit +/-0.02)")
print(f"    Root cause: {d['root_cause'][:88]}...")
print(f"    >> Recommended: {d['action'][:88]}")

print("\n[4] APPLY FIX — power-cycle EPS")
r = post("/tools/restart_steering_unit")
print(f"    {r['action']}: ok={r['ok']}")
time.sleep(6)

print("\n[5] VERIFY — validation passes again")
d = get("/diagnose")
print(f"    test={d['test_status']}  status={d['status']}")
print(f"    {d['summary'][:88]}")

print("\n" + "=" * 56)
print("  REHEARSAL COMPLETE")
print("=" * 56)
