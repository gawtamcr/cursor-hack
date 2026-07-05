# Symptom-level steering diagnostics. The monitor reports what it measures;
# it never claims a root cause.
string status              # OK | DEGRADED | FAILED
string symptom             # ok | steering_pull | oscillation | sluggish_response |
                           # reversed_response | unresponsive | no_feedback
float32 cmd_angle          # latest commanded steering angle (rad)
float32 actual_angle       # latest measured steering angle (rad)
float32 angle_error_mean   # mean(actual - cmd) over the window (rad)
float32 angle_error_stddev
float32 cmd_stddev
float32 actual_stddev
float32 cmd_actual_corr    # Pearson correlation at zero lag (0 when cmd variance below floor)
float32 response_lag_s     # lag of peak cross-correlation, 0..1.5 s
float32 yaw_rate           # angular_velocity.z from the IMU (rad/s)
float32 speed_mps
float32 feedback_rate_hz
