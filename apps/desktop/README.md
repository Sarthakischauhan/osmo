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
If a server already runs at `127.0.0.1:8787`, it is reused and left running on quit. Configure that
server with `OSMO_TOOLS=osmo,search,image,desktop` and `OSMO_DESKTOP_VISION_MODEL` yourself.
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

- **macOS:** grant Microphone, Screen Recording and Accessibility to Osmo (or the development
  Electron/Python processes if prompted); restart after granting Screen Recording.
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
Web assets and Python server source are included. **Python and its dependencies are not bundled**:
install the server requirements and PyAutoGUI in a local Python environment and set
`OSMO_DESKTOP_PYTHON` to its absolute interpreter path when launching the packaged app.
This is a developer distribution, not yet a standalone consumer installer. Signing, notarization,
a bundled Python runtime and automatic updates are follow-up release work.

`OSMO_DESKTOP_URL` attaches to an already-running loopback HTTP origin and disables managed server
startup; remote origins are rejected. `OSMO_DESKTOP_PYTHON` overrides the Python used for both
managed server startup and input. `OSMO_DESKTOP_VISION_MODEL` is read by the server, not the renderer.
