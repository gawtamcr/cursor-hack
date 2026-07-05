from __future__ import annotations

import time
from collections import deque

import rclpy
from ackermann_msgs.msg import AckermannDrive, AckermannDriveStamped
from rclpy.node import Node
from sensor_msgs.msg import Imu
from std_msgs.msg import Float32

from raise_interfaces.msg import SteeringHealth

from raise_core.steering_classifier import classify_steering


WINDOW_PAIRS = 80  # 4 s at the 20 Hz feedback rate


class SteeringMonitor(Node):
    """Publish symptom-level steering health by comparing intent with feedback."""

    def __init__(self) -> None:
        super().__init__("steering_monitor")
        self.declare_parameter("cmd_topic", "/control/ackermann_cmd")
        self.declare_parameter("feedback_topic", "/vehicle/steering/feedback")
        self.declare_parameter("health_topic", "/steering/health")

        cmd_topic = self.get_parameter("cmd_topic").get_parameter_value().string_value
        feedback_topic = self.get_parameter("feedback_topic").get_parameter_value().string_value
        health_topic = self.get_parameter("health_topic").get_parameter_value().string_value

        self.latest_cmd: float | None = None
        self.pairs: deque[tuple[float, float]] = deque(maxlen=WINDOW_PAIRS)
        self.feedback_times: deque[float] = deque(maxlen=WINDOW_PAIRS)
        self.pairs_seen = 0
        self.yaw_rate = 0.0
        self.speed_mps = 0.0

        self.create_subscription(AckermannDrive, cmd_topic, self._on_cmd, 10)
        self.create_subscription(AckermannDriveStamped, feedback_topic, self._on_feedback, 10)
        self.create_subscription(Imu, "/vehicle/imu", self._on_imu, 10)
        self.create_subscription(Float32, "/vehicle/gps/speed", self._on_gps_speed, 10)

        self.publisher = self.create_publisher(SteeringHealth, health_topic, 10)
        self.create_timer(0.5, self._publish_health)
        self.get_logger().info(
            f"Monitoring {cmd_topic} vs {feedback_topic}; health on {health_topic}"
        )

    def _on_cmd(self, msg: AckermannDrive) -> None:
        self.latest_cmd = float(msg.steering_angle)

    def _on_feedback(self, msg: AckermannDriveStamped) -> None:
        if self.latest_cmd is None:
            return
        self.pairs.append((self.latest_cmd, float(msg.drive.steering_angle)))
        self.feedback_times.append(time.monotonic())
        self.pairs_seen += 1

    def _on_imu(self, msg: Imu) -> None:
        self.yaw_rate = float(msg.angular_velocity.z)

    def _on_gps_speed(self, msg: Float32) -> None:
        self.speed_mps = float(msg.data)

    def _publish_health(self) -> None:
        # Stay silent until the pipeline has produced its first paired sample;
        # otherwise sim startup (~15 s) would open a spurious incident.
        if self.pairs_seen == 0:
            return

        now = time.monotonic()
        stale_s = now - self.feedback_times[-1] if self.feedback_times else float("inf")

        rate_hz = 0.0
        if len(self.feedback_times) >= 2:
            span = self.feedback_times[-1] - self.feedback_times[0]
            if span > 0:
                rate_hz = (len(self.feedback_times) - 1) / span

        snapshot = classify_steering(
            [pair[0] for pair in self.pairs],
            [pair[1] for pair in self.pairs],
            # Measured wall-clock spacing, not the nominal 20 Hz: the sim can
            # run below real time, and lag is injected in wall-clock seconds.
            dt=1.0 / rate_hz if rate_hz > 0 else 0.05,
            seconds_since_last_feedback=stale_s,
            pairs_seen=self.pairs_seen,
        )

        msg = SteeringHealth()
        msg.status = snapshot.status
        msg.symptom = snapshot.symptom
        msg.cmd_angle = snapshot.cmd_angle
        msg.actual_angle = snapshot.actual_angle
        msg.angle_error_mean = snapshot.angle_error_mean
        msg.angle_error_stddev = snapshot.angle_error_stddev
        msg.cmd_stddev = snapshot.cmd_stddev
        msg.actual_stddev = snapshot.actual_stddev
        msg.cmd_actual_corr = snapshot.cmd_actual_corr
        msg.response_lag_s = snapshot.response_lag_s
        msg.yaw_rate = self.yaw_rate
        msg.speed_mps = self.speed_mps
        msg.feedback_rate_hz = rate_hz
        self.publisher.publish(msg)


def main() -> None:
    rclpy.init()
    node = SteeringMonitor()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
