"""python -m core_server"""

from __future__ import annotations

import argparse

import uvicorn
from dotenv import load_dotenv

from core_server.app import create_app
from core_server.config import build_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Symphony core-server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--model", default=None, help="Model id (default: OPENAI_MODEL)")
    parser.add_argument(
        "--system-prompt",
        default=None,
        help="Override the default system prompt",
    )
    args = parser.parse_args()
    load_dotenv(override=True)
    kwargs = {}
    if args.system_prompt is not None:
        kwargs["system_prompt"] = args.system_prompt
    config = build_config(model_id=args.model, **kwargs)
    uvicorn.run(create_app(config), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
