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
    paths = ensure_dirs(data_root, category)
    now = utc_now_iso()
    item_id = f"{now.replace(':', '').replace('+', 'Z')}-{slugify_url(url)}"
    item_path = paths["queue"] / f"{item_id}.json"

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
            created_at=now,
        )

    return IngestResult(
        category=category,
        queue_count=len(queue_items(data_root, category)),
        target_count=target_count,
        threshold_reached=threshold_reached,
        draft_package=draft_package,
        item_id=item_id,
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
    for index, item_path in enumerate(selected, start=1):
        item = json.loads(item_path.read_text(encoding="utf-8"))
        item["status"] = "packaged"
        item["package_id"] = package_id
        item["package_position"] = index
        item["processing"]["instagram_draft_status"] = "manual_package_ready"
        package_item_path = package_dir / f"{index:02d}-{item_path.name}"
        write_json(package_item_path, item)
        shutil.move(str(item_path), str(paths["archive"] / item_path.name))
        manifest_items.append({
            "position": index,
            "item_id": item["item_id"],
            "url": item["url"],
        })

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
