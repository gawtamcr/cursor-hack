from __future__ import annotations

import time
from collections import deque
from typing import Final

import numpy as np
import rclpy
from ackermann_msgs.msg import AckermannDrive
from rclpy.node import Node

from raise_interfaces.srv import SetFault


VALID_FAULTS: Final[set[str]] = {
    "none",
    "steer_bias",
    "steer_stuck",
    "steer_noise",
    "steer_lag",
    "steer_reversed",
}

BIAS_RAD = 0.06
NOISE_SIGMA_RAD = 0.04
LAG_S = 0.6
LAG_BUFFER_S = 2.0
NOISE_SEED = 2024


class SteeringFaultInjector(Node):
    """Relay steering commands while applying a repeatable, operator-selected fault."""

    def __init__(self) -> None:
        # Deliberately named like a real vehicle component: the agent sees node
        # names via its tools, and "fault_injector" would give the demo away.
        super().__init__("eps_controller")
        self.declare_parameter("input_topic", "/control/ackermann_cmd")
        self.declare_parameter("output_topic", "/cmd_ackermann")

        input_topic = self.get_parameter("input_topic").get_parameter_value().string_value
        output_topic = self.get_parameter("output_topic").get_parameter_value().string_value

        self.fault_type = "none"
        self.stuck_angle: float | None = None
        self.last_output_angle = 0.0
        self.lag_buffer: deque[tuple[float, float]] = deque()
        self.rng = np.random.default_rng(NOISE_SEED)

        self.publisher = self.create_publisher(AckermannDrive, output_topic, 10)
        self.subscription = self.create_subscription(
            AckermannDrive, input_topic, self._on_cmd, 10
        )
        self.service = self.create_service(SetFault, "/fault/set", self._set_fault)
        self.get_logger().info(f"Relaying {input_topic} to {output_topic}; faults via /fault/set")

    def _set_fault(self, request: SetFault.Request, response: SetFault.Response) -> SetFault.Response:
        requested = request.fault_type.strip().lower()
        if requested not in VALID_FAULTS:
            response.ok = False
            response.message = (
                f"Unknown fault '{request.fault_type}'. Valid faults: {sorted(VALID_FAULTS)}"
            )
            return response

        self.fault_type = requested
        self.stuck_angle = self.last_output_angle if requested == "steer_stuck" else None
        self.lag_buffer.clear()
        self.rng = np.random.default_rng(NOISE_SEED)  # repeatable wobble per activation
        response.ok = True
        response.message = f"Fault set to {self.fault_type}"
        self.get_logger().info(response.message)
        return response

    def _on_cmd(self, msg: AckermannDrive) -> None:
        out = AckermannDrive()
        out.speed = msg.speed
        out.steering_angle = self._faulted_angle(float(msg.steering_angle))
        self.last_output_angle = float(out.steering_angle)
        self.publisher.publish(out)

    def _faulted_angle(self, angle: float) -> float:
        if self.fault_type == "steer_bias":
            return angle + BIAS_RAD
        if self.fault_type == "steer_reversed":
            return -angle
        if self.fault_type == "steer_stuck":
            if self.stuck_angle is None:
                self.stuck_angle = self.last_output_angle
            return self.stuck_angle
        if self.fault_type == "steer_noise":
            return angle + float(self.rng.normal(0.0, NOISE_SIGMA_RAD))
        if self.fault_type == "steer_lag":
            now = time.monotonic()
            self.lag_buffer.append((now, angle))
            while self.lag_buffer and self.lag_buffer[0][0] < now - LAG_BUFFER_S:
                self.lag_buffer.popleft()
            delayed = [a for (t, a) in self.lag_buffer if t <= now - LAG_S]
            # Until the buffer spans the delay, keep emitting its oldest entry.
            return delayed[-1] if delayed else self.lag_buffer[0][1]
        return angle


def main() -> None:
    rclpy.init()
    node = SteeringFaultInjector()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
