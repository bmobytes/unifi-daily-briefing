from __future__ import annotations

import argparse

import uvicorn

from .config import get_settings
from .service import BriefingService


def main() -> None:
    parser = argparse.ArgumentParser(prog="unifi-daily-briefing")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect")
    sub.add_parser("report")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()

    if args.command == "serve":
        uvicorn.run("unifi_daily_briefing.web:app", host=args.host, port=args.port)
        return

    service = BriefingService(get_settings())
    if args.command == "collect":
        result = service.collect()
    else:
        result = service.generate_report()
    print(result)


if __name__ == "__main__":
    main()
