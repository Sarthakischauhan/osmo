# Osmo desktop

Electron hosts the existing Osmo voice UI, with an isolated preload bridge for opt-in computer
support. Symphony still owns the tool loop. The native app operates the **primary display**;
it does not automate the server's desktop.

## Run from source

Install the repository's prerequisites (Bun, uv, Python 3.12), then:

```sh
make install
cd apps/symphony-server
uv pip install -p .venv 'pyautogui>=0.9.54,<1'
cd ../..
make build
OSMO_DESKTOP_VISION_MODEL=openai:gpt-4.1 bun run desktop
```

The app starts a loopback Python server, serves the built web UI, and stops its server on quit.
It generates `OSMO_DESKTOP_TOKEN` for that process. A server already running on the port is reused
only when it echoes that same token; another healthy local server is not trusted with native access.
Connect the voice and vision providers through the existing Providers UI. No keys go into Electron.
`openai:gpt-4.1` is an example image-capable text model; any compatible connected core_ai model can
be selected. A realtime voice model is not a vision model.

For hot reload, run the backend separately with desktop tools enabled, then:

```sh
# Terminal 1 (from repository root)
OSMO_TOOLS=osmo,search,image,desktop OSMO_DESKTOP_VISION_MODEL=openai:gpt-4.1 make dev-server
# Terminal 2
bun run dev:desktop
```

## Sidebar and appearance

Electron uses the same persistent thread sidebar as the web app: create, reopen, rename, fork
and delete threads. The footer adds provider connections, native computer support, and Day/Night
appearance. Collapsing the sidebar keeps computer support available in the header.

Models and voices are chosen by hovering or keyboard-focusing the orb. There is no separate
model picker pill. The home screen follows the local time through dawn, morning, midday,
dusk, evening and night, with shared monospace artwork and reduced-motion support.

Changing threads ends the active voice session and revokes native control. Stored threads and
background runs keep PR #14's persistence and approval behavior.

## Two windows, one session

The app has the full window and a small always-on-top **orb** window (`/#orb` of the same page). Only
one shows at a time, and the orb is what the app opens to. Main owns the voice session (the one
Symphony WebSocket, and its audio); both windows only ask for it and listen, over one typed IPC
contract (`src/session-contract.js`, mirrored in `src/preload.cjs`). Switching windows, or reloading
one, never ends or duplicates the session.

- Minimizing or closing the full window, or **Cmd/Ctrl+Shift+O**, hides it and shows the orb.
  Closing also revokes computer support; **Quit** in the Osmo menu quits.
- Double-clicking the orb, or the same shortcut, shows the full window and hides the orb.
- The orb starts 24 px in from the lower right of the work area. Hovering it opens the agent log
  below it, or above it when there is no room below.

- A single click on the orb starts the session only after the double-click window has passed, so a
  double-click only expands. No platform API reports the double-click interval (the DOM has none, and
  Electron has none), so `apps/web/src/singleClick.ts` uses a fixed 500 ms.
- The orb adopts the thread the workspace has open (reported over `workspace:thread`); the workspace
  window is the only sender. With no thread open, the session starts a new one.

**Audio.** Electron's main process has no way to capture a microphone or play sound (no
`getUserMedia`, no Web Audio, no Node audio API), so main keeps a third, hidden window (`/#audio` of
the same page, `show: false`) whose only job is audio. It reuses the composer's `MicCapture` and
`PcmPlayer`, and talks to main over the same typed contract: `audio:start`/`audio:stop` and
`audio:play`/`audio:flush` come from main, `audio:started` and `audio:frame` (PCM16 mic frames) go
back. Main implements `AudioPort` (`src/audio-port.js`) over those messages; the socket and session
stay in main, so the call survives switching windows. Only that window may use the microphone
(`allowMicrophone` in `src/policy.js`, checked against its web contents). If it crashes, the call
ends and the page reloads. Output levels and previews do not cross the contract, so the orb's level
stays flat in Electron.

## Use

1. Start a voice session with the orb.
2. Click **Computer support**, then approve the native dialog.
3. Ask, for example, “Look at my screen and help me find the browser settings.”
4. Stop with **Stop computer support**, the Osmo menu, or **Ctrl/Cmd+Shift+Escape** globally.
   Ending the voice session, disconnecting, reloading, or closing the window also revokes control.

`computer.observe` takes a fresh screenshot and asks the configured vision model a specific
question. Only its textual observation is passed back to the voice model. Images are sent to the
vision provider and are not added to conversation history by this tool. The voice model can then
request click, type, key, and scroll operations directly or delegate a longer task with run_agent.
The background run receives only its originating desktop session's capability; disconnecting or
revoking consent stops pending native work without granting another thread control.

The app rejects requests unless native consent is active, validates each input, serializes native
operations, rejects iframe IPC, and invalidates in-flight operations when control stops. The stop
shortcut also kills a running input subprocess. Moving the pointer to a screen corner triggers
PyAutoGUI's failsafe. These mechanisms do not roll back input already sent.

## OS setup and limits

- **macOS:** grant Screen Recording and Accessibility to a signed Osmo build, then quit (Cmd+Q)
  and reopen it. Unsigned builds lose the grants; in development, grant your terminal instead. See
  [docs/desktop-signing.md](../../docs/desktop-signing.md).
- **Windows:** use a Python interpreter with PyAutoGUI installed.
- **Linux:** X11 is required for input. Wayland input is not supported in this first version.
- Primary display only. Click coordinates are normalized relative to the full screenshot,
  mapped to logical display bounds so Retina scaling does not double the coordinates.
- Typing currently supports ASCII, newline and tab. Dragging, secondary monitors, arbitrary
  scripts, shell execution, and background wake-word listening are not implemented.
- The first version uses visual grounding, not an accessibility tree. Observe after each action
  and confirm the outcome. The tool instructs the model to ask before consequential actions;
  it does not enforce a per-action approval dialog.

## Build installers

```sh
bun run dist:desktop
# Or create an unpacked app for testing:
bun run --filter @osmo/desktop pack
```

Build on the target OS. Electron Builder emits DMG, NSIS, or AppImage under `apps/desktop/release`.
Web assets, Python server source, and a uv-managed CPython 3.12 runtime with the server and
PyAutoGUI dependencies are included. The packaged app uses that runtime unless
`OSMO_DESKTOP_PYTHON` overrides it. On macOS, run `bun run --filter @osmo/desktop signing:create`
once so local builds are signed with a stable self-signed identity
([docs/desktop-signing.md](../../docs/desktop-signing.md)). Developer ID signing, notarization, and
automatic updates are still follow-up release work.

`OSMO_DESKTOP_URL` attaches to an already-running loopback HTTP origin and disables managed server
startup; remote origins are rejected. `OSMO_DESKTOP_PYTHON` overrides the Python used for both
managed server startup and input. `OSMO_DESKTOP_VISION_MODEL` is read by the server, not the renderer.
