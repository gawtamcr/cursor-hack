import { useEffect, useRef, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { motion } from "framer-motion";
import { toast } from "sonner";

import { TopBar } from "@/components/blackbox/TopBar";
import { LeftRail } from "@/components/blackbox/LeftRail";
import { PromptBar } from "@/components/blackbox/PromptBar";
import { ReplayCanvas } from "@/components/blackbox/ReplayCanvas";
import { ProgressRail } from "@/components/blackbox/ProgressRail";
import { ValidationStrip } from "@/components/blackbox/ValidationStrip";
import { AgentPanel } from "@/components/blackbox/AgentPanel";
import { TechnicalDrawer } from "@/components/blackbox/TechnicalDrawer";
import { PlmView } from "@/components/blackbox/PlmView";
import { DiagnosticPipeline } from "@/components/blackbox/DiagnosticPipeline";
import { AGENT_STEPS, type NavKey, type ProgressKey } from "@/lib/blackbox";
import { FILED_ISSUE, type PlmIssue } from "@/lib/plm";
import { appleEase } from "@/lib/motion";
import { getDiagnosis, injectFault, restartSteeringUnit, type Diagnosis } from "@/lib/diagnose";

export const Route = createFileRoute("/")({
  component: Index,
});

type RegState = "pending" | "running" | "passed";

function Index() {
  const [nav, setNav] = useState<NavKey>("plm");
  const [prompt, setPrompt] = useState("Car steers too little during left turns.");
  const [diagnosing, setDiagnosing] = useState(false);
  const [visibleSteps, setVisibleSteps] = useState(0);
  const [replayKey, setReplayKey] = useState(0);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerTab, setDrawerTab] = useState("Trace");
  const [previewed, setPreviewed] = useState(false);
  const [regression, setRegression] = useState<RegState>("pending");
  const [fixSynced, setFixSynced] = useState(false);
  const [live, setLive] = useState<Diagnosis | null>(null);
  const [busy, setBusy] = useState(false);
  const [pipelineKey, setPipelineKey] = useState(0); // bump to re-run the pipeline
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  // Pull a fresh live diagnosis from the running ROS2 sim.
  const refreshLive = async () => {
    try {
      setLive(await getDiagnosis());
    } catch {
      setLive(null); // sim offline -> AgentPanel falls back to scripted content
    }
  };

  const clearTimers = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  };

  const revealSteps = () => {
    clearTimers();
    setVisibleSteps(0);
    AGENT_STEPS.forEach((_, i) => {
      timers.current.push(setTimeout(() => setVisibleSteps(i + 1), 260 * (i + 1)));
    });
  };

  // Initial page-load reveal
  useEffect(() => {
    revealSteps();
    return clearTimers;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleDiagnose = async () => {
    setDiagnosing(true);
    setReplayKey((k) => k + 1);
    revealSteps();
    // Query the live ROS2 sim + PLM in parallel with the step animation.
    await refreshLive();
    timers.current.push(
      setTimeout(
        () => {
          setDiagnosing(false);
          setLive((d) =>
            d
              ? (toast.success("Diagnosis complete", {
                  description: `${d.status} · ${d.requirement_id} · ${d.symptom}`,
                }),
                d)
              : (toast.success("Diagnosis complete", {
                  description: "Understeer traced to control clamp ordering.",
                }),
                d),
          );
        },
        260 * AGENT_STEPS.length + 300,
      ),
    );
  };

  // Demo controls: inject the steering defect / apply the fix on the live sim.
  const handleInjectFault = async () => {
    setBusy(true);
    await injectFault("steer_bias");
    await new Promise((r) => setTimeout(r, 6000)); // let the monitor window refill
    setBusy(false);
    toast.info("Steering fault injected", { description: "EPS bias offset · steer_bias" });
    setPipelineKey((k) => k + 1); // re-run the pipeline against the faulted graph
  };

  const handleFixLive = async () => {
    setBusy(true);
    await restartSteeringUnit();
    await new Promise((r) => setTimeout(r, 6000));
    setBusy(false);
    toast.success("EPS power-cycled", {
      description: "Steering re-calibrated · validation passing",
    });
    setPipelineKey((k) => k + 1); // re-run the pipeline; should return to PASS
  };

  const handlePreview = () => {
    setPreviewed(true);
    setDrawerTab("Patch");
    setDrawerOpen(true);
  };

  const handleTechnical = () => {
    setDrawerOpen(true);
  };

  const handleApply = () => {
    toast.success("Cursor context copied", {
      description: "Patch, file map and regression test ready in Cursor.",
    });
  };

  const handleRunRegression = () => {
    setRegression("running");
    setNav("validation");
    timers.current.push(
      setTimeout(() => {
        setRegression("passed");
        toast.success("Regression passed", {
          description: "Path error 1.4m → 0.12m · steering within tolerance.",
        });
      }, 1900),
    );
  };

  const handleOpenIssue = (issue: PlmIssue) => {
    // The steering issue (PLM-1047) is backed by the LIVE ROS2 sim: opening it
    // pulls a real cited diagnosis instead of a scripted one.
    if (issue.region === "steering" || issue.id === FILED_ISSUE.id) {
      setNav("replay");
      void refreshLive();
      toast.success("Connected to live vehicle", {
        description: "Querying ROS2 steering graph + PLM…",
      });
    } else {
      setLive(null);
      toast.info("No live telemetry", {
        description: "Only the steering issue is backed by the live ROS2 sim.",
      });
    }
  };

  const handleSyncToPlm = () => {
    if (fixSynced) return;
    setFixSynced(true);
    toast.success("Fix synced to PLM", {
      description: "PLM-1047 · Steering Controller · marked Resolved",
    });
    setNav("plm");
  };

  const progressDone: ProgressKey[] =
    regression === "passed"
      ? ["replay", "diagnose", "trace", "fix", "validate"]
      : previewed
        ? ["replay", "diagnose", "trace", "fix"]
        : ["replay", "diagnose", "trace"];

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background text-foreground">
      <TopBar onTechnical={handleTechnical} />

      <div className="flex min-h-0 flex-1">
        <LeftRail active={nav} onSelect={setNav} />

        {/* Central workspace */}
        <motion.main
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: appleEase }}
          className="flex min-w-0 flex-1 flex-col gap-6 overflow-y-auto p-8"
        >
          {nav === "plm" ? (
            <PlmView fixSynced={fixSynced} onOpenIssue={handleOpenIssue} />
          ) : (
            <>
              <PromptBar
                value={prompt}
                onChange={setPrompt}
                onDiagnose={handleDiagnose}
                diagnosing={diagnosing}
              />

              <p className="px-0.5 text-[13px] leading-relaxed text-muted-foreground">
                Opened from PLM-1047. Cursor queries the live ROS2 steering graph, correlates it
                against the PLM requirements, and prepares the fix.
              </p>

              {/* Live demo controls — drive the real sim from the UI */}
              <div className="flex items-center gap-2.5">
                <button
                  type="button"
                  disabled={busy}
                  onClick={handleInjectFault}
                  className="rounded-lg bg-anomaly-soft px-3 py-1.5 text-[13px] font-medium text-anomaly transition-colors hover:opacity-80 disabled:opacity-50"
                >
                  Inject steering fault
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={handleFixLive}
                  className="rounded-lg bg-success-soft px-3 py-1.5 text-[13px] font-medium text-success transition-colors hover:opacity-80 disabled:opacity-50"
                >
                  Power-cycle EPS (fix)
                </button>
                {live && (
                  <span className="text-xs text-muted-foreground">
                    Live: {live.status} · {live.symptom}
                  </span>
                )}
              </div>

              {/* Real pipeline: Cursor SDK -> ROS MCP -> nodes/topics -> analysis
                  -> diagnosis, populated live from the running sim. */}
              <DiagnosticPipeline key={pipelineKey} onDiagnosis={setLive} />

              <ReplayCanvas replayKey={replayKey} />

              <ProgressRail done={progressDone} />

              <ValidationStrip regression={regression} />
            </>
          )}
        </motion.main>

        <AgentPanel
          visibleSteps={visibleSteps}
          regression={regression}
          canRunRegression={previewed}
          fixSynced={fixSynced}
          live={live}
          onApply={handleApply}
          onPreview={handlePreview}
          onTechnical={handleTechnical}
          onRunRegression={handleRunRegression}
          onSyncToPlm={handleSyncToPlm}
        />
      </div>

      <TechnicalDrawer
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
        tab={drawerTab}
        onTabChange={setDrawerTab}
      />
    </div>
  );
}
