from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from curation_bot.core import CurationBotError, ingest_link, status


class PersonalCurationBotCoreTests(unittest.TestCase):
    def test_ingest_queues_item_below_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = ingest_link(
                category="finds",
                text_or_url="check this https://www.instagram.com/p/example1/",
                data_root=root,
            )
            self.assertEqual(result.queue_count, 1)
            self.assertFalse(result.threshold_reached)
            self.assertIsNone(result.draft_package)
            queued = list((root / "queues" / "finds").glob("*.json"))
            self.assertEqual(len(queued), 1)
            payload = json.loads(queued[0].read_text())
            self.assertEqual(payload["status"], "queued")
            self.assertEqual(payload["processing"]["instagram_draft_status"], "not_attempted")

    def test_threshold_creates_draft_package_and_archives_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ingest_link(category="finds", text_or_url="https://www.instagram.com/p/example1/", data_root=root)
            result = ingest_link(category="finds", text_or_url="https://www.instagram.com/p/example2/", data_root=root)

            self.assertTrue(result.threshold_reached)
            self.assertIsNotNone(result.draft_package)
            package = Path(result.draft_package or "")
            self.assertTrue((package / "manifest.json").exists())
            manifest = json.loads((package / "manifest.json").read_text())
            self.assertEqual(manifest["item_count"], 2)
            self.assertEqual(manifest["status"], "ready_for_manual_instagram_posting")
            self.assertIn("not an Instagram native app draft", manifest["important_boundary"])
            self.assertEqual(len(list((root / "queues" / "finds").glob("*.json"))), 0)
            self.assertEqual(len(list((root / "archive" / "finds").glob("*.json"))), 2)

    def test_status_reports_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ingest_link(category="live", text_or_url="https://www.instagram.com/p/live1/", data_root=root)
            report = status(root)
            self.assertEqual(report["live"]["queue_count"], 1)
            self.assertEqual(report["live"]["target_count"], 2)
            self.assertIn("fashion", report)

    def test_rejects_unsupported_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CurationBotError) as ctx:
                ingest_link(category="finds", text_or_url="https://example.com/nope", data_root=Path(tmp))
            self.assertEqual(ctx.exception.code, "unsupported_url")

    def test_rejects_unknown_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CurationBotError) as ctx:
                ingest_link(category="random", text_or_url="https://www.instagram.com/p/example1/", data_root=Path(tmp))
            self.assertEqual(ctx.exception.code, "unknown_category")


if __name__ == "__main__":
    unittest.main()
