import { ArrowUp, CalendarClock, Check, ChevronDown, CircleAlert, LoaderCircle, Square } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { threadApi, type AgentRun } from "@/lib/threads";
import { decideApproval, fetchRuns, startTask, stopTask, type ApprovalRequest } from "@/lib/runs";
import { agentModelApi, type AgentModelState } from "@/agentModel";
import { calendarApi, taskOrigin, taskPrompt, type TaskLink } from "@/lib/calendar";

const active = (run: AgentRun) => run.status === "running" || run.status === "waiting_approval";
const label = (status: AgentRun["status"]) => ({ running: "Working", waiting_approval: "Needs approval", completed: "Completed", failed: "Failed", cancelled: "Stopped" })[status] ?? status;

/** A quiet task view backed by Osmo's existing Symphony run manager. */
export function AgentWorkbench({ onOpenThread, onOpenSettings, onOpenAutomation }: { onOpenThread: (id: string) => void; onOpenSettings: () => void; onOpenAutomation?: (id: string) => void }) {
  const [model, setModel] = useState<AgentModelState>();
  const [task, setTask] = useState("");
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [phases, setPhases] = useState<Record<string, string>>({});
  // Which tasks an automation started (and from which calendar event), by run id.
  const [origins, setOrigins] = useState<Record<string, TaskLink>>({});
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    try {
      const list = await fetchRuns(0);
      setRuns([...list.runs].sort((a, b) => b.createdAt - a.createdAt));
      setApprovals(list.approvals);
      setPhases(list.phases);
      setOffline(false);
      const links = await calendarApi().tasks().catch(() => []);
      setOrigins(Object.fromEntries(links.map((link) => [link.runId, link])));
    } catch { setOffline(true); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => {
    void refresh();
    const refreshModel = () => void agentModelApi().get().then(setModel).catch(() => setModel(undefined));
    refreshModel();
    window.addEventListener("focus", refreshModel);
    const timer = window.setInterval(() => { void refresh(); refreshModel(); }, 3000);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", refreshModel); };
  }, [refresh]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const text = task.trim();
    if (!text || busy) return;
    setBusy(true);
    setError("");
    try {
      const thread = await threadApi().create();
      await startTask(thread.id, text);
      setTask("");
      await refresh();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not start task."); }
    finally { setBusy(false); }
  };
  const stop = async (id: string) => {
    try {
      await stopTask(id);
      await refresh();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not stop task."); }
  };
  const decide = async (id: string, approved: boolean) => {
    try {
      if (!await decideApproval(id, approved)) throw new Error("That approval is no longer pending.");
      await refresh();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not submit approval."); }
  };
  const running = runs.filter(active);
  const recent = runs.filter((run) => !active(run));
  const renderRun = (run: AgentRun) => <TaskRow key={run.runId} run={run} approval={approvals.find((item) => item.runId === run.runId)} origin={origins[run.runId]} phase={phases[run.runId]} onOpenThread={onOpenThread} onOpenAutomation={onOpenAutomation} onStop={stop} onDecide={decide} />;
  return <main className="agent-workbench osmo-scroll">
    <div className="agent-workbench-inner">
      <header className="agent-workbench-header"><div><h1>Tasks</h1><p>Give Osmo something to work on in the background.</p></div></header>
      <form className="agent-workbench-form" onSubmit={(event) => void submit(event)}>
        <label className="sr-only" htmlFor="agent-task">Describe a task for Osmo</label>
        <textarea id="agent-task" value={task} onChange={(event) => setTask(event.target.value)} maxLength={4000} placeholder="What can Osmo work on?" rows={3} />
        <div className="agent-workbench-form-footer"><button type="button" onClick={onOpenSettings} title="Background agent settings">{model ? `${model.effective} · Settings` : "Agent settings · unavailable"}</button><button type="submit" disabled={busy || !task.trim()} aria-label={busy ? "Starting task" : "Start task"} title="Start task">{busy ? <LoaderCircle size={16} /> : <ArrowUp size={16} />}</button></div>
      </form>
      {error && <p className="agent-workbench-error" role="alert">{error}</p>}
      {offline && <p className="agent-workbench-error" role="status">Can’t refresh tasks. Check your connection to Osmo.</p>}
      {loading ? <p className="agent-workbench-empty">Loading tasks…</p> : runs.length === 0 ? <div className="agent-workbench-empty"><strong>Your tasks will appear here</strong><p>Start one above, or ask Osmo to work on something while you talk.</p></div> : <>
        {running.length > 0 && <section aria-label="In progress"><div className="agent-workbench-list-title"><h2>In progress</h2><span>{running.length}</span></div><div className="agent-workbench-list">{running.map(renderRun)}</div></section>}
        {recent.length > 0 && <section aria-label="Recent tasks"><div className="agent-workbench-list-title"><h2>Recent</h2><span>{recent.length}</span></div><div className="agent-workbench-list">{recent.map(renderRun)}</div></section>}
      </>}
    </div>
  </main>;
}

interface TaskRowProps {
  run: AgentRun;
  approval: ApprovalRequest | undefined;
  origin: TaskLink | undefined;
  phase: string | undefined;
  onOpenThread: (id: string) => void;
  onOpenAutomation: ((id: string) => void) | undefined;
  onStop: (id: string) => Promise<void>;
  onDecide: (id: string, approved: boolean) => Promise<void>;
}

function TaskRow({ run, approval, origin, phase, onOpenThread, onOpenAutomation, onStop, onDecide }: TaskRowProps) {
  return <article className="agent-workbench-run">
      <div className="agent-run-icon" data-status={run.status} aria-hidden="true">{run.status === "running" ? <LoaderCircle size={15} /> : run.status === "completed" ? <Check size={15} /> : <CircleAlert size={15} />}</div>
      <div className="agent-run-content">
        <div className="agent-workbench-run-top"><span className="agent-workbench-status" data-status={run.status}>{label(run.status)}</span><time dateTime={new Date(run.createdAt * 1000).toISOString()}>{new Date(run.createdAt * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</time></div>
        <h3>{origin ? taskPrompt(run.task) : run.task}</h3>
        {origin && onOpenAutomation && <button type="button" className="agent-workbench-origin" onClick={() => onOpenAutomation(origin.automationId)} title="Open the automation in Calendar"><CalendarClock size={12} aria-hidden /><span>{taskOrigin(origin)}</span></button>}
        {phase && <p role="status">{phase}</p>}
        {run.result && <details className="agent-run-result"><summary>View result <ChevronDown size={14} /></summary><p>{run.result}</p></details>}
        {approval && <div className="agent-workbench-approval"><strong>Approval needed</strong><p>{approval.summary}</p><button type="button" onClick={() => void onDecide(approval.approvalId, true)}>Approve</button><button type="button" onClick={() => void onDecide(approval.approvalId, false)}>Deny</button></div>}
        <div className="agent-workbench-actions"><button type="button" onClick={() => onOpenThread(run.threadId)}>Open conversation</button>{active(run) && <button type="button" onClick={() => void onStop(run.runId)}><Square size={11} /> Stop</button>}</div>
      </div>
    </article>;
}
