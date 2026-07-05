# RAISE demo — containerized on ROS 2 Jazzy (the host WSL2 has no ROS).
#
# The repo's native target is ROS Lyrical / Py3.14 (scripts/setup_host.sh),
# which has no public Docker base. Jazzy is the realistic containerizable
# distro: it ships apt binaries for ackermann_msgs and webots_ros2, so we do
# NOT vendor webots_ros2 the way setup_host.sh does.
#
# Phase 0a scope: build only the ROS-only graph (raise_interfaces + raise_core)
# and bring the nodes up. Webots (raise_webots) is added in Phase 0b.
#
# Nothing under ros_ws/src/** is modified — the packages are copied in as-is.
FROM osrf/ros:jazzy-desktop

SHELL ["/bin/bash", "-c"]

ENV DEBIAN_FRONTEND=noninteractive

# ROS message deps used by raise_core / raise_interfaces. These are the exact
# <depend> entries from the package.xml files, as Jazzy apt binaries.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ros-jazzy-ackermann-msgs \
      python3-opencv \
      python3-colcon-common-extensions \
      python3-fastapi python3-uvicorn python3-pydantic \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /ws

# Copy only the ROS-only packages for Phase 0a. raise_webots is intentionally
# left out here; it is added when Phase 0b introduces the Webots plant.
COPY ros_ws/src/raise_interfaces ./src/raise_interfaces
COPY ros_ws/src/raise_core       ./src/raise_core

RUN source /opt/ros/jazzy/setup.bash \
    && colcon build --symlink-install \
         --packages-select raise_interfaces raise_core

# Source ROS + the workspace overlay for every interactive/exec shell.
RUN echo 'source /opt/ros/jazzy/setup.bash' >> /root/.bashrc \
    && echo 'source /ws/install/setup.bash' >> /root/.bashrc

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

# Default: launch the ROS-only graph (injector + monitor + lane_follower).
CMD ["ros2", "launch", "raise_core", "core.launch.py"]
