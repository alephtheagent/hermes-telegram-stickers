#!/usr/bin/env python3
"""Comprehensive test suite for telegram-stickers plugin."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Ensure current plugin directory is in sys.path
PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import stickers
from stickers import (
    StickerService,
    StickerStorage,
    TelegramClient,
    extract_pack_name,
    extract_video_keyframe,
    parse_vision_analysis,
    score_sticker,
    search_stickers,
    telegram_add_stickers_handler,
    telegram_list_sticker_packs_handler,
    telegram_send_sticker_handler,
)
import __init__ as plugin_init


class TestStickerHelpers(unittest.TestCase):
    """Test helper functions."""

    def test_extract_pack_name(self):
        self.assertEqual(extract_pack_name("Sam_Altman"), "Sam_Altman")
        self.assertEqual(
            extract_pack_name("https://t.me/addstickers/Sam_Altman"), "Sam_Altman"
        )
        self.assertEqual(
            extract_pack_name("https://telegram.me/addstickers/Animals/"), "Animals"
        )
        self.assertEqual(
            extract_pack_name("tg://addstickers?set=hot_cherry"), "hot_cherry"
        )
        self.assertEqual(extract_pack_name(""), "")

    def test_parse_vision_analysis(self):
        text = (
            "An image of Sam Altman pointing his finger forward with an intense, confident expression.\n"
            "Tags: sam altman, pointing, confident, leader, serious"
        )
        desc, tags = parse_vision_analysis(text, fallback_emoji="👉")
        self.assertIn("pointing his finger", desc)
        self.assertIn("sam altman", tags)
        self.assertIn("confident", tags)
        self.assertIn("serious", tags)

    def test_parse_vision_analysis_fallback(self):
        desc, tags = parse_vision_analysis("", fallback_emoji="🔥")
        self.assertEqual(desc, "Sticker depicting 🔥")
        self.assertEqual(tags, ["🔥"])


class TestStickerStorageAndSearch(unittest.TestCase):
    """Test storage indexing and search ranking."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_stickers_")
        self.storage = StickerStorage(base_dir=Path(self.test_dir))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_read_empty_index(self):
        data = self.storage.load()
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["packs"], {})
        self.assertEqual(data["stickers"], [])

    def test_upsert_and_read_index(self):
        stickers_sample = [
            {
                "file_id": "file_1",
                "file_unique_id": "uid_1",
                "emoji": "👍",
                "description": "Sam Altman showing thumbs up with a smile",
                "mood_tags": ["альтман", "одобрение", "thumbs up", "улыбка"],
                "pack_name": "Sam_Altman",
                "pack_title": "Sam Altman Pack",
                "format": "static",
            },
            {
                "file_id": "file_2",
                "file_unique_id": "uid_2",
                "emoji": "😱",
                "description": "Mellstroy looking shocked and screaming in disbelief",
                "mood_tags": ["меллстрой", "шок", "крик", "disbelief", "screaming"],
                "pack_name": "Mellstroy_Memes",
                "pack_title": "Mellstroy Pack",
                "format": "static",
            },
            {
                "file_id": "file_3",
                "file_unique_id": "uid_3",
                "emoji": "😂",
                "description": "A laughing cat rolling on the floor laughing tears of joy",
                "mood_tags": ["смех", "ржу", "lol", "cat", "joy"],
                "pack_name": "Funny_Cats",
                "pack_title": "Funny Cats Pack",
                "format": "animated",
            },
        ]

        self.storage.upsert_pack_stickers(
            pack_name="Sam_Altman",
            pack_title="Sam Altman Pack",
            stickers=[stickers_sample[0]],
        )
        self.storage.upsert_pack_stickers(
            pack_name="Mellstroy_Memes",
            pack_title="Mellstroy Pack",
            stickers=[stickers_sample[1]],
        )
        self.storage.upsert_pack_stickers(
            pack_name="Funny_Cats",
            pack_title="Funny Cats Pack",
            stickers=[stickers_sample[2]],
            is_animated=True,
        )

        loaded = self.storage.load()
        self.assertEqual(len(loaded["stickers"]), 3)
        self.assertEqual(len(loaded["packs"]), 3)
        self.assertIn("Sam_Altman", loaded["packs"])
        self.assertIn("Mellstroy_Memes", loaded["packs"])

        # Test search: 'альтман'
        res = search_stickers(loaded["stickers"], query="альтман")
        self.assertTrue(len(res) > 0)
        self.assertEqual(res[0][0]["file_id"], "file_1")

        # Test search: 'шок'
        res = search_stickers(loaded["stickers"], query="шок")
        self.assertTrue(len(res) > 0)
        self.assertEqual(res[0][0]["file_id"], "file_2")

        # Test search: 'смех'
        res = search_stickers(loaded["stickers"], query="смех")
        self.assertTrue(len(res) > 0)
        self.assertEqual(res[0][0]["file_id"], "file_3")

        # Test search: emoji
        res = search_stickers(loaded["stickers"], query="👍")
        self.assertTrue(len(res) > 0)
        self.assertEqual(res[0][0]["file_id"], "file_1")

        # Test pack filter
        res = search_stickers(loaded["stickers"], query="joy", pack="Funny_Cats")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0][0]["file_id"], "file_3")

        res_wrong_pack = search_stickers(loaded["stickers"], query="joy", pack="Sam_Altman")
        self.assertEqual(len(res_wrong_pack), 0)


