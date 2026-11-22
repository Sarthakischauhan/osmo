import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { Clock } from "./Clock";

type TopbarProps = {
  title: string;
  /** When the call started (shows its clock), if one is going. */
  startedAt: number | undefined;
  connected: boolean;
  onEnd: () => void;
};

/** The conversation's top bar: the title, the call's clock and its End button. */
export function Topbar({ title, startedAt, connected, onEnd }: TopbarProps) {
  const sidebar = useSidebar();
  return (
    <header className="topbar">
      <div className="brand">
        {sidebar.state === "collapsed" && <SidebarTrigger aria-label="Expand sidebar" className="osmo-icon-btn" />}
        <img className="brand-mark" src="/favicon.svg" alt="" width={18} height={18} />
        <strong>{title}</strong>
      </div>
      <div className="topbar-right">
        {startedAt !== undefined && <Clock since={startedAt} />}
        {connected && (
          <button type="button" className="end" onClick={onEnd}>
            <span aria-hidden>✕</span> End
          </button>
        )}
      </div>
    </header>
  );
}
