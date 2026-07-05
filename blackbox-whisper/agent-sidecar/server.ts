/**
 * Cursor SDK agent sidecar — a tiny standalone HTTP server (Bun) that runs the
 * REAL @cursor/sdk local agent and exposes it at http://localhost:8790/diagnose.
 *
 * Why a sidecar instead of a TanStack server function: this app's TanStack
 * Start version has a broken server-fn result serializer (Seroval "step 3"
 * error on ANY server fn, even a trivial one). The sidecar sidesteps that
 * entirely and mirrors the /diagnose HTTP contract the frontend already uses,
 * so the browser just fetches it like the rule-based shim.
 *
 * The agent is DURABLE (one instance, reused across requests), runs on the
 * LOCAL runtime (only local can reach the ROS2 sim), calls the ros-steering
 * MCP tools, and reads the PLM rules under the workspace root.
 *
 * Run:  CURSOR_API_KEY=... bun run agent-sidecar/server.ts
 */
import { Agent } from "@cursor/sdk";

const PORT = Number(process.env.AGENT_SIDECAR_PORT ?? 8790);
const WORKSPACE_ROOT = "/home/gawtam_wsl/thesis/CURSOR";

const MCP_SERVERS = {
  "ros-steering": {
    command: "wsl.exe",
    args: [
      "-d",
      "Ubuntu",
      "--",
      "/home/gawtam_wsl/thesis/CURSOR/GIT/raise-demo/mcp-server/.venv/bin/python",
      "/home/gawtam_wsl/thesis/CURSOR/GIT/raise-demo/mcp-server/ros_steering_mcp.py",
    ],
  },
};

const PROMPT = `The steering validation test is failing. Use the ros-steering MCP tools (get_steering_health, get_steering_stats) to inspect the LIVE vehicle, and read the PLM requirement in .cursor/rules/plm-steering.mdc.
Reply with a JSON object ONLY (no markdown fence) with keys: test_status ("PASS"|"FAIL"), status, symptom, requirement_id, known_issue, summary (one paragraph citing the REQ-STEER id and recommended fix), root_cause, action. Base every field on the live tool output.`;

let agentPromise: ReturnType<typeof Agent.create> | null = null;
function getAgent() {
  if (!agentPromise) {
    agentPromise = Agent.create({
      runtime: "local",
      cwd: WORKSPACE_ROOT,
      apiKey: process.env.CURSOR_API_KEY!,
      model: { id: "composer-2.5" },
    });
  }
  return agentPromise;
}

function parse(text: string) {
  const m = text.match(/\{[\s\S]*\}/);
  if (m) {
    try {
      const o = JSON.parse(m[0]);
      return {
        test_status: o.test_status === "PASS" ? "PASS" : "FAIL",
        status: String(o.status ?? "UNKNOWN"),
        symptom: String(o.symptom ?? ""),
        requirement_id: String(o.requirement_id ?? "—"),
        known_issue: String(o.known_issue ?? ""),
        summary: String(o.summary ?? text),
        root_cause: String(o.root_cause ?? ""),
        action: String(o.action ?? ""),
        source: "cursor-agent",
      };
    } catch {
      /* fall through */
    }
  }
  return {
    test_status: /fail/i.test(text) ? "FAIL" : "PASS",
    status: "UNKNOWN",
    symptom: "",
    requirement_id: "—",
    known_issue: "",
    summary: text,
    root_cause: "",
    action: "",
    source: "cursor-agent",
  };
}

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "*",
  "Access-Control-Allow-Headers": "*",
};

Bun.serve({
  port: PORT,
  async fetch(req) {
    const url = new URL(req.url);
    if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
    if (url.pathname === "/health") {
      return Response.json(
        { ok: true, hasKey: Boolean(process.env.CURSOR_API_KEY) },
        { headers: CORS },
      );
    }
    if (url.pathname === "/diagnose") {
      try {
        const body = (await req.json().catch(() => ({}))) as { prompt?: string };
        const agent = await getAgent();
        const run = await agent.send(body.prompt?.trim() || PROMPT, { mcpServers: MCP_SERVERS });
        for await (const _ of run.stream()) void _;
        const res = await run.wait();
        return Response.json(parse(res.result ?? ""), { headers: CORS });
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        return Response.json(
          {
            test_status: "FAIL",
            status: "AGENT_ERROR",
            summary: `Agent failed: ${msg}`,
            source: "cursor-agent",
          },
          { status: 500, headers: CORS },
        );
      }
    }
    return new Response("not found", { status: 404, headers: CORS });
  },
});

console.log(
  `Cursor agent sidecar on http://localhost:${PORT} (key: ${process.env.CURSOR_API_KEY ? "set" : "MISSING"})`,
);
