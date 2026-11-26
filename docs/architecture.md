# Architecture

Osmo combines a voice-first client, a Python session server, Symphony's agent execution, and a protocol-aware Chat SDK. The [README](../README.md) is the product tour; [configuration](configuration.md) covers runtime behavior and integrations.

## Responsibilities

| Part | Responsibility |
| --- | --- |
| `apps/web` | Composes the conversation, canvas, Tasks, notes, calendar, and provider UI. |
| `apps/desktop` | Electron host, orb window, audio bridge, and consent-gated native computer support. |
| `packages/osmo-composer` | Microphone capture, playback, orb, and browser-side session transport. |
| `packages/osmo-widgets` | Widget schemas and renderers integrated with Chat SDK. |
| `apps/symphony-server` | Provider credentials, live voice sessions, tools, background runs, persistence, and integrations. |
| Symphony / core_ai | Agent turns, tools, model integration, and execution events. |
| Chat SDK | Protocol-aware conversation rendering and interactive presentation. |

## Voice and session flow

The client captures speech and streams audio to the Python session endpoint. The server's default voice path connects directly to xAI Realtime; this is separate from the background-agent model selection. Model events drive tool execution, validated widget payloads, transcripts, audio, and client activity state. Widget interactions return through the session as user actions.

The protocol separates execution from presentation: the server coordinates work, while the client renders structured events rather than scraping output. The separate `/api` text-run interface uses the vendored `core_server`.

## Interruptions and audio

Talking over Osmo ends its speech, not its background work. The client detects roughly 150 ms of speech, cuts playback locally, and tells the server to interrupt. Audio from that answer still arriving is discarded until the server confirms the interruption.

`packages/osmo-composer/src/echoGuard.ts` keeps playback from being mistaken for user speech: microphone frames are held back unless they stand out from the assistant's audio. While music plays, only speech that stands out from it reaches server turn detection; other frames become silence. The client also ducks music during spoken responses.


## Spotify integration: handoff by default, Spotify Connect when authorized

Osmo **does not stream Spotify audio or create a DRM-protected playback device**. Local files remain playable in the embedded music player. The optional connected Spotify mode sends play, pause, resume and volume requests to the user's *existing Spotify client* through server-side Spotify Web API calls. OAuth PKCE access tokens remain encrypted server-side and are no longer sent to the browser renderer.

For a user without a connected account or a playable search result, `music_play` emits `music.command(action="handoff", query=...)`. The sidebar offers a **user-clickable** Spotify search link. Handoff opens a search page; it does not guarantee a track starts playing and cannot automatically control the app.

Connected control requires Spotify Premium, an accessible Spotify playback device, and Spotify API access for the OAuth app. As of November 2026, development-mode Spotify apps are limited to five authorized users; public deployment needs approved expanded access. If the device is unavailable, the player reports that Spotify needs to be opened.

Osmo only reports playback as accepted after the local audio engine or Spotify Connect endpoint succeeds. Spotify remote audio cannot be ducked from the embedded renderer, and provider-side turn detection still operates independently of music playback.

## Local Silero VAD

Osmo uses **Silero VAD v5 locally** for speech evidence during assistant playback and music. It shares the microphone stream with the 24 kHz PCM capture, and uses the bundled ONNX model, worklet, and WASM assets under \`/vad/\`. No model weights or microphone audio need to be downloaded or uploaded for local VAD.

- **Osmo silent:** audio flows to the provider continuously. Silero does not decide whether a user turn ends and does not suppress quiet speech.
- **Osmo speaking:** the echo guard buffers pre-roll. Silero-positive frames enable an interruption when they also clear a conservative acoustic-level check. Osmo immediately cuts playback.
- **Music:** Silero-positive speech passes with pre-roll; music-only frames are replaced by silence. Provider VAD still decides turn boundaries.
- **Unavailable model/WASM:** falls back to the existing RMS echo guard without stopping the voice session.
- **Permissions:** one \`getUserMedia\` stream is shared; stopping a session closes it once.

The realtime provider still handles transcription, text responses, and turn detection. Silero **does not** perform speech-to-text. For production testing, compare quiet voice, music, speaker leakage, barge-ins, and lost-model startup in both browser and packaged desktop builds.

## Voice diagnostics and missing transcription

Audio is captured in 100 ms PCM16 frames. EchoGuard gates during assistant playback to avoid self-interruption, but it must **never** suppress the first words after playback stops. When playing music, the guard can still suppress low-volume speech; pause music if debugging quiet speech.

For an opt-in, privacy-safe trace (frame counts only, no PCM or transcripts in these counters):

- Web: open Osmo with `?debugAudio=1` in the URL, then check the browser console for captured/forwarded/held frame counts.
- Desktop: launch with `OSMO_AUDIO_DEBUG=1`; the hidden audio renderer logs capture/forward/held counters to its console, and the Python server logs frames forwarded to the voice provider.
- The server logs provider speech-start, speech-stop and transcript-length at DEBUG level. Missing transcripts after 10 seconds and provider voice errors now surface to the client as `voice.error`.

If capture increases but forwarded stays flat, investigate EchoGuard. If forwarded increases but provider speech events do not arrive, check provider connection and VAD. If a transcript reaches the server but does not render, inspect the client event/parser path.

## Notes as agent context and cross-references

The voice model reads the configured notes vaults **by default** with `vault_search` and `vault_read`; `vault_write` needs approval to create, append or replace a note. The background agent has no notes or calendar tools: when its result should be saved, the voice model saves it.

The Notes view is the only interface. Notes support YAML frontmatter tags for filtering and discovery.

Notes can refer to other Osmo records using explicit links:
`[[chat:THREAD_ID]]`, `[[automation:AUTOMATION_ID]]`, and `[[event:EVENT_ID]]`.
The editor displays these as clickable reference chips, navigating to a stored chat or its calendar record. Reference content is treated as untrusted data, not a new instruction. Lookups stay bounded and on the user's existing local storage. Agent results should mention source titles and IDs.

Agents can pick up work described in the notes when asked, without a separate issue database or issues tab.

## Background work

The voice model delegates with `run_agent` and continues the conversation. Tasks have independent status and cancellation. Approval requests, completion, and failures notify and interrupt the voice model; intermediate steps remain in the task UI and status API.

Durable runs checkpoint conversation and execution events to disk so recoverable work can resume after restart. Browser processes belong to a thread and can outlive individual runs. Calendar automations start ordinary durable runs rather than a separate execution system.

See [configuration](configuration.md) for recovery limits, retention, browser lifecycle, and scheduling semantics.

## Trust and storage boundaries

- Provider keys stay in the server's secret store, separately from conversation records.
- Threads and integrations use local SQLite storage; durable execution has its own adjacent run database.
- Browser actions use approvals, site rules, and explicit takeover controls. A persistent Osmo browser profile can carry user logins into later agent runs.
- Vault tools validate paths and enforce approval for writes by default.
- Desktop computer support requires native consent and acts on the user's primary display, not a server desktop.

These are different boundaries, not one universal sandbox. Review [configuration](configuration.md) and [desktop controls](../apps/desktop/README.md#use) before enabling tools with real-world effects.
