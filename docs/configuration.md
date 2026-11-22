# Configuration

Osmo's Python server owns provider credentials, tools, background runs, and persistence.
For local setup, start with the [README](../README.md#run-locally).

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

OpenRouter uses its documented PKCE key exchange. Vercel uses Sign in with Vercel
(OAuth authorization code + PKCE) once an app is registered: set `OSMO_VERCEL_OAUTH_CLIENT_ID`
and `OSMO_VERCEL_OAUTH_CLIENT_SECRET`, with callback `{origin}/providers/vercel/oauth/callback`
and scopes `openid offline_access`. Without them Vercel takes an API key. xAI, OpenAI, and Gemini
also use API keys; they do not document a third-party OAuth flow that gives Osmo the Realtime or
inference access it needs.

## Tools and models

| Variable | Purpose |
| --- | --- |
| `OSMO_TOOLS` | Comma-separated tool categories: `search`, `image`, `osmo`, `browser`; everything except `browser` by default. See [desktop setup](../apps/desktop/README.md) for desktop tools. |
| `EXA_API_KEY` | Lifts Exa's shared rate limit for web search. |
| `OSMO_IMAGE_MODEL` | core_ai image model; default `grok:grok-imagine-image-2.0`. |
| `OSMO_AGENT_MODEL` | core_ai background agent model; default `grok:grok-4.7`. |
| `XAI_BASE_URL` | Overrides the xAI endpoint. |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Optional tracing of each spoken turn to Langfuse. |
| `LANGFUSE_BASE_URL` | Langfuse URL for a non-EU host. |

## Background agents and browser use

The voice model keeps widgets, voice changes, and computer support. It hands web search to a
background agent with `run_agent`, which returns a run ID immediately. `agent_status` and
`cancel_agent` follow or cancel it. Talking over Osmo stops its speech, not the background run.

The agent has `web_search`. To enable its browser tools, include `browser` in `OSMO_TOOLS` and
set `OSMO_BROWSER_PATH` to a Chrome or Chromium executable. This adds `browser_open`,
`browser_read`, `browser_click`, `browser_type`, and `browser_scroll`.

Each Osmo session (thread) gets one headless Chrome, driven over CDP in its own process group,
shown live in an agent browser widget. Later runs reuse the same page. The widget keeps streaming
between runs and is restored when a client reconnects. In fullscreen, you can click, scroll,
and type into the page yourself.

The browser closes:

- When you press the widget's **Close** button.
- After 30 minutes without use, unless a run is still using it. Tool calls, fullscreen input,
  opening the thread, and a run ending count as use.
- When the session ends. If a run is still going, it finishes in the background first and stores
  its result with the thread; the browser closes afterward. A new session on the thread before
  then keeps it open.
- When the server shuts down. On startup, the server also reaps Chrome processes left by a killed server.

### Approvals and status

Agent tools named in `OSMO_APPROVAL_TOOLS` wait for **Approve / Deny** in the app
(`POST /api/approvals/{id}`). The default is `browser_click,browser_type`. Your own input in the
fullscreen browser needs no approval; the agent's actions still do.

The voice model is notified, and interrupted, when the agent needs approval, finishes, or fails.
The agent's current step appears only in the widget and in `agent_status`'s `phase`.

## Persistence

Threads, messages, agent runs, and provider connections are kept in SQLite at `OSMO_DATABASE_FILE`.
The database is in memory when this is unset; set a file path to keep it across server restarts.
Provider keys are stored separately in the secret store described above.

The thread REST API is:

- `GET/POST /api/threads`
- `GET/PATCH/DELETE /api/threads/{id}`
- `POST /api/threads/{id}/fork`
