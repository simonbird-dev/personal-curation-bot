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


@dataclass(frozen=True)
class MediaDownloadResult:
    package_dir: str
    provider: str
    copied_count: int
    media_status: str
    copied_files: list[str]


@dataclass(frozen=True)
class PackageReadinessResult:
    package_dir: str
    package_ready_for_instagram_draft: bool
    media_status: str
    blockers: list[str]
    warnings: list[str]
    safe_next_step: str
    items: list[dict[str, Any]]


@dataclass(frozen=True)
class ManualReviewPackResult:
    package_dir: str
    review_pack_path: str
    caption_path: str
    checklist_path: str
    package_ready_for_instagram_draft: bool
    media_status: str
    blockers: list[str]
    safe_next_step: str


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


def load_media_manifest(package_dir: Path) -> dict[str, Any]:
    manifest_path = package_dir / "media_manifest.json"
    try:
        media_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CurationBotError("media_manifest_missing", f"Media manifest not found: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise CurationBotError("media_manifest_bad_json", f"Media manifest is not valid JSON: {manifest_path}: {exc}") from exc
    if not isinstance(media_manifest, dict) or media_manifest.get("schema_version") != "personal_curation_media_plan_v0_1":
        raise CurationBotError("unsupported_media_manifest", "Expected personal_curation_media_plan_v0_1.")
    if not isinstance(media_manifest.get("items"), list):
        raise CurationBotError("media_manifest_bad_shape", "Media manifest is missing items list.")
    return media_manifest


def select_media_plan_items(media_manifest: dict[str, Any], selected_shortcode: str | None = None) -> list[dict[str, Any]]:
    items = [item for item in media_manifest["items"] if isinstance(item, dict)]
    if selected_shortcode is None:
        if len(items) != 1:
            raise CurationBotError(
                "media_selection_required",
                "Media manifest has multiple items; provide a selected shortcode before using a single local fixture file.",
            )
        return items

    selected = []
    for item in items:
        selected_media = item.get("selected_media")
        if isinstance(selected_media, dict) and selected_media.get("shortcode") == selected_shortcode:
            selected.append(item)
    if not selected:
        raise CurationBotError("media_selection_not_found", f"No media plan item found for shortcode: {selected_shortcode}")
    return selected


def media_manifest_status(items: list[dict[str, Any]]) -> str:
    if items and all(item.get("status") == "downloaded" for item in items):
        return "media_downloaded"
    if any(item.get("status") == "downloaded" for item in items):
        return "media_partially_downloaded"
    return "media_not_downloaded"


def update_package_manifest_media_status(package_dir: Path, media_status: str) -> None:
    manifest_path = package_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    if not isinstance(manifest, dict):
        return
    manifest["media_status"] = media_status.replace("media_", "")
    downloaded_by_item_id = {}
    media_manifest = load_media_manifest(package_dir)
    for item in media_manifest["items"]:
        if isinstance(item, dict):
            downloaded_by_item_id[item.get("item_id")] = item.get("status")
    for item in manifest.get("items", []):
        if isinstance(item, dict) and item.get("item_id") in downloaded_by_item_id:
            item["media_status"] = downloaded_by_item_id[item.get("item_id")]
    write_json(manifest_path, manifest)


def expected_media_destination(package_dir: Path, expected_relative_path: str) -> Path:
    relative = Path(expected_relative_path)
    if relative.is_absolute() or not relative.parts or relative.parts[0] != "media" or ".." in relative.parts:
        raise CurationBotError("unsafe_media_path", "Expected media path must be a relative path under media/.")
    destination = package_dir / relative
    try:
        destination.resolve().relative_to((package_dir / "media").resolve())
    except ValueError as exc:
        raise CurationBotError("unsafe_media_path", "Expected media path must stay under the package media directory.") from exc
    return destination


def check_package_readiness(package_dir: Path) -> PackageReadinessResult:
    blockers: list[str] = []
    warnings: list[str] = []
    readiness_items: list[dict[str, Any]] = []

    manifest_path = package_dir / "manifest.json"

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        manifest = None
        blockers.append(f"Missing manifest.json at {manifest_path}")
    except json.JSONDecodeError as exc:
        manifest = None
        blockers.append(f"manifest.json is not valid JSON: {exc}")

    if isinstance(manifest, dict):
        if manifest.get("schema_version") != "personal_curation_draft_package_v0_1":
            blockers.append("manifest.json has unsupported schema_version.")
        if manifest.get("status") != "ready_for_manual_instagram_posting":
            warnings.append(f"Package manifest status is {manifest.get('status')!r}, not ready_for_manual_instagram_posting.")
    elif manifest is not None:
        blockers.append("manifest.json must be a JSON object.")

    try:
        media_manifest = load_media_manifest(package_dir)
    except CurationBotError as exc:
        media_manifest = None
        blockers.append(exc.message)

    existing_count = 0
    expected_count = 0
    if isinstance(media_manifest, dict):
        for index, item in enumerate(media_manifest.get("items", []), start=1):
            if not isinstance(item, dict):
                blockers.append(f"Media manifest item {index} is not a JSON object.")
                continue
            raw_selected_media = item.get("selected_media")
            selected_media: dict[str, Any] = raw_selected_media if isinstance(raw_selected_media, dict) else {}
            shortcode = selected_media.get("shortcode") or item.get("item_id") or f"item-{index}"
            expected_relative_path = item.get("expected_media_relative_path")
            readiness_item = {
                "position": item.get("position"),
                "item_id": item.get("item_id"),
                "shortcode": shortcode,
                "expected_media_relative_path": expected_relative_path,
                "manifest_status": item.get("status"),
                "file_exists": False,
            }
            if not isinstance(expected_relative_path, str):
                blockers.append(f"Missing expected media path for {shortcode}")
                readiness_items.append(readiness_item)
                continue
            expected_count += 1
            try:
                destination = expected_media_destination(package_dir, expected_relative_path)
            except CurationBotError as exc:
                blockers.append(f"Unsafe media path for {shortcode}: {exc.message}")
                readiness_items.append(readiness_item)
                continue
            readiness_item["expected_media_path"] = str(destination)
            readiness_item["file_exists"] = destination.exists() and destination.is_file()
            if readiness_item["file_exists"]:
                existing_count += 1
                readiness_item["actual_media_path"] = str(destination)
            else:
                blockers.append(f"Missing downloaded media for {shortcode}: {expected_relative_path}")
            readiness_items.append(readiness_item)

    if expected_count == 0:
        media_status = "media_not_downloaded"
    elif existing_count == expected_count:
        media_status = "media_downloaded"
    elif existing_count > 0:
        media_status = "media_partially_downloaded"
    else:
        media_status = "media_not_downloaded"

    package_ready = not blockers and media_status == "media_downloaded"
    if package_ready:
        safe_next_step = "Package is ready for the Instagram draft automation pre-flight; browser/account automation still requires a separate approved lane."
    elif media_status == "media_partially_downloaded":
        safe_next_step = "Run execute-media-download for the missing selected shortcode(s) before browser automation."
    elif any("manifest.json" in blocker for blocker in blockers):
        safe_next_step = "Repair or recreate the draft package manifest before media download or browser automation."
    elif any("media_manifest" in blocker or "Media manifest" in blocker for blocker in blockers):
        safe_next_step = "Create or repair the package media_manifest.json before media download or browser automation."
    else:
        safe_next_step = "Run execute-media-download for each selected media item before browser automation."

    return PackageReadinessResult(
        package_dir=str(package_dir),
        package_ready_for_instagram_draft=package_ready,
        media_status=media_status,
        blockers=blockers,
        warnings=warnings,
        safe_next_step=safe_next_step,
        items=readiness_items,
    )


def _load_package_manifest_for_review(package_dir: Path) -> dict[str, Any]:
    manifest_path = package_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CurationBotError("manifest_missing", f"Draft package manifest not found: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise CurationBotError("manifest_bad_json", f"Draft package manifest is not valid JSON: {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise CurationBotError("manifest_bad_shape", "Draft package manifest must be a JSON object.")
    if manifest.get("schema_version") != "personal_curation_draft_package_v0_1":
        raise CurationBotError("unsupported_manifest", "Expected personal_curation_draft_package_v0_1.")
    return manifest


def build_manual_caption(manifest: dict[str, Any]) -> str:
    """Build a safe default caption from local package metadata only."""
    category = str(manifest.get("category") or "curation")
    items = manifest.get("items", [])
    source_lines: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("url"):
                source_lines.append(str(item["url"]))
    if source_lines:
        return f"{category} draft prepared by personal curation bot.\n\nSources:\n" + "\n".join(source_lines) + "\n"
    return f"{category} draft prepared by personal curation bot.\n"


def build_manual_review_markdown(*, manifest: dict[str, Any], readiness: PackageReadinessResult, caption: str) -> str:
    lines = [
        "# Manual Instagram Posting Review Pack",
        "",
        "This is a local review artefact only. It does not log into Instagram, open a browser, call Apify live, download media, or publish/share anything.",
        "",
        f"- Package: `{manifest.get('package_id')}`",
        f"- Category: `{manifest.get('category')}`",
        f"- Item count: `{manifest.get('item_count')}`",
        f"- Media status: `{readiness.media_status}`",
        f"- Ready for Instagram draft automation: `{readiness.package_ready_for_instagram_draft}`",
        f"- Safe next step: {readiness.safe_next_step}",
        "",
        "## Caption draft",
        "",
        "```text",
        caption.rstrip(),
        "```",
        "",
        "## Media checklist",
        "",
    ]
    if readiness.items:
        for item in readiness.items:
            status_label = "present" if item.get("file_exists") else "missing"
            lines.append(f"- `{item.get('shortcode')}` — {status_label} — `{item.get('expected_media_relative_path')}`")
    else:
        lines.append("- No selected-media checklist items found.")
    if readiness.blockers:
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {blocker}" for blocker in readiness.blockers)
    if readiness.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in readiness.warnings)
    lines.extend([
        "",
        "## Boundary",
        "",
        "Do not run browser/account automation from this pack unless a separate approved lane confirms package readiness, account safety, and the stop-before-share boundary.",
        "",
    ])
    return "\n".join(lines)


def build_manual_review_pack(package_dir: Path) -> ManualReviewPackResult:
    """Create a human-readable local posting pack without touching any live account."""
    manifest = _load_package_manifest_for_review(package_dir)
    readiness = check_package_readiness(package_dir)
    caption = build_manual_caption(manifest)
    review_dir = package_dir / "manual_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    caption_path = review_dir / "caption.txt"
    checklist_path = review_dir / "media_checklist.json"
    review_pack_path = review_dir / "manual_review_pack.md"
    caption_path.write_text(caption, encoding="utf-8")
    checklist_payload = {
        "schema_version": "personal_curation_manual_review_checklist_v0_1",
        "package_id": manifest.get("package_id"),
        "category": manifest.get("category"),
        "package_ready_for_instagram_draft": readiness.package_ready_for_instagram_draft,
        "media_status": readiness.media_status,
        "blockers": readiness.blockers,
        "warnings": readiness.warnings,
        "safe_next_step": readiness.safe_next_step,
        "items": readiness.items,
        "boundary": "Local review pack only; no Instagram login, browser automation, Apify live call, external media download, or publish/share action performed.",
    }
    write_json(checklist_path, checklist_payload)
    review_pack_path.write_text(
        build_manual_review_markdown(manifest=manifest, readiness=readiness, caption=caption),
        encoding="utf-8",
    )
    return ManualReviewPackResult(
        package_dir=str(package_dir),
        review_pack_path=str(review_pack_path),
        caption_path=str(caption_path),
        checklist_path=str(checklist_path),
        package_ready_for_instagram_draft=readiness.package_ready_for_instagram_draft,
        media_status=readiness.media_status,
        blockers=readiness.blockers,
        safe_next_step=readiness.safe_next_step,
    )


def execute_media_download(
    *,
    package_dir: Path,
    provider: str | None,
    fixture_file: Path | None = None,
    selected_shortcode: str | None = None,
) -> MediaDownloadResult:
    """Execute an approved media-source adapter.

    Currently the only approved provider is local-fixture. This deliberately refuses
    implicit/live providers so later Apify or browser-backed downloaders cannot be
    smuggled in without an explicit provider boundary.
    """
    if not provider:
        raise CurationBotError("media_provider_required", "Refusing media download without an explicit approved provider.")
    if provider != "local-fixture":
        raise CurationBotError("unsupported_media_provider", f"Unsupported media provider: {provider}")
    if fixture_file is None:
        raise CurationBotError("fixture_file_required", "local-fixture provider requires --fixture-file.")
    if not fixture_file.exists() or not fixture_file.is_file():
        raise CurationBotError("fixture_file_missing", f"Fixture file not found: {fixture_file}")

    media_manifest = load_media_manifest(package_dir)
    selected_items = select_media_plan_items(media_manifest, selected_shortcode=selected_shortcode)
    copied_files = []
    for item in selected_items:
        expected_relative_path = item.get("expected_media_relative_path")
        if not isinstance(expected_relative_path, str):
            raise CurationBotError("unsafe_media_path", "Expected media path must be a relative path under media/.")
        destination = expected_media_destination(package_dir, expected_relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixture_file, destination)
        item["status"] = "downloaded"
        item["provider"] = provider
        item["source_fixture_file"] = str(fixture_file)
        item["actual_media_path"] = str(destination)
        item["actual_media_relative_path"] = expected_relative_path
        item["download_boundary"] = "Local fixture file copied only; no live Apify call, raw media URL, Instagram login, browser automation, or external download was performed."
        copied_files.append(str(destination))

    media_status = media_manifest_status([item for item in media_manifest["items"] if isinstance(item, dict)])
    media_manifest["status"] = media_status
    media_manifest["last_provider"] = provider
    media_manifest["important_boundary"] = "Media files marked downloaded only from an explicit approved provider. local-fixture copies a local test file and performs no live download."
    write_json(package_dir / "media_manifest.json", media_manifest)
    update_package_manifest_media_status(package_dir, media_status)
    return MediaDownloadResult(
        package_dir=str(package_dir),
        provider=provider,
        copied_count=len(copied_files),
        media_status=media_status,
        copied_files=copied_files,
    )


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
