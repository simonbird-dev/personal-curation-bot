from __future__ import annotations

from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = APP_ROOT / ".runtime"
INSTAGRAM_PROFILE_DIR = RUNTIME_ROOT / "instagram-browser-profile"
SCREENSHOTS_DIR = RUNTIME_ROOT / "screenshots"


def ensure_runtime_dirs() -> None:
    INSTAGRAM_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
