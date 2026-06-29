from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import RUNTIME_ROOT

ACCOUNTS_ROOT = RUNTIME_ROOT / "instagram-accounts"
ACTIVE_ACCOUNT_FILE = RUNTIME_ROOT / "active-instagram-account.json"


class AccountConfigError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class InstagramAccountRef:
    account_id: str
    profile_dir: Path


def _safe_account_id(account_id: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in account_id.strip().lower())
    clean = clean.strip("-")
    if not clean:
        raise AccountConfigError("missing_account_id", "Account id cannot be empty.")
    if clean in {"default", "active", "runtime"}:
        raise AccountConfigError("reserved_account_id", f"Reserved account id: {clean}")
    return clean[:64]


def account_ref(account_id: str) -> InstagramAccountRef:
    safe_id = _safe_account_id(account_id)
    return InstagramAccountRef(
        account_id=safe_id,
        profile_dir=ACCOUNTS_ROOT / safe_id / "browser-profile",
    )


def set_active_account(account_id: str) -> InstagramAccountRef:
    ref = account_ref(account_id)
    ref.profile_dir.mkdir(parents=True, exist_ok=True)
    ACTIVE_ACCOUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_ACCOUNT_FILE.write_text(json.dumps({"active_account_id": ref.account_id}, indent=2) + "\n", encoding="utf-8")
    return ref


def get_active_account() -> InstagramAccountRef:
    if not ACTIVE_ACCOUNT_FILE.exists():
        raise AccountConfigError(
            "missing_active_account",
            "No active Instagram account is configured. Run: python -m curation_bot.instagram_accounts set-active --account-id test",
        )
    data: dict[str, Any] = json.loads(ACTIVE_ACCOUNT_FILE.read_text(encoding="utf-8"))
    return account_ref(str(data.get("active_account_id", "")))


def list_accounts() -> list[str]:
    if not ACCOUNTS_ROOT.exists():
        return []
    return sorted(path.name for path in ACCOUNTS_ROOT.iterdir() if path.is_dir())
