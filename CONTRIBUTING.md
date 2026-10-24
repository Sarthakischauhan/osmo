# Contributing

Thanks for contributing to Osmo. The project pairs Symphony's durable agent harness with a first-class chat SDK that speaks the harness protocol. Keep changes aligned with that separation: the harness owns agent execution and tools; the SDK owns protocol-aware interaction and presentation.

## Prerequisites

- Node.js and pnpm (the repository pins pnpm in `package.json`)
- Python 3.11 or newer and [uv](https://docs.astral.sh/uv/)
- An `XAI_API_KEY` for live voice sessions (tests do not require one)

## Setup

From the repository root:

```sh
pnpm install
cd apps/symphony-server
uv venv -p 3.12 .venv
uv pip install --no-sources -p .venv -r requirements.txt
```

The `--no-sources` option preserves the vendored `core_server`'s upstream workspace metadata.

## Run locally

Start the server from `apps/symphony-server`:

```sh
XAI_API_KEY=... .venv/bin/python -m symphony_server
```

It listens on `127.0.0.1:8787` by default. In a separate terminal, from the repository root, start the web client:

```sh
pnpm dev:web
```

Vite serves the client on port `5173` and proxies `/ws` and `/api` to the local server. You can override the proxy targets with `SYMPHONY_URL` (WebSocket) and `SYMPHONY_HTTP_URL` (HTTP).

## Checks

Run JavaScript workspace checks from the root:

```sh
pnpm typecheck
pnpm test
```

Run Python server checks from `apps/symphony-server`:

```sh
.venv/bin/mypy
.venv/bin/python -m pytest -q
```

Server tests use a local fake realtime WebSocket and mocked HTTP; they do not need a network connection or API key.

## Architecture notes

- `apps/symphony-server` currently connects its voice session directly to the xAI Realtime API. The API key stays server-side.
- The separate `/api` text-run API is built on the vendored `core_server`.
- `packages/osmo-composer` owns microphone capture/playback, the orb, and the browser-side session transport.
- `packages/osmo-widgets` defines Osmo widget schemas and renderers and integrates them with the chat SDK.
- `apps/web` composes the browser UI with the chat SDK and Osmo packages.

Keep credentials out of source control and never expose provider keys in browser code.
