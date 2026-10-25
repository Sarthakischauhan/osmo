# One container: symphony-server (Python) serving the built web app and /ws.
FROM oven/bun:1.3.2 AS web
WORKDIR /repo
COPY package.json bun.lock tsconfig.base.json ./
COPY packages ./packages
COPY apps/web ./apps/web
RUN bun install --frozen-lockfile && bun run --filter @osmo/web build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /repo
COPY vendor ./vendor
COPY apps/symphony-server ./apps/symphony-server
WORKDIR /repo/apps/symphony-server
RUN uv venv /venv && uv pip install --no-sources -p /venv -r requirements.txt
COPY --from=web /repo/apps/web/dist /srv/web
ENV OSMO_STATIC_DIR=/srv/web PORT=8080
EXPOSE 8080
# XAI_API_KEY must come from the host's secret store at runtime; it is never baked in.
CMD ["sh", "-c", "/venv/bin/python -m symphony_server --host 0.0.0.0 --port ${PORT}"]
