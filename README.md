# Cursor Enabled Hardware — PLM Assistant for Physical AI

A hackathon PoC: a **PLM (product-lifecycle-management) assistant for physical AI**. A test
engineer asks in natural language. A **Cursor agent** holds the product's requirements as durable
memory, and when a test fails it queries the **live ROS2 system through an MCP server**, correlates
the live signals against the PLM specs, and returns a root-cause diagnosis that cites a specific
requirement ID plus a recommended fix.

**Demo product:** a self-driving car (AV-1) with a defective steering subsystem.
**Demo line:** *"the steering validation test is failing, diagnose it"* → the agent pulls the
steering requirement from PLM → queries the live ROS2 steering topics via MCP → shows the
commanded-vs-measured deviation → names the root cause tied to `REQ-STEER-001` → recommends the fix.

---

## What's in this folder

This is one git repo containing two subprojects plus the demo glue:

| Path | What it is |
|------|-----------|
| [`raise-demo/`](raise-demo/) | The **backend**: a containerized ROS2 simulation of the steering-defected car, the ROS2 MCP server, and the live-diagnosis HTTP layer. |
| [`blackbox-whisper/`](blackbox-whisper/) | The **frontend**: the "Cursor Enabled Hardware" web app (TanStack Start + React + Tailwind/shadcn) with the PLM view, the live diagnostic pipeline, and the Cursor-agent integration. |

The **PLM memory** the agent cites lives one level up at the workspace root (so the agent's `cwd`
sees it): `../.cursor/rules/plm-steering.mdc`, `../.cursor/mcp.json`, and `../docs/plm/`
(actuator datasheet, known-issue log, test report TR-0417).

---

## Architecture

```
Cursor SDK agent (local runtime, blackbox-whisper/agent-sidecar)
        │  send("diagnose the steering test")
        ▼
ROS steering MCP server (stdio)            raise-demo/mcp-server/ros_steering_mcp.py
        │  HTTP → localhost:8000
        ▼
Read-only HTTP shim (in the container)     raise-demo/docker/mcp_http.py
        │  rclpy
        ▼
Live ROS2 graph (Jazzy, in Docker)         raise-demo/ros_ws/  +  docker/synthetic_plant.py
   /control/ackermann_cmd → eps_controller (fault injector) → /cmd_ackermann → plant
   steering_monitor compares intent vs measured → /steering/health (status + symptom)
        ▲
        └── correlated against PLM requirements → cited diagnosis (REQ-STEER-001 …)
```

Two diagnosis paths share one HTTP contract (`/diagnose`):
- **Rule-based** — `raise-demo/docker/plm_diagnosis.py` maps the live `/steering/health` symptom to a
  requirement + root cause + fix. Instant, no LLM.
- **Real Cursor agent** — `blackbox-whisper/agent-sidecar/server.ts` runs a durable `@cursor/sdk`
  **local** agent that calls the MCP tools, reads the PLM rules, and writes the cited diagnosis.
  (A local runtime is required — a cloud VM can't reach the sim on this machine.)

> **Why a sidecar for the agent?** This app's TanStack Start version has a broken server-function
> result serializer, so the agent runs in a small standalone Bun HTTP server instead of a route.

---

## Prerequisites

- **Docker** (Docker Desktop on WSL2) — runs the ROS2 sim; no host ROS install needed.
- **Bun** — runs the frontend and the agent sidecar (`curl -fsSL https://bun.sh/install | bash`).
- **Python 3** with a venv for the MCP server (already set up at
  `raise-demo/mcp-server/.venv`; recreate with `python3 -m venv` + `pip install mcp httpx pytest`).
- **A Cursor API key** on a paid plan (Background Agent API) — only needed for the *real agent*
  path. Put it in `blackbox-whisper/.env.local` as `CURSOR_API_KEY=...` (gitignored).

---

## Run the demo

Three processes. From this `GIT/` folder:

```bash
# 1) ROS2 sim + read-only HTTP shim  (publishes localhost:8000)
cd raise-demo && docker compose up -d

# 2) Cursor agent sidecar  (localhost:8790) — only for the real-agent path
cd ../blackbox-whisper && set -a && source .env.local && set +a && bun run agent-sidecar/server.ts

# 3) The web app  (localhost:8080)
cd blackbox-whisper && set -a && source .env.local && set +a && bun run dev
```

Open **http://localhost:8080** → click the steering issue (PLM-1047) → watch the live diagnostic
pipeline populate from the real graph. Use **Inject steering fault** / **Power-cycle EPS (fix)** to
drive the sim, and toggle **Cursor Agent: ON** to run the real `@cursor/sdk` agent for the diagnosis.

### Verify without the UI

```bash
# live diagnosis (rule-based) straight from the container shim
curl -s localhost:8000/diagnose

# inject the defect, then diagnose
curl -s -X POST localhost:8000/tools/set_fault -H 'Content-Type: application/json' -d '{"fault":"steer_bias"}'
curl -s localhost:8000/diagnose            # → TEST FAIL · REQ-STEER-001 · steering_pull

# the real Cursor agent (sidecar must be running)
curl -s -X POST localhost:8790/diagnose -H 'Content-Type: application/json' -d '{}'

# live-graph validation test (pytest)
raise-demo/mcp-server/.venv/bin/python -m pytest raise-demo/tests/test_steering_validation.py -v
```

---

## The steering fault & requirement

`eps_controller` (a disguised fault injector) can apply five steering faults via the `/fault/set`
service. The demo uses **`steer_bias`**: a constant **+0.06 rad** offset — the vehicle still tracks
command *direction* (`cmd_actual_corr` ≈ 0.99) but holds a steady bias, which violates
**`REQ-STEER-001`** (commanded-vs-measured deviation must stay within **±0.02 rad**). Root cause:
EPS zero-point calibration drift (known issue **KI-2231**); fix: power-cycle the EPS ECU.

See [`raise-demo/README.md`](raise-demo/README.md) for the full ROS2 graph, topics, and the
Webots-vs-synthetic-plant details, and [`blackbox-whisper/CLAUDE.md`](blackbox-whisper/CLAUDE.md)
for the frontend stack.
