"""Entry point: `python -m agent`."""

from __future__ import annotations

import argparse
import asyncio


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal WhatsApp AI Agent")
    parser.add_argument("--config", default="config/config.yaml", help="Path to config file")
    parser.add_argument("--demo", action="store_true", help="Run offline demo simulation (spec §65)")
    args = parser.parse_args()

    if args.demo:
        from agent.main import run_demo
    else:
        from agent.main import run_agent

    try:
        asyncio.run(run_demo(args.config) if args.demo else run_agent(args.config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()