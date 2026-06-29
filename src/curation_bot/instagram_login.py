from __future__ import annotations

import argparse
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

from .paths import INSTAGRAM_PROFILE_DIR, SCREENSHOTS_DIR, ensure_runtime_dirs


def open_login(headless: bool, slow_mo_ms: int = 100) -> None:
    ensure_runtime_dirs()
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(INSTAGRAM_PROFILE_DIR),
            headless=headless,
            slow_mo=slow_mo_ms,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        print(f"Instagram browser profile: {INSTAGRAM_PROFILE_DIR}")
        print("Log in manually in the opened browser window if visible.")
        print("When logged in, close the browser window or press Ctrl+C in this terminal.")
        try:
            page.wait_for_timeout(10 * 60 * 1000)
        finally:
            context.close()


def check_login() -> int:
    ensure_runtime_dirs()
    screenshot = SCREENSHOTS_DIR / "instagram-login-check.png"
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(INSTAGRAM_PROFILE_DIR),
            headless=True,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        page.screenshot(path=str(screenshot), full_page=False)
        body = page.locator("body").inner_text(timeout=5000).lower()
        context.close()

    logged_in_signals = ["stories", "messages", "notifications", "profile"]
    login_signals = ["log in", "sign up", "forgot password"]
    if any(signal in body for signal in logged_in_signals) and not all(signal in body for signal in login_signals):
        print(f"login_status=probably_logged_in screenshot={screenshot}")
        return 0
    print(f"login_status=not_logged_in_or_unclear screenshot={screenshot}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    open_cmd = sub.add_parser("open-login", help="Open Instagram in the dedicated persistent browser profile.")
    open_cmd.add_argument("--headless", action="store_true", help="Run headless. Manual login needs a visible browser, so this is mainly for diagnostics.")
    sub.add_parser("check-login", help="Check whether the dedicated browser profile appears logged in.")
    args = parser.parse_args()

    if args.command == "open-login":
        if not args.headless and not os.environ.get("DISPLAY"):
            print("error=no_display")
            print("This VM has no visible desktop DISPLAY in the current shell. Use a VNC/desktop session, SSH with X forwarding, or an approved browser-control surface, then run this command there:")
            print("  . .venv/bin/activate && PYTHONPATH=src python -m curation_bot.instagram_login open-login")
            return 2
        open_login(headless=args.headless)
        return 0
    if args.command == "check-login":
        return check_login()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
