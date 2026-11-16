"""papi sync [source ...]   pull data into SQLite
papi serve               run the HTTP API
papi mcp                 run the MCP server over stdio (for AI agents)
"""

from __future__ import annotations

import argparse
import sys

from .config import load_config
from .db import connect


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="papi", description="Personal API")
    parser.add_argument("--config", help="path to config.toml (default: ./config.toml or $PAPI_CONFIG)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="run collectors (all enabled, or the ones named)")
    p_sync.add_argument("sources", nargs="*")

    p_serve = sub.add_parser("serve", help="run the API server")
    p_serve.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1 (local only)")
    p_serve.add_argument("--port", type=int, default=8000)

    sub.add_parser("mcp", help="run the MCP server over stdio")

    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.command == "sync":
        from .collectors import COLLECTORS, sync

        unknown = set(args.sources) - set(COLLECTORS)
        if unknown:
            parser.error(f"unknown source(s): {', '.join(sorted(unknown))}; "
                         f"available: {', '.join(COLLECTORS)}")
        conn = connect(config["database"]["path"])
        summary = sync(conn, config, args.sources or None)
        if not summary:
            print("No sources enabled. Enable some under [sources.*] in config.toml.")
            return 1
        for name, result in summary.items():
            if result["ok"]:
                print(f"{name}: {result['records']} records")
            else:
                print(f"{name}: FAILED - {result['error']}", file=sys.stderr)
        return 0 if all(r["ok"] for r in summary.values()) else 1

    if args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(config), host=args.host, port=args.port)

    if args.command == "mcp":
        from .mcp_server import create_server

        create_server(config).run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
