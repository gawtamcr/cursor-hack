# RAISE Demo: AI Troubleshooting Agent for a Self-Driving Car with Faulty Steering

This repository contains a hackathon-ready demo of an AI troubleshooting agent
for a simulated self-driving car. A Tesla Model 3 drives through a 3D city in
Webots; its steering can be deliberately sabotaged. A steering monitor compares
commanded intent with the measured rack angle (plus IMU and GPS evidence) and
publishes symptom-level diagnostics, a safety gate slows or stops the car, and
a Tesla-style dashboard lets an operator watch the camera, inject faults, and
work with an NVIDIA Nemotron-powered agent to diagnose and verify fixes.

## Architecture

```text
Webots (TeslaModel3 "vehicle", raise_city.wbt)
  /vehicle/camera/image_color   camera (lane following + dashboard feed)
  /vehicle/imu, /vehicle/gps    independent motion evidence
  chassis_interface plugin:     /cmd_ackermann in -> Driver API;
                                /vehicle/steering/feedback out (measured angle, 20 Hz)

lane_follower --/control/ackermann_cmd--> eps_controller --/cmd_ackermann--> car
                     (intent)              (fault injector, /fault/set)
steering_monitor: intent vs. feedback -> /steering/health (2 Hz, symptom only)
agent_backend:    incidents -> LLM agent with ROS tools -> dashboard (port 8000)
```

## Repository layout

```text
ros_ws/src/
  raise_interfaces/      # SteeringHealth.msg and SetFault.srv
  raise_core/            # steering monitor/classifier, fault injector, lane follower
  raise_webots/          # Webots city world, vehicle URDF + driver plugin, launch
agent_backend/
  app.py                 # FastAPI, WebSocket, MJPEG, rclpy bridge
  agent.py               # Nemotron loop with tool use and fallback responses
  tools.py               # tool implementations exposed to the model
  static/index.html      # single-file Tesla-style dashboard
scripts/
  setup_host.sh          # one-time ROS 2 Lyrical + Webots install (sudo)
  run_all.sh             # tmux launcher (sim + backend)
  smoke_ws.py            # WebSocket end-to-end smoke test
tests/                   # deterministic steering-classifier tests
```

## One-time host setup (Ubuntu 26.04)

```bash
sudo bash scripts/setup_host.sh
```

The script is idempotent and does all of this:

- adds the ROS 2 and Cyberbotics apt repositories
- installs ROS 2 Lyrical (`ros-lyrical-desktop`, `ros-dev-tools`), the
  `webots_ros2` build/runtime deps (`webots-ros2-importer`, `vision-msgs`,
  `python-cmake-module`, `tinyxml2-vendor`, `pluginlib`, `tf2-geometry-msgs`,
  `ackermann-msgs`, `cv-bridge`, `libyaml-cpp-dev`, `python3-dev`), colcon,
  OpenCV, tmux, and Webots R2025a
- vendors the `webots_ros2` driver source into `ros_ws/src/webots_ros2`
  (pinned to 2025.0.1 — there is no Lyrical binary of the C++ driver yet),
  COLCON_IGNOREs the example packages we do not need, and patches its
  CMakeLists so it builds against Python 3.14 (upstream hardcodes 3.10/3.12)

**Anaconda warning:** if conda auto-activates in your shell, its Python
shadows the system interpreter ROS is built against and breaks both the build
and runtime (`No module named 'em'`, wrong `Python3` found by CMake). Run
`conda deactivate` first — `scripts/run_all.sh` strips conda from its tmux
shells automatically, but manual shells must do it themselves.

Sanity gate before first use (also downloads the Webots city assets, so it
needs network on the first run):

```bash
source /opt/ros/lyrical/setup.bash
cd ros_ws && colcon build --symlink-install && source install/setup.bash
QT_QPA_PLATFORM=xcb ros2 launch webots_ros2_tesla robot_launch.py
```

