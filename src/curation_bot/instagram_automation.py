from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class InstagramAutomationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DraftAutomationPlan:
    package_id: str
    category: str
    item_count: int
    status: str
    next_action: str


def load_manifest(package_dir: Path) -> dict[str, Any]:
    manifest_path = package_dir / "manifest.json"
    if not manifest_path.exists():
        raise InstagramAutomationError("missing_manifest", f"No manifest found at {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "personal_curation_draft_package_v0_1":
        raise InstagramAutomationError("unsupported_manifest", "Unsupported draft package schema.")
    return manifest


def plan_browser_draft_automation(package_dir: Path) -> DraftAutomationPlan:
    """Validate a ready package and return the next safe automation step.

    This deliberately does not log into Instagram, read cookies, or click UI.
    It is the boundary between the local bot core and the later browser/device
    automation runner.
    """
    manifest = load_manifest(package_dir)
    if manifest.get("status") != "ready_for_manual_instagram_posting":
        raise InstagramAutomationError("package_not_ready", "Package is not marked ready for Instagram preparation.")
    item_count = int(manifest.get("item_count", 0))
    if item_count < 1:
        raise InstagramAutomationError("empty_package", "Package contains no items.")
    return DraftAutomationPlan(
        package_id=str(manifest["package_id"]),
        category=str(manifest["category"]),
        item_count=item_count,
        status="ready_for_browser_automation_spike",
        next_action="Open dedicated Instagram browser profile, require Simon/manual login if needed, upload prepared media, stop before Share/Post.",
    )