class TestVideoKeyframeExtraction(unittest.TestCase):
    """Test ffmpeg frame extraction."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_ffmpeg_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_extract_video_keyframe_with_ffmpeg(self):
        video_path = Path(self.test_dir) / "test.webm"
        output_frame = Path(self.test_dir) / "frame.png"

        gen_cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "testsrc=duration=0.5:size=128x128:rate=10",
            "-c:v", "libvpx-vp9",
            str(video_path),
        ]
        res = subprocess.run(gen_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            self.skipTest("ffmpeg libvpx-vp9 encoding not available for test video")

        loop = asyncio.get_event_loop()
        extracted = loop.run_until_complete(extract_video_keyframe(video_path, output_frame))
        self.assertTrue(extracted)
        self.assertTrue(output_frame.exists())
        self.assertGreater(output_frame.stat().st_size, 0)


class TestStickerService(unittest.TestCase):
    """Test StickerService operations with mock Telegram API & Vision."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_service_")
        self.storage = StickerStorage(base_dir=Path(self.test_dir))
        self.mock_tg = MagicMock(spec=TelegramClient)
        self.mock_tg.token = "123456:FAKE_TOKEN"

        async def mock_vision(image_path: str, prompt: str):
            return json.dumps({
                "success": True,
                "analysis": "A sticker of a cute happy shiba inu dog.\nTags: dog, shiba, happy, cute, smile",
            })

        self.service = StickerService(
            storage=self.storage,
            tg_client=self.mock_tg,
            vision_analyzer=mock_vision,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_index_pack_static_and_video(self):
        self.mock_tg.get_sticker_set = AsyncMock(return_value={
            "name": "Dog_Pack",
            "title": "Dog Stickers",
            "is_animated": False,
            "is_video": False,
            "stickers": [
                {
                    "file_id": "file_dog_static",
                    "file_unique_id": "uid_dog_static",
                    "emoji": "🐶",
                    "is_animated": False,
                    "is_video": False,
                },
                {
                    "file_id": "file_dog_video",
                    "file_unique_id": "uid_dog_video",
                    "emoji": "🐕",
                    "is_animated": False,
                    "is_video": True,
                },
            ],
        })

        async def mock_get_file_info(file_id):
            if "video" in file_id:
                return {"file_id": file_id, "file_path": "stickers/dog_vid.webm"}
            return {"file_id": file_id, "file_path": "stickers/dog_img.webp"}

        self.mock_tg.get_file_info = AsyncMock(side_effect=mock_get_file_info)

        async def mock_download_file(file_path, dest_path):
            Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
            Path(dest_path).write_bytes(b"dummy image data")
            return dest_path

        self.mock_tg.download_file = AsyncMock(side_effect=mock_download_file)

        loop = asyncio.get_event_loop()
        res = loop.run_until_complete(self.service.index_pack("Dog_Pack", limit=10))

        self.assertTrue(res["success"])
        self.assertEqual(res["pack_name"], "Dog_Pack")
        self.assertEqual(res["indexed_count"], 2)

        loaded = self.storage.load()
        self.assertEqual(len(loaded["stickers"]), 2)
        self.assertIn("Dog_Pack", loaded["packs"])

        search_res = search_stickers(loaded["stickers"], query="happy shiba")
        self.assertTrue(len(search_res) > 0)
        self.assertEqual(search_res[0][0]["emoji"], "🐶")

    def test_send_best_matching_sticker(self):
        self.storage.upsert_pack_stickers(
            pack_name="Animals",
            pack_title="Animal Pack",
            stickers=[
                {
                    "file_id": "file_123",
                    "file_unique_id": "uid_123",
                    "emoji": "🦊",
                    "description": "A sly cunning red fox winking",
                    "mood_tags": ["fox", "sly", "winking", "хитрая лиса"],
                    "pack_name": "Animals",
                    "pack_title": "Animal Pack",
                }
            ],
        )

        self.mock_tg.send_sticker = AsyncMock(return_value={
            "message_id": 999,
            "chat": {"id": 12345},
        })

        loop = asyncio.get_event_loop()
        send_res = loop.run_until_complete(
            self.service.send_best_matching_sticker(
                query="хитрая лиса",
                chat_id="12345",
            )
        )

        self.assertTrue(send_res["success"])
        self.assertEqual(send_res["message_id"], 999)
        self.assertEqual(send_res["matched"]["file_id"], "file_123")
        self.assertEqual(send_res["matched"]["emoji"], "🦊")
        self.mock_tg.send_sticker.assert_called_once_with("12345", "file_123")

    def test_list_packs(self):
        self.storage.upsert_pack_stickers(
            pack_name="Pack_A",
            pack_title="Pack Alpha",
            stickers=[
                {
                    "file_id": "f_a",
                    "file_unique_id": "uid_a",
                    "emoji": "😎",
                    "mood_tags": ["cool", "glasses"],
                    "pack_name": "Pack_A",
                }
            ],
        )
        packs_res = self.service.list_packs()
        self.assertTrue(packs_res["success"])
        self.assertEqual(packs_res["total_packs"], 1)
        self.assertEqual(packs_res["packs"][0]["name"], "Pack_A")
        self.assertEqual(packs_res["packs"][0]["title"], "Pack Alpha")


class TestPluginToolRegistration(unittest.TestCase):
    """Test Hermes register(ctx) hook."""

    def test_register_hook(self):
        registered_tools = {}

        class MockCtx:
            def register_tool(self, **kwargs):
                registered_tools[kwargs["name"]] = kwargs

        plugin_init.register(MockCtx())

        self.assertIn("telegram_add_stickers", registered_tools)
        self.assertIn("telegram_send_sticker", registered_tools)
        self.assertIn("telegram_list_sticker_packs", registered_tools)

        add_tool = registered_tools["telegram_add_stickers"]
        self.assertEqual(add_tool["toolset"], "telegram_stickers")
        self.assertTrue(add_tool["is_async"])
        self.assertIn("pack_name", add_tool["schema"]["parameters"]["properties"])

        send_tool = registered_tools["telegram_send_sticker"]
        self.assertIn("query", send_tool["schema"]["parameters"]["properties"])


class TestLiveStickerIntegration(unittest.TestCase):
    """Optional live test against real Telegram Bot API and Vision tool."""

    def test_live_index_and_search(self):
        token = stickers.get_telegram_token()
        if not token:
            self.skipTest("No TELEGRAM_BOT_TOKEN available in environment")

        loop = asyncio.get_event_loop()
        res_str = loop.run_until_complete(
            stickers.telegram_add_stickers_handler({"pack_name": "Animals", "limit": 2})
        )
        data = json.loads(res_str)
        self.assertTrue(data.get("success"), f"Live indexing failed: {res_str}")
        self.assertEqual(data.get("pack_name"), "Animals")

        list_res = json.loads(
            loop.run_until_complete(stickers.telegram_list_sticker_packs_handler({}))
        )
        self.assertTrue(list_res.get("success"))
        self.assertGreaterEqual(list_res.get("total_packs", 0), 1)

        storage = stickers.StickerStorage()
        loaded = storage.load()
        search_res = stickers.search_stickers(loaded.get("stickers", []), query="doge")
        self.assertTrue(len(search_res) > 0)


def run_tests():
    is_live = "--live" in sys.argv
    if is_live:
        sys.argv.remove("--live")

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestStickerHelpers))
    suite.addTests(loader.loadTestsFromTestCase(TestStickerStorageAndSearch))
    suite.addTests(loader.loadTestsFromTestCase(TestVideoKeyframeExtraction))
    suite.addTests(loader.loadTestsFromTestCase(TestStickerService))
    suite.addTests(loader.loadTestsFromTestCase(TestPluginToolRegistration))

    if is_live:
        suite.addTests(loader.loadTestsFromTestCase(TestLiveStickerIntegration))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()



if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
