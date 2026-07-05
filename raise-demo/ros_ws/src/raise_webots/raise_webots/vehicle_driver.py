from __future__ import annotations

import math

import rclpy
from ackermann_msgs.msg import AckermannDrive, AckermannDriveStamped
from std_msgs.msg import Empty


FEEDBACK_PERIOD_S = 0.05  # 20 Hz steering feedback

# Spawn pose from raise_city.wbt; the reset teleports the car back here after
# a crash so a botched fault run never ends the demo.
SPAWN_TRANSLATION = [31.4381, 47.0076, 0.400134]
SPAWN_ROTATION = [0.0, 0.0, 1.0, 3.1415]


class VehicleDriver:
    """webots_ros2 plugin bridging AckermannDrive commands to the vehicle Driver API.

    Publishes the measured steering angle and speed so downstream monitors can
    compare commanded intent with what the rack actually did.
    """

    def init(self, webots_node, properties):
        self.__robot = webots_node.robot
        self.__steps_per_feedback = max(
            1, round(FEEDBACK_PERIOD_S * 1000.0 / self.__robot.getBasicTimeStep())
        )
        self.__step_count = 0

        rclpy.init(args=None)
        # Named like a real vehicle component: the troubleshooting agent lists
        # node names with its tools, so nothing here may hint at a simulation.
        self.__node = rclpy.create_node("chassis_interface")
        self.__node.create_subscription(
            AckermannDrive, "/cmd_ackermann", self.__cmd_ackermann_callback, 1
        )
        self.__node.create_subscription(Empty, "/vehicle/reset", self.__reset_callback, 1)
        self.__feedback_publisher = self.__node.create_publisher(
            AckermannDriveStamped, "/vehicle/steering/feedback", 10
        )

    def __cmd_ackermann_callback(self, message):
        # Driver convention: cruising speed in km/h, steering angle in rad.
        self.__robot.setCruisingSpeed(message.speed)
        self.__robot.setSteeringAngle(message.steering_angle)

    def __reset_callback(self, _message):
        # Driver extends Supervisor (the vehicle has `supervisor TRUE`), so it
        # may teleport itself back onto the road.
        self.__robot.setCruisingSpeed(0.0)
        self.__robot.setSteeringAngle(0.0)
        node = self.__robot.getSelf()
        node.getField("translation").setSFVec3f(SPAWN_TRANSLATION)
        node.getField("rotation").setSFRotation(SPAWN_ROTATION)
        node.resetPhysics()

    def step(self):
        rclpy.spin_once(self.__node, timeout_sec=0)

        self.__step_count += 1
        if self.__step_count % self.__steps_per_feedback != 0:
            return

        angle = self.__robot.getSteeringAngle()
        speed_kmh = self.__robot.getCurrentSpeed()
        message = AckermannDriveStamped()
        message.header.stamp = self.__node.get_clock().now().to_msg()
        message.drive.steering_angle = 0.0 if math.isnan(angle) else float(angle)
        message.drive.speed = 0.0 if math.isnan(speed_kmh) else float(speed_kmh) / 3.6
        self.__feedback_publisher.publish(message)
