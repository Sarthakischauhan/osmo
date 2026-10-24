# core-server

A FastAPI service for creating harness runs and streaming their events over Server-Sent Events (SSE). Import the application from `core_server`; the package is currently workspace-only and is not published on PyPI.

## Run from the repository

```sh
uv sync
uv run --package core-server core-server
```

The default bind address is `127.0.0.1:8000`. Do not expose the service to an untrusted network without adding authentication; the server has no built-in authentication. See [SECURITY.md](../SECURITY.md).

## Continue

- [Server guide](../docs/packages/core-server.md): endpoints, run lifecycle, sessions, and request policy.
- [CLI reference](../docs/reference/cli.md#core-server)
- [Harness events](../docs/developer-guide/events.md)
- [Source installation](../docs/getting-started/installation.md)
