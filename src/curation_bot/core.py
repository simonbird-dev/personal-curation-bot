from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://\S+")
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "categories.json"


class CurationBotError(Exception):
    """Stable app-level error with a short code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class IngestResult:
    category: str
    queue_count: int
    target_count: int
    threshold_reached: bool
    draft_package: str | None
    item_id: str


def load_capture_record(path: Path) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CurationBotError("capture_record_missing", f"Capture record not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CurationBotError("capture_record_bad_json", f"Capture record is not valid JSON: {path}: {exc}") from exc
    if not isinstance(record, dict):
        raise CurationBotError("capture_record_bad_shape", "Capture record must be a JSON object.")
    if record.get("schema_version") != "apify_selected_media_capture_record_v0_1":
        raise CurationBotError("unsupported_capture_record", "Expected apify_selected_media_capture_record_v0_1.")
    source = record.get("source")
    selected = record.get("selected_media")
    quality = record.get("quality_flags")
    if not isinstance(source, dict) or not isinstance(selected, dict) or not isinstance(quality, dict):
        raise CurationBotError("capture_record_bad_shape", "Capture record is missing source, selected_media, or quality_flags.")
    if quality.get("raw_media_urls_redacted") is not True:
        raise CurationBotError("unsafe_capture_record", "Capture record must redact raw media URLs before bot intake.")
    if not isinstance(source.get("source_url"), str) or not source["source_url"]:
        raise CurationBotError("capture_record_missing_source_url", "Capture record is missing source.source_url.")
    return record


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_categories(config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, dict[str, Any]]:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise CurationBotError("invalid_config", "Category config must be a non-empty object.")
    for name, cfg in data.items():
        if not isinstance(cfg, dict) or not isinstance(cfg.get("target_count"), int) or cfg["target_count"] < 1:
            raise CurationBotError("invalid_config", f"Category {name!r} needs target_count >= 1.")
    return data


def extract_first_url(text: str) -> str:
    match = URL_RE.search(text)
    if not match:
        raise CurationBotError("missing_url", "Message does not contain a URL.")
    return match.group(0).rstrip(").,]")


def validate_supported_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    supported_hosts = {"instagram.com", "pin.it", "pinterest.com"}
    if host not in supported_hosts and not host.endswith(".instagram.com") and not host.endswith(".pinterest.com"):
        raise CurationBotError("unsupported_url", f"Unsupported host: {parsed.netloc}")
    return url


def slugify_url(url: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", urlparse(url).path.strip("/"))
    return slug.strip("-")[:48] or "link"


def ensure_dirs(data_root: Path, category: str) -> dict[str, Path]:
    paths = {
        "queue": data_root / "queues" / category,
        "drafts": data_root / "draft_packages" / category,
        "archive": data_root / "archive" / category,
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def queue_items(data_root: Path, category: str) -> list[Path]:
    queue_dir = data_root / "queues" / category
    if not queue_dir.exists():
        return []
    return sorted(queue_dir.glob("*.json"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def expected_media_extension(selected_media: dict[str, Any]) -> str:
    kind = str(selected_media.get("media_url_kind_for_future_capture") or "")
    media_type = str(selected_media.get("type") or "").lower()
    if kind == "videoUrl" or "video" in media_type:
        return ".mp4"
    if "image" in kind or "image" in media_type:
        return ".jpg"
    return ".media"


def build_media_plan_entry(*, item: dict[str, Any], position: int, package_dir: Path) -> dict[str, Any] | None:
    selected_media = item.get("selected_media")
    if not isinstance(selected_media, dict):
        return None
    shortcode = selected_media.get("shortcode") or item.get("item_id")
    safe_shortcode = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(shortcode)).strip("-") or f"item-{position:02d}"
    expected_relative_path = Path("media") / f"{position:02d}-{safe_shortcode}{expected_media_extension(selected_media)}"
    return {
        "position": position,
        "item_id": item["item_id"],
        "selected_media": selected_media,
        "capture_record_path": item.get("capture_record_path"),
        "expected_media_path": str(package_dir / expected_relative_path),
        "expected_media_relative_path": expected_relative_path.as_posix(),
        "status": "not_downloaded",
        "download_boundary": "No live Apify call, raw media URL, Instagram login, browser automation, or media download has been performed for this package.",
    }


def queue_item_and_maybe_package(
    *,
    category: str,
    item: dict[str, Any],
    data_root: Path,
    config_path: Path,
    created_at: str,
) -> IngestResult:
    categories = load_categories(config_path)
    if category not in categories:
        raise CurationBotError("unknown_category", f"Unknown category: {category}")

    paths = ensure_dirs(data_root, category)
    item_path = paths["queue"] / f"{item['item_id']}.json"
    write_json(item_path, item)

    items = queue_items(data_root, category)
    target_count = categories[category]["target_count"]
    draft_package = None
    threshold_reached = len(items) >= target_count
    if threshold_reached:
        draft_package = create_draft_package(
            category=category,
            data_root=data_root,
            target_count=target_count,
            created_at=created_at,
        )

    return IngestResult(
        category=category,
        queue_count=len(queue_items(data_root, category)),
        target_count=target_count,
        threshold_reached=threshold_reached,
        draft_package=draft_package,
        item_id=item["item_id"],
    )


def ingest_link(
    *,
    category: str,
    text_or_url: str,
    data_root: Path,
    config_path: Path = DEFAULT_CONFIG_PATH,
    source: str = "cli",
) -> IngestResult:
    categories = load_categories(config_path)
    if category not in categories:
        raise CurationBotError("unknown_category", f"Unknown category: {category}")

    url = validate_supported_url(extract_first_url(text_or_url))
    now = utc_now_iso()
    item_id = f"{now.replace(':', '').replace('+', 'Z')}-{slugify_url(url)}"
    item = {
        "schema_version": "personal_curation_link_item_v0_1",
        "item_id": item_id,
        "category": category,
        "source": source,
        "url": url,
        "status": "queued",
        "created_at": now,
        "processing": {
            "capture_status": "pending",
            "media_status": "not_downloaded",
            "instagram_draft_status": "not_attempted",
        },
    }
    return queue_item_and_maybe_package(
        category=category,
        item=item,
        data_root=data_root,
        config_path=config_path,
        created_at=now,
    )


def ingest_capture_record(
    *,
    category: str,
    capture_record_path: Path,
    data_root: Path,
    config_path: Path = DEFAULT_CONFIG_PATH,
    source: str = "apify_capture",
) -> IngestResult:
    categories = load_categories(config_path)
    if category not in categories:
        raise CurationBotError("unknown_category", f"Unknown category: {category}")

    record = load_capture_record(capture_record_path)
    record_source = record["source"]
    selected_media = record["selected_media"]
    url = validate_supported_url(record_source["source_url"])
    now = utc_now_iso()
    shortcode = record_source.get("source_shortcode") or slugify_url(url)
    selected_index = selected_media.get("selected_index_1based")
    item_id = f"{now.replace(':', '').replace('+', 'Z')}-{shortcode}-slide{selected_index}"
    item = {
        "schema_version": "personal_curation_link_item_v0_1",
        "item_id": item_id,
        "category": category,
        "source": source,
        "url": url,
        "status": "queued",
        "created_at": now,
        "capture_record_path": str(capture_record_path),
        "selected_media": {
            "selected_index_1based": selected_media.get("selected_index_1based"),
            "shortcode": selected_media.get("shortcode"),
            "type": selected_media.get("type"),
            "media_url_kind_for_future_capture": selected_media.get("media_url_kind_for_future_capture"),
        },
        "processing": {
            "capture_status": "captured",
            "media_status": "not_downloaded",
            "instagram_draft_status": "not_attempted",
        },
    }
    return queue_item_and_maybe_package(
        category=category,
        item=item,
        data_root=data_root,
        config_path=config_path,
        created_at=now,
    )


def create_draft_package(*, category: str, data_root: Path, target_count: int, created_at: str | None = None) -> str:
    created_at = created_at or utc_now_iso()
    paths = ensure_dirs(data_root, category)
    items = queue_items(data_root, category)
    if len(items) < target_count:
        raise CurationBotError("threshold_not_met", f"Need {target_count} items, have {len(items)}.")

    selected = items[:target_count]
    package_id = f"{created_at.replace(':', '').replace('+', 'Z')}-{category}-{target_count}-items"
    package_dir = paths["drafts"] / package_id
    package_dir.mkdir(parents=True, exist_ok=False)

    manifest_items = []
    media_plan_items = []
    for index, item_path in enumerate(selected, start=1):
        item = json.loads(item_path.read_text(encoding="utf-8"))
        item["status"] = "packaged"
        item["package_id"] = package_id
        item["package_position"] = index
        item["processing"]["instagram_draft_status"] = "manual_package_ready"
        package_item_path = package_dir / f"{index:02d}-{item_path.name}"
        write_json(package_item_path, item)
        shutil.move(str(item_path), str(paths["archive"] / item_path.name))
        manifest_item = {
            "position": index,
            "item_id": item["item_id"],
            "url": item["url"],
        }
        if item.get("capture_record_path"):
            manifest_item["capture_record_path"] = item["capture_record_path"]
        if isinstance(item.get("selected_media"), dict):
            manifest_item["selected_media"] = item["selected_media"]
            media_plan_entry = build_media_plan_entry(item=item, position=index, package_dir=package_dir)
            if media_plan_entry is not None:
                media_plan_items.append(media_plan_entry)
                manifest_item["expected_media_relative_path"] = media_plan_entry["expected_media_relative_path"]
                manifest_item["media_status"] = media_plan_entry["status"]
        manifest_items.append(manifest_item)

    manifest = {
        "schema_version": "personal_curation_draft_package_v0_1",
        "package_id": package_id,
        "category": category,
        "created_at": created_at,
        "item_count": len(manifest_items),
        "status": "ready_for_manual_instagram_posting",
        "important_boundary": "This is a local prepared package, not an Instagram native app draft and not an automated post.",
        "items": manifest_items,
    }
    if media_plan_items:
        media_dir = package_dir / "media"
        media_dir.mkdir(exist_ok=True)
        media_plan = {
            "schema_version": "personal_curation_media_plan_v0_1",
            "package_id": package_id,
            "category": category,
            "created_at": created_at,
            "status": "media_not_downloaded",
            "important_boundary": "This file is a storage/download contract only. It contains expected local media paths and selected-media metadata, not raw media URLs or downloaded files.",
            "items": media_plan_items,
        }
        write_json(package_dir / "media_manifest.json", media_plan)
        manifest["media_manifest_path"] = str(package_dir / "media_manifest.json")
        manifest["media_status"] = "not_downloaded"
    write_json(package_dir / "manifest.json", manifest)
    return str(package_dir)


def status(data_root: Path, config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    categories = load_categories(config_path)
    result = {}
    for category, cfg in categories.items():
        result[category] = {
            "queue_count": len(queue_items(data_root, category)),
            "target_count": cfg["target_count"],
            "draft_package_count": len(list((data_root / "draft_packages" / category).glob("*"))) if (data_root / "draft_packages" / category).exists() else 0,
        }
    return result
