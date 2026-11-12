import contextlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app import bot, pipeline
from app.ai import Extraction
from app.bookmarks import parse_bookmark
from app.config import Profile
from app.fetch import PermanentError, Post
from app.notion import Notion

SHORTCUT = """selected_collection: vid content ideas

link: https://vm.tiktok.com/ZN8PXSHgU/

attachements_link: https://unneeded.example/tunnel?sig=never-use-this

tags: [0]: Touch Designer
[1]: Video
"""


class ParserTests(unittest.TestCase):
    def test_resharing_same_post_uses_new_job_but_redelivery_is_stable(self):
        url = 'https://www.tiktok.com/@a/video/123'
        first = bot.job_id(1, url, 10)
        self.assertEqual(first, bot.job_id(1, url + '?tracking=x', 10))
        self.assertNotEqual(first, bot.job_id(1, url, 11))
        self.assertNotEqual(first, bot.job_id(2, url, 10))

    def test_android_numbered_tags_and_optional_ignored_attachment(self):
        for text in (SHORTCUT, SHORTCUT.replace(
                'attachements_link: https://unneeded.example/tunnel?sig=never-use-this', '')):
            result = parse_bookmark(text)
            self.assertEqual(result.url, 'https://vm.tiktok.com/ZN8PXSHgU/')
            self.assertEqual(result.collections, ['vid content ideas'])
            self.assertEqual(result.tags, ['Touch Designer', 'Video'])
            self.assertNotIn('never-use-this', str(result))
            self.assertEqual(set(result.context()), {'collections', 'tags'})

    def test_json_lists_multiline_fields_and_unicode_names(self):
        result = parse_bookmark('selected_collection: ["interior", "inspo", "interior"]\n'
                                'link:\nhttps://www.tiktok.com/@a/video/123\n'
                                'tags: ["Touch Designer", "Video", "Video"]')
        self.assertEqual(result.collections, ['interior', 'inspo'])
        self.assertEqual(result.tags, ['Touch Designer', 'Video'])

    def test_missing_tags_are_empty(self):
        self.assertEqual(parse_bookmark('selected_collection: inspo\n'
            'link: https://vm.tiktok.com/123/').tags, [])

    def test_invalid_inputs_do_not_queue_multiple_or_guess(self):
        for text in (
            'selected_collection: fake\nlink: https://vm.tiktok.com/123/',
            'selected_collection: inspo\nlink: https://vm.tiktok.com/123/ https://vm.tiktok.com/456/',
            'selected_collection: inspo\nlink: https://vm.tiktok.com/123/\nlink: https://vm.tiktok.com/456/',
            'selected_collection: inspo\nattachements_link: https://vm.tiktok.com/123/',
            'selected_collection: inspo\nlink: https://evil.example/https://vm.tiktok.com/123/',
            'selected_collection: inspo\nlink: https://instagram.com/reel/123',
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_bookmark(text)


class ShortcutAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_passes_only_explicit_context_to_one_job(self):
        profile = SimpleNamespace(notion_mode='moodboard')
        m = SimpleNamespace(from_user=SimpleNamespace(id=511728563), text=SHORTCUT,
                            caption=None, chat=SimpleNamespace(id=2), message_id=3, reply=AsyncMock())
        redis = AsyncMock()
        with patch.object(bot, 'get_settings', return_value=SimpleNamespace(profiles={511728563: profile})):
            await bot.on_message(m, redis)
        redis.enqueue_job.assert_awaited_once()
        call = redis.enqueue_job.call_args
        self.assertEqual(call.args, ('process_tiktok', 511728563, 2, 3, 'https://vm.tiktok.com/ZN8PXSHgU/'))
        self.assertEqual(call.kwargs['bookmark'], {'collections': ['vid content ideas'],
                                                 'tags': ['Touch Designer', 'Video']})
        self.assertNotIn('never-use-this', str(call))

    async def test_bot_rejects_invalid_shortcut_without_enqueue(self):
        m = SimpleNamespace(from_user=SimpleNamespace(id=1), text='https://vm.tiktok.com/123/',
                            caption=None, reply=AsyncMock())
        redis = AsyncMock()
        with patch.object(bot, 'get_settings', return_value=SimpleNamespace(
                profiles={1: SimpleNamespace(notion_mode='moodboard')})):
            await bot.on_message(m, redis)
        redis.enqueue_job.assert_not_awaited()
        m.reply.assert_awaited_once()

    async def test_rejected_enqueue_does_not_claim_queued(self):
        m = SimpleNamespace(from_user=SimpleNamespace(id=1), text=SHORTCUT, caption=None,
                            chat=SimpleNamespace(id=1), message_id=2, reply=AsyncMock())
        redis = AsyncMock()
        redis.enqueue_job.return_value = None
        with patch.object(bot, 'get_settings', return_value=SimpleNamespace(
                profiles={1: SimpleNamespace(notion_mode='moodboard')})):
            await bot.on_message(m, redis)
        self.assertNotIn('Queued 1', m.reply.call_args.args[0])
        self.assertIn('already queued or processed', m.reply.call_args.args[0])

    async def test_relation_resolution_paginates_and_matches_exact_titles(self):
        nc = Notion(AsyncMock(), 'test')
        nc.database_schema = AsyncMock(return_value={'Title': {'type': 'title'}})
        nc._req = AsyncMock(side_effect=[
            {'results': [{'id': 'wrong-case', 'properties': {'Title': {'title': [{'plain_text': 'fashion'}]}}}],
             'has_more': True, 'next_cursor': 'next'},
            {'results': [{'id': 'right', 'properties': {'Title': {'title': [{'plain_text': 'Fashion'}]}}}],
             'has_more': False},
        ])
        result = await nc.relation_pages({'Relationship': {'relation': {'database_id': 'actual-target'}}},
                                          'Relationship', ['Fashion'])
        self.assertEqual(result, {'Fashion': ['right']})
        self.assertEqual(nc._req.call_args.kwargs['json']['start_cursor'], 'next')
        self.assertEqual(nc._req.call_args.args, ('POST', '/databases/actual-target/query'))

    async def test_verification_checks_files_and_every_paginated_media_block(self):
        nc = Notion(AsyncMock(), 'test')
        items = [{'url': 'https://media.example/1', 'filename': '1.mp4', 'content_type': 'video/mp4'},
                 {'url': 'https://media.example/2', 'filename': '2.jpg', 'content_type': 'image/jpeg'}]
        page = {'properties': {'Files & media': {'files': [
            {'external': {'url': i['url']}} for i in items]}}}
        first = {'results': [{'type': 'video', 'video': {'external': {'url': items[0]['url']}}}],
                 'has_more': True, 'next_cursor': 'next'}
        second = {'results': [{'type': 'image', 'image': {'external': {'url': items[1]['url']}}}],
                  'has_more': False}
        nc._req = AsyncMock(side_effect=[page, first, second])
        await nc.verify_external_media('page', items)
        self.assertEqual(nc._req.call_args.kwargs['params']['start_cursor'], 'next')
        nc._req = AsyncMock(side_effect=[page, {'results': [], 'has_more': False}])
        with self.assertRaisesRegex(ValueError, '1.mp4: missing video body block'):
            await nc.verify_external_media('page', items)

    async def exercise_pipeline(self, slideshow=False, verification_failure=False):
        schema = {
            'Name': {'type': 'title'}, 'Link': {'type': 'url'},
            'Tags': {'type': 'multi_select', 'multi_select': {'options': [
                {'name': n, 'id': n} for n in ('Video', 'Content Idea', 'TikTok', 'Touch Designer')]}},
            'Relationship': {'type': 'multi_select', 'multi_select': {'options': [
                {'name': 'Content Creation', 'id': 'content'}]}},
            'Files & media': {'type': 'files'},
        }
        nc = AsyncMock()
        nc.database_schema.return_value = schema
        nc.find_existing.return_value = None
        nc.create_page.return_value = {'id': 'page', 'url': 'https://notion.example/page'}
        if verification_failure:
            nc.verify_external_media.side_effect = ValueError('video.mp4: missing video body block')
        storage = MagicMock()
        storage.public_base = 'https://media.example'
        storage.public_url.side_effect = lambda key: storage.public_base + '/' + key
        store = MagicMock()
        store.get.return_value = None
        profile = Profile(511728563, 'partner', 'test', 'db', notion_mode='moodboard')
        post = Post('https://www.tiktok.com/@a/photo/123' if slideshow else
                    'https://www.tiktok.com/@a/video/123', '123', slideshow, {})
        bookmark = {'collections': ['vid content ideas'], 'tags': ['Touch Designer', 'Video']}
        with tempfile.TemporaryDirectory() as td, contextlib.ExitStack() as stack:
            root = Path(td)
            s = SimpleNamespace(data_dir=root, tiktok_cookies_file=None, max_frames=10, stt_base_url=None)
            stack.enter_context(patch.object(pipeline.notion, 'Notion', return_value=nc))
            stack.enter_context(patch.object(pipeline.ai, 'extract', AsyncMock(return_value=Extraction(tags=['invented']))))
            stack.enter_context(patch.object(pipeline.fetch, 'download_video', return_value=SimpleNamespace(video_path=root/'v.mp4')))
            stack.enter_context(patch.object(pipeline.fetch, 'download_slideshow', return_value=SimpleNamespace(
                image_paths=[root/'1.jpg', root/'2.jpg', root/'3.jpg'], audio_path=None)))
            stack.enter_context(patch.object(pipeline.media, 'normalize', return_value=(root/'v.mp4', {})))
            stack.enter_context(patch.object(pipeline.media, 'duration', return_value=1))
            stack.enter_context(patch.object(pipeline.media, 'thumbnail', return_value=root/'thumb.jpg'))
            stack.enter_context(patch.object(pipeline.media, 'keyframes', return_value=[]))
            stack.enter_context(patch.object(pipeline.media, 'extract_audio', return_value=None))
            if verification_failure:
                with self.assertRaises(PermanentError):
                    await pipeline._archive(s, AsyncMock(), storage, store, profile, post, None, bookmark)
                store.put.assert_not_called()
                return
            result = await pipeline._archive(s, AsyncMock(), storage, store, profile, post, None, bookmark)
        props = nc.create_page.call_args.args[1]
        self.assertEqual(result.title, 'TikTok - vid content ideas')
        self.assertIn('Touch Designer', result.tags)
        self.assertNotIn('invented', result.tags)
        self.assertEqual(len(props['Files & media']['files']), 3 if slideshow else 1)
        self.assertEqual(set(props), {'Name', 'Link', 'Tags', 'Relationship', 'Files & media'})
        nc.upload.assert_not_awaited()
        nc.verify_external_media.assert_awaited_once()
        store.put.assert_called_once()
        archived = storage.put_json.call_args.args[0]
        self.assertEqual(archived['bookmark'], bookmark)
        self.assertEqual(len(archived['media_attachments']), 3 if slideshow else 1)

    async def test_video_pipeline_uses_explicit_tags_and_no_notion_uploads(self):
        await self.exercise_pipeline()

    async def test_slideshow_pipeline_preserves_every_image(self):
        await self.exercise_pipeline(slideshow=True)

    async def test_failed_verification_does_not_mark_success(self):
        await self.exercise_pipeline(verification_failure=True)

    async def test_retry_reuses_existing_page_and_reports_incomplete_attachment(self):
        schema = {'Name': {'type': 'title'}, 'Link': {'type': 'url'},
                  'Tags': {'type': 'multi_select', 'multi_select': {
                      'options': [{'name': n, 'id': n} for n in ('Inspo', 'TikTok')]}},
                  'Files & media': {'type': 'files'}}
        nc = AsyncMock()
        nc.database_schema.return_value = schema
        nc.find_existing.return_value = {'id': 'original', 'url': 'https://notion.example/original'}
        nc.verify_external_media.side_effect = ValueError('video.mp4: missing video body block')
        storage, store = MagicMock(), MagicMock()
        storage.public_base = 'https://media.example'
        storage.get_json.return_value = {'bookmark': {'collections': ['inspo'], 'tags': []},
                                         'media_attachments': [{'filename': 'video.mp4'}]}
        store.get.return_value = None
        with patch.object(pipeline.notion, 'Notion', return_value=nc), tempfile.TemporaryDirectory() as td:
            profile = Profile(1, 'partner', 'test', 'db', notion_mode='moodboard')
            with self.assertRaisesRegex(PermanentError, 'video.mp4: missing'):
                await pipeline._archive(SimpleNamespace(data_dir=Path(td)), AsyncMock(), storage,
                    store, profile, Post('https://www.tiktok.com/@a/video/123', '123', False),
                    None, {'collections': ['inspo'], 'tags': []})
        nc.create_page.assert_not_awaited()
        store.put.assert_not_called()
        nc.verify_external_media.assert_awaited_once_with('original', [{'filename': 'video.mp4'}])


if __name__ == '__main__':
    unittest.main()
