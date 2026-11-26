# Configuration

Osmo's Python server owns provider credentials, tools, background runs, and persistence.
For local setup, start with [getting started](getting-started.md). See [architecture](architecture.md) for implementation boundaries.

## Provider keys: file or Infisical

Provider keys entered in **Connect provider** live only on the server, behind a `SecretStore`
(`apps/symphony-server/src/symphony_server/secret_store.py`). `OSMO_SECRET_STORE` picks one:

- `file` (default, for local use): one Fernet-encrypted file, `OSMO_CREDENTIALS_FILE`
  (default `~/.config/osmo/credentials.enc`), with the key from `OSMO_CREDENTIALS_KEY` or, when that is
  unset, a key generated once into `<OSMO_CREDENTIALS_FILE>.key` (mode 0600).
- `infisical`: an [Infisical](https://infisical.com) project, through the official Python SDK
  (`infisicalsdk`) with machine-identity universal auth.

The API never returns a key: `GET /providers` reports only whether each provider is connected, and
no key is ever logged. The `XAI_API_KEY` environment variable is not read; connect xAI in the app.

### Infisical setup

1. Create a project in Infisical (Secrets Management) and note its project ID (Project Settings) and
   the environment slug to use, for example `prod`.
2. Create a machine identity: Organization > Access Control > Machine Identities > Create. Universal
   Auth is configured by default; under it, select Add Client Secret and copy the Client ID and Client Secret.
3. Add the identity to the project (Project > Access Control > Machine Identities > Add Machine
   Identity to Project) with a role that can read, create, edit and delete secrets and folders under `/osmo`.
4. Run the server with:

   | Variable | Value |
   | --- | --- |
   | `OSMO_SECRET_STORE` | `infisical` |
   | `INFISICAL_CLIENT_ID` | the identity's Client ID |
   | `INFISICAL_CLIENT_SECRET` | its Client Secret |
   | `INFISICAL_PROJECT_ID` | the project ID |
   | `INFISICAL_ENV` | the environment slug, e.g. `prod` |
   | `INFISICAL_CACHE_TTL_S` | optional; seconds to cache reads, default `60` |
   | `INFISICAL_SITE_URL` | optional; default `https://app.infisical.com` (EU cloud: `https://eu.infisical.com`, or your self-hosted URL) |

The server logs in on first use and tracks the token's lifetime (`expiresIn`): it logs in again
60 seconds (or 10% of the lifetime, if shorter) before the token expires. If Infisical still
answers 401, it logs in again and retries the call once; a second failure returns 502. No restart is
needed when tokens expire, but the Client Secret itself must stay valid (TTL 0 means it never expires).

Each owner's credentials are cached in memory for `INFISICAL_CACHE_TTL_S` seconds (default 60),
and the cache is dropped on every connect or disconnect. A key changed directly in Infisical shows
up within that window.

### Folder layout

Each credential is a folder `/osmo/<owner>/<provider>` holding one secret, `KEY`, whose comment is the
auth kind (`api_key` or `oauth`); for example `/osmo/default/openrouter/KEY`. The owner is always
`default` today; it is a parameter of the store so per-user keys can come later. The folders are
created on first use, and disconnecting a provider deletes its folder.

### Provider sign-in

Grok, ChatGPT, and Claude offer core_ai subscription sign-in for background-agent text runs. Voice and images still require an API key. Subscription model availability depends on the provider and account.

When `OSMO_AGENT_MODEL` is unset, Osmo selects a connected subscription (Grok, then ChatGPT, then Claude), falling back to a connected text-provider API key and core_ai's default model for that provider. Set `OSMO_AGENT_MODEL` to choose explicitly. Connecting a provider unlocks only its supported capabilities: xAI is the default voice, agent, and image path; other providers may support text, voice, or vision only.

OpenRouter uses its documented PKCE key exchange. Vercel uses Sign in with Vercel (OAuth authorization code + PKCE) once an app is registered: set `OSMO_VERCEL_OAUTH_CLIENT_ID` and `OSMO_VERCEL_OAUTH_CLIENT_SECRET`, with callback `{origin}/providers/vercel/oauth/callback` and scopes `openid offline_access`.

## Tools and models

| Variable | Purpose |
| --- | --- |
| `OSMO_TOOLS` | Comma-separated categories: `search`, `image`, `osmo`, `vault`, `calendar`, `music`, `browser`, `desktop`. All except `browser` and `desktop` are enabled by default (the desktop app adds `desktop` when it starts its own server); `code` takes effect only where the macOS sandbox is available. |
| `EXA_API_KEY` | Lifts Exa's shared rate limit for web search. |
| `OSMO_IMAGE_MODEL` | core_ai image model; default `grok:grok-imagine-image-2.0`. |
| `OSMO_AGENT_MODEL` | Explicit background-agent model; when unset, uses the connected-provider selection above. |
| `OSMO_AGENT_MAX_TURNS` | Maximum turns per run; default `60`. |
| `XAI_BASE_URL` | Overrides the xAI endpoint. |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Optional tracing of each spoken turn to Langfuse. |
| `LANGFUSE_BASE_URL` | Langfuse URL for a non-EU host. |

See [desktop setup](../apps/desktop/README.md) for desktop tools and vision-model configuration.

## Background agents and browser use

The voice model handles quick answers, notes, the calendar, music and voice changes. It delegates work with `run_agent`, which returns a run ID immediately; `agent_status` and `cancel_agent` follow or cancel it. Talking over Osmo stops its speech, not the background run. See [architecture](architecture.md) for audio interruption handling.

No tool belongs to both sides. The voice model answers quick questions with `web_search` and owns notes, calendar and music tools; it hands longer work, anything to show on screen and anything on the computer to the background agent with `run_agent`. The agent has the widget tools (`render_widget`, `update_widget`), `computer` (with `desktop` in `OSMO_TOOLS` and computer support on in the desktop app) and, with `browser` in `OSMO_TOOLS`, the browser tools. Osmo detects Google Chrome, Chromium, Edge, or Brave (macOS app bundles, then PATH); `OSMO_BROWSER_PATH` overrides detection.

| Setting | Behavior |
| --- | --- |
| `OSMO_BROWSER_VISIBLE=1` | Opens a real browser window as well as streaming it; otherwise headless. |
| `OSMO_BROWSER_PROFILE=1` | Uses Osmo's persistent profile at `~/.config/osmo/browser-profile`. A folder path selects a different location. |
| `OSMO_BROWSER_RULES` | Per-site rules, for example `github.com=allow,news.example=ask,bank.example=deny`. |

Each session (thread) gets one browser, driven over CDP in its own process group and shown in a live widget. Later runs reuse the page; streaming continues between runs and the widget returns on reconnect. Tools include `browser_open`, `browser_read`, `browser_click`, `browser_type`, `browser_scroll`, `browser_back`, `browser_forward`, `browser_press` (one named key), `browser_select` (a dropdown option), and `browser_wait` (up to 10 seconds for text).

The browser closes on the widget's **Close** button, server shutdown, or after 30 minutes without use unless a run is active. Tool calls, fullscreen input, opening the thread, and a run ending count as use. Ending a session closes the browser after any active run finishes and stores its result; reconnecting on that thread before then keeps it open. Startup reaps browser processes left by a killed server.

### Takeover, profiles, and site rules

In fullscreen you can click, scroll, and type yourself. **Take over** stops every agent browser tool until **Hand back**. `browser_read` lists controls without their values and marks password, one-time-code, and card fields sensitive; `browser_type` refuses those fields so you handle logins and payments.

The persistent profile is Osmo's, never your everyday Chrome profile. While one thread holds it, others get throwaway profiles. Logins you make persist for the agent too, including future runs. On a site you took over on, an `allow` rule no longer skips approval.

Rules cover subdomains; `*.site` covers only subdomains, and the longest matching pattern wins:

- `allow`: clicks, typing, and key presses can proceed without asking, subject to the takeover exception above.
- `ask`: always asks for those actions.
- `deny`: prevents opening the site, leaves redirects there immediately, and refuses browser tools on the page.

The widget shows Trusted or Blocked status.

### Approvals and status

Tools named in `OSMO_APPROVAL_TOOLS` wait for **Approve / Deny** in the app (`POST /api/approvals/{id}`). Defaults are `browser_click`, `browser_type`, `browser_press`, `vault_write`, and `automation_create`. Voice tool calls use the same approval list. Your own fullscreen input needs no approval; agent browser actions also follow site rules.

The voice model is notified and interrupted when the agent needs approval, finishes, or fails. The current step appears in the widget and `agent_status`'s `phase`.

## Persistence and recovery

Threads, messages, agent runs, and provider connections are kept in SQLite at `OSMO_DATABASE_FILE`, default `~/.config/osmo/osmo.db`. Records survive restart. Provider keys live separately in the secret store.

Durable runs store submissions, events, and conversation checkpoints after each model turn in `runs.db` beside that database. After a crash, kill, or upgrade, the next start resumes recoverable runs with the same run ID and session, asking the agent to check its work before continuing. The card shows **Resumed after Osmo restarted**. Cancelled runs do not resume; a run is abandoned after three restarts, and unrecoverable runs are marked failed. Finished runs are kept for 30 days.

Thread API: `GET/POST /api/threads`, `GET/PATCH/DELETE /api/threads/{id}`, and `POST /api/threads/{id}/fork`.

## Notes and vaults

`OSMO_VAULTS` is a comma-separated list of `name=path` Markdown folders. By default, Osmo creates `notes` at `~/Osmo/Notes`; an empty value disables notes. The SQLite FTS5 index lives at `OSMO_VAULT_INDEX_FILE` (default `~/.config/osmo/vault-index.db`), refreshes by mtime on startup, and stays current through a file watcher, including edits from other editors.

The voice model gets `vault_search`, `vault_read`, and `vault_write` in the `vault` category; the background agent does not. Writes require approval by default. Paths must be relative to the vault: absolute paths, `..`, hidden folders, non-Markdown files, and escaping symlinks are refused. Reads and writes open each path component from the vault root with `O_NOFOLLOW` and reject notes with multiple hard links, protecting against symlink swaps after validation.

The Notes screen lists, searches, and edits files with CodeMirror. APIs are `GET /api/vault/notes`, `GET /api/vault/search`, and `GET/PUT /api/vault/note`. Your own saves need no approval. While `vault_write` requires approval, only the desktop app holding the per-launch token can save through the API; the agent cannot bypass its tool approval via the route.

## Calendar and automations

Events and automations share the thread database. Events carry an IANA time zone and optional RRULE; recurrence expands in wall-clock time so weekly times stay consistent across DST. `OSMO_TIMEZONE` sets the tools' time zone (default: the machine's). Import and export `.ics` at `POST /api/calendar/import` and `GET /api/calendar/export.ics`.

