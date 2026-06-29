from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from curation_bot.instagram_accounts import account_ref, set_active_account, get_active_account, list_accounts


class InstagramAccountConfigTests(unittest.TestCase):
    def test_account_ref_sanitises_id(self):
        ref = account_ref("Main Account!")
        self.assertEqual(ref.account_id, "main-account")
        self.assertTrue(str(ref.profile_dir).endswith("instagram-accounts/main-account/browser-profile"))

    def test_active_account_is_runtime_state_not_content_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            with patch("curation_bot.instagram_accounts.ACCOUNTS_ROOT", runtime / "accounts"), patch(
                "curation_bot.instagram_accounts.ACTIVE_ACCOUNT_FILE", runtime / "active.json"
            ):
                first = set_active_account("test")
                second = set_active_account("main")
                self.assertEqual(get_active_account().account_id, "main")
                self.assertEqual({first.account_id, second.account_id}, set(list_accounts()))
                self.assertNotEqual(first.profile_dir, second.profile_dir)


if __name__ == "__main__":
    unittest.main()
