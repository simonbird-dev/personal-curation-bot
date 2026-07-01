from __future__ import annotations

import argparse
import json
from pathlib import Path

from .apify_capture import CaptureError, capture_from_dataset
from .core import CurationBotError, check_package_readiness, execute_media_download, ingest_capture_record, ingest_link, status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="personal-curation-bot")
    parser.add_argument("--data-root", default="data", help="Local app data root. Defaults to ./data")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Queue a link into a category and create a draft package when threshold is reached.")
    ingest.add_argument("--category", required=True, help="Category, e.g. finds/live/fashion")
    ingest.add_argument("--url", required=True, help="URL or message containing a URL")
    ingest.add_argument("--source", default="cli", help="Source label, e.g. cli/telegram")

    capture = sub.add_parser("capture-apify", help="Create a sanitised selected-media capture record from an existing Apify detailedData dataset and queue it.")
    capture.add_argument("--category", required=True, help="Category, e.g. finds/live/fashion")
    capture.add_argument("--dataset", type=Path, required=True, help="Existing Apify detailedData dataset JSON. This command does not call Apify live.")
    capture.add_argument("--source-url", required=True, help="Instagram post/reel URL.")
    capture.add_argument("--selected-slide", type=int, required=True, help="1-based selected slide/media index.")
    capture.add_argument("--stream", help="Optional stream label, e.g. /finds or /live")

    capture_record = sub.add_parser("ingest-capture-record", help="Queue an already-created sanitised capture record.")
    capture_record.add_argument("--category", required=True, help="Category, e.g. finds/live/fashion")
    capture_record.add_argument("--capture-record", type=Path, required=True, help="Path to apify_selected_media_capture_record_v0_1 JSON")
    capture_record.add_argument("--source", default="apify_capture", help="Source label")

    media = sub.add_parser("execute-media-download", help="Execute an explicit approved media provider for a prepared package. Currently only local-fixture is supported.")
    media.add_argument("--package", dest="package_dir", type=Path, required=True, help="Draft package directory containing media_manifest.json")
    media.add_argument("--provider", required=True, help="Approved provider. Currently: local-fixture")
    media.add_argument("--fixture-file", type=Path, help="Local fixture file to copy when provider=local-fixture")
    media.add_argument("--selected-shortcode", help="Required when the package has multiple media items and one fixture file is supplied")

    readiness = sub.add_parser("check-package-readiness", help="Check whether a prepared package has all media files needed before Instagram draft automation.")
    readiness.add_argument("--package", dest="package_dir", type=Path, required=True, help="Draft package directory containing manifest.json and media_manifest.json")

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
        if args.command == "capture-apify":
            capture_record_path = capture_from_dataset(
                source_url=args.source_url,
                selected_slide=args.selected_slide,
                dataset_path=args.dataset,
                data_root=data_root,
                category=args.category,
                stream=args.stream,
            )
            result = ingest_capture_record(
                category=args.category,
                capture_record_path=capture_record_path,
                data_root=data_root,
            )
            print(json.dumps({"capture_record": str(capture_record_path), **result.__dict__}, indent=2, sort_keys=True))
            return 0
        if args.command == "ingest-capture-record":
            result = ingest_capture_record(
                category=args.category,
                capture_record_path=args.capture_record,
                data_root=data_root,
                source=args.source,
            )
            print(json.dumps(result.__dict__, indent=2, sort_keys=True))
            return 0
        if args.command == "execute-media-download":
            result = execute_media_download(
                package_dir=args.package_dir,
                provider=args.provider,
                fixture_file=args.fixture_file,
                selected_shortcode=args.selected_shortcode,
            )
            print(json.dumps(result.__dict__, indent=2, sort_keys=True))
            return 0
        if args.command == "check-package-readiness":
            result = check_package_readiness(package_dir=args.package_dir)
            print(json.dumps(result.__dict__, indent=2, sort_keys=True))
            return 0
        if args.command == "status":
            print(json.dumps(status(data_root), indent=2, sort_keys=True))
            return 0
    except (CurationBotError, CaptureError) as exc:
        print(json.dumps({"error": exc.code, "message": exc.message}, indent=2, sort_keys=True))
        return 2

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
