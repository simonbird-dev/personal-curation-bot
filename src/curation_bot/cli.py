from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import CurationBotError, ingest_link, status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="personal-curation-bot")
    parser.add_argument("--data-root", default="data", help="Local app data root. Defaults to ./data")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Queue a link into a category and create a draft package when threshold is reached.")
    ingest.add_argument("--category", required=True, help="Category, e.g. finds/live/fashion")
    ingest.add_argument("--url", required=True, help="URL or message containing a URL")
    ingest.add_argument("--source", default="cli", help="Source label, e.g. cli/telegram")

    sub.add_parser("status", help="Show local queue/package status.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    data_root = Path(args.data_root)

    try:
        if args.command == "ingest":
            result = ingest_link(
                category=args.category,
                text_or_url=args.url,
                data_root=data_root,
                source=args.source,
            )
            print(json.dumps(result.__dict__, indent=2, sort_keys=True))
            return 0
        if args.command == "status":
            print(json.dumps(status(data_root), indent=2, sort_keys=True))
            return 0
    except CurationBotError as exc:
        print(json.dumps({"error": exc.code, "message": exc.message}, indent=2, sort_keys=True))
        return 2

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
