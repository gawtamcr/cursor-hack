from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from cv_bridge import CvBridge
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import Bool, Empty

from agent import TroubleshootingAgent
from raise_interfaces.msg import SteeringHealth
from raise_interfaces.srv import SetFault
from tools import AgentTools


STATIC_DIR = Path(__file__).parent / "static"
# When no frame has arrived for this long, the MJPEG stream shows a placeholder
# instead of silently re-serving the stale frame (which looks like "freeze").
STALE_FRAME_S = 1.5


def _no_signal_jpeg() -> bytes:
    frame = np.full((360, 640, 3), 16, dtype=np.uint8)
    cv2.putText(frame, "NO SIGNAL", (170, 190), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (60, 60, 230), 3, cv2.LINE_AA)
    _, encoded = cv2.imencode(".jpg", frame)
    return encoded.tobytes()


NO_SIGNAL_JPEG = _no_signal_jpeg()


class ROSBridge(Node):
    def __init__(self, on_health: Callable[[dict[str, Any]], None], on_car_state: Callable[[dict[str, Any]], None]) -> None:
        super().__init__("agent_backend_bridge")
        self.bridge = CvBridge()
        self.latest_health: dict[str, Any] | None = None
        self.health_seq = 0
        self.latest_jpeg: bytes | None = None
        self.last_frame_monotonic: float | None = None
        self.latest_car_driving = False
        self.car_state_seen = False
        self.on_health = on_health
        self.on_car_state = on_car_state

        qos_camera = qos_profile_sensor_data
        qos_camera.reliability = QoSReliabilityPolicy.RELIABLE
        self.create_subscription(SteeringHealth, "/steering/health", self._health_cb, 10)
        self.create_subscription(Image, "/vehicle/camera/image_color", self._image_cb, qos_camera)
        self.create_subscription(Bool, "/car/driving", self._car_state_cb, 10)
        self.fault_client = self.create_client(SetFault, "/fault/set")
        self.reset_publisher = self.create_publisher(Empty, "/vehicle/reset", 1)

    def reset_car(self) -> None:
        self.reset_publisher.publish(Empty())

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
        self.on_health(dict(self.latest_health))

    def _image_cb(self, msg: Image) -> None:
        # The Webots camera publishes bgra8; cv_bridge drops the alpha channel.
        frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if ok:
            self.latest_jpeg = encoded.tobytes()
            self.last_frame_monotonic = time.monotonic()

    def _car_state_cb(self, msg: Bool) -> None:
        driving = bool(msg.data)
        changed = driving != self.latest_car_driving or not self.car_state_seen
        self.latest_car_driving = driving
        self.car_state_seen = True
        if changed:
            self.on_car_state({"type": "car_state", "driving": driving})

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
        return sorted(name for name, _namespace in self.get_node_names_and_namespaces())


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.connections.discard(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        stale = []
        for websocket in list(self.connections):
            try:
                await websocket.send_text(json.dumps(message))
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self.disconnect(websocket)


class IncidentCoordinator:
    # Fallback so an incident cannot stay open forever if the operator never
    # clicks "Done, check again" after health recovers.
    AUTO_RESOLVE_AFTER_S = 30.0
    # Health arrives every 0.5 s; requiring consecutive bad samples keeps a
    # single sim hiccup from opening an incident and firing an LLM call.
    OPEN_AFTER_BAD_SAMPLES = 3

    def __init__(self) -> None:
        self.current_id = 0
        self.open_incident_id: int | None = None
        self.last_symptom = "unknown"
        self.ok_since: float | None = None
        self.bad_streak = 0
        self.incident_symptom: str | None = None
        self.agent: TroubleshootingAgent | None = None

    def attach_agent(self, agent: TroubleshootingAgent) -> None:
        self.agent = agent

    async def handle_health(self, health: dict[str, Any]) -> None:
        status = health["status"]
        symptom = health["symptom"]
        await manager.broadcast(health)

        self.bad_streak = self.bad_streak + 1 if status != "OK" else 0
        if self.open_incident_id is None and self.bad_streak >= self.OPEN_AFTER_BAD_SAMPLES:
            self.current_id += 1
            self.open_incident_id = self.current_id
            self.ok_since = None
            self.incident_symptom = symptom
            await manager.broadcast(
                {
                    "type": "incident",
                    "incident_id": self.open_incident_id,
                    "state": "open",
                    "symptom": symptom,
                }
            )
            if self.agent:
                asyncio.create_task(self.agent.start_incident(self.open_incident_id, symptom))

        # Recovered health does not close the incident by itself: the agent is
        # supposed to verify the fix via "Done, check again". Only auto-resolve
        # after health has stayed OK long enough that verification clearly
        # never happened.
        if self.open_incident_id is not None:
            if status == "OK":
                now = time.monotonic()
                if self.ok_since is None:
                    self.ok_since = now
                    await manager.broadcast(
                        {
                            "type": "incident",
                            "incident_id": self.open_incident_id,
                            "state": "awaiting_verification",
                            "symptom": self.last_symptom,
                        }
                    )
                elif now - self.ok_since >= self.AUTO_RESOLVE_AFTER_S:
                    await self.resolve_open_incident()
            else:
                self.ok_since = None
                # A different fault injected mid-incident changes the symptom;
                # the agent must re-assess rather than keep chasing the old one.
                if symptom != self.incident_symptom:
                    self.incident_symptom = symptom
                    await manager.broadcast(
                        {
                            "type": "incident",
                            "incident_id": self.open_incident_id,
                            "state": "updated",
                            "symptom": symptom,
                        }
                    )
                    if self.agent:
                        asyncio.create_task(
                            self.agent.operator_message(
                                self.open_incident_id,
                                f"Automated update: the steering symptom changed to '{symptom}'. "
                                "Re-assess the diagnosis with tools before giving further steps.",
                            )
                        )

        self.last_symptom = symptom

    async def operator_message(self, text: str) -> None:
        if self.open_incident_id is None:
            await manager.broadcast(
                {
                    "type": "agent_msg",
                    "incident_id": 0,
                    "role": "assistant",
                    "text": "No active incident. Inject a fault or wait for steering health to degrade.",
                    "done": True,
                }
            )
            return
        if self.agent:
            await self.agent.operator_message(self.open_incident_id, text)

    async def check_again(self) -> None:
        if self.open_incident_id is None:
            return
        resolved = False
        if self.agent:
            resolved = await self.agent.check_again(self.open_incident_id)
        if resolved:
            await self.resolve_open_incident()

    async def resolve_open_incident(self) -> None:
        if self.open_incident_id is None:
            return
        incident_id = self.open_incident_id
        self.open_incident_id = None
        self.ok_since = None
        self.incident_symptom = None
        await manager.broadcast(
            {
                "type": "incident",
                "incident_id": incident_id,
                "state": "resolved",
                "symptom": self.last_symptom,
            }
        )


app = FastAPI(title="RAISE AI Troubleshooting Agent")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
manager = ConnectionManager()
incidents = IncidentCoordinator()
ros_bridge: ROSBridge | None = None
loop: asyncio.AbstractEventLoop | None = None
spin_thread: threading.Thread | None = None


def _threadsafe(coro: Any) -> None:
    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, loop)


