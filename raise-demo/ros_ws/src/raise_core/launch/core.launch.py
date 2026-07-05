from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription(
        [
            Node(
                package="raise_core",
                executable="steering_fault_injector",
                name="eps_controller",
            ),
            Node(package="raise_core", executable="steering_monitor", name="steering_monitor"),
            Node(package="raise_core", executable="lane_follower", name="lane_follower"),
        ]
    )
