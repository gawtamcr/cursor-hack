/**
 * Live diagnosis client — talks to the RAISE sim's read-only HTTP shim
 * (GIT/raise-demo/docker/mcp_http.py), served by the running ROS2 container.
 *
 * The shim reads the live /steering/health topic, correlates it against the
 * PLM requirements (.cursor/rules/plm-steering.mdc), and returns a cited
 * diagnosis. This is the real data behind the steering issue (PLM-1047).
 *
 * Set VITE_RAISE_SHIM_URL to override; defaults to the container's published
 * port on localhost.
 */

const SHIM_URL =
  (import.meta as { env?: Record<string, string> }).env?.VITE_RAISE_SHIM_URL ??
  "http://localhost:8000";

export type SteeringHealth = {
  status: string; // OK | DEGRADED | FAILED
  symptom: string;
  cmd_angle: number;
  actual_angle: number;
  angle_error_mean: number;
  cmd_actual_corr: number;
  response_lag_s: number;
  feedback_rate_hz: number;
};

export type Diagnosis = {
  test_status: "PASS" | "FAIL";
  status: string;
  symptom: string;
  requirement_id: string;
  title: string;
  root_cause: string;
  action: string;
  known_issue: string;
  summary: string;
  evidence: {
    cmd_angle: number;
    actual_angle: number;
    angle_error_mean: number;
    cmd_actual_corr: number;
    response_lag_s: number;
    feedback_rate_hz: number;
  };
  claims: { label: string; value: string }[];
};

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${SHIM_URL}${path}`, init);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

/** True if the sim container/shim is reachable. */
export async function simOnline(): Promise<boolean> {
  try {
    const h = await j<{ ok: boolean; bridge_ready: boolean }>("/health");
    return Boolean(h.bridge_ready);
  } catch {
    return false;
  }
}

/** Live steering health snapshot (for the telemetry poll). */
export function getSteeringHealth() {
  return j<SteeringHealth>("/tools/get_steering_health");
}

export type TopicInfo = {
  topic: string;
  types: string[];
  publishers: number;
  subscribers: number;
};

/** Live ROS2 node names (from the running graph). */
export function getNodeList() {
  return j<{ nodes: string[] }>("/tools/get_node_list");
}

/** Live type + publisher/subscriber counts for a ROS2 topic. */
export function getTopicInfo(topic: string) {
  return j<TopicInfo>(`/tools/get_topic_info?topic=${encodeURIComponent(topic)}`);
}

/** The steering signal-chain topics the diagnosis inspects. */
export const STEERING_TOPICS = [
  "/control/ackermann_cmd",
  "/cmd_ackermann",
  "/vehicle/steering/feedback",
  "/steering/health",
] as const;

/** Run a live PLM diagnosis against the ROS2 steering graph. */
export function getDiagnosis() {
  return j<Diagnosis>("/diagnose");
}

/** Demo control: inject a steering fault into the live actuator. */
export function injectFault(fault: string) {
  return j<{ ok: boolean; message: string; fault: string }>("/tools/set_fault", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fault }),
  });
}

/** Demo control: power-cycle the EPS (clears the active fault). */
export function restartSteeringUnit() {
  return j<{ ok: boolean; action: string }>("/tools/restart_steering_unit", {
    method: "POST",
  });
}

// ── Real @cursor/sdk agent (via the local sidecar; see agent-sidecar/) ──
const AGENT_URL =
  (import.meta as { env?: Record<string, string> }).env?.VITE_AGENT_URL ?? "http://localhost:8790";

export type AgentDiagnosis = {
  test_status: "PASS" | "FAIL";
  status: string;
  symptom: string;
  requirement_id: string;
  known_issue: string;
  summary: string;
  root_cause: string;
  action: string;
  source: string;
};

/** Run the real Cursor SDK local agent for a cited, LLM-authored diagnosis. */
export async function diagnoseWithCursorAgent(prompt?: string): Promise<AgentDiagnosis> {
  const res = await fetch(`${AGENT_URL}/diagnose`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
  if (!res.ok && res.status !== 500) throw new Error(`agent -> ${res.status}`);
  return res.json() as Promise<AgentDiagnosis>;
}
