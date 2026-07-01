from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import check_package_readiness
from .instagram_accounts import AccountConfigError, InstagramAccountRef, get_active_account, set_active_account
from .paths import SCREENSHOTS_DIR, ensure_runtime_dirs


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


@dataclass(frozen=True)
class DraftMedia:
    media_paths: tuple[Path, ...]
    caption: str


@dataclass(frozen=True)
class DraftRunResult:
    status: str
    account_id: str
    screenshot_path: Path
    detail: str


SUPPORTED_MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"}


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
    readiness = check_package_readiness(package_dir)
    if not readiness.package_ready_for_instagram_draft:
        detail = readiness.safe_next_step
        if readiness.blockers:
            detail = f"{detail} Blockers: {'; '.join(readiness.blockers)}"
        raise InstagramAutomationError("package_media_not_ready", detail)
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


def _manifest_caption(manifest: dict[str, Any]) -> str:
    category = str(manifest.get("category", "curation"))
    items = manifest.get("items", [])
    source_lines = []
    if isinstance(items, list):
        for item in items[:10]:
            if isinstance(item, dict) and item.get("url"):
                source_lines.append(str(item["url"]))
    source_block = "\n".join(source_lines)
    if source_block:
        return f"{category} draft prepared by personal curation bot.\n\nSources:\n{source_block}"
    return f"{category} draft prepared by personal curation bot."


