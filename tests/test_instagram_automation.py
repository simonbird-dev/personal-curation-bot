from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from curation_bot.core import ingest_link
from curation_bot.instagram_automation import InstagramAutomationError, plan_browser_draft_automation


class InstagramAutomationBoundaryTests(unittest.TestCase):
    def test_ready_package_produces_browser_automation_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ingest_link(category="finds", text_or_url="https://www.instagram.com/p/example1/", data_root=root)
            result = ingest_link(category="finds", text_or_url="https://www.instagram.com/p/example2/", data_root=root)
            plan = plan_browser_draft_automation(Path(result.draft_package or ""))
            self.assertEqual(plan.category, "finds")
            self.assertEqual(plan.item_count, 2)
            self.assertEqual(plan.status, "ready_for_browser_automation_spike")
            self.assertIn("stop before Share/Post", plan.next_action)

    def test_missing_manifest_fails_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(InstagramAutomationError) as ctx:
                plan_browser_draft_automation(Path(tmp))
            self.assertEqual(ctx.exception.code, "missing_manifest")


if __name__ == "__main__":
    unittest.main()
