from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from curation_bot.apify_capture import CaptureError, capture_from_dataset
from curation_bot.core import CurationBotError, build_manual_review_pack, check_package_readiness, execute_media_download, ingest_capture_record


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


class ApifyCapturePipelineTests(unittest.TestCase):
    def build_two_item_package(self, root: Path) -> Path:
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
        ingest_capture_record(category="finds", capture_record_path=first_record, data_root=root)
        result = ingest_capture_record(category="finds", capture_record_path=second_record, data_root=root)
        return Path(result.draft_package or "")

    def test_capture_from_dataset_writes_sanitised_record_without_raw_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset_path = root / "dataset.json"
            dataset_path.write_text(json.dumps(DATASET), encoding="utf-8")

            record_path = capture_from_dataset(
                source_url="https://www.instagram.com/p/ABC123/?img_index=2",
                selected_slide=2,
                dataset_path=dataset_path,
                data_root=root,
                category="finds",
                stream="/finds",
            )

            self.assertEqual(record_path.name, "ABC123-slide2.json")
            record = json.loads(record_path.read_text())
            self.assertEqual(record["schema_version"], "apify_selected_media_capture_record_v0_1")
            self.assertEqual(record["selected_media"]["shortcode"], "CHILD2")
            self.assertEqual(record["selected_media"]["media_url_kind_for_future_capture"], "videoUrl")
            text = record_path.read_text()
            self.assertNotIn("https://signed.example/video.mp4", text)
            self.assertNotIn("https://signed.example/image1.jpg", text)

    def test_ingest_capture_record_queues_captured_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset_path = root / "dataset.json"
            dataset_path.write_text(json.dumps(DATASET), encoding="utf-8")
            record_path = capture_from_dataset(
                source_url="https://www.instagram.com/p/ABC123/?img_index=2",
                selected_slide=2,
                dataset_path=dataset_path,
                data_root=root,
                category="finds",
                stream="/finds",
            )

            result = ingest_capture_record(category="finds", capture_record_path=record_path, data_root=root)

            self.assertEqual(result.queue_count, 1)
            queued = list((root / "queues" / "finds").glob("*.json"))
            self.assertEqual(len(queued), 1)
            item = json.loads(queued[0].read_text())
            self.assertEqual(item["processing"]["capture_status"], "captured")
            self.assertEqual(item["selected_media"]["shortcode"], "CHILD2")
            self.assertEqual(item["capture_record_path"], str(record_path))

    def test_ingest_capture_record_package_manifest_preserves_selected_media_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
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

            ingest_capture_record(category="finds", capture_record_path=first_record, data_root=root)
            result = ingest_capture_record(category="finds", capture_record_path=second_record, data_root=root)

            self.assertTrue(result.threshold_reached)
            manifest_path = Path(result.draft_package or "") / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest["item_count"], 2)
            items_by_shortcode = {item["selected_media"]["shortcode"]: item for item in manifest["items"]}
            self.assertEqual(items_by_shortcode["CHILD2"]["capture_record_path"], str(first_record))
            self.assertEqual(items_by_shortcode["CHILD2"]["selected_media"]["media_url_kind_for_future_capture"], "videoUrl")
            self.assertTrue(items_by_shortcode["CHILD2"]["expected_media_relative_path"].endswith("-CHILD2.mp4"))
            self.assertEqual(items_by_shortcode["CHILD2"]["media_status"], "not_downloaded")
            self.assertEqual(items_by_shortcode["CHILD1"]["capture_record_path"], str(second_record))
            media_manifest = json.loads((Path(result.draft_package or "") / "media_manifest.json").read_text())
            self.assertEqual(media_manifest["schema_version"], "personal_curation_media_plan_v0_1")
            self.assertEqual(media_manifest["status"], "media_not_downloaded")
            self.assertIn("not raw media URLs", media_manifest["important_boundary"])
            media_items_by_shortcode = {item["selected_media"]["shortcode"]: item for item in media_manifest["items"]}
            self.assertTrue(media_items_by_shortcode["CHILD2"]["expected_media_relative_path"].endswith("-CHILD2.mp4"))
            self.assertTrue(media_items_by_shortcode["CHILD1"]["expected_media_relative_path"].endswith("-CHILD1.jpg"))

    def test_capture_rejects_out_of_range_selected_slide(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset_path = root / "dataset.json"
            dataset_path.write_text(json.dumps(DATASET), encoding="utf-8")

            with self.assertRaises(CaptureError) as ctx:
                capture_from_dataset(
                    source_url="https://www.instagram.com/p/ABC123/",
                    selected_slide=4,
                    dataset_path=dataset_path,
                    data_root=root,
                    category="finds",
                )
            self.assertEqual(ctx.exception.code, "selected_index_out_of_range")

    def test_ingest_rejects_unredacted_capture_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = {
                "schema_version": "apify_selected_media_capture_record_v0_1",
                "source": {"source_url": "https://www.instagram.com/p/ABC123/"},
                "selected_media": {},
                "quality_flags": {"raw_media_urls_redacted": False},
            }
            record_path = root / "unsafe.json"
            record_path.write_text(json.dumps(record), encoding="utf-8")

            with self.assertRaises(CurationBotError) as ctx:
                ingest_capture_record(category="finds", capture_record_path=record_path, data_root=root)
            self.assertEqual(ctx.exception.code, "unsafe_capture_record")
    def test_media_download_refuses_without_explicit_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "package"
            package.mkdir()
            (package / "media_manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "personal_curation_media_plan_v0_1",
                        "items": [
                            {
                                "item_id": "item-1",
                                "selected_media": {"shortcode": "CHILD2"},
                                "expected_media_relative_path": "media/01-CHILD2.mp4",
                                "status": "not_downloaded",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            fixture = root / "fixture.mp4"
            fixture.write_bytes(b"fake-video")

            with self.assertRaises(CurationBotError) as ctx:
                execute_media_download(package_dir=package, provider=None, fixture_file=fixture)
            self.assertEqual(ctx.exception.code, "media_provider_required")

    def test_media_download_refuses_unapproved_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "package"
            package.mkdir()
            (package / "media_manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "personal_curation_media_plan_v0_1",
                        "items": [
                            {
                                "item_id": "item-1",
                                "selected_media": {"shortcode": "CHILD2"},
                                "expected_media_relative_path": "media/01-CHILD2.mp4",
                                "status": "not_downloaded",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            fixture = root / "fixture.mp4"
            fixture.write_bytes(b"fake-video")

            with self.assertRaises(CurationBotError) as ctx:
                execute_media_download(package_dir=package, provider="apify-live", fixture_file=fixture)
            self.assertEqual(ctx.exception.code, "unsupported_media_provider")

    def test_local_fixture_media_download_marks_selected_item_downloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
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
            ingest_capture_record(category="finds", capture_record_path=first_record, data_root=root)
            result = ingest_capture_record(category="finds", capture_record_path=second_record, data_root=root)
            package_dir = Path(result.draft_package or "")
            fixture = root / "fixture.mp4"
            fixture.write_bytes(b"fake-video")

            download = execute_media_download(
                package_dir=package_dir,
                provider="local-fixture",
                fixture_file=fixture,
                selected_shortcode="CHILD2",
            )

            self.assertEqual(download.copied_count, 1)
            self.assertEqual(download.media_status, "media_partially_downloaded")
            copied_path = Path(download.copied_files[0])
            self.assertEqual(copied_path.read_bytes(), b"fake-video")
            self.assertTrue(copied_path.name.endswith("-CHILD2.mp4"))
            media_manifest = json.loads((package_dir / "media_manifest.json").read_text())
            items_by_shortcode = {item["selected_media"]["shortcode"]: item for item in media_manifest["items"]}
            self.assertEqual(items_by_shortcode["CHILD2"]["status"], "downloaded")
            self.assertEqual(items_by_shortcode["CHILD2"]["provider"], "local-fixture")
            self.assertEqual(items_by_shortcode["CHILD1"]["status"], "not_downloaded")
            self.assertIn("no live Apify call", items_by_shortcode["CHILD2"]["download_boundary"])
            manifest = json.loads((package_dir / "manifest.json").read_text())
            manifest_items_by_shortcode = {item["selected_media"]["shortcode"]: item for item in manifest["items"]}
            self.assertEqual(manifest["media_status"], "partially_downloaded")
            self.assertEqual(manifest_items_by_shortcode["CHILD2"]["media_status"], "downloaded")

    def test_local_fixture_requires_shortcode_for_multi_item_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset_path = root / "dataset.json"
            dataset_path.write_text(json.dumps(DATASET), encoding="utf-8")
            first_record = capture_from_dataset(
                source_url="https://www.instagram.com/p/ABC123/?img_index=2",
                selected_slide=2,
                dataset_path=dataset_path,
                data_root=root,
                category="finds",
            )
            second_record = capture_from_dataset(
                source_url="https://www.instagram.com/p/ABC123/?img_index=1",
                selected_slide=1,
                dataset_path=dataset_path,
                data_root=root,
                category="finds",
            )
            ingest_capture_record(category="finds", capture_record_path=first_record, data_root=root)
            result = ingest_capture_record(category="finds", capture_record_path=second_record, data_root=root)
            fixture = root / "fixture.mp4"
            fixture.write_bytes(b"fake-video")

            with self.assertRaises(CurationBotError) as ctx:
                execute_media_download(package_dir=Path(result.draft_package or ""), provider="local-fixture", fixture_file=fixture)
            self.assertEqual(ctx.exception.code, "media_selection_required")

    def test_package_readiness_blocks_when_manifest_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "package"
            package.mkdir()

            result = check_package_readiness(package)

            self.assertFalse(result.package_ready_for_instagram_draft)
            self.assertEqual(result.media_status, "media_not_downloaded")
            self.assertTrue(any("Missing manifest.json" in blocker for blocker in result.blockers))
            self.assertIn("Repair or recreate", result.safe_next_step)

    def test_package_readiness_reports_partial_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self.build_two_item_package(root)
            fixture = root / "fixture.mp4"
            fixture.write_bytes(b"fake-video")
            execute_media_download(
                package_dir=package,
                provider="local-fixture",
                fixture_file=fixture,
                selected_shortcode="CHILD2",
            )

            result = check_package_readiness(package)

            self.assertFalse(result.package_ready_for_instagram_draft)
            self.assertEqual(result.media_status, "media_partially_downloaded")
            self.assertTrue(any("Missing downloaded media for CHILD1" in blocker for blocker in result.blockers))
            items_by_shortcode = {item["shortcode"]: item for item in result.items}
            self.assertFalse(items_by_shortcode["CHILD1"]["file_exists"])
            self.assertTrue(items_by_shortcode["CHILD2"]["file_exists"])

    def test_package_readiness_ready_when_all_expected_media_files_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self.build_two_item_package(root)
            video_fixture = root / "fixture.mp4"
            image_fixture = root / "fixture.jpg"
            video_fixture.write_bytes(b"fake-video")
            image_fixture.write_bytes(b"fake-image")
            execute_media_download(
                package_dir=package,
                provider="local-fixture",
                fixture_file=video_fixture,
                selected_shortcode="CHILD2",
            )
            execute_media_download(
                package_dir=package,
                provider="local-fixture",
                fixture_file=image_fixture,
                selected_shortcode="CHILD1",
            )

            result = check_package_readiness(package)

            self.assertTrue(result.package_ready_for_instagram_draft)
            self.assertEqual(result.media_status, "media_downloaded")
            self.assertEqual(result.blockers, [])
            self.assertIn("ready", result.safe_next_step)

    def test_package_readiness_blocks_unsafe_media_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self.build_two_item_package(root)
            media_manifest_path = package / "media_manifest.json"
            media_manifest = json.loads(media_manifest_path.read_text())
            media_manifest["items"][0]["expected_media_relative_path"] = "../escape.mp4"
            media_manifest_path.write_text(json.dumps(media_manifest), encoding="utf-8")

            result = check_package_readiness(package)

            self.assertFalse(result.package_ready_for_instagram_draft)
            self.assertTrue(any("Unsafe media path" in blocker for blocker in result.blockers))

    def test_manual_review_pack_writes_caption_checklist_and_boundary_without_ready_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self.build_two_item_package(root)

            result = build_manual_review_pack(package)

            self.assertEqual(result.media_status, "media_not_downloaded")
            self.assertFalse(result.package_ready_for_instagram_draft)
            self.assertTrue(Path(result.review_pack_path).exists())
            self.assertTrue(Path(result.caption_path).exists())
            self.assertTrue(Path(result.checklist_path).exists())
            caption = Path(result.caption_path).read_text(encoding="utf-8")
            self.assertIn("finds draft prepared", caption)
            self.assertIn("https://www.instagram.com/p/ABC123/", caption)
            review_text = Path(result.review_pack_path).read_text(encoding="utf-8")
            self.assertIn("does not log into Instagram", review_text)
            self.assertIn("Missing downloaded media for CHILD2", review_text)
            checklist = json.loads(Path(result.checklist_path).read_text(encoding="utf-8"))
            self.assertEqual(checklist["schema_version"], "personal_curation_manual_review_checklist_v0_1")
            self.assertIn("no Instagram login", checklist["boundary"])

    def test_manual_review_pack_reports_ready_after_all_local_fixture_media_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self.build_two_item_package(root)
            video_fixture = root / "fixture.mp4"
            image_fixture = root / "fixture.jpg"
            video_fixture.write_bytes(b"fake-video")
            image_fixture.write_bytes(b"fake-image")
            execute_media_download(
                package_dir=package,
                provider="local-fixture",
                fixture_file=video_fixture,
                selected_shortcode="CHILD2",
            )
            execute_media_download(
                package_dir=package,
                provider="local-fixture",
                fixture_file=image_fixture,
                selected_shortcode="CHILD1",
            )

            result = build_manual_review_pack(package)

            self.assertTrue(result.package_ready_for_instagram_draft)
            self.assertEqual(result.media_status, "media_downloaded")
            checklist = json.loads(Path(result.checklist_path).read_text(encoding="utf-8"))
            self.assertEqual(checklist["blockers"], [])
            self.assertTrue(all(item["file_exists"] for item in checklist["items"]))


if __name__ == "__main__":
    unittest.main()
