from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from curation_bot.apify_capture import capture_from_dataset
from curation_bot.core import execute_media_download, ingest_capture_record, ingest_link
from curation_bot.instagram_automation import (
    InstagramAutomationError,
    _require_playwright,
    plan_browser_draft_automation,
    resolve_draft_media,
)


DATASET = [
    {
        "id": "parent-1",
        "shortCode": "ABC123",
        "url": "https://www.instagram.com/p/ABC123/",
        "inputUrl": "https://www.instagram.com/p/ABC123/?img_index=2",
        "ownerUsername": "example_account",
        "type": "Sidecar",
        "productType": "carousel_container",
        "timestamp": "2026-06-29T00:00:00.000Z",
        "childPosts": [
            {
                "id": "child-1",
                "shortCode": "CHILD1",
                "type": "Image",
                "displayUrl": "https://signed.example/image1.jpg",
            },
            {
                "id": "child-2",
                "shortCode": "CHILD2",
                "type": "Video",
                "displayUrl": "https://signed.example/thumb.jpg",
                "videoUrl": "https://signed.example/video.mp4",
            },
        ],
    }
]


def write_test_config(root: Path, target_count: int = 2) -> Path:
    config_path = root / "categories.json"
    config_path.write_text(
        json.dumps(
            {
                "finds": {"target_count": target_count},
                "live": {"target_count": target_count},
                "mixes": {"target_count": target_count},
                "fashion": {"target_count": target_count},
            }
        ),
        encoding="utf-8",
    )
    return config_path


class InstagramAutomationBoundaryTests(unittest.TestCase):
    def build_selected_media_package(self, root: Path) -> Path:
        dataset_path = root / "dataset.json"
        dataset_path.write_text(json.dumps(DATASET), encoding="utf-8")
        first_record = capture_from_dataset(
            source_url="https://www.instagram.com/p/ABC123/?img_index=2",
            selected_slide=2,
            dataset_path=dataset_path,
            data_root=root,
            category="finds",
            stream="/finds",
        )
        second_record = capture_from_dataset(
            source_url="https://www.instagram.com/p/ABC123/?img_index=1",
            selected_slide=1,
            dataset_path=dataset_path,
            data_root=root,
            category="finds",
            stream="/finds",
        )
        ingest_capture_record(category="finds", capture_record_path=first_record, data_root=root, config_path=write_test_config(root))
        result = ingest_capture_record(category="finds", capture_record_path=second_record, data_root=root, config_path=write_test_config(root))
        return Path(result.draft_package or "")

    def make_selected_media_package_ready(self, package: Path, root: Path) -> None:
        fixture_video = root / "fixture.mp4"
        fixture_video.write_bytes(b"fake-video")
        fixture_image = root / "fixture.jpg"
        fixture_image.write_bytes(b"fake-image")
        execute_media_download(package_dir=package, provider="local-fixture", fixture_file=fixture_video, selected_shortcode="CHILD2")
        execute_media_download(package_dir=package, provider="local-fixture", fixture_file=fixture_image, selected_shortcode="CHILD1")

    def test_ready_package_produces_browser_automation_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self.build_selected_media_package(root)
            self.make_selected_media_package_ready(package, root)
            plan = plan_browser_draft_automation(package)
            self.assertEqual(plan.category, "finds")
            self.assertEqual(plan.item_count, 2)
            self.assertEqual(plan.status, "ready_for_browser_automation_spike")
            self.assertIn("stop before Share/Post", plan.next_action)

    def test_incomplete_selected_media_package_fails_before_browser_automation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self.build_selected_media_package(root)

            with self.assertRaises(InstagramAutomationError) as ctx:
                plan_browser_draft_automation(package)
            self.assertEqual(ctx.exception.code, "package_media_not_ready")
            self.assertIn("Run execute-media-download", ctx.exception.message)
            self.assertIn("Missing downloaded media", ctx.exception.message)

    def test_missing_manifest_fails_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(InstagramAutomationError) as ctx:
                plan_browser_draft_automation(Path(tmp))
            self.assertEqual(ctx.exception.code, "missing_manifest")

    def test_resolve_draft_media_requires_uploadable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ingest_link(category="finds", text_or_url="https://www.instagram.com/p/example1/", data_root=root, config_path=write_test_config(root))
            result = ingest_link(category="finds", text_or_url="https://www.instagram.com/p/example2/", data_root=root, config_path=write_test_config(root))
            with self.assertRaises(InstagramAutomationError) as ctx:
                resolve_draft_media(Path(result.draft_package or ""))
            self.assertEqual(ctx.exception.code, "missing_media")

    def test_resolve_draft_media_uses_package_media_and_caption_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ingest_link(category="finds", text_or_url="https://www.instagram.com/p/example1/", data_root=root, config_path=write_test_config(root))
            result = ingest_link(category="finds", text_or_url="https://www.instagram.com/p/example2/", data_root=root, config_path=write_test_config(root))
            package = Path(result.draft_package or "")
            media_dir = package / "media"
            media_dir.mkdir()
            image = media_dir / "test.png"
            image.write_bytes(b"not-a-real-image-but-path-validation-only")

            draft_media = resolve_draft_media(package)
            self.assertEqual(draft_media.media_paths, (image.resolve(),))
            self.assertIn("finds draft", draft_media.caption)
            self.assertIn("https://www.instagram.com/p/example1/", draft_media.caption)

    def test_missing_playwright_fails_with_stable_error_code(self):
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "playwright.sync_api":
                raise ModuleNotFoundError("No module named 'playwright'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            with self.assertRaises(InstagramAutomationError) as ctx:
                _require_playwright()
        self.assertEqual(ctx.exception.code, "playwright_missing")
        self.assertIn("approved setup lane", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
