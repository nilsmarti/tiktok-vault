import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import pipeline, worker
from app.fetch import Post
from app.imports import collection_records


def record(collection="beauty_health", kind="video"):
    return {"123": {"video_id": "123", "creator": "Creator", "creator_id": "creator",
        "description": "Original caption https://www.tiktok.com/@other/video/456",
        "url": f"https://www.tiktok.com/@creator/{kind}/123",
        "thumbnail_url": "./local/image.jpg", "collection_name": collection, "post_type": kind}}


class CollectionTests(unittest.TestCase):
    def test_metadata_and_photo_type_preserved_without_extracting_caption_links(self):
        source = record(kind="photo")
        rows = collection_records(json.dumps(source))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bookmark"]["collections"], ["beauty/health"])
        self.assertEqual(rows[0]["bookmark"]["source_record"], source["123"])

    def test_approved_aliases(self):
        for source, target in (("career_education", "career/education"),
                               ("graphics_ressources_websites", "graphics ressources")):
            self.assertEqual(collection_records(json.dumps(record(source)))[0]
                             ["bookmark"]["collections"], [target])

    def test_invalid_records_reject_whole_file(self):
        for field, value in (("collection_name", "unknown"), ("video_id", "456"),
                             ("url", "https://evil.example/video/123"),
                             ("post_type", "photo"), ("description", None)):
            source = record()
            source["123"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                collection_records(json.dumps(source))

    def test_duplicate_json_keys_are_not_silently_lost(self):
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            collection_records('{"123": {}, "123": {}}')


class CollectionAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_keeps_context_and_continuation_format(self):
        row = collection_records(json.dumps(record()))[0]
        redis = AsyncMock()
        redis.lrange.return_value = [json.dumps(row).encode()] * 100
        await worker.import_favorites({"redis": redis, "job_try": 1}, 1, 2, 3,
                                      "favorites:test", structured=True)
        calls = redis.enqueue_job.call_args_list
        self.assertEqual(calls[0].kwargs["bookmark"], row["bookmark"])
        self.assertEqual(calls[-1].args[-1], 100)
        self.assertTrue(calls[-1].kwargs["structured"])

    async def test_original_caption_and_creator_reach_archive(self):
        row = collection_records(json.dumps(record()))[0]
        post = Post(row["url"], "123", False, {"description": "thin live caption"})
        archive = AsyncMock()
        with patch.object(pipeline.fetch, "canonicalize", AsyncMock(return_value=post.url)), \
             patch.object(pipeline.fetch, "resolve", return_value=post), \
             patch.object(pipeline, "_archive", archive):
            await pipeline.run(SimpleNamespace(tiktok_cookies_file=None), AsyncMock(), None, None,
                SimpleNamespace(notion_mode="moodboard"), post.url, bookmark=row["bookmark"])
        self.assertEqual(post.meta["description"], row["bookmark"]["source_record"]["description"])
        self.assertEqual(post.meta["uploader"], "Creator")
        self.assertEqual(post.meta["source_record"], record()["123"])


if __name__ == "__main__":
    unittest.main()
