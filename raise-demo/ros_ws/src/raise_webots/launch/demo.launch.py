"""Launch the full RAISE steering demo: Webots city, vehicle driver, core nodes."""

import os

import launch
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions.path_join_substitution import PathJoinSubstitution
from webots_ros2_driver.webots_controller import WebotsController
from webots_ros2_driver.webots_launcher import WebotsLauncher


def generate_launch_description():
    package_dir = get_package_share_directory("raise_webots")
    core_dir = get_package_share_directory("raise_core")

    webots = WebotsLauncher(
        world=PathJoinSubstitution([package_dir, "worlds", "raise_city.wbt"]),
        ros2_supervisor=True,
    )

    vehicle_driver = WebotsController(
        robot_name="vehicle",
        parameters=[
            {"robot_description": os.path.join(package_dir, "resource", "raise_vehicle.urdf")}
        ],
        respawn=True,
    )

    core = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(core_dir, "launch", "core.launch.py"))
    )

    return LaunchDescription(
        [
            webots,
            webots._supervisor,
            vehicle_driver,
            core,
            # Kill every node once the Webots window is closed.
            launch.actions.RegisterEventHandler(
                event_handler=launch.event_handlers.OnProcessExit(
                    target_action=webots,
                    on_exit=[launch.actions.EmitEvent(event=launch.events.Shutdown())],
                )
            ),
        ]
    )