Optional: put `export NVIDIA_API_KEY="..."` in a `.env` file at the repo root
for Nemotron tool-use responses via NVIDIA's OpenAI-compatible endpoint.
Without it, the backend uses a deterministic local fallback diagnosis so the
demo still runs.

## Run everything

```bash
scripts/run_all.sh     # tmux: builds the workspace, opens Webots, starts the backend
```

Open the dashboard at <http://localhost:8000>. The Webots window appears once
the build finishes; the car starts lane-following after ~30 s of sim warm-up.

Manual equivalent (remember `conda deactivate` in every shell):

```bash
source /opt/ros/lyrical/setup.bash
export WEBOTS_HOME=/usr/local/webots QT_QPA_PLATFORM=xcb
cd ros_ws && colcon build --symlink-install && source install/setup.bash
ros2 launch raise_webots demo.launch.py
```

and in another shell:

```bash
source /opt/ros/lyrical/setup.bash && source ros_ws/install/setup.bash
cd agent_backend
python3 -m venv --system-site-packages .venv && source .venv/bin/activate
pip install -r requirements.txt
source ../.env  # optional NVIDIA_API_KEY
uvicorn app:app --host 0.0.0.0 --port 8000
```

To stop everything: close the Webots window (that shuts down all ROS nodes),
`tmux kill-session -t raise-demo`, and Ctrl-C the backend if started manually.

The backend defaults to:

```bash
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_MODEL=nvidia/nemotron-3-ultra-550b-a55b
NVIDIA_TEMPERATURE=1
NVIDIA_TOP_P=0.95
NVIDIA_MAX_TOKENS=16384
NVIDIA_REASONING_BUDGET=16384
```

## Faults

| Button   | Fault string     | Symptom classified   | Visible car behavior                      |
|----------|------------------|----------------------|-------------------------------------------|
| PULL     | `steer_bias`     | `steering_pull`      | drifts toward the lane edge, slows down    |
| STUCK    | `steer_stuck`    | `unresponsive`       | stops (FAILED), wheels sweep for diagnosis |
| WOBBLE   | `steer_noise`    | `oscillation`        | wobbles in the lane, slows down            |
| LAG      | `steer_lag`      | `sluggish_response`  | sloppy cornering, slows down               |
| REVERSED | `steer_reversed` | `reversed_response`  | veers wrong way, then stops (FAILED)       |
| CLEAR    | `none`           | `ok` after recovery  | resumes cruise                             |

The safety gate keeps the car limping at reduced speed on DEGRADED symptoms so
the fault stays visible, and stops it on FAILED. While stopped, the lane
follower sweeps the wheels gently so the monitor keeps enough steering
excitation to re-assess the actuator - and to verify the fix.

`RESET CAR` tows the car back to its spawn pose (supervisor teleport) — use it
if a fault run ends against the guardrail.

## Demo script

1. Let the car cruise; the status chip shows `OK`, gear shows `D`.
2. Click `PULL`. The car drifts, health flips to `DEGRADED/steering_pull`, an
   incident opens, and the agent diagnoses a constant angle offset with tools.
3. Follow the agent's steps (or click `CLEAR` to "power-cycle the steering
   ECU"), then click `Done, check again`.
4. The agent verifies with fresh steering stats, the incident resolves, and the
   car resumes cruise.
5. Try `REVERSED` for a dramatic FAILED + safety stop, `WOBBLE` and `LAG` for
   subtler symptoms.

## Useful ROS commands

```bash
ros2 topic echo /steering/health
ros2 topic echo /vehicle/steering/feedback
ros2 service call /fault/set raise_interfaces/srv/SetFault "{fault_type: steer_bias}"
ros2 service call /fault/set raise_interfaces/srv/SetFault "{fault_type: none}"
ros2 topic hz /vehicle/imu
```

## Testing

The steering classifier is independent of ROS and is tested with synthetic
command/feedback time series:

```bash
python3 -m pytest tests
```

End-to-end smoke test against a running demo:

```bash
python3 scripts/smoke_ws.py
```
