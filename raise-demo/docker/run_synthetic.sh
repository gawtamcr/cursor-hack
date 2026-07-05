#!/usr/bin/env bash
# Phase 0b (synthetic-plant path): run the real core graph plus the synthetic
# plant that stands in for Webots. Sourced ROS + workspace overlay come from the
# entrypoint. The plant script is mounted at /plant/synthetic_plant.py.
set -e
source /opt/ros/jazzy/setup.bash
source /ws/install/setup.bash

# Core graph: eps_controller (fault injector), steering_monitor, lane_follower.
ros2 launch raise_core core.launch.py &
CORE_PID=$!

# Synthetic plant: command sweep + /vehicle/steering/feedback + IMU + GPS.
python3 /plant/synthetic_plant.py &
PLANT_PID=$!

# Read-only HTTP tool shim on :8000 (the boundary the host MCP server calls).
# tools.py is mounted next to mcp_http.py so it imports AgentTools verbatim.
python3 /plant/mcp_http.py &
SHIM_PID=$!

trap 'kill $CORE_PID $PLANT_PID $SHIM_PID 2>/dev/null' TERM INT
wait -n $CORE_PID $PLANT_PID $SHIM_PID
