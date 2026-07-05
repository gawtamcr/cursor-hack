#!/usr/bin/env python3
"""ROS2 steering-diagnostics MCP server (stdio) for the RAISE PLM demo.

Runs on the HOST (WSL2, no ROS needed). It exposes the live ROS2 steering
diagnostics to Cursor by forwarding each tool call over HTTP to the read-only
shim published by the sim container on localhost:8000 (docker/mcp_http.py).

Boundary: Cursor <-- stdio --> this server <-- HTTP --> container shim <-- rclpy
--> live ROS2 graph. No rclpy or docker exec on the host.

Registered in .cursor/mcp.json so tools are callable from Cursor's Agent chat
(Path A gate) and later from the @cursor/sdk agent.
"""
from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

# The sim container publishes the shim here (docker-compose maps -p 8000:8000).
SHIM_URL = os.environ.get("RAISE_SHIM_URL", "http://localhost:8000")
TIMEOUT_S = 10.0

mcp = FastMCP("ros-steering")


async def _get(path: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        try:
            r = await client.get(f"{SHIM_URL}{path}", params=params)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            return {"error": f"cannot reach ROS shim at {SHIM_URL}{path}: {e}. "
                             "Is the sim container up (docker compose up -d)?"}


async def _post(path: str, json: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        try:
            r = await client.post(f"{SHIM_URL}{path}", json=json)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            return {"error": f"cannot reach ROS shim at {SHIM_URL}{path}: {e}. "
                             "Is the sim container up (docker compose up -d)?"}


@mcp.tool()
async def get_steering_health() -> dict:
    """Latest steering health snapshot from the live ROS2 graph (/steering/health).

    Returns status (OK/DEGRADED/FAILED), symptom (e.g. steering_pull,
    sluggish_response, reversed_response, unresponsive, oscillation,
    no_feedback), and the numbers behind it: cmd_angle vs actual_angle,
    angle_error_mean, cmd_actual_corr, response_lag_s, yaw_rate, speed_mps,
    feedback_rate_hz. Start a steering diagnosis here.
    """
    return await _get("/tools/get_steering_health")


@mcp.tool()
async def get_steering_stats() -> dict:
    """Wait for two FRESH /steering/health samples, then return the stats.

    Use this to verify current behaviour against live evidence (e.g. to confirm
    a fix took effect) rather than a possibly stale snapshot. `fresh: true`
    means the numbers are from samples published after the call.
    """
    return await _get("/tools/get_steering_stats")


@mcp.tool()
async def get_topic_info(topic: str) -> dict:
    """ROS2 topic type and publisher/subscriber counts for `topic`.

    Useful to confirm the signal chain is intact, e.g.
    /vehicle/steering/feedback (measured rack angle),
    /control/ackermann_cmd (commanded intent), /cmd_ackermann (post-fault).
    """
    return await _get("/tools/get_topic_info", params={"topic": topic})


@mcp.tool()
async def get_node_list() -> dict:
    """List the ROS2 nodes currently visible on the live graph."""
    return await _get("/tools/get_node_list")


@mcp.tool()
async def set_fault(fault: str) -> dict:
    """Inject a steering fault into the live actuator via the /fault/set service.

    Valid values: none, steer_bias, steer_reversed, steer_stuck, steer_noise,
    steer_lag. Mainly for demo setup / reproduction; `none` clears the fault.
    """
    return await _post("/tools/set_fault", json={"fault": fault})


@mcp.tool()
async def restart_steering_unit() -> dict:
    """Power-cycle the steering ECU (clears the active fault). Use only after
    telling the operator; then verify recovery with get_steering_stats.
    """
    return await _post("/tools/restart_steering_unit")


if __name__ == "__main__":
    mcp.run()
