import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import type { DesktopStatus } from './desktopBridge';

export function useDesktopSupport() {
  const bridge = window.osmoDesktop;
  const [status, setStatus] = useState<DesktopStatus>();
  const [pending, setPending] = useState(false);
  useEffect(() => {
    if (!bridge) return;
    let active = true;
    const unsubscribe = bridge.onStatus(value => { if (active) setStatus(value); });
    void bridge.status().then(value => { if (active) setStatus(value); }).catch(error => toast.error(String(error)));
    return () => { active = false; unsubscribe(); void bridge.setEnabled(false); };
  }, [bridge]);
  const toggle = async () => {
    if (!bridge) return;
    setPending(true);
    try { setStatus(await bridge.setEnabled(!status?.enabled)); }
    catch (error) { toast.error(error instanceof Error ? error.message : String(error)); }
    finally { setPending(false); }
  };
  return {
    available: !!bridge,
    status,
    pending,
    toggle,
  };
}

export function DesktopSupport({ desktop }: { desktop: ReturnType<typeof useDesktopSupport> }) {
  if (!desktop.available) return null;
  const enabled = desktop.status?.enabled ?? false;
  return <button type="button" className={`desktop-support${enabled ? ' active' : ''}`}
    aria-pressed={enabled} disabled={desktop.pending}
    title={enabled ? 'Screen and computer control active. Stop: Ctrl/Cmd+Shift+Escape' : 'Allow screen inspection and computer control'}
    onClick={() => void desktop.toggle()}>
    <span className="desktop-support-dot" aria-hidden />
    {enabled ? 'Stop computer support' : 'Computer support'}
  </button>;
}
