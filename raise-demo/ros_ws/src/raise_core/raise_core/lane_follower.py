from __future__ import annotations

import math
import time

import cv2
import numpy as np
import rclpy
from ackermann_msgs.msg import AckermannDrive
from rclpy.node import Node
from rclpy.qos import QoSReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import Bool

from raise_interfaces.msg import SteeringHealth


# Raised from the upstream tesla example's 0.0005 so that pixel-level lane
# jitter keeps the commanded-angle variance above the steering monitor's
# detection floor even on straight segments.
CONTROL_COEFFICIENT = 0.001
LANE_TARGET_X = 190

CRUISE_SPEED_KMH = 30.0
DEGRADED_SPEED_KMH = 20.0  # keep limping so the fault stays visible

# While safety-stopped, sweep the wheels gently so the monitor keeps enough
# steering excitation to re-assess the actuator (and the fix, once applied).
DIAG_SWEEP_AMPLITUDE_RAD = 0.03
DIAG_SWEEP_HZ = 0.4


class LaneFollower(Node):
    """Camera lane following with a steering-health safety gate."""

    def __init__(self) -> None:
        super().__init__("lane_follower")
        self.steering_status = "OK"
        self.driving: bool | None = None

        self.cmd_publisher = self.create_publisher(AckermannDrive, "/control/ackermann_cmd", 1)
        self.driving_publisher = self.create_publisher(Bool, "/car/driving", 10)

        qos_camera = qos_profile_sensor_data
        qos_camera.reliability = QoSReliabilityPolicy.RELIABLE
        self.create_subscription(Image, "/vehicle/camera/image_color", self._on_image, qos_camera)
        self.create_subscription(SteeringHealth, "/steering/health", self._on_health, 10)
        # Re-announce the driving state so late-joining subscribers (the web
        # backend) see the current value, not just future transitions.
        self.create_timer(2.0, self._reannounce_driving)

    def _on_health(self, msg: SteeringHealth) -> None:
        self.steering_status = msg.status

    def _on_image(self, msg: Image) -> None:
        command = AckermannDrive()
        command.speed = CRUISE_SPEED_KMH
        command.steering_angle = self._lane_steering(msg)

        if self.steering_status == "FAILED":
            command.speed = 0.0
            command.steering_angle = DIAG_SWEEP_AMPLITUDE_RAD * math.sin(
                2.0 * math.pi * DIAG_SWEEP_HZ * time.monotonic()
            )
        elif self.steering_status == "DEGRADED":
            command.speed = DEGRADED_SPEED_KMH

        self.cmd_publisher.publish(command)
        self._publish_driving(command.speed > 0.1)

    def _lane_steering(self, msg: Image) -> float:
        img = np.frombuffer(msg.data, dtype=np.uint8).reshape((msg.height, msg.width, 4))
        img = img[160:190, :]
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
        img = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
        mask = cv2.inRange(img, np.array([50, 110, 150]), np.array([120, 255, 255]))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            return 0.0
        largest = max(contours, key=cv2.contourArea)
        moments = cv2.moments(largest)
        if moments["m00"] == 0:
            return 0.0
        center_x = int(moments["m10"] / moments["m00"])
        return (center_x - LANE_TARGET_X) * CONTROL_COEFFICIENT

    def _publish_driving(self, driving: bool) -> None:
        if driving == self.driving:
            return
        self.driving = driving
        self._reannounce_driving()

    def _reannounce_driving(self) -> None:
        if self.driving is None:
            return
        msg = Bool()
        msg.data = self.driving
        self.driving_publisher.publish(msg)


def main() -> None:
    rclpy.init()
    node = LaneFollower()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
