.PHONY: install dev dev-web dev-server build typecheck test server-test server-check start dev-desktop desktop dist-desktop

install:
	bun install
	cd apps/symphony-server && uv venv -p 3.12 .venv && uv pip install --no-sources -p .venv -r requirements.txt

dev: dev-web

dev-web:
	bun run --filter @osmo/web dev

dev-server:
	cd apps/symphony-server && .venv/bin/python -m symphony_server

start:
	cd apps/symphony-server && .venv/bin/python -m symphony_server --host 0.0.0.0

build:
	bun run --filter @osmo/web build

typecheck:
	bun run --filter '*' typecheck

test:
	bun run --filter '*' test

server-test:
	cd apps/symphony-server && .venv/bin/python -m pytest -q

server-check:
	cd apps/symphony-server && .venv/bin/mypy

dev-desktop:
	bun run dev:desktop

desktop:
	bun run desktop

dist-desktop:
	bun run dist:desktop
