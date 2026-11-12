import contextlib
import io
import unittest
from unittest.mock import AsyncMock, patch

from app.moodboard import Attachment, COLLECTIONS, plan_bookmark
from scripts.inspect_moodboard import inspect


class MoodboardTests(unittest.TestCase):
    def setUp(self):
        tags = sorted({tag for c in COLLECTIONS.values() for tag in c.tags} | {"TikTok", "Extra"})
        relationships = sorted({v for c in COLLECTIONS.values() for v in c.relationships})
        projects = sorted({v for c in COLLECTIONS.values() for v in c.projects})
        self.schema = {
            "Title": {"type": "title"},
            "Link": {"type": "url"},
            "Tags": {"type": "multi_select", "multi_select": {
                "options": [{"id": f"tag-{i}", "name": name} for i, name in enumerate(tags)]}},
            "Relationship": {"type": "multi_select", "multi_select": {
                "options": [{"id": f"rel-{i}", "name": name} for i, name in enumerate(relationships)]}},
            "Projects": {"type": "relation"},
            "Files & media": {"type": "files"},
            "Status": {"type": "select", "select": {"options": []}},
        }
        self.relations = {"Projects": {name: [f"project-{i}"] for i, name in enumerate(projects)}}

    def plan(self, collections=None, tags=None, attachments=None):
        return plan_bookmark(collections=collections or ["inspo"], tags=tags or [],
                             attachments=attachments or [], schema=self.schema,
                             relations=self.relations, link="https://www.tiktok.com/@a/video/123")

    def test_all_collections_can_be_resolved_without_creating_options(self):
        self.assertEqual(len(COLLECTIONS), 23)
        for name in COLLECTIONS:
            with self.subTest(collection=name):
                props = self.plan([name])["properties"]
                self.assertEqual(props["Title"]["title"][0]["text"]["content"], "TikTok - " + name)
                self.assertNotIn("Status", props)
                for field in ("Tags", "Relationship", "Projects"):
                    if field in props:
                        for value in next(iter(props[field].values())):
                            self.assertEqual(set(value), {"id"})

    def test_union_dedupes_and_ignores_unknown_explicit_tags(self):
        result = self.plan(["fashion inspo", "Sewing/customizing", "fashion inspo"],
                           ["Fashion", "Extra", "made-up", "fashion"])
        values = result["properties"]["Tags"]["multi_select"]
        self.assertEqual(len(values), 5)  # Fashion, Inspo, DIY, TikTok, Extra
        self.assertEqual(len({v["id"] for v in values}), 5)
        self.assertEqual(result["ignored_tags"], ["made-up", "fashion"])

    def test_unspecified_properties_are_omitted(self):
        self.assertEqual(set(self.plan()["properties"]), {"Title", "Link", "Tags"})

    def test_missing_required_tag_is_an_error(self):
        self.schema["Tags"]["multi_select"]["options"] = []
        with self.assertRaisesRegex(ValueError, "missing existing values"):
            self.plan()

    def test_unknown_collection_is_not_guessed(self):
        with self.assertRaisesRegex(ValueError, "exactly"):
            self.plan(["graphics resources"])

    def test_project_names_preserve_unicode_and_spacing(self):
        name = COLLECTIONS["interior"].projects[0]
        self.assertEqual(name, "//ｉｎｔｅｒｉｏｒ_")
        result = self.plan(["interior"])
        self.assertEqual(result["properties"]["Projects"]["relation"],
                         [{"id": self.relations["Projects"][name][0]}])

    def test_missing_or_ambiguous_related_page_is_an_error(self):
        for ids in ([], ["first", "second"]):
            self.relations["Projects"]["// ｓｅｗｉｎｇ_"] = ids
            with self.assertRaisesRegex(ValueError, "exactly one existing page"):
                self.plan(["fashion hacks"])

    def test_relationship_can_be_an_existing_relation(self):
        self.schema["Relationship"] = {"type": "relation"}
        self.relations["Relationship"] = {"Photography": ["photo"], "Content Creation": ["content"]}
        result = self.plan(["photography+editing"])
        self.assertEqual(result["properties"]["Relationship"]["relation"],
                         [{"id": "photo"}, {"id": "content"}])

    def test_single_select_does_not_drop_multiple_relationships(self):
        prop = self.schema["Relationship"]
        self.schema["Relationship"] = {"type": "select", "select": prop["multi_select"]}
        with self.assertRaisesRegex(ValueError, "multiple required values"):
            self.plan(["photography+editing"])

    def test_every_attachment_in_property_and_body_with_correct_type(self):
        items = [Attachment("https://media.example/a?signature=123", "a.mp4", "video/mp4"),
                 Attachment("https://media.example/b", "b.jpg", "image/jpeg"),
                 Attachment("https://media.example/c", "c.pdf", "application/pdf")]
        result = self.plan(attachments=items)
        self.assertEqual([b["type"] for b in result["children"]], ["video", "image", "file"])
        files = result["properties"]["Files & media"]["files"]
        self.assertEqual([f["external"]["url"] for f in files], [i.url for i in items])
        for block, item in zip(result["children"], items):
            self.assertEqual(block[block["type"]]["external"]["url"], item.url)

    def test_projects_require_relation_type(self):
        self.schema["Projects"] = {"type": "multi_select", "multi_select": {"options": []}}
        with self.assertRaisesRegex(ValueError, "relation property"):
            self.plan(["interior"])

    def test_streaming_does_not_require_tech_setup_tag(self):
        result = self.plan(["streaming/gaming"])["properties"]
        options = self.schema["Tags"]["multi_select"]["options"]
        tiktok_id = next(o["id"] for o in options if o["name"] == "TikTok")
        self.assertEqual(result["Tags"]["multi_select"], [{"id": tiktok_id}])
        self.assertIn("Relationship", result)
        self.assertIn("Projects", result)

    def test_unsupported_platform_and_spoofed_host_are_rejected(self):
        for link in ("https://instagram.com/reel/123", "https://tiktok.com.evil.org/video/123"):
            with self.assertRaisesRegex(ValueError, "TikTok"):
                plan_bookmark(collections=["inspo"], tags=[], attachments=[],
                              schema=self.schema, link=link)


class InspectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_only_reads_actual_relation_target(self):
        fixture = MoodboardTests()
        fixture.setUp()
        fixture.schema["Projects"]["relation"] = {"database_id": "actual-projects"}
        calls = []

        async def request(method, path, **kw):
            calls.append((method, path))
            if (method, path) == ("GET", "/databases/board"):
                from app.moodboard import DATABASE_NAME
                return {"title": [{"plain_text": DATABASE_NAME}], "properties": fixture.schema}
            self.assertEqual((method, path), ("POST", "/databases/actual-projects/query"))
            name = kw["json"]["filter"]["title"]["equals"]
            return {"results": [{"id": fixture.relations["Projects"][name][0],
                                 "properties": {"Project name": {"title": [{"plain_text": name}]}}}],
                    "has_more": False}

        nc = AsyncMock()
        nc._req.side_effect = request
        nc.database_schema.return_value = {"Project name": {"type": "title"}}
        with patch.dict("os.environ", {"NOTION_TOKEN_PARTNER": "private-test-token"}), \
             patch("scripts.inspect_moodboard.Notion", return_value=nc), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(await inspect("board"), 0)
        nc.database_schema.assert_awaited_once_with("actual-projects")
        self.assertTrue(all(method == "GET" or path.endswith("/query") for method, path in calls))
        self.assertNotIn("private-test-token", output.getvalue())
        self.assertEqual(output.getvalue().count("OK "), 23)

    async def test_wrong_database_name_stops_before_relations(self):
        nc = AsyncMock()
        nc._req.return_value = {"title": [{"plain_text": "Other database"}]}
        with patch.dict("os.environ", {"NOTION_TOKEN_PARTNER": "test"}), \
             patch("scripts.inspect_moodboard.Notion", return_value=nc), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(await inspect("board"), 1)
        nc._req.assert_awaited_once_with("GET", "/databases/board")
        nc.database_schema.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
