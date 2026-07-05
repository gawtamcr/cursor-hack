#!/usr/bin/env bash
# One-time host setup for the RAISE steering demo (Ubuntu 26.04).
# Installs ROS 2 Lyrical, webots_ros2, and Webots. Run with:
#   sudo bash scripts/setup_host.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run me with sudo: sudo bash $0" >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "==> Enabling universe + prerequisites"
add-apt-repository -y universe
apt-get update
apt-get install -y curl wget gnupg lsb-release

echo "==> Adding ROS 2 apt repository"
curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
    -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo "$UBUNTU_CODENAME") main" \
    > /etc/apt/sources.list.d/ros2.list

echo "==> Adding Cyberbotics (Webots) apt repository"
mkdir -p /etc/apt/keyrings
wget -qO- https://cyberbotics.com/Cyberbotics.asc > /etc/apt/keyrings/Cyberbotics.asc
echo "deb [signed-by=/etc/apt/keyrings/Cyberbotics.asc] https://cyberbotics.com/debian binary-amd64/" \
    > /etc/apt/sources.list.d/Cyberbotics.list

# webots_ros2's C++ driver has no Lyrical binary yet (only importer/msgs are
# synced), so the driver is built from source in ros_ws/src/webots_ros2 —
# these packages cover its build and runtime dependencies.
echo "==> Installing ROS 2 Lyrical, webots_ros2 build deps, tooling"
apt-get update
apt-get install -y \
    ros-lyrical-desktop \
    ros-dev-tools \
    ros-lyrical-webots-ros2-importer \
    ros-lyrical-vision-msgs \
    ros-lyrical-python-cmake-module \
    ros-lyrical-tinyxml2-vendor \
    ros-lyrical-pluginlib \
    ros-lyrical-tf2-geometry-msgs \
    ros-lyrical-ackermann-msgs \
    ros-lyrical-cv-bridge \
    libyaml-cpp-dev \
    build-essential \
    cmake \
    git \
    python3-dev \
    python3-colcon-common-extensions \
    python3-opencv \
    python3-numpy \
    python3-pytest \
    python3-venv \
    tmux

echo "==> Installing Webots"
apt-get install -y webots

# The webots_ros2 C++ driver has no Lyrical binary yet: vendor its source into
# the workspace (as the invoking user, not root) and keep only what we need.
echo "==> Vendoring webots_ros2 source into ros_ws/src"
RUN_AS="${SUDO_USER:-$USER}"
WS_SRC="$(cd "$(dirname "$0")/.." && pwd)/ros_ws/src"
if [ ! -d "${WS_SRC}/webots_ros2" ]; then
    sudo -u "${RUN_AS}" git clone --depth 1 --branch 2025.0.1 \
        https://github.com/cyberbotics/webots_ros2.git "${WS_SRC}/webots_ros2"
fi
# Build only driver + msgs + tesla; ignore the other example packages.
for p in webots_ros2 webots_ros2_control webots_ros2_crazyflie webots_ros2_epuck \
         webots_ros2_husarion webots_ros2_importer webots_ros2_mavic webots_ros2_tests \
         webots_ros2_tiago webots_ros2_turtlebot webots_ros2_universal_robot; do
    sudo -u "${RUN_AS}" touch "${WS_SRC}/webots_ros2/${p}/COLCON_IGNORE"
done
# The 2025.0.1 CMakeLists hardcodes Python 3.10/3.12 per known distro and
# predates lyrical (Python 3.14): add a generic fallback branch.
DRIVER_CMAKE="${WS_SRC}/webots_ros2/webots_ros2_driver/CMakeLists.txt"
if ! grep -q "Distros newer than this release" "${DRIVER_CMAKE}"; then
    sudo -u "${RUN_AS}" python3 - "${DRIVER_CMAKE}" <<'PYEOF'
import sys
path = sys.argv[1]
text = open(path).read()
old = '''elseif($ENV{ROS_DISTRO} MATCHES "rolling")
  find_package(Python 3.12 EXACT REQUIRED COMPONENTS Development)
endif()'''
new = '''elseif($ENV{ROS_DISTRO} MATCHES "rolling")
  find_package(Python 3.12 EXACT REQUIRED COMPONENTS Development)
else()
  # Distros newer than this release (e.g. lyrical / Python 3.14): take the
  # system interpreter's headers instead of a hardcoded version.
  find_package(Python REQUIRED COMPONENTS Development)
endif()'''
assert text.count(old) == 1, "unexpected CMakeLists content"
open(path, "w").write(text.replace(old, new))
print("patched", path)
PYEOF
fi

echo
echo "Done. Sanity checks (run in a shell WITHOUT anaconda active — 'conda deactivate'):"
echo "  source /opt/ros/lyrical/setup.bash && ros2 doctor --report | head"
echo "  QT_QPA_PLATFORM=xcb ros2 launch webots_ros2_tesla robot_launch.py   # stock demo gate (after building the workspace)"
