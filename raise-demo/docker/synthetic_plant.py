#!/usr/bin/env python3
"""Synthetic vehicle plant — a headless stand-in for the Webots simulation.

Webots won't boot reliably in this WSL2 container (webots_ros2's WebotsLauncher
hangs on WSL path detection), so this node substitutes for the *physical plant*
only. Everything else in the demo stays real and untouched: the fault injector
(`eps_controller`), the steering monitor, and the classifier all run exactly as
authored under ros_ws/src/**.

What the real system did that this reproduces:
  - lane_follower publishes a steering command on /control/ackermann_cmd, but it
    only does so on camera frames. With no camera there is no command and the
    whole pipeline is idle. So this node ALSO drives a gentle steering command
    sweep on /control/ackermann_cmd, mimicking lane-following excitation and
    keeping cmd_stddev above the monitor's MIN_CMD_STD (0.002 rad).
  - The Webots vehicle_driver ("chassis_interface") subscribed /cmd_ackermann
    (the fault-injected command) and published the measured rack angle on
    /vehicle/steering/feedback at 20 Hz, plus /vehicle/imu and /vehicle/gps/speed.
    This node reproduces that: it reads /cmd_ackermann and emits a faithful,
    slightly-lagged actual angle (first-order rack dynamics) so a HEALTHY system
    reads actual ~= cmd, while any injected fault (bias/reversed/stuck/noise/lag)
    shows up in /steering/health exactly as it would with Webots.

This file lives under docker/, NOT ros_ws/src/**, and is mounted into the
container at runtime — the working sim packages are never modified.
"""
from __future__ import annotations

import math
import time

import rclpy
from ackermann_msgs.msg import AckermannDrive, AckermannDriveStamped
from geometry_msgs.msg import Quaternion
from rclpy.node import Node
from sensor_msgs.msg import Imu
from std_msgs.msg import Float32


# 20 Hz feedback, matching the real vehicle_driver's FEEDBACK_PERIOD_S = 0.05.
FEEDBACK_HZ = 20.0
# First-order rack time constant: the actual angle chases the commanded angle.
# Small enough that a healthy system reads actual ~= cmd (no false lag symptom).
RACK_TAU_S = 0.06
# Gentle steering excitation so cmd_stddev stays > MIN_CMD_STD (0.002 rad).
# Mirrors lane_follower's diagnostic sweep amplitude/rate.
CMD_AMPLITUDE_RAD = 0.05
CMD_HZ = 0.25
CRUISE_SPEED_MPS = 30.0 / 3.6  # 30 km/h, matching lane_follower CRUISE_SPEED_KMH


def _yaw_to_quaternion(yaw: float) -> Quaternion:
    q = Quaternion()
    q.z = math.sin(yaw / 2.0)
    q.w = math.cos(yaw / 2.0)
    return q


class SyntheticPlant(Node):
    def __init__(self) -> None:
        super().__init__("chassis_interface")  # same node name the agent sees

        # The fault-injected command coming out of eps_controller.
        self.create_subscription(
            AckermannDrive, "/cmd_ackermann", self._on_faulted_cmd, 10
        )

        # Stand in for lane_follower's command source (no camera in this plant).
        self.cmd_pub = self.create_publisher(AckermannDrive, "/control/ackermann_cmd", 1)
        # Stand in for the Webots vehicle_driver + IMU/GPS plugins.
        self.feedback_pub = self.create_publisher(
            AckermannDriveStamped, "/vehicle/steering/feedback", 10
        )
        self.imu_pub = self.create_publisher(Imu, "/vehicle/imu", 10)
        self.gps_speed_pub = self.create_publisher(Float32, "/vehicle/gps/speed", 10)

        self.faulted_angle = 0.0    # latest /cmd_ackermann steering angle
        self.actual_angle = 0.0     # rack state (first-order chase of faulted_angle)
        self.yaw = 0.0
        self.t0 = time.monotonic()
        self.last_step = self.t0

        self.create_timer(1.0 / FEEDBACK_HZ, self._step)
        self.get_logger().info(
            "Synthetic plant up: driving /control/ackermann_cmd sweep, "
            "publishing /vehicle/steering/feedback + IMU + GPS at 20 Hz"
        )

    def _on_faulted_cmd(self, msg: AckermannDrive) -> None:
        self.faulted_angle = float(msg.steering_angle)

    def _step(self) -> None:
        now = time.monotonic()
        dt = now - self.last_step
        self.last_step = now
        t = now - self.t0

        # 1) Emit the lane-following-style command (pre-fault intent).
        cmd = AckermannDrive()
        cmd.steering_angle = CMD_AMPLITUDE_RAD * math.sin(2.0 * math.pi * CMD_HZ * t)
        cmd.speed = CRUISE_SPEED_MPS
        self.cmd_pub.publish(cmd)

        # 2) Rack dynamics: actual angle chases the FAULTED command (first-order).
        alpha = 1.0 - math.exp(-dt / RACK_TAU_S) if dt > 0 else 1.0
        self.actual_angle += alpha * (self.faulted_angle - self.actual_angle)

        # 3) Publish measured rack angle (what the monitor compares against cmd).
        fb = AckermannDriveStamped()
        fb.header.stamp = self.get_clock().now().to_msg()
        fb.drive.steering_angle = self.actual_angle
        fb.drive.speed = CRUISE_SPEED_MPS
        self.feedback_pub.publish(fb)

        # 4) IMU yaw rate ~ v/L * tan(steer); a small bicycle-model proxy so the
        #    /steering/health yaw_rate field is populated and moves with the fault.
        wheelbase = 2.875  # Tesla Model 3
        yaw_rate = CRUISE_SPEED_MPS / wheelbase * math.tan(self.actual_angle)
        self.yaw += yaw_rate * dt
        imu = Imu()
        imu.header.stamp = fb.header.stamp
        imu.orientation = _yaw_to_quaternion(self.yaw)
        imu.angular_velocity.z = yaw_rate
        self.imu_pub.publish(imu)

        # 5) GPS speed (the monitor subscribes /vehicle/gps/speed as Float32).
        speed = Float32()
        speed.data = CRUISE_SPEED_MPS
        self.gps_speed_pub.publish(speed)


def main() -> None:
    rclpy.init()
    node = SyntheticPlant()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
