from __future__ import annotations

import asyncio
import time
from typing import Any


class AgentTools:
    def __init__(self, ros_bridge: Any) -> None:
        self.ros = ros_bridge

    async def get_steering_health(self) -> dict[str, Any]:
        return self.ros.latest_health or {
            "status": "UNKNOWN",
            "symptom": "no_data",
            "cmd_angle": 0.0,
            "actual_angle": 0.0,
            "angle_error_mean": 0.0,
            "angle_error_stddev": 0.0,
            "cmd_actual_corr": 0.0,
            "response_lag_s": 0.0,
            "yaw_rate": 0.0,
            "speed_mps": 0.0,
            "feedback_rate_hz": 0.0,
        }

    async def get_topic_info(self, topic: str) -> dict[str, Any]:
        return self.ros.get_topic_info(topic)

    async def get_node_list(self) -> dict[str, Any]:
        return {"nodes": self.ros.get_node_names()}

    async def get_steering_stats(self) -> dict[str, Any]:
        # Wait for two health samples published after this call so the agent
        # verifies against fresh evidence, not the snapshot it already saw.
        start_seq = getattr(self.ros, "health_seq", 0)
        deadline = time.monotonic() + 3.0
        while getattr(self.ros, "health_seq", 0) < start_seq + 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.2)

        health = await self.get_steering_health()
        return {
            "fresh": getattr(self.ros, "health_seq", 0) >= start_seq + 2,
            "cmd_angle": health.get("cmd_angle", 0.0),
            "actual_angle": health.get("actual_angle", 0.0),
            "angle_error_mean": health.get("angle_error_mean", 0.0),
            "angle_error_stddev": health.get("angle_error_stddev", 0.0),
            "cmd_actual_corr": health.get("cmd_actual_corr", 0.0),
            "response_lag_s": health.get("response_lag_s", 0.0),
            "yaw_rate": health.get("yaw_rate", 0.0),
            "speed_mps": health.get("speed_mps", 0.0),
            "feedback_rate_hz": health.get("feedback_rate_hz", 0.0),
        }

    async def restart_steering_unit(self) -> dict[str, Any]:
        ok, message = await asyncio.to_thread(self.ros.set_fault, "none")
        return {"ok": ok, "message": message, "action": "steering ECU power-cycle"}

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "get_steering_health":
            return await self.get_steering_health()
        if name == "get_topic_info":
            return await self.get_topic_info(
                str(arguments.get("topic", "/vehicle/steering/feedback"))
            )
        if name == "get_node_list":
            return await self.get_node_list()
        if name == "get_steering_stats":
            return await self.get_steering_stats()
        if name == "restart_steering_unit":
            return await self.restart_steering_unit()
        return {"error": f"unknown tool: {name}"}


TOOL_DEFINITIONS = [
    {
        "name": "get_steering_health",
        "description": "Return the latest /steering/health symptom-level diagnostics.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_topic_info",
        "description": "Return ROS topic type and publisher/subscriber counts.",
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_node_list",
        "description": "List currently visible ROS node names.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_steering_stats",
        "description": "Wait ~1s for fresh steering health samples and return current stats. Use this to verify a fix.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "restart_steering_unit",
        "description": "Power-cycle the steering ECU and re-initialize the actuator (takes ~2s). Use only after telling the operator.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


OPENAI_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }
    for tool in TOOL_DEFINITIONS
]
