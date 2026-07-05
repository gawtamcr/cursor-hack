from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Awaitable, Callable

from openai import AsyncOpenAI

from tools import OPENAI_TOOL_DEFINITIONS, AgentTools


SYSTEM_PROMPT = """You are the AI troubleshooting agent for the RAISE self-driving car demo.
The car is a ROS 2 vehicle with electric power steering. Steering intent is published on
/control/ackermann_cmd and the measured rack angle comes back on /vehicle/steering/feedback;
an IMU (/vehicle/imu) and GPS (/vehicle/gps) provide independent motion evidence.
Known steering symptom classes: pulls to one side, oscillation/wobble, sluggish or delayed
response, reversed response, unresponsive steering, and loss of feedback telemetry.
The steering monitor reports symptoms only; do not assume root cause without checking tools.

When an incident opens:
1. Inspect the latest health and relevant ROS graph data with tools.
2. Diagnose in concise operator-friendly language.
3. Provide numbered steps the human can perform.
4. When asked to verify, re-run tools and either close confidently or give the next best check.

Keep responses short enough for a live demo. Include the evidence behind the diagnosis.
"""


Emit = Callable[[dict[str, Any]], Awaitable[None]]


class TroubleshootingAgent:
    # Hard stop for a model that keeps chaining tool calls; after this many
    # rounds it must answer from the evidence it already has.
    MAX_TOOL_ITERATIONS = 12

    def __init__(self, tools: AgentTools, emit: Emit) -> None:
        self.tools = tools
        self.emit = emit
        # start_incident/operator_message/check_again can fire concurrently
        # (health edge vs. operator typing); interleaved appends to
        # self.messages produce tool results detached from their calls.
        self.lock = asyncio.Lock()
        api_key = os.getenv("NVIDIA_API_KEY")
        self.client = (
            AsyncOpenAI(
                base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
                api_key=api_key,
            )
            if api_key
            else None
        )
        self.model = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
        self.messages: list[dict[str, Any]] = []

    async def start_incident(self, incident_id: int, symptom: str) -> None:
        async with self.lock:
            self.messages = [
                {
                    "role": "user",
                    "content": (
                        f"Incident {incident_id} opened. The current camera symptom is '{symptom}'. "
                        "Diagnose the issue and give the operator step-by-step checks/fixes."
                    ),
                }
            ]
            await self._run(incident_id)

    async def operator_message(self, incident_id: int, text: str) -> None:
        async with self.lock:
            self.messages.append({"role": "user", "content": text})
            await self._run(incident_id)

    async def check_again(self, incident_id: int) -> bool:
        async with self.lock:
            self.messages.append(
                {
                    "role": "user",
                    "content": (
                        "The operator clicked Done, check again. Re-run tools, verify whether the camera is healthy, "
                        "and explicitly say if the incident is resolved."
                    ),
                }
            )
            await self._run(incident_id)
        health = await self.tools.get_steering_health()
        return health.get("status") == "OK"

    async def _run(self, incident_id: int) -> None:
        if self.client is None:
            await self._fallback_response(incident_id)
            return

        try:
            await self._run_model_loop(incident_id)
        except Exception as exc:  # a rate-limited/down API must not kill the incident flow
            self._sanitize_conversation()
            await self.emit(
                {
                    "type": "agent_msg",
                    "incident_id": incident_id,
                    "role": "assistant",
                    "text": f"(model backend unavailable: {exc}; using local fallback diagnosis)",
                    "done": False,
                }
            )
            await self._fallback_response(incident_id)

    def _sanitize_conversation(self) -> None:
        # A failed run can leave a trailing assistant tool_calls message with
        # missing tool results; the next API call would reject that sequence.
        while self.messages:
            last = self.messages[-1]
            if last["role"] == "user" or (last["role"] == "assistant" and not last.get("tool_calls")):
                break
            self.messages.pop()

    async def _run_model_loop(self, incident_id: int) -> None:
        for _ in range(self.MAX_TOOL_ITERATIONS):
            assistant_message = await self._stream_completion(incident_id)
            self.messages.append(assistant_message)

            tool_calls = assistant_message.get("tool_calls", [])
            if not tool_calls:
                return

            for tool_call in tool_calls:
                function = tool_call.get("function", {})
                name = str(function.get("name", ""))
                arguments = self._parse_arguments(function.get("arguments", "{}"))
                await self.emit(
                    {
                        "type": "tool_call",
                        "incident_id": incident_id,
                        "name": name,
                        "arguments": arguments,
                    }
                )
                result = await self.tools.call(name, arguments)
                await self.emit(
                    {
                        "type": "tool_result",
                        "incident_id": incident_id,
                        "name": name,
                        "result": result,
                    }
                )
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result),
                    }
                )

        self.messages.append(
            {
                "role": "user",
                "content": (
                    "Tool budget exhausted. Summarize your diagnosis and the operator's next steps now, "
                    "using only the evidence already gathered."
                ),
            }
        )
        self.messages.append(await self._stream_completion(incident_id, allow_tools=False))

    async def _stream_completion(self, incident_id: int, allow_tools: bool = True) -> dict[str, Any]:
        tool_kwargs: dict[str, Any] = (
            {"tools": OPENAI_TOOL_DEFINITIONS, "tool_choice": "auto"} if allow_tools else {}
        )
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *self.messages],
            temperature=float(os.getenv("NVIDIA_TEMPERATURE", "1")),
            top_p=float(os.getenv("NVIDIA_TOP_P", "0.95")),
            max_tokens=int(os.getenv("NVIDIA_MAX_TOKENS", "16384")),
            **tool_kwargs,
            extra_body={
                "chat_template_kwargs": {"enable_thinking": True},
                "reasoning_budget": int(os.getenv("NVIDIA_REASONING_BUDGET", "16384")),
            },
            stream=True,
        )

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_call_chunks: dict[int, dict[str, Any]] = {}

        async for chunk in stream:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                reasoning_parts.append(reasoning)
                await self.emit(
                    {"type": "agent_thinking", "incident_id": incident_id, "text": reasoning}
                )

            if delta.content:
                content_parts.append(delta.content)
                await self.emit(
                    {"type": "agent_delta", "incident_id": incident_id, "text": delta.content}
                )

            for tool_call in delta.tool_calls or []:
                index = int(tool_call.index)
                current = tool_call_chunks.setdefault(
                    index,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if tool_call.id:
                    current["id"] = tool_call.id
                if tool_call.function:
                    if tool_call.function.name:
                        current["function"]["name"] += tool_call.function.name
                    if tool_call.function.arguments:
                        current["function"]["arguments"] += tool_call.function.arguments

        content = "".join(content_parts)
        tool_calls = [tool_call_chunks[index] for index in sorted(tool_call_chunks)]
        if content:
            await self.emit(
                {
                    "type": "agent_msg",
                    "incident_id": incident_id,
                    "role": "assistant",
                    "text": content,
                    "done": not tool_calls,
                }
            )

        message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message

    def _parse_arguments(self, arguments: Any) -> dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        try:
            parsed = json.loads(str(arguments or "{}"))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def _fallback_response(self, incident_id: int) -> None:
        health = await self.tools.get_steering_health()
        symptom = health.get("symptom", "unknown")
        diagnosis = {
            "steering_pull": "The measured rack angle carries a constant offset versus the command - the car pulls to one side. Typical causes: miscalibrated steering-angle sensor or bent linkage after an impact.",
            "oscillation": "The rack jitters around the commanded angle - the car wobbles in the lane. Typical causes: noisy angle sensor, loose linkage, or an unstable EPS gain.",
            "sluggish_response": "The rack tracks the command but with a significant delay - cornering becomes sloppy. Typical causes: degraded EPS motor or latency on the control bus.",
            "reversed_response": "The rack moves opposite to the command. Typical causes: swapped motor phases or inverted sensor calibration after service.",
            "unresponsive": "Steering commands change but the rack does not move. Typical causes: seized actuator, dead EPS motor, or a tripped ECU.",
            "no_feedback": "No steering-angle telemetry is arriving. Check the feedback publisher, wiring harness, and ECU power.",
        }.get(symptom, "Steering diagnostics are degraded; inspect the latest health metrics and ROS graph.")

        steps = (
            f"{diagnosis}\n\n"
            "1. Confirm /vehicle/steering/feedback is being published and the EPS/chassis nodes are alive.\n"
            "2. Inspect the steering hardware condition that matches the symptom.\n"
            "3. Power-cycle the steering ECU, then click Done, check again.\n\n"
            f"Evidence: status={health.get('status')} symptom={symptom} "
            f"cmd_angle={health.get('cmd_angle')} actual_angle={health.get('actual_angle')} "
            f"error_mean={health.get('angle_error_mean')} error_stddev={health.get('angle_error_stddev')} "
            f"corr={health.get('cmd_actual_corr')} lag_s={health.get('response_lag_s')} "
            f"yaw_rate={health.get('yaw_rate')} speed_mps={health.get('speed_mps')}"
        )
        await self.emit(
            {
                "type": "agent_msg",
                "incident_id": incident_id,
                "role": "assistant",
                "text": steps,
                "done": True,
            }
        )
