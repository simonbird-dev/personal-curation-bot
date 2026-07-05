from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import CurationBotError, DEFAULT_CONFIG_PATH, IngestResult, ingest_link, status

COMMAND_TO_CATEGORY = {
    "/finds": "finds",
    "/live": "live",
    "/mixes": "mixes",
    "/fashion": "fashion",
}

URL_RE = re.compile(r"https?://\S+")
TAG_RE = re.compile(r"#[\w-]+")
SELECTED_SLIDE_RE = re.compile(r"\b(?:slide|frame)\s*(\d+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class TelegramIntakeResult:
    action: str
    category: str | None
    command: str | None
    reply: str
    ingest_result: IngestResult | None = None
    status_report: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": self.action,
            "category": self.category,
            "command": self.command,
            "reply": self.reply,
        }
        if self.ingest_result is not None:
            payload["ingest_result"] = self.ingest_result.__dict__
        if self.status_report is not None:
            payload["status_report"] = self.status_report
        if self.metadata is not None:
            payload["metadata"] = self.metadata
        return payload


def _tokens(text: str) -> list[str]:
    return [part.strip() for part in text.strip().split() if part.strip()]


def _normalise_command(token: str) -> str | None:
    if not token.startswith("/"):
        return None
    command = token.split("@", 1)[0].lower()
    return command


def extract_command(text: str) -> str | None:
    for token in _tokens(text):
        command = _normalise_command(token)
        if command:
            return command
    return None


def extract_url(text: str) -> str | None:
    match = URL_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(").,]")


def selected_slide_from_message(text: str) -> int | None:
    match = SELECTED_SLIDE_RE.search(text)
    if not match:
        return None
    try:
        value = int(match.group(1))
    except ValueError:
        return None
    return value if value >= 1 else None


def metadata_from_message(text: str, command: str | None) -> dict[str, Any]:
    tags = [tag[1:].lower() for tag in TAG_RE.findall(text)]
    words = _tokens(text)
    command_index = next((i for i, token in enumerate(words) if _normalise_command(token) == command), None)
    primary_tag = None
    selected_slide = selected_slide_from_message(text)
    if command == "/finds":
        start_index = command_index + 1 if command_index is not None else 0
        for token in words[start_index:]:
            if token.startswith("http"):
                continue
            normalised = _normalise_command(token)
            if normalised and normalised != command:
                clean = re.sub(r"[^a-zA-Z0-9_-]+", "", normalised.lstrip("/")).lower()
                if clean:
                    primary_tag = clean
                    break
            if token.startswith("#"):
                primary_tag = token[1:].lower()
                break
            clean = re.sub(r"[^a-zA-Z0-9_-]+", "", token).lower()
            if clean and clean not in {"slide", "frame"} and not clean.isdigit():
                primary_tag = clean
                break
    metadata: dict[str, Any] = {
        "tags": tags,
        "primary_tag": primary_tag,
        "original_text": text,
    }
    if selected_slide is not None:
        metadata["selected_slide"] = selected_slide
        metadata["selected_media_status"] = "source_post_link_requires_extraction"
    return metadata


def handle_telegram_text(*, text: str, data_root: Path, config_path: Path = DEFAULT_CONFIG_PATH) -> TelegramIntakeResult:
    """Handle one Telegram-style message without needing a live Telegram bot token.

    This is the safe local adapter for the future dedicated Telegram bot: command
    parsing, category routing, and reply text are tested here, while the actual
    Telegram transport can be added later without changing curation state rules.
    """
    stripped = text.strip()
    if not stripped:
        raise CurationBotError("empty_telegram_message", "Telegram message is empty.")

    command = extract_command(stripped)
    url = extract_url(stripped)
    if command not in COMMAND_TO_CATEGORY and command not in {"/status", "/queue", "/help", None} and url:
        words = _tokens(stripped)
        command_index = next((i for i, token in enumerate(words) if _normalise_command(token) == command), None)
        url_index = next((i for i, token in enumerate(words) if token.startswith("http")), None)
        if command_index is not None and url_index is not None and command_index > url_index:
            command = "/finds"
    if command in {"/status", "/queue"}:
        report = status(data_root, config_path=config_path)
        lines = ["Curation queue status:"]
        for category, category_report in report.items():
            lines.append(
                f"- {category}: {category_report['queue_count']}/{category_report['target_count']} queued, "
                f"{category_report['draft_package_count']} package(s)"
            )
            for subqueue_name, subqueue_report in category_report.get("subqueues", {}).items():
                lines.append(
                    f"  - {category}/{subqueue_name}: {subqueue_report['queue_count']}/{subqueue_report['target_count']} queued, "
                    f"{subqueue_report['draft_package_count']} package(s)"
                )
        return TelegramIntakeResult(
            action="status",
            category=None,
            command=command,
            reply="\n".join(lines),
            status_report=report,
        )

    if command in {"/help", None} and not extract_url(stripped):
        return TelegramIntakeResult(
            action="help",
            category=None,
            command=command,
            reply=(
                "Send /finds, /live, /mixes, or /fashion with an Instagram/Pinterest/SoundCloud link.\n"
                "Examples:\n"
                "/finds /house slide 5 https://www.instagram.com/p/ABC123/\n"
                "/live https://www.instagram.com/reel/ABC123/\n"
                "/mixes https://soundcloud.com/example/set\n"
                "/fashion https://pin.it/example\n"
                "Use /status or /queue to see queues."
            ),
        )

    if command not in COMMAND_TO_CATEGORY:
        raise CurationBotError(
            "unknown_telegram_command",
            "Use /finds, /live, /mixes, or /fashion with a supported Instagram/Pinterest/SoundCloud link, or /status.",
        )

    if not extract_url(stripped):
        raise CurationBotError("missing_url", f"{command} needs an Instagram, Pinterest, or SoundCloud URL.")

    category = COMMAND_TO_CATEGORY[command]
    metadata = metadata_from_message(stripped, command)
    subcategory = metadata.get("primary_tag") if category == "finds" else None
    if category == "finds" and not subcategory:
        raise CurationBotError("missing_finds_genre", "/finds needs a dynamic slash genre such as /house, /techno, or /deep-tribal.")
    result = ingest_link(
        category=category,
        text_or_url=stripped,
        data_root=data_root,
        config_path=config_path,
        source="telegram",
        subcategory=subcategory,
        source_metadata=metadata,
    )
    queue_label = result.queue_key or category
    if result.threshold_reached and result.draft_package:
        reply = (
            f"Batch threshold reached for {queue_label} ({result.target_count}/{result.target_count}).\n"
            f"Draft package is ready: {result.draft_package}"
        )
    else:
        reply = f"Queued {queue_label} item {result.queue_count}/{result.target_count}."
    if metadata.get("primary_tag"):
        reply += f"\nPrimary tag noted: {metadata['primary_tag']}"
    return TelegramIntakeResult(
        action="ingest",
        category=category,
        command=command,
        reply=reply,
        ingest_result=result,
        metadata=metadata,
    )
