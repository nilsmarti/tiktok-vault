import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import httpx

from app.ai import Extraction
from app.config import _load_profiles
from app.db import Store
from app.imports import links, identity
from app.notion import Notion, adapt_properties, build_properties
from app.worker import import_favorites


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.props = build_properties(Extraction(title="Saved"), {}, is_slideshow=False,
                                      archive_uri="s3://test/post")

    def test_minimal_existing_database(self):
        self.assertEqual(adapt_properties(self.props, {"Title": {"type": "title"}}, {}),
                         {"Title": self.props["Name"]})

    def test_mapping_and_omission(self):
        schema = {"Title": {"type": "title"}, "Source": {"type": "url"},
                  "Status": {"type": "status"}}
        result = adapt_properties(self.props, schema, {"URL": "Source", "Summary": ""})
        self.assertEqual(set(result), {"Title", "Source"})

    def test_invalid_explicit_mapping(self):
        for schema, mapping in [({"Title": {"type": "title"}}, {"URL": "Missing"}),
                                ({"Title": {"type": "title"}}, {"URL": "Title"}),
                                ({"Title": {"type": "title"}}, {"Name": ""})]:
            with self.assertRaises(ValueError):
                adapt_properties(self.props, schema, mapping)

    def test_duplicate_mapping(self):
        props = {"Name": self.props["Name"], "Summary": {"rich_text": []},
                 "Creator": {"rich_text": []}}
        with self.assertRaises(ValueError):
            adapt_properties(props, {"Title": {"type": "title"}, "Text": {"type": "rich_text"}},
                             {"Summary": "Text", "Creator": "Text"})


class ImportTests(unittest.TestCase):
    def test_json_text_and_dedupe(self):
        data = {"Favorite Videos": [{"Link": "https://www.tiktok.com/@a/video/123?x=1"},
                                    {"Link": "https://www.tiktok.com/@a/video/123?x=2"},
                                    {"Link": "https://www.tiktok.com/@a/photo/456"}]}
        for text in [json.dumps(data), json.dumps(data).replace("/", r"\/")]:
            self.assertEqual(links(text), ["https://www.tiktok.com/@a/video/123",
                                           "https://www.tiktok.com/@a/photo/456"])

    def test_tiktok_export_share_links(self):
        text = "https://www.tiktokv.com/share/video/123/?region=CH\nhttps://www.tiktok.com/@a/video/123"
        self.assertEqual(links(text), ["https://www.tiktokv.com/share/video/123/"])
        self.assertEqual(identity(links(text)[0]), "123")

    def test_large_export(self):
        text = "\n".join(f"https://www.tiktokv.com/share/video/{i}/" for i in range(10000))
        self.assertEqual(len(links(text)), 10000)

    def test_host_boundaries(self):
        self.assertEqual(links("https://tiktok.com.evil.org/@a/video/123"), [])

    def test_identity(self):
        self.assertEqual(identity("https://www.tiktok.com/@a/video/123?x=1"), "123")


class ProfileTests(unittest.TestCase):
    def test_duplicate_users_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "profiles.yaml"
            path.write_text('profiles:\n  one: &p\n    telegram_user_id: 1\n'
                            '    notion_token: test\n    notion_database_id: abc\n  two: *p\n')
            with self.assertRaisesRegex(ValueError, "duplicate telegram_user_id"):
                _load_profiles(path)

    def test_sqlite_user_isolation_and_shared_connections(self):
        with tempfile.TemporaryDirectory() as td:
            a = Store(Path(td) / "index.db")
            b = Store(Path(td) / "index.db")
            try:
                a.put("one", "tiktok", "123", "url-one", "one/123")
                b.put("two", "tiktok", "123", "url-two", "two/123")
                self.assertEqual(b.get("one", "tiktok", "123"), "url-one")
                self.assertEqual(a.get("two", "tiktok", "123"), "url-two")
            finally:
                a.conn.close()
                b.conn.close()


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_batch_is_bounded_and_resumes(self):
        redis = AsyncMock()
        urls = [f"https://www.tiktok.com/@a/video/{i}" for i in range(250)]
        redis.lrange.return_value = [url.encode() for url in urls[:100]]
        await import_favorites({"redis": redis}, 1, 2, 3, "favorites:test")
        calls = redis.enqueue_job.call_args_list
        self.assertEqual(len(calls), 101)
        self.assertEqual(calls[-1].args, ("import_favorites", 1, 2, 3, "favorites:test", 100))
        self.assertTrue(all(c.kwargs["_expires"] == 30 * 86400 for c in calls))
        self.assertEqual(len({c.kwargs["_job_id"] for c in calls[:-1]}), 100)

    async def test_partial_batch_and_user_job_isolation(self):
        redis = AsyncMock()
        redis.lrange.return_value = [b"https://www.tiktok.com/@a/video/123"]
        await import_favorites({"redis": redis}, 1, 2, 3, "favorites:one", 100)
        first = redis.enqueue_job.call_args.kwargs["_job_id"]
        self.assertEqual(redis.enqueue_job.call_count, 1)
        redis.lrange.assert_awaited_once_with("favorites:one", 100, 199)
        await import_favorites({"redis": redis}, 2, 2, 3, "favorites:two", 100)
        self.assertNotEqual(first, redis.enqueue_job.call_args.kwargs["_job_id"])

    async def test_notion_rate_limits_retries_too(self):
        redis = AsyncMock()
        redis.eval.return_value = b"0"
        calls = []
        def handle(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, json={"ok": True})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            self.assertEqual(await Notion(http, "private-token", redis)._req("GET", "/users/me"),
                             {"ok": True})
        self.assertEqual(redis.eval.await_count, 2)
        self.assertNotIn("private-token", redis.eval.call_args.args[-1])

    async def test_existing_page_query_uses_mapping(self):
        def handle(request):
            self.assertEqual(json.loads(request.content)["filter"],
                             {"property": "Source", "url": {"equals": "https://example.org"}})
            return httpx.Response(200, json={"results": [{"url": "existing-page"}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            result = await Notion(http, "token").find_existing("db", {"Source": {"type": "url"}},
                                                               {"URL": "Source"}, "https://example.org")
            self.assertEqual(result["url"], "existing-page")

    async def test_no_url_property_needs_no_query(self):
        nc = Notion(AsyncMock(), "token")
        self.assertIsNone(await nc.find_existing("db", {}, {}, "url"))
        nc.http.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