def _media_candidates(package_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for root_name in ["media", "selected-media", "uploads"]:
        root = package_dir / root_name
        if root.exists():
            for path in sorted(root.iterdir()):
                if path.is_file() and path.suffix.lower() in SUPPORTED_MEDIA_EXTENSIONS:
                    candidates.append(path)
    return candidates


def resolve_draft_media(package_dir: Path, media_paths: list[Path] | None = None, caption: str | None = None) -> DraftMedia:
    manifest = load_manifest(package_dir)
    selected = [path.expanduser().resolve() for path in (media_paths or _media_candidates(package_dir))]
    if not selected:
        raise InstagramAutomationError(
            "missing_media",
            "No uploadable media found. Add files under package/media/ or pass --media /path/to/image-or-video.",
        )
    bad = [path for path in selected if not path.exists() or not path.is_file()]
    if bad:
        raise InstagramAutomationError("media_not_found", f"Media file does not exist: {bad[0]}")
    unsupported = [path for path in selected if path.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS]
    if unsupported:
        raise InstagramAutomationError("unsupported_media", f"Unsupported media type: {unsupported[0].suffix}")
    return DraftMedia(media_paths=tuple(selected), caption=caption or _manifest_caption(manifest))


def _active_or_named_account(account_id: str | None) -> InstagramAccountRef:
    if account_id:
        return set_active_account(account_id)
    try:
        return get_active_account()
    except AccountConfigError:
        return set_active_account("test")


def _require_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise InstagramAutomationError(
            "playwright_missing",
            "Playwright is not installed in this environment, so browser/account automation cannot run. Install it only in an approved setup lane.",
        ) from exc
    return PlaywrightTimeoutError, sync_playwright


def check_instagram_session(account_id: str | None = None) -> DraftRunResult:
    """Headless session check with minimal screenshot evidence.

    This intentionally avoids saving page text/logs because the logged-in home page
    can expose private feed content.
    """
    _, sync_playwright = _require_playwright()

    ensure_runtime_dirs()
    ref = _active_or_named_account(account_id)
    screenshot = SCREENSHOTS_DIR / f"instagram-draft-session-check-{ref.account_id}.png"
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(ref.profile_dir),
            headless=True,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        body = page.locator("body").inner_text(timeout=5000).lower()
        page.screenshot(path=str(screenshot), full_page=False)
        context.close()

    login_page = "log into instagram" in body or "phone number, username or email" in body or "forgot password" in body
    if login_page:
        return DraftRunResult(
            status="not_logged_in",
            account_id=ref.account_id,
            screenshot_path=screenshot,
            detail="Instagram showed the login screen for this local browser profile.",
        )
    return DraftRunResult(
        status="probably_logged_in",
        account_id=ref.account_id,
        screenshot_path=screenshot,
        detail="Instagram did not show the login screen; safe draft automation can be attempted.",
    )


def prepare_instagram_draft(
    *,
    package_dir: Path,
    media_paths: list[Path] | None = None,
    caption: str | None = None,
    account_id: str | None = None,
    headless: bool = True,
) -> DraftRunResult:
    """Upload prepared media into Instagram web UI and stop before final Share/Post.

    Safety boundary: this function may click Create/New post and Next through the
    upload/crop/filter stages, but it must not click final Share/Post. It stops on
    the review/caption screen and saves a screenshot.
    """
    ensure_runtime_dirs()
    plan_browser_draft_automation(package_dir)
    draft_media = resolve_draft_media(package_dir, media_paths=media_paths, caption=caption)
    PlaywrightTimeoutError, sync_playwright = _require_playwright()
    ref = _active_or_named_account(account_id)
    screenshot = SCREENSHOTS_DIR / f"instagram-draft-ready-{ref.account_id}.png"

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(ref.profile_dir),
            headless=headless,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.instagram.com/create/select/", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        body = page.locator("body").inner_text(timeout=8000).lower()
        login_url = "/accounts/login" in page.url.lower()
        login_body = "log into instagram" in body or "forgot password" in body
        blank_login_redirect = login_url and not body.strip()
        if login_url or login_body:
            page.screenshot(path=str(screenshot), full_page=False)
            context.close()
            detail = "Instagram redirected this browser profile to login; run terminal-login before attempting a draft."
            if blank_login_redirect:
                detail = "Instagram redirected this browser profile to a blank login shell; run terminal-login before attempting a draft."
            return DraftRunResult(
                status="not_logged_in",
                account_id=ref.account_id,
                screenshot_path=screenshot,
                detail=detail,
            )

        try:
            file_input = page.locator('input[type="file"]').first
            file_input.set_input_files([str(path) for path in draft_media.media_paths], timeout=15000)
        except PlaywrightTimeoutError as exc:
            page.screenshot(path=str(screenshot), full_page=False)
            context.close()
            return DraftRunResult(
                status="upload_control_not_found",
                account_id=ref.account_id,
                screenshot_path=screenshot,
                detail=f"Could not find Instagram upload file input: {exc}",
            )

        # Instagram usually requires two Next clicks: crop/selection -> edit/filter -> caption/review.
        for _ in range(2):
            try:
                page.get_by_role("button", name="Next").click(timeout=15000)
                page.wait_for_timeout(2500)
            except Exception:
                break

        # Fill caption if the field is exposed. Failure here is non-fatal: the key boundary is no final Share click.
        for selector in ['textarea[aria-label*="caption"]', 'textarea', '[contenteditable="true"]']:
            try:
                locator = page.locator(selector).first
                locator.fill(draft_media.caption, timeout=2500)
                break
            except Exception:
                continue

        page.screenshot(path=str(screenshot), full_page=False)
        body = page.locator("body").inner_text(timeout=8000).lower()
        context.close()

    if "share" in body or "write a caption" in body or "caption" in body:
        return DraftRunResult(
            status="stopped_before_share",
            account_id=ref.account_id,
            screenshot_path=screenshot,
            detail="Draft flow reached the review/caption area and stopped before final Share/Post.",
        )
    return DraftRunResult(
        status="stopped_unclear_screen",
        account_id=ref.account_id,
        screenshot_path=screenshot,
        detail="Automation uploaded/advanced as far as it safely could, but the final screen was unclear. It did not click Share/Post.",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    check_cmd = sub.add_parser("check-session", help="Check whether the active Instagram browser profile appears logged in.")
    check_cmd.add_argument("--account-id")

    plan_cmd = sub.add_parser("plan", help="Validate a local draft package for browser automation.")
    plan_cmd.add_argument("package_dir")

    draft_cmd = sub.add_parser("prepare-draft", help="Prepare an Instagram web draft and stop before Share/Post.")
    draft_cmd.add_argument("package_dir")
    draft_cmd.add_argument("--media", action="append", default=[], help="Media file to upload. Can be supplied multiple times.")
    draft_cmd.add_argument("--caption", help="Caption override. Defaults to package source summary.")
    draft_cmd.add_argument("--account-id")
    draft_cmd.add_argument("--headed", action="store_true", help="Use a visible browser; requires DISPLAY.")

    args = parser.parse_args()
    try:
        if args.command == "check-session":
            result = check_instagram_session(account_id=args.account_id)
        elif args.command == "plan":
            plan = plan_browser_draft_automation(Path(args.package_dir))
            print(json.dumps(plan.__dict__, indent=2))
            return 0
        elif args.command == "prepare-draft":
            result = prepare_instagram_draft(
                package_dir=Path(args.package_dir),
                media_paths=[Path(path) for path in args.media] or None,
                caption=args.caption,
                account_id=args.account_id,
                headless=not args.headed,
            )
        else:
            return 2
    except InstagramAutomationError as exc:
        print(f"error={exc.code} detail={exc.message}")
        return 2

    print(f"status={result.status}")
    print(f"account_id={result.account_id}")
    print(f"screenshot={result.screenshot_path}")
    print(f"detail={result.detail}")
    return 0 if result.status in {"probably_logged_in", "stopped_before_share", "stopped_unclear_screen"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
