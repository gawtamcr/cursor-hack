#!/usr/bin/env bash
# Source ROS 2 and the built workspace overlay, then exec the given command.
set -e
source /opt/ros/jazzy/setup.bash
source /ws/install/setup.bash
exec "$@"
