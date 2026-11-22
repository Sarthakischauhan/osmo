// The tray (menu bar) item that keeps Osmo's automations visible while the window is closed. The server, and with it
// the scheduler, runs for as long as the app does; closing the window only goes back to the orb or the tray, and
// Osmo never registers itself to start at login. The tray reads the scheduler's state over the local API, shows a
// notification for each automation run, and offers Pause all / Resume all.

export const TRAY_POLL_MS = 20_000;

/** "Today, 3:00 PM · in 3 min" for the next run, as GUI's tray mockup has it. */
export function nextLabel(iso, now = new Date()) {
  const at = new Date(iso);
  const time = at.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  const sameDay = at.toDateString() === now.toDateString();
  const day = sameDay ? 'Today' : at.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
  const minutes = Math.round((at.getTime() - now.getTime()) / 60_000);
  return `${day}, ${time}${minutes >= 0 && minutes < 120 ? ` · in ${minutes} min` : ''}`;
}

/** The tray menu's template for a scheduler status (`GET /api/automations/status`), or for a server that is not up. */
export function trayTemplate(status, actions, now = new Date()) {
  if (!status) {
    return [
      { label: 'Osmo is starting…', enabled: false },
      { type: 'separator' },
      { label: 'Open Osmo', click: actions.open },
      { label: 'Quit Osmo', click: actions.quit },
    ];
  }
  const running = status.paused ? 'Paused' : status.running > 0 ? `${status.running} running` : 'Idle';
  const next = status.next && status.next.nextRunAt && !status.paused
    ? [{ label: 'Next', enabled: false }, { label: status.next.title, enabled: false }, { label: nextLabel(status.next.nextRunAt, now), enabled: false }]
    : [{ label: 'Nothing scheduled', enabled: false }];
  return [
    { label: 'Running', enabled: false },
    { label: running, enabled: false },
    { type: 'separator' },
    ...next,
    { type: 'separator' },
    { label: status.paused ? 'Resume all' : 'Pause all', click: () => actions.pause(!status.paused) },
    { label: 'Open calendar', click: actions.openCalendar },
    { label: 'Open Osmo', click: actions.open },
    { type: 'separator' },
    { label: 'Quit Osmo', click: actions.quit },
  ];
}

/** What a finished automation run says in a notification; nothing for one that only started an agent run's thread. */
export function runNotice(run) {
  if (run.status === 'skipped') return { title: `${run.title} was skipped`, body: run.detail };
  if (run.status === 'error') return { title: `${run.title} failed`, body: run.detail };
  if (run.status === 'started') return { title: run.title, body: 'Started in the background. Its result lands in a new thread.' };
  return { title: run.title, body: run.detail };
}

/**
 * The tray: `request(path, init)` calls the local server and resolves to parsed JSON; `createTray(menu)` makes the
 * tray item and returns `{ setContextMenu, setToolTip, destroy }`; `buildMenu(template)` and `notify({ title, body })`
 * are Electron's. Returns `{ refresh, stop }`.
 */
export function createAutomationTray({ request, createTray, buildMenu, notify, open, openCalendar, quit, every = setInterval, stopEvery = clearInterval }) {
  let last;
  let status;
  const tray = createTray();
  const actions = {
    open,
    openCalendar,
    quit,
    pause: async paused => {
      try { status = await request('/api/automations/pause', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ paused }) }); }
      catch { /* the next refresh shows what the server has */ }
      render();
    },
  };
  const render = () => {
    tray.setContextMenu(buildMenu(trayTemplate(status, actions)));
    tray.setToolTip(status ? `Osmo · automations ${status.paused ? 'paused' : 'on'}` : 'Osmo');
  };
  async function refresh() {
    try {
      status = await request('/api/automations/status');
      if (last === undefined) last = status.last;
      else if (status.last > last) {
        const listed = await request(`/api/automations/runs?after=${last}`);
        for (const run of listed.runs) notify(runNotice(run));
        last = listed.last;
      }
    } catch {
      status = undefined;
    }
    render();
  }
  render();
  const timer = every(() => void refresh(), TRAY_POLL_MS);
  // The poll never keeps the process alive on its own (quitting is the app's decision).
  timer?.unref?.();
  void refresh();
  return { refresh, stop: () => { stopEvery(timer); tray.destroy(); } };
}
