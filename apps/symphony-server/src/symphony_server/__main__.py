"""python -m symphony_server"""

from __future__ import annotations

import argparse
import logging
import os

import certifi
from dotenv import load_dotenv

# This Python build has no system CA bundle. Set the current Mozilla bundle before HTTP clients import.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import httpx
import uvicorn

from symphony_server.app import create_app, osmo_core_config, osmo_tools
from symphony_server.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Osmo's Symphony server")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8787")))
    args = parser.parse_args()
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    client = httpx.AsyncClient(timeout=10)
    tools = osmo_tools(client)
    app = create_app(settings, core_config=osmo_core_config(settings, tools), tools=tools, http_client=client)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
