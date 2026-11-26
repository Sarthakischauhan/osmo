# Run Osmo locally

## Requirements

- [Bun](https://bun.sh/) and [uv](https://docs.astral.sh/uv/).
- The install target creates a Python 3.12 environment through uv.
- An xAI API key for the default live voice experience, added in the app. Tests need no provider keys.

## Install

```sh
git clone https://github.com/Sarthakischauhan/osmo.git
cd osmo
make install
```

## Start the app

Run these commands from the repository root in separate terminals:

```sh
# Terminal 1: Python server
make dev-server
```

```sh
# Terminal 2: web client
make dev-web
```

Open **http://localhost:5173**, connect xAI in the **Providers** panel, and start a voice session with the orb. Provider keys stay on the server. The `XAI_API_KEY` environment variable is not read.

The server listens on `127.0.0.1:8787`. Vite serves the client on port `5173` and proxies `/ws`, `/api`, `/voice-models`, and `/providers` to it. Override the targets with `SYMPHONY_URL` (WebSocket) and `SYMPHONY_HTTP_URL` (HTTP) if needed.

Try a small request first. Background-agent models and optional browser and desktop tools depend on your provider connections and configuration. A connected provider unlocks only its supported capabilities, not every feature.

## Keep your work

Threads and related records persist by default in `~/.config/osmo/osmo.db`. Set `OSMO_DATABASE_FILE` to choose another location. Provider credentials are kept separately in the server's secret store.

## Next steps

- [Desktop setup](../apps/desktop/README.md): orb window, computer support, OS permissions, and packaging.
- [Configuration](configuration.md): provider sign-in, tools, browser controls, notes, calendar, and music.
- [Architecture](architecture.md): how the client, server, Symphony, and Chat SDK fit together.
- [Contributing](../CONTRIBUTING.md): development commands and tests.
