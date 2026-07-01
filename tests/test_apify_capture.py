from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from curation_bot.apify_capture import CaptureError, capture_from_dataset
from curation_bot.core import CurationBotError, ingest_capture_record


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


if __name__ == "__main__":
    unittest.main()
