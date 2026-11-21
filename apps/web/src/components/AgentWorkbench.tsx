import { ArrowUp, Check, ChevronDown, CircleAlert, LoaderCircle, Square } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { agentRunSchema, threadApi, type AgentRun } from "@/lib/threads";
import { decideApproval, fetchRuns, type ApprovalRequest } from "@/lib/runs";
import { agentModelApi, type AgentModelState } from "@/agentModel";

const active = (run: AgentRun) => run.status === "running" || run.status === "waiting_approval";
const label = (status: AgentRun["status"]) => ({ running: "Working", waiting_approval: "Needs approval", completed: "Completed", failed: "Failed", cancelled: "Stopped" })[status] ?? status;

/** A quiet task view backed by Osmo's existing Symphony run manager. */
export function AgentWorkbench({ onOpenThread, onOpenSettings }: { onOpenThread: (id: string) => void; onOpenSettings: () => void }) {
  const [model, setModel] = useState<AgentModelState>();
  const [task, setTask] = useState("");
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [phases, setPhases] = useState<Record<string, string>>({});
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
      const response = await fetch("/api/runs/start/task", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ threadId: thread.id, task: text }) });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : `Could not start task (${response.status}).`);
      }
      agentRunSchema.parse(await response.json());
      setTask("");
      await refresh();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not start task."); }
    finally { setBusy(false); }
  };
  const stop = async (id: string) => {
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(id)}/stop`, { method: "POST" });
      if (!response.ok) throw new Error("Could not stop task.");
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
  const renderRun = (run: AgentRun) => {
    const approval = approvals.find((item) => item.runId === run.runId);
    return <article className="agent-workbench-run" key={run.runId}>
      <div className="agent-run-icon" data-status={run.status} aria-hidden="true">{run.status === "running" ? <LoaderCircle size={15} /> : run.status === "completed" ? <Check size={15} /> : <CircleAlert size={15} />}</div>
      <div className="agent-run-content">
        <div className="agent-workbench-run-top"><span className="agent-workbench-status" data-status={run.status}>{label(run.status)}</span><time dateTime={new Date(run.createdAt * 1000).toISOString()}>{new Date(run.createdAt * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</time></div>
        <h3>{run.task}</h3>
        {phases[run.runId] && <p role="status">{phases[run.runId]}</p>}
        {run.result && <details className="agent-run-result"><summary>View result <ChevronDown size={14} /></summary><p>{run.result}</p></details>}
        {approval && <div className="agent-workbench-approval"><strong>Approval needed</strong><p>{approval.summary}</p><button type="button" onClick={() => void decide(approval.approvalId, true)}>Approve</button><button type="button" onClick={() => void decide(approval.approvalId, false)}>Deny</button></div>}
        <div className="agent-workbench-actions"><button type="button" onClick={() => onOpenThread(run.threadId)}>Open conversation</button>{active(run) && <button type="button" onClick={() => void stop(run.runId)}><Square size={11} /> Stop</button>}</div>
      </div>
    </article>;
  };
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
