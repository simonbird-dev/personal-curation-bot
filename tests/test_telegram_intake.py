from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from curation_bot.core import CurationBotError
from curation_bot.telegram_intake import handle_telegram_text


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


class TelegramIntakeTests(unittest.TestCase):
    def test_finds_message_queues_link_and_extracts_primary_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = handle_telegram_text(
                text="/finds /house slide 5 https://www.instagram.com/p/example1/",
                data_root=Path(tmp),
            )

            self.assertEqual(result.action, "ingest")
            self.assertEqual(result.category, "finds")
            self.assertEqual(result.command, "/finds")
            ingest_result = result.ingest_result
            metadata = result.metadata
            self.assertIsNotNone(ingest_result)
            self.assertIsNotNone(metadata)
            assert ingest_result is not None
            assert metadata is not None
            self.assertEqual(ingest_result.queue_count, 1)
            self.assertEqual(ingest_result.target_count, 8)
            self.assertEqual(ingest_result.subcategory, "house")
            self.assertEqual(ingest_result.queue_key, "finds/house")
            self.assertIn("Queued finds/house item 1/8", result.reply)
            self.assertEqual(metadata["primary_tag"], "house")

    def test_second_message_returns_ready_package_path_with_test_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = write_test_config(root, target_count=2)
            handle_telegram_text(text="/finds /house https://www.instagram.com/p/example1/", data_root=root, config_path=config_path)
            result = handle_telegram_text(text="/finds /house https://www.instagram.com/p/example2/", data_root=root, config_path=config_path)

            ingest_result = result.ingest_result
            self.assertIsNotNone(ingest_result)
            assert ingest_result is not None
            self.assertTrue(ingest_result.threshold_reached)
            self.assertIsNotNone(ingest_result.draft_package)
            self.assertIn("Batch threshold reached for finds/house (2/2)", result.reply)
            self.assertIn("Draft package is ready", result.reply)

    def test_draft_package_preserves_selected_slide_extraction_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = write_test_config(root, target_count=2)
            handle_telegram_text(text="/finds /house slide 5 https://www.instagram.com/p/example1/", data_root=root, config_path=config_path)
            result = handle_telegram_text(text="/finds /house slide 3 https://www.instagram.com/p/example2/", data_root=root, config_path=config_path)

            self.assertIsNotNone(result.ingest_result)
            assert result.ingest_result is not None
            package = Path(result.ingest_result.draft_package or "")
            manifest = json.loads((package / "manifest.json").read_text())
            self.assertEqual(manifest["items"][0]["source_metadata"]["selected_slide"], 5)
            self.assertEqual(manifest["items"][0]["selected_media_request"]["selected_index_1based"], 5)
            self.assertEqual(manifest["items"][0]["selected_media_request"]["status"], "requires_extraction_from_source_post")
            self.assertEqual(manifest["items"][1]["selected_media_request"]["selected_index_1based"], 3)

    def test_status_command_reports_queues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handle_telegram_text(text="/live https://www.instagram.com/reel/example1/", data_root=root)
            result = handle_telegram_text(text="/status", data_root=root)

            self.assertEqual(result.action, "status")
            self.assertIn("live: 1/8 queued", result.reply)
            self.assertIn("mixes: 0/5 queued", result.reply)
            status_report = result.status_report
            self.assertIsNotNone(status_report)
            assert status_report is not None
            self.assertEqual(status_report["live"]["queue_count"], 1)
            self.assertEqual(status_report["live"]["target_count"], 8)

    def test_url_slide_then_slash_genre_defaults_to_finds(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = handle_telegram_text(
                text="https://www.instagram.com/p/DaSy99HjXlU/?igsh=abc slide 1 /electronica",
                data_root=Path(tmp),
            )
            self.assertEqual(result.action, "ingest")
            self.assertEqual(result.category, "finds")
            self.assertEqual(result.command, "/finds")
            self.assertIsNotNone(result.ingest_result)
            self.assertIsNotNone(result.metadata)
            assert result.ingest_result is not None
            assert result.metadata is not None
            self.assertEqual(result.ingest_result.queue_key, "finds/electronica")
            self.assertEqual(result.metadata["selected_slide"], 1)
            self.assertEqual(result.metadata["selected_media_status"], "source_post_link_requires_extraction")
            queued = list((Path(tmp) / "queues" / "finds" / "electronica").glob("*.json"))
            self.assertEqual(len(queued), 1)
            payload = json.loads(queued[0].read_text())
            self.assertEqual(payload["source_metadata"]["selected_slide"], 1)
            self.assertEqual(payload["source_metadata"]["selected_media_status"], "source_post_link_requires_extraction")
            self.assertIn("Queued finds/electronica item 1/8", result.reply)

    def test_mixes_accepts_soundcloud_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = handle_telegram_text(text="/mixes https://soundcloud.com/example/set", data_root=Path(tmp))
            self.assertEqual(result.action, "ingest")
            self.assertEqual(result.category, "mixes")
            self.assertIn("Queued mixes item 1/5", result.reply)

    def test_finds_dynamic_genres_have_separate_thresholds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = write_test_config(root, target_count=2)
            handle_telegram_text(text="/finds /house https://www.instagram.com/p/house1/", data_root=root, config_path=config_path)
            techno = handle_telegram_text(text="/finds /techno https://www.instagram.com/p/techno1/", data_root=root, config_path=config_path)
            self.assertIsNotNone(techno.ingest_result)
            assert techno.ingest_result is not None
            self.assertFalse(techno.ingest_result.threshold_reached)
            self.assertEqual(techno.ingest_result.queue_count, 1)
            self.assertEqual(techno.ingest_result.queue_key, "finds/techno")
            house = handle_telegram_text(text="/finds /house https://www.instagram.com/p/house2/", data_root=root, config_path=config_path)
            self.assertIsNotNone(house.ingest_result)
            assert house.ingest_result is not None
            self.assertTrue(house.ingest_result.threshold_reached)
            self.assertIn("finds-house-2-items", house.ingest_result.draft_package or "")
            status_result = handle_telegram_text(text="/status", data_root=root, config_path=config_path)
            self.assertIn("finds/house: 0/2 queued, 1 package(s)", status_result.reply)
            self.assertIn("finds/techno: 1/2 queued, 0 package(s)", status_result.reply)

    def test_rejects_unknown_command_with_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CurationBotError) as ctx:
                handle_telegram_text(text="/random https://www.instagram.com/p/example1/", data_root=Path(tmp))
            self.assertEqual(ctx.exception.code, "unknown_telegram_command")

    def test_help_without_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = handle_telegram_text(text="/help", data_root=Path(tmp))
            self.assertEqual(result.action, "help")
            self.assertIn("/finds", result.reply)
            self.assertIn("/mixes", result.reply)


if __name__ == "__main__":
    unittest.main()