@app.on_event("startup")
async def startup() -> None:
    global loop, ros_bridge, spin_thread
    loop = asyncio.get_running_loop()
    rclpy.init(args=None)
    ros_bridge = ROSBridge(
        on_health=lambda health: _threadsafe(incidents.handle_health(health)),
        on_car_state=lambda state: _threadsafe(manager.broadcast(state)),
    )
    incidents.attach_agent(TroubleshootingAgent(AgentTools(ros_bridge), manager.broadcast))
    spin_thread = threading.Thread(target=rclpy.spin, args=(ros_bridge,), daemon=True)
    spin_thread.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    if ros_bridge:
        ros_bridge.destroy_node()
    if rclpy.ok():
        rclpy.shutdown()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/video")
async def video() -> StreamingResponse:
    async def frames():
        while True:
            jpeg = NO_SIGNAL_JPEG
            if ros_bridge and ros_bridge.latest_jpeg and ros_bridge.last_frame_monotonic is not None:
                if time.monotonic() - ros_bridge.last_frame_monotonic <= STALE_FRAME_S:
                    jpeg = ros_bridge.latest_jpeg
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            await asyncio.sleep(0.07)

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    if ros_bridge and ros_bridge.latest_health:
        await websocket.send_text(json.dumps(ros_bridge.latest_health))
    if ros_bridge:
        await websocket.send_text(json.dumps({"type": "car_state", "driving": ros_bridge.latest_car_driving}))
    try:
        while True:
            raw = await websocket.receive_text()
            message = json.loads(raw)
            msg_type = message.get("type")
            if msg_type == "inject_fault":
                fault = str(message.get("fault", "none"))
                if ros_bridge:
                    # set_fault blocks up to its timeouts; keep the event loop
                    # (MJPEG stream, broadcasts) responsive while it waits.
                    ok, response = await asyncio.to_thread(ros_bridge.set_fault, fault)
                else:
                    ok, response = False, "ROS bridge not ready"
                await manager.broadcast(
                    {"type": "fault_result", "fault": fault, "ok": ok, "message": response}
                )
            elif msg_type == "operator_msg":
                await incidents.operator_message(str(message.get("text", "")))
            elif msg_type == "check_again":
                await incidents.check_again()
            elif msg_type == "reset_car":
                if ros_bridge:
                    ros_bridge.reset_car()
                await manager.broadcast({"type": "car_reset"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
