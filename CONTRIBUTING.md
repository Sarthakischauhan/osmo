# Contributing

Thanks for contributing to Osmo. The project pairs Symphony's durable agent harness with a first-class chat SDK that speaks the harness protocol. Keep changes aligned with that separation: the harness owns agent execution and tools; the SDK owns protocol-aware interaction and presentation.

## Prerequisites

- [Bun](https://bun.sh/) and [uv](https://docs.astral.sh/uv/)
- Python 3.11 or newer and [uv](https://docs.astral.sh/uv/)
- An `XAI_API_KEY` for live voice sessions (tests do not require one)

## Setup

From the repository root:

```sh
make install
```

The `--no-sources` option preserves the vendored `core_server`'s upstream workspace metadata.

## Run locally

Start the Python server from the repository root with `make dev-server` (set `XAI_API_KEY` in the environment). It listens on `127.0.0.1:8787` by default. In a separate terminal, start the web client with `make dev-web` (or `make dev`):

```sh
make dev-web
```

Vite serves the client on port `5173` and proxies `/ws` and `/api` to the local server. You can override the proxy targets with `SYMPHONY_URL` (WebSocket) and `SYMPHONY_HTTP_URL` (HTTP).

## Checks

Run JavaScript workspace checks from the root:

```sh
make typecheck
make test
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
