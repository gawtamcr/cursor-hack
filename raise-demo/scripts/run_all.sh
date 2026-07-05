#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="${SESSION:-raise-demo}"
ROS_DISTRO="${ROS_DISTRO:-lyrical}"

# Anaconda's python shadows the system interpreter ROS is built against;
# every demo shell must strip it (PATH *and* CONDA_* vars — CMake's
# FindPython3 honors CONDA_PREFIX even when PATH is clean).
STRIP_CONDA='unset CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_EXE CONDA_PYTHON_EXE CONDA_SHLVL CONDA_PROMPT_MODIFIER _CE_CONDA _CE_M VIRTUAL_ENV; export PATH=$(echo "$PATH" | tr ":" "\n" | grep -v anaconda3 | paste -sd:) && hash -r'

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is required to run the multi-process demo" >&2
  exit 1
fi

tmux has-session -t "${SESSION}" 2>/dev/null && tmux kill-session -t "${SESSION}"

# Sentinel written only after a successful build, so the backend window cannot
# source a stale install/ while this run's build is still in progress.
BUILD_DONE="${ROOT_DIR}/ros_ws/.build_done"
rm -f "${BUILD_DONE}"

tmux new-session -d -s "${SESSION}" -c "${ROOT_DIR}" -n sim

# QT_QPA_PLATFORM=xcb: Webots is a Qt/X11 app; run it through XWayland.
tmux send-keys -t "${SESSION}:sim" "${STRIP_CONDA} && export QT_QPA_PLATFORM=xcb WEBOTS_HOME=/usr/local/webots && source /opt/ros/${ROS_DISTRO}/setup.bash && cd '${ROOT_DIR}/ros_ws' && colcon build --symlink-install && touch '${BUILD_DONE}' && source install/setup.bash && ros2 launch raise_webots demo.launch.py" C-m

tmux new-window -t "${SESSION}" -c "${ROOT_DIR}" -n backend
tmux send-keys -t "${SESSION}:backend" "${STRIP_CONDA} && source /opt/ros/${ROS_DISTRO}/setup.bash && [ -f '${ROOT_DIR}/.env' ] && source '${ROOT_DIR}/.env'; until [ -f '${BUILD_DONE}' ]; do echo 'Waiting for ROS workspace build...'; sleep 1; done && source '${ROOT_DIR}/ros_ws/install/setup.bash' && cd '${ROOT_DIR}/agent_backend' && python3 -m venv --system-site-packages .venv && source .venv/bin/activate && pip install -r requirements.txt && python -m uvicorn app:app --host 0.0.0.0 --port 8000" C-m

echo "Started tmux session '${SESSION}'."
echo "The Webots window opens once the workspace build finishes (first build downloads city assets)."
echo "Open http://localhost:8000 after the backend window finishes installing dependencies."
echo "Attach with: tmux attach -t ${SESSION}"
