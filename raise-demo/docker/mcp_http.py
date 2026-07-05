#!/usr/bin/env python3
"""Read-only HTTP tool shim — the boundary the host-side MCP server calls.

This is a standalone FastAPI service that exposes the RAISE steering diagnostic
tools as plain JSON over localhost:8000. It reuses the existing tool surface
(agent_backend/tools.py -> AgentTools) VERBATIM, wrapped around a minimal rclpy
bridge. It deliberately does NOT import agent_backend/app.py or the LLM agent —
so it needs no cv2/camera/NVIDIA key, and it modifies NOTHING under
ros_ws/src/** or agent_backend/**. (Resolves the "one-line include_router"
open question: no existing file is touched at all.)

Endpoints mirror the 5 AgentTools methods:
  GET  /health                      -> shim liveness
  GET  /tools/get_steering_health   -> latest /steering/health snapshot
  GET  /tools/get_steering_stats    -> waits for 2 fresh samples, returns stats
  GET  /tools/get_topic_info?topic= -> topic type + pub/sub counts
  GET  /tools/get_node_list         -> visible ROS node names
  POST /tools/set_fault  {fault}    -> call the real /fault/set service
  POST /tools/restart_steering_unit -> clear the fault (power-cycle analog)

Run inside the sim container (ROS + workspace sourced). tools.py is importable
because it is mounted next to this file and added to sys.path below.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Callable

# tools.py is mounted alongside this file; make it importable without touching
# the original agent_backend package layout.
sys.path.insert(0, str(Path(__file__).parent))

import rclpy
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from rclpy.node import Node

from raise_interfaces.msg import SteeringHealth
from raise_interfaces.srv import SetFault

# Reused verbatim from agent_backend/tools.py (mounted next to this file).
from tools import AgentTools
# PLM symptom -> requirement -> root-cause -> fix mapping (mounted alongside).
from plm_diagnosis import diagnose as plm_diagnose


class MinimalROSBridge(Node):
    """The subset of agent_backend's ROSBridge that AgentTools actually needs.

    AgentTools only touches: latest_health, health_seq, get_topic_info(),
    get_node_names(), set_fault(). No camera / cv2 / LLM here.
    """

    def __init__(self) -> None:
        super().__init__("mcp_http_bridge")
        self.latest_health: dict[str, Any] | None = None
        self.health_seq = 0
        self.create_subscription(SteeringHealth, "/steering/health", self._health_cb, 10)
        self.fault_client = self.create_client(SetFault, "/fault/set")

    def _health_cb(self, msg: SteeringHealth) -> None:
        self.latest_health = {
            "type": "health",
            "status": msg.status,
            "symptom": msg.symptom,
            "cmd_angle": round(float(msg.cmd_angle), 4),
            "actual_angle": round(float(msg.actual_angle), 4),
            "angle_error_mean": round(float(msg.angle_error_mean), 4),
            "angle_error_stddev": round(float(msg.angle_error_stddev), 4),
            "cmd_actual_corr": round(float(msg.cmd_actual_corr), 3),
            "response_lag_s": round(float(msg.response_lag_s), 2),
            "yaw_rate": round(float(msg.yaw_rate), 3),
            "speed_mps": round(float(msg.speed_mps), 2),
            "feedback_rate_hz": round(float(msg.feedback_rate_hz), 1),
        }
        self.health_seq += 1

    def set_fault(self, fault: str, timeout_s: float = 2.0) -> tuple[bool, str]:
        if not self.fault_client.wait_for_service(timeout_sec=timeout_s):
            return False, "Service /fault/set is not available"
        request = SetFault.Request()
        request.fault_type = fault
        future = self.fault_client.call_async(request)
        event = threading.Event()
        future.add_done_callback(lambda _: event.set())
        if not event.wait(timeout_s):
            return False, "Timed out waiting for /fault/set"
        result = future.result()
        return bool(result.ok), str(result.message)

    def get_topic_info(self, topic: str) -> dict[str, Any]:
        names_and_types = dict(self.get_topic_names_and_types())
        return {
            "topic": topic,
            "types": names_and_types.get(topic, []),
            "publishers": len(self.get_publishers_info_by_topic(topic)),
            "subscribers": len(self.get_subscriptions_info_by_topic(topic)),
        }

    def get_node_names(self) -> list[str]:
        return sorted(name for name, _ns in self.get_node_names_and_namespaces())


app = FastAPI(title="RAISE MCP HTTP shim (read-only)")
# The blackbox-whisper frontend runs on a different origin (Vite dev :8080),
# so the browser needs CORS to fetch /diagnose and /tools/* cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
_bridge: MinimalROSBridge | None = None
_tools: AgentTools | None = None


class FaultBody(BaseModel):
    fault: str


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "bridge_ready": _bridge is not None}


@app.get("/tools/get_steering_health")
async def get_steering_health() -> dict[str, Any]:
    return await _tools.get_steering_health()


@app.get("/tools/get_steering_stats")
async def get_steering_stats() -> dict[str, Any]:
    return await _tools.get_steering_stats()


@app.get("/tools/get_topic_info")
async def get_topic_info(topic: str) -> dict[str, Any]:
    return await _tools.get_topic_info(topic)


@app.get("/tools/get_node_list")
async def get_node_list() -> dict[str, Any]:
    return await _tools.get_node_list()


@app.post("/tools/set_fault")
async def set_fault(body: FaultBody) -> dict[str, Any]:
    import asyncio

    ok, message = await asyncio.to_thread(_bridge.set_fault, body.fault)
    return {"ok": ok, "message": message, "fault": body.fault}


@app.post("/tools/restart_steering_unit")
async def restart_steering_unit() -> dict[str, Any]:
    return await _tools.restart_steering_unit()


@app.get("/diagnose")
async def diagnose() -> dict[str, Any]:
    """Live PLM diagnosis: read /steering/health, correlate against the PLM
    requirements, return a cited root cause + recommended action.

    Same output contract the @cursor/sdk agent will later produce, so the web
    panel is unaffected when the LLM path replaces this rule-based one.
    """
    health = await _tools.get_steering_health()
    return plm_diagnose(health)


@app.get("/", response_class=HTMLResponse)
async def panel() -> str:
    """Self-contained PLM assistant web panel (no build step, no Node)."""
    return _PANEL_HTML


_PANEL_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PLM Assistant — AV-1 Steering</title>
<style>
  :root{
    --bg:#0b0d12; --card:#12151c; --muted:#1a1e27; --border:#232838;
    --fg:#e6e9ef; --dim:#8b93a7; --accent:#5b8cff; --accent-fg:#fff;
    --ok:#2fbf71; --ok-soft:#0f2a1e; --warn:#f5a524; --anomaly:#ff5c72;
    --warn-soft:#2a1f0d; --anomaly-soft:#2a1017;
  }
  *{box-sizing:border-box} body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
    background:var(--bg);color:var(--fg);font-size:14px;line-height:1.5}
  .wrap{max-width:1100px;margin:0 auto;padding:24px}
  header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}
  .brand{display:flex;align-items:center;gap:12px}
  .logo{width:38px;height:38px;border-radius:10px;background:var(--accent);display:grid;place-items:center;
    font-weight:800;color:var(--accent-fg)}
  h1{font-size:16px;margin:0;font-weight:700} .sub{color:var(--dim);font-size:12px;margin-top:2px}
  .pill{display:inline-flex;align-items:center;gap:7px;border-radius:999px;padding:5px 11px;font-size:12px;
    font-weight:600;border:1px solid var(--border)}
  .dot{width:8px;height:8px;border-radius:50%}
  .grid{display:grid;grid-template-columns:1.3fr 1fr;gap:16px} @media(max-width:820px){.grid{grid-template-columns:1fr}}
  .card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px}
  .card h2{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);margin:0 0 12px}
  .promptbar{display:flex;gap:10px;margin-bottom:16px}
  .promptbar input{flex:1;background:var(--muted);border:1px solid var(--border);border-radius:10px;
    color:var(--fg);padding:12px 14px;font-size:14px}
  button{background:var(--accent);color:var(--accent-fg);border:0;border-radius:10px;padding:12px 18px;
    font-weight:600;cursor:pointer;font-size:14px} button.ghost{background:var(--muted);color:var(--fg);border:1px solid var(--border)}
  button:disabled{opacity:.5;cursor:default}
  .diag-summary{font-size:15px;font-weight:600;line-height:1.45;margin:2px 0 14px}
  .reqcard{border:1px solid var(--border);background:var(--muted);border-radius:12px;padding:14px;margin-bottom:14px}
  .reqid{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:700;color:var(--accent);font-size:15px}
  .reqtitle{font-weight:600;margin-top:4px} .reqbody{color:var(--dim);font-size:13px;margin-top:8px}
  .kv{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}
  .metric{background:var(--muted);border:1px solid var(--border);border-radius:10px;padding:10px 12px}
  .metric .l{color:var(--dim);font-size:12px} .metric .v{font-weight:700;margin-top:2px;font-variant-numeric:tabular-nums}
  .row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:10px}
  .tag{font-size:11px;font-weight:600;border-radius:8px;padding:3px 9px;border:1px solid var(--border)}
  .tag.ok{color:var(--ok);background:var(--ok-soft);border-color:#1c5138}
  .tag.fail{color:var(--anomaly);background:var(--anomaly-soft);border-color:#5a2230}
  .fixbox{border:1px solid #26406e;background:#111a2e;border-radius:12px;padding:13px;margin-top:12px}
  .fixbox .l{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--accent);font-weight:700}
  .fixbox .t{margin-top:5px;font-weight:600}
  .controls{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
  .controls button{padding:8px 12px;font-size:12.5px}
  .live .metric .v{transition:color .2s}
  .muted{color:var(--dim);font-size:12.5px}
  .empty{color:var(--dim);padding:8px 0}
</style></head>
<body><div class="wrap">
  <header>
    <div class="brand">
      <div class="logo">AV</div>
      <div><h1>PLM Assistant — AV-1 Steering</h1>
      <div class="sub">Live ROS2 diagnostics correlated against product requirements</div></div>
    </div>
    <span class="pill" id="conn"><span class="dot" style="background:var(--dim)"></span>connecting…</span>
  </header>

  <div class="promptbar">
    <input id="q" value="the steering validation test is failing, diagnose it" />
    <button id="diagnose">Diagnose</button>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Diagnosis</h2>
      <div id="diag"><div class="empty">Click <b>Diagnose</b> to run a live PLM diagnosis against the ROS2 steering graph.</div></div>
    </div>

    <div class="card live">
      <h2>Live steering telemetry</h2>
      <div class="kv" id="telemetry">
        <div class="metric"><div class="l">Status</div><div class="v" id="m-status">—</div></div>
        <div class="metric"><div class="l">Symptom</div><div class="v" id="m-symptom">—</div></div>
        <div class="metric"><div class="l">Commanded</div><div class="v" id="m-cmd">—</div></div>
        <div class="metric"><div class="l">Measured</div><div class="v" id="m-act">—</div></div>
        <div class="metric"><div class="l">Mean deviation</div><div class="v" id="m-err">—</div></div>
        <div class="metric"><div class="l">Feedback rate</div><div class="v" id="m-hz">—</div></div>
      </div>
      <div class="controls">
        <span class="muted">Demo controls:</span>
        <button class="ghost" onclick="setFault('steer_bias')">Inject bias</button>
        <button class="ghost" onclick="setFault('steer_lag')">Inject lag</button>
        <button class="ghost" onclick="setFault('steer_reversed')">Inject reversed</button>
        <button class="ghost" onclick="restart()">Restart EPS (fix)</button>
      </div>
    </div>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);
async function poll(){
  try{
    const h = await (await fetch('/tools/get_steering_health')).json();
    $('conn').innerHTML = '<span class="dot" style="background:var(--ok)"></span>ROS2 live';
    const ok = h.status === 'OK';
    $('m-status').textContent = h.status; $('m-status').style.color = ok ? 'var(--ok)' : 'var(--anomaly)';
    $('m-symptom').textContent = h.symptom;
    $('m-cmd').textContent = (h.cmd_angle>=0?'+':'')+h.cmd_angle.toFixed(3)+' rad';
    $('m-act').textContent = (h.actual_angle>=0?'+':'')+h.actual_angle.toFixed(3)+' rad';
    $('m-err').textContent = (h.angle_error_mean>=0?'+':'')+h.angle_error_mean.toFixed(3)+' rad';
    $('m-err').style.color = Math.abs(h.angle_error_mean) >= 0.02 ? 'var(--anomaly)' : 'var(--fg)';
    $('m-hz').textContent = h.feedback_rate_hz.toFixed(1)+' Hz';
  }catch(e){ $('conn').innerHTML = '<span class="dot" style="background:var(--anomaly)"></span>sim offline'; }
}
async function diagnose(){
  $('diagnose').disabled = true;
  $('diag').innerHTML = '<div class="empty">Querying live ROS2 graph and PLM…</div>';
  try{
    const d = await (await fetch('/diagnose')).json();
    const cls = d.test_status === 'PASS' ? 'ok':'fail';
    const claims = d.claims.map(c=>`<div class="metric"><div class="l">${c.label}</div><div class="v">${c.value}</div></div>`).join('');
    $('diag').innerHTML = `
      <div class="row"><span class="tag ${cls}">TEST ${d.test_status}</span>
        <span class="tag ${cls}">${d.status}</span><span class="muted">${d.symptom}</span></div>
      <div class="diag-summary" style="margin-top:12px">${d.summary}</div>
      <div class="reqcard">
        <div class="reqid">${d.requirement_id}${d.known_issue?` · ${d.known_issue}`:''}</div>
        <div class="reqtitle">${d.title}</div>
        <div class="reqbody">${d.root_cause}</div>
      </div>
      <div class="kv">${claims}</div>
      <div class="fixbox"><div class="l">Recommended action</div><div class="t">${d.action}</div></div>`;
  }catch(e){ $('diag').innerHTML = '<div class="empty">Diagnosis failed — is the sim container up?</div>'; }
  $('diagnose').disabled = false;
}
async function setFault(f){ await fetch('/tools/set_fault',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({fault:f})}); setTimeout(poll,300); }
async function restart(){ await fetch('/tools/restart_steering_unit',{method:'POST'}); setTimeout(poll,300); }
$('diagnose').onclick = diagnose;
poll(); setInterval(poll, 1000);
</script>
</body></html>"""


def main() -> None:
    global _bridge, _tools
    rclpy.init(args=None)
    _bridge = MinimalROSBridge()
    _tools = AgentTools(_bridge)
    spin = threading.Thread(target=rclpy.spin, args=(_bridge,), daemon=True)
    spin.start()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
