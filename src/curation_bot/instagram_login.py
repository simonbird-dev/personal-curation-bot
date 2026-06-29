from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
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


def _login_status_from_body(body: str) -> bool:
    body = body.lower()
    logged_in_signals = ["stories", "messages", "notifications", "profile"]
    login_signals = ["log in", "sign up", "forgot password"]
    return any(signal in body for signal in logged_in_signals) and not all(signal in body for signal in login_signals)


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
        body = page.locator("body").inner_text(timeout=5000)
        context.close()

    if _login_status_from_body(body):
        print(f"login_status=probably_logged_in screenshot={screenshot}")
        return 0
    print(f"login_status=not_logged_in_or_unclear screenshot={screenshot}")
    return 1


def login_from_terminal() -> int:
    """Log into Instagram in headless Chromium using credentials typed into the VM terminal.

    This keeps credentials out of Telegram/chat, source files, Git, and normal logs.
    Password input uses getpass so it is not echoed to the terminal.
    """
    ensure_runtime_dirs()
    username = input("Instagram username/email/phone: ").strip()
    password = getpass.getpass("Instagram password (not echoed): ")
    if not username or not password:
        print("login_status=missing_credentials")
        return 2

    screenshot = SCREENSHOTS_DIR / "instagram-terminal-login-result.png"
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(INSTAGRAM_PROFILE_DIR),
            headless=True,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        try:
            page.get_by_label("Phone number, username, or email").fill(username, timeout=8000)
        except PlaywrightTimeoutError:
            page.locator('input[name="username"]').fill(username, timeout=8000)
        try:
            page.get_by_label("Password").fill(password, timeout=8000)
        except PlaywrightTimeoutError:
            page.locator('input[name="password"]').fill(password, timeout=8000)
        page.get_by_role("button", name="Log in").click(timeout=8000)
        page.wait_for_timeout(8000)

        body = page.locator("body").inner_text(timeout=8000)
        if "two-factor" in page.url.lower() or "security code" in body.lower() or "confirmation code" in body.lower():
            code = input("Instagram 2FA/security code, if requested: ").strip()
            if code:
                candidates = [
                    'input[name="verificationCode"]',
                    'input[aria-label*="Security Code"]',
                    'input[aria-label*="security code"]',
                    'input[type="tel"]',
                    'input[type="text"]',
                ]
                for selector in candidates:
                    try:
                        page.locator(selector).first.fill(code, timeout=2000)
                        break
                    except Exception:
                        continue
                try:
                    page.get_by_role("button", name="Confirm").click(timeout=5000)
                except Exception:
                    try:
                        page.get_by_role("button", name="Submit").click(timeout=5000)
                    except Exception:
                        page.keyboard.press("Enter")
                page.wait_for_timeout(8000)
                body = page.locator("body").inner_text(timeout=8000)

        for button_name in ["Not now", "Not Now", "Save info", "Save Info"]:
            try:
                page.get_by_role("button", name=button_name).click(timeout=1500)
                page.wait_for_timeout(1000)
            except Exception:
                pass

        page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        body = page.locator("body").inner_text(timeout=8000)
        page.screenshot(path=str(screenshot), full_page=False)
        context.close()

    if _login_status_from_body(body):
        print(f"login_status=probably_logged_in screenshot={screenshot}")
        return 0
    print(f"login_status=not_logged_in_or_checkpoint screenshot={screenshot}")
    print("If Instagram shows a checkpoint, browser/device verification will need an interactive browser surface or manual approval step.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    open_cmd = sub.add_parser("open-login", help="Open Instagram in the dedicated persistent browser profile.")
    open_cmd.add_argument("--headless", action="store_true", help="Run headless. Manual login needs a visible browser, so this is mainly for diagnostics.")
    sub.add_parser("terminal-login", help="Prompt for Instagram credentials in the VM terminal and log in headlessly without echoing the password.")
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
    if args.command == "terminal-login":
        return login_from_terminal()
    if args.command == "check-login":
        return check_login()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
