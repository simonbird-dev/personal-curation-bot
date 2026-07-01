from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL = "apify/instagram-post-scraper"


class CaptureError(Exception):
    """Controlled capture failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CaptureRequest:
    source_url: str
    selected_index_1based: int
    stream: str | None = None
    category: str | None = None

    @property
    def shortcode(self) -> str:
        shortcode = extract_shortcode(self.source_url)
        if not shortcode:
            raise CaptureError("invalid_instagram_url", "Could not extract Instagram shortcode from source_url.")
        return shortcode


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_shortcode(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"instagram\.com/(?:p|reel|tv)/([^/?#]+)/?", url)
    return match.group(1) if match else None


def normalise_url_for_match(url: str | None) -> str:
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def load_dataset(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CaptureError("dataset_missing", f"Dataset file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CaptureError("dataset_bad_json", f"Dataset is not valid JSON: {path}: {exc}") from exc
    if not isinstance(data, list):
        raise CaptureError("dataset_not_list", "Expected Apify dataset JSON to be a list of item objects.")
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise CaptureError("dataset_bad_item", f"Dataset item at index {index} is not an object.")
    return data


def item_shortcode(item: dict[str, Any]) -> str | None:
    for key in ("shortCode", "shortcode", "code"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    for key in ("url", "inputUrl", "sourceUrl", "shortcodeUrl", "displayUrl"):
        value = item.get(key)
        found = extract_shortcode(value) if isinstance(value, str) else None
        if found:
            return found
    return None


def item_match_urls(item: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    for key in ("url", "inputUrl", "sourceUrl", "shortcodeUrl"):
        value = item.get(key)
        if isinstance(value, str) and value.startswith("http"):
            urls.add(normalise_url_for_match(value))
    return urls


def find_parent_item(dataset: list[dict[str, Any]], request: CaptureRequest) -> dict[str, Any]:
    target_shortcode = request.shortcode
    target_url_key = normalise_url_for_match(request.source_url)

    by_shortcode: dict[str, dict[str, Any]] = {}
    by_url: dict[str, dict[str, Any]] = {}
    for item in dataset:
        shortcode = item_shortcode(item)
        if shortcode:
            by_shortcode[shortcode] = item
        for url_key in item_match_urls(item):
            by_url[url_key] = item

    if target_shortcode in by_shortcode:
        return by_shortcode[target_shortcode]
    if target_url_key in by_url:
        return by_url[target_url_key]
    raise CaptureError("parent_item_not_found", f"No Apify dataset item matched shortcode {target_shortcode}.")


def choose_selected_media(parent: dict[str, Any], selected_index_1based: int) -> tuple[dict[str, Any], str, int]:
    if selected_index_1based < 1:
        raise CaptureError("invalid_selected_index", "selected_index_1based must be >= 1.")

    child_posts = parent.get("childPosts")
    if isinstance(child_posts, list) and child_posts:
        index_0 = selected_index_1based - 1
        if index_0 >= len(child_posts):
            raise CaptureError(
                "selected_index_out_of_range",
                f"Selected index {selected_index_1based} is out of range for {len(child_posts)} child posts.",
            )
        selected = child_posts[index_0]
        if not isinstance(selected, dict):
            raise CaptureError("selected_child_bad_shape", "Selected child post is not an object.")
        return selected, "childPosts", index_0

    if selected_index_1based == 1:
        return parent, "parent", 0

    raise CaptureError("selected_index_out_of_range", "Non-carousel parent media only supports selected_index_1based=1.")


def media_type_of(item: dict[str, Any]) -> str | None:
    value = item.get("type") or item.get("productType")
    return str(value) if value is not None else None


def has_http_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("http")


def first_image_present(item: dict[str, Any]) -> bool:
    if has_http_url(item.get("displayUrl")):
        return True
    images = item.get("images")
    return isinstance(images, list) and any(has_http_url(v) for v in images)


def build_sanitised_record(request: CaptureRequest, dataset: list[dict[str, Any]]) -> dict[str, Any]:
    parent = find_parent_item(dataset, request)
    selected, selected_path, selected_index_0based = choose_selected_media(parent, request.selected_index_1based)
    raw_child_posts = parent.get("childPosts")
    child_posts: list[Any] = raw_child_posts if isinstance(raw_child_posts, list) else []

    selected_video_present = has_http_url(selected.get("videoUrl"))
    selected_image_present = first_image_present(selected)

    return {
        "schema_version": "apify_selected_media_capture_record_v0_1",
        "generated_at": utc_now(),
        "capture_method": TOOL,
        "data_detail_level_required": "detailedData",
        "source": {
            "source_url": request.source_url,
            "source_shortcode": request.shortcode,
            "stream": request.stream,
            "category": request.category,
        },
        "parent": {
            "shortcode": item_shortcode(parent),
            "id": parent.get("id"),
            "url_present": has_http_url(parent.get("url")),
            "input_url_present": has_http_url(parent.get("inputUrl")),
            "owner_username": parent.get("ownerUsername"),
            "type": parent.get("type"),
            "product_type": parent.get("productType"),
            "timestamp": parent.get("timestamp"),
            "child_count": len(child_posts),
        },
        "selected_media": {
            "selected_index_1based": request.selected_index_1based,
            "selected_index_0based": selected_index_0based,
            "selected_path": selected_path,
            "shortcode": item_shortcode(selected),
            "id": selected.get("id"),
            "type": media_type_of(selected),
            "video_url_present": selected_video_present,
            "image_url_present": selected_image_present,
            "media_url_kind_for_future_capture": "videoUrl" if selected_video_present else ("image/displayUrl" if selected_image_present else "none"),
        },
        "quality_flags": {
            "mapped": True,
            "thumbnail_only_video_success": False,
            "first_slide_fallback": False,
            "raw_media_urls_redacted": True,
            "media_downloaded": False,
        },
    }


def record_filename(record: dict[str, Any]) -> str:
    source = record.get("source", {})
    selected = record.get("selected_media", {})
    shortcode = source.get("source_shortcode") if isinstance(source, dict) else None
    selected_index = selected.get("selected_index_1based") if isinstance(selected, dict) else None
    if not isinstance(shortcode, str) or not shortcode:
        raise CaptureError("record_missing_shortcode", "Capture record is missing source.source_shortcode.")
    if not isinstance(selected_index, int) or selected_index < 1:
        raise CaptureError("record_missing_selected_index", "Capture record is missing selected_media.selected_index_1based.")
    return f"{shortcode}-slide{selected_index}.json"


def write_capture_record(*, record: dict[str, Any], data_root: Path, category: str) -> Path:
    output_dir = data_root / "capture_records" / category
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / record_filename(record)
    output_path.write_text(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def capture_from_dataset(*, source_url: str, selected_slide: int, dataset_path: Path, data_root: Path, category: str, stream: str | None = None) -> Path:
    request = CaptureRequest(source_url=source_url, selected_index_1based=selected_slide, stream=stream, category=category)
    record = build_sanitised_record(request, load_dataset(dataset_path))
    return write_capture_record(record=record, data_root=data_root, category=category)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a sanitised selected-media capture record from an existing Apify detailedData dataset.")
    parser.add_argument("--dataset", type=Path, required=True, help="Path to existing Apify detailedData dataset JSON.")
    parser.add_argument("--source-url", required=True, help="Instagram post/reel URL.")
    parser.add_argument("--selected-slide", required=True, type=int, help="1-based selected slide/media index.")
    parser.add_argument("--category", required=True, help="Bot category, e.g. finds/live/fashion.")
    parser.add_argument("--stream", help="Optional stream label, e.g. /finds or /live.")
    parser.add_argument("--data-root", type=Path, default=Path("data"), help="Local app data root. Defaults to ./data")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        output_path = capture_from_dataset(
            source_url=args.source_url,
            selected_slide=args.selected_slide,
            dataset_path=args.dataset,
            data_root=args.data_root,
            category=args.category,
            stream=args.stream,
        )
    except CaptureError as exc:
        print(json.dumps({"error": exc.code, "message": exc.message}, indent=2, sort_keys=True))
        return 2
    print(json.dumps({"capture_record": str(output_path)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
