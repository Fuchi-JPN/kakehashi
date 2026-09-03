"""CLI: kakehashi serve / validate / version."""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .config import DEFAULT_CONFIG_PATH, ConfigStore


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kakehashi")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run proxy server")
    s.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=None)
    v = sub.add_parser("validate", help="validate config file")
    v.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    sub.add_parser("version", help="print version")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "version":
        print(__version__)
        return 0
    if args.cmd == "validate":
        try:
            ConfigStore(args.config)
            print("config OK:", args.config)
            return 0
        except Exception as e:
            print(f"config NG: {e}", file=sys.stderr)
            return 1
    if args.cmd == "serve":
        import logging
        import uvicorn
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(name)s %(levelname)s %(message)s")
        from .app import create_app
        app = create_app(args.config)
        store = app.state.store
        cfg = store.get()
        host = args.host or cfg.server.host
        port = args.port or int(os.environ.get("KAKEHASHI_PORT", cfg.server.port))
        uvicorn.run(app, host=host, port=port)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