Automations fire at a time, optionally repeating, or at an event start/end plus an offset. They can start a background task, raise a notification, or append to a note. Agent tasks are durable runs on threads named after the automation. Tasks links back to the automation and event in Calendar and supports stopping runs; the automation panel lists recent tasks. The automation run stays `started` until completion, then becomes `ok` or `error`. `GET /api/automations/tasks` lists task links.

The server scheduler skips jobs more than 10 minutes overdue instead of running them late. **Pause all** creates no backlog. The desktop tray shows current and upcoming work, notifies runs, and pauses/resumes automations. Closing the window leaves the app and automations running; Osmo does not add itself to login items.

The voice model gets `calendar_events` (`show` renders a Today agenda), `calendar_add_event`, and approval-gated `automation_create`.

## Music

`OSMO_MUSIC_DIRS` selects local audio folders (default `~/Music`). Mutagen reads tags and covers; file serving supports Range requests and cannot leave those folders. `music_play` searches local files and Spotify and sends a queue to the app; `music_control` pauses, resumes, skips, or changes volume; `music_status` reports playback.

Playback happens in the client, with a sidebar mini player and shared state. During speech, volume ducks to one quarter over 150 ms and returns over 400 ms.

Spotify requires Premium and your own Spotify developer app:

1. Create an app at developer.spotify.com and register `http://127.0.0.1:<port>/api/music/spotify/callback` as a redirect.
2. In the desktop app, choose **Connect Spotify** in the sidebar, paste the app's Client ID and choose **Save & connect**. Or set `OSMO_SPOTIFY_CLIENT_ID`; it takes precedence and cannot be changed from the app.
3. PKCE needs no client secret; tokens remain in Osmo's database. Changing the Client ID forgets the previous sign-in.

Spotify requires Widevine and plays only in the desktop app, built on castLabs Electron for Content Security. Local files also play in a plain browser. See [music and Widevine](../apps/desktop/README.md#music-and-widevine).
