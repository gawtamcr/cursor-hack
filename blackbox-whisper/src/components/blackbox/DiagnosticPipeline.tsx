import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Sparkles,
  Plug,
  Network,
  LineChart,
  Stethoscope,
  ArrowRight,
  Loader2,
  Check,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { appleEase } from "@/lib/motion";
import {
  getNodeList,
  getTopicInfo,
  getDiagnosis,
  diagnoseWithCursorAgent,
  STEERING_TOPICS,
  type Diagnosis,
  type TopicInfo,
} from "@/lib/diagnose";

/**
 * Live diagnostic pipeline visualization. Each stage runs a REAL call against
 * the running sim's MCP HTTP shim and renders the actual response — no mock
 * data. The stages mirror the real path:
 *   Cursor Agent -> ROS MCP -> Nodes & Topics -> Data analysis -> Diagnosis
 */

type StageState = "idle" | "running" | "done";

const TOOL_CALLS = [
  "get_node_list()",
  "get_topic_info(/vehicle/steering/feedback)",
  "get_steering_health()",
  "get_steering_stats()",
];

export function DiagnosticPipeline({ onDiagnosis }: { onDiagnosis?: (d: Diagnosis) => void }) {
  const [stage, setStage] = useState(0); // how many stages have started
  const [running, setRunning] = useState(false);
  const [nodes, setNodes] = useState<string[] | null>(null);
  const [topics, setTopics] = useState<TopicInfo[] | null>(null);
  const [result, setResult] = useState<Diagnosis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [useAgent, setUseAgent] = useState(false); // Cursor SDK agent vs rule-based
  const [agentText, setAgentText] = useState<string | null>(null);
  const seq = useRef(0);

  async function run() {
    const myRun = ++seq.current;
    setRunning(true);
    setError(null);
    setNodes(null);
    setTopics(null);
    setResult(null);
    setStage(1); // Cursor Agent issues the request

    const step = (n: number) => new Promise((r) => setTimeout(r, n));

    try {
      // Stage 2: ROS MCP — tool calls begin (visual dwell so the calls read).
      await step(500);
      if (seq.current !== myRun) return;
      setStage(2);

      // Stage 3: Nodes & Topics — REAL graph introspection.
      await step(400);
      const nodeRes = await getNodeList();
      if (seq.current !== myRun) return;
      setNodes(nodeRes.nodes);
      const topicRes = await Promise.all(STEERING_TOPICS.map((t) => getTopicInfo(t)));
      if (seq.current !== myRun) return;
      setTopics(topicRes);
      setStage(3);

      // Stage 4: Data analysis — the live diagnosis carries the numbers.
      // Always fetch the rule-based diagnosis first: it populates the live
      // telemetry numbers (Stage 4) and is the AgentPanel's data source.
      await step(400);
      const diag = await getDiagnosis();
      if (seq.current !== myRun) return;
      setResult(diag);
      onDiagnosis?.(diag);
      setStage(4);

      // Stage 5: Diagnosis. If the Cursor Agent is enabled, the REAL @cursor/sdk
      // local agent produces the cited narrative (it calls the same MCP tools
      // + reads the PLM rules server-side); otherwise show the rule-based one.
      setAgentText(null);
      if (useAgent) {
        const a = await diagnoseWithCursorAgent();
        if (seq.current !== myRun) return;
        setAgentText(a.summary);
        // Prefer the agent's cited fields when it returned structured output.
        setResult((r) =>
          r
            ? { ...r, summary: a.summary, requirement_id: a.requirement_id || r.requirement_id }
            : r,
        );
      }
      setStage(5);
    } catch {
      if (seq.current !== myRun) return;
      setError("Sim offline — start the container: docker compose up -d");
    } finally {
      if (seq.current === myRun) setRunning(false);
    }
  }

  // Auto-run once on mount so the pipeline is populated when the issue opens.
  useEffect(() => {
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const st = (n: number): StageState =>
    stage > n ? "done" : stage === n ? (running ? "running" : "done") : "idle";

  return (
    <section className="rounded-2xl bg-card p-5 shadow-card">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
            Live diagnostic pipeline
          </p>
          <h3 className="mt-0.5 text-sm font-semibold tracking-[-0.01em] text-foreground">
            Cursor SDK → ROS MCP → live ROS2 graph → diagnosis
          </h3>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setUseAgent((v) => !v)}
            disabled={running}
            className={cn(
              "flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] font-medium transition-colors disabled:opacity-50",
              useAgent ? "bg-primary/10 text-primary" : "bg-secondary text-muted-foreground",
            )}
            title="Run the real @cursor/sdk local agent for the final diagnosis"
          >
            <Sparkles className="h-3.5 w-3.5" />
            {useAgent ? "Cursor Agent: ON" : "Cursor Agent: OFF"}
          </button>
          <button
            type="button"
            onClick={run}
            disabled={running}
            className="rounded-lg bg-secondary px-3 py-1.5 text-[13px] font-medium text-foreground transition-colors hover:bg-secondary/70 disabled:opacity-50"
          >
            {running ? "Running…" : "Re-run"}
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-3 rounded-lg bg-anomaly-soft px-3 py-2 text-[13px] font-medium text-anomaly">
          {error}
        </div>
      )}

      <div className="flex flex-col gap-2.5">
        <Stage
          state={st(1)}
          icon={<Sparkles className="h-4 w-4" />}
          title="Cursor Agent (SDK)"
          subtitle={useAgent ? "Durable local agent — LIVE" : "Durable local agent (enable above)"}
        >
          <Mono>send("diagnose the steering validation test")</Mono>
        </Stage>

        <Connector active={stage >= 2} />

        <Stage
          state={st(2)}
          icon={<Plug className="h-4 w-4" />}
          title="ROS MCP server"
          subtitle="stdio → HTTP → rclpy (read-only)"
        >
          <div className="flex flex-wrap gap-1.5">
            {TOOL_CALLS.map((t) => (
              <span
                key={t}
                className="rounded-md bg-card px-2 py-0.5 font-mono text-[11px] text-muted-foreground shadow-hairline"
              >
                {t}
              </span>
            ))}
          </div>
        </Stage>

        <Connector active={stage >= 3} />

        <Stage
          state={st(3)}
          icon={<Network className="h-4 w-4" />}
          title="Live ROS2 nodes & topics"
          subtitle={nodes ? `${nodes.length} nodes on the graph` : "Introspecting graph…"}
        >
          {nodes && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {nodes.map((n) => (
                <span
                  key={n}
                  className="rounded-md bg-card px-2 py-0.5 font-mono text-[11px] text-foreground shadow-hairline"
                >
                  {n}
                </span>
              ))}
            </div>
          )}
          {topics && (
            <div className="overflow-hidden rounded-lg shadow-hairline">
              <table className="w-full border-collapse text-left font-mono text-[11px]">
                <tbody>
                  {topics.map((t) => (
                    <tr key={t.topic} className="border-b border-border/60 last:border-0">
                      <td className="bg-card px-2 py-1 text-foreground">{t.topic}</td>
                      <td className="bg-card px-2 py-1 text-muted-foreground">
                        {t.types[0]?.replace("ackermann_msgs/msg/", "") ?? "—"}
                      </td>
                      <td className="bg-card px-2 py-1 text-right text-muted-foreground">
                        {t.publishers}p · {t.subscribers}s
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Stage>

        <Connector active={stage >= 4} />

        <Stage
          state={st(4)}
          icon={<LineChart className="h-4 w-4" />}
          title="Data analysis"
          subtitle="Commanded vs measured over the 4 s window"
        >
          {result && (
            <div className="grid grid-cols-2 gap-1.5">
              {result.claims.slice(0, 4).map((c) => (
                <div key={c.label} className="rounded-md bg-card px-2.5 py-1.5 shadow-hairline">
                  <p className="text-[10px] uppercase tracking-[0.06em] text-muted-foreground">
                    {c.label}
                  </p>
                  <p className="font-mono text-[12px] tabular-nums text-foreground">{c.value}</p>
                </div>
              ))}
            </div>
          )}
        </Stage>

        <Connector active={stage >= 5} />

        <Stage
          state={st(5)}
          icon={<Stethoscope className="h-4 w-4" />}
          title="Diagnosis"
          subtitle={result ? `Cites ${result.requirement_id}` : "Correlating with PLM…"}
          last
        >
          {result && (
            <div
              className={cn(
                "rounded-lg px-3 py-2",
                result.test_status === "PASS" ? "bg-success-soft" : "bg-anomaly-soft",
              )}
            >
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    "rounded-full px-2 py-0.5 text-[11px] font-semibold",
                    result.test_status === "PASS"
                      ? "bg-success/15 text-success"
                      : "bg-anomaly/15 text-anomaly",
                  )}
                >
                  TEST {result.test_status}
                </span>
                <span className="font-mono text-[12px] font-semibold text-foreground">
                  {result.requirement_id}
                </span>
                {result.known_issue && (
                  <span className="font-mono text-[11px] text-muted-foreground">
                    {result.known_issue}
                  </span>
                )}
              </div>
              <p className="mt-1.5 text-[12.5px] leading-relaxed text-foreground">
                {agentText ?? result.summary}
              </p>
              {agentText && (
                <p className="mt-1.5 flex items-center gap-1 text-[10px] font-medium uppercase tracking-[0.06em] text-primary">
                  <Sparkles className="h-3 w-3" /> Generated by the Cursor SDK agent
                </p>
              )}
            </div>
          )}
        </Stage>
      </div>
    </section>
  );
}

function Mono({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-md bg-card px-2 py-0.5 font-mono text-[11px] text-muted-foreground shadow-hairline">
      {children}
    </span>
  );
}

function Connector({ active }: { active: boolean }) {
  return (
    <div className="ml-[15px] flex h-4 items-center">
      <motion.div
        initial={false}
        animate={{ backgroundColor: active ? "var(--primary)" : "var(--border)" }}
        transition={{ duration: 0.4 }}
        className="h-full w-px"
      />
      <ArrowRight
        className={cn(
          "-ml-[7px] h-3 w-3 transition-colors",
          active ? "text-primary" : "text-border",
        )}
      />
    </div>
  );
}

function Stage({
  state,
  icon,
  title,
  subtitle,
  children,
  last,
}: {
  state: StageState;
  icon: React.ReactNode;
  title: string;
  subtitle: string;
  children?: React.ReactNode;
  last?: boolean;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, x: -6 }}
      animate={{ opacity: state === "idle" ? 0.45 : 1, x: 0 }}
      transition={{ duration: 0.4, ease: appleEase }}
      className="flex gap-3"
    >
      <div
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] transition-colors",
          state === "done"
            ? "bg-success-soft text-success"
            : state === "running"
              ? "bg-replay-soft text-primary"
              : "bg-secondary text-muted-foreground",
        )}
      >
        {state === "running" ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : state === "done" ? (
          <Check className="h-4 w-4" strokeWidth={2.5} />
        ) : (
          icon
        )}
      </div>
      <div className={cn("min-w-0 flex-1", !last && "pb-0.5")}>
        <div className="flex items-baseline gap-2">
          <p className="text-[13px] font-semibold text-foreground">{title}</p>
          <p className="truncate text-[11px] text-muted-foreground">{subtitle}</p>
        </div>
        <AnimatePresence>
          {state !== "idle" && children && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              transition={{ duration: 0.35, ease: appleEase }}
              className="mt-1.5"
            >
              {children}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}
