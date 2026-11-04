import io
import json
import os
import tempfile
import unittest
from unittest import mock

import main


def make_deal(appid, name, final, original, discount, image="http://img/x.jpg"):
    return {
        "id": str(appid),
        "name": name,
        "final_price": final,
        "original_price": original,
        "discount_percent": discount,
        "header_image": image,
    }


class TestSelectNewDeals(unittest.TestCase):
    def test_first_run_all_pushed(self):
        deals = [make_deal(570, "Dota 2", 2999, 14999, 80)]
        self.assertEqual(len(main.select_new_deals(deals, {})), 1)

    def test_same_price_pushed_again(self):
        deals = [make_deal(570, "Dota 2", 2999, 14999, 80)]
        pushed = {"570": {"price": 2999, "discount": 80, "when": "2026-11-03"}}
        self.assertEqual(len(main.select_new_deals(deals, pushed)), 1)

    def test_lower_price_pushed(self):
        deals = [make_deal(570, "Dota 2", 1999, 14999, 87)]
        pushed = {"570": {"price": 2999, "discount": 80, "when": "2026-11-03"}}
        self.assertEqual(len(main.select_new_deals(deals, pushed)), 1)

    def test_higher_price_skipped(self):
        deals = [make_deal(570, "Dota 2", 3999, 14999, 73)]
        pushed = {"570": {"price": 2999, "discount": 80, "when": "2026-11-03"}}
        self.assertEqual(main.select_new_deals(deals, pushed), [])

    def test_malformed_record_treated_as_unseen(self):
        deals = [make_deal(570, "Dota 2", 2999, 14999, 80)]
        pushed = {"570": {"discount": 80}}
        self.assertEqual(len(main.select_new_deals(deals, pushed)), 1)

    def test_sorted_discount_desc_then_price_asc(self):
        deals = [
            make_deal(1, "a", 5000, 10000, 50),
            make_deal(2, "b", 3000, 10000, 80),
            make_deal(3, "c", 2000, 10000, 80),
        ]
        result = main.select_new_deals(deals, {})
        self.assertEqual([d["id"] for d in result], ["3", "2", "1"])


class TestLoadPushed(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        self.assertEqual(main.load_pushed(os.path.join("no", "such", "file.json")), {})

    def test_valid_file_returns_pushed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "pushed": {"570": {"price": 1}}}, f)
            self.assertEqual(main.load_pushed(path), {"570": {"price": 1}})

    def test_broken_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            self.assertEqual(main.load_pushed(path), {})

    def test_non_dict_top_level_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("null")
            self.assertEqual(main.load_pushed(path), {})


def mock_urlopen(payload):
    """构造可 with 的假 urlopen 返回值（内部为 BytesIO）。"""
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))
    cm = mock.MagicMock()
    cm.__enter__.return_value = body
    cm.__exit__.return_value = False
    return cm


FAKE_PAYLOAD = {
    "specials": {
        "id": "specials",
        "name": "Specials",
        "items": [
            {"id": 570, "name": "Dota 2", "discount_percent": 80,
             "original_price": 14999, "final_price": 2999,
             "header_image": "http://img/570.jpg", "type": 1},
            {"id": 730, "name": "CS2 no discount", "discount_percent": 0,
             "original_price": 0, "final_price": 0,
             "header_image": "http://img/730.jpg", "type": 1},
            {"id": 999, "name": "broken item", "discount_percent": 50},  # 缺 final_price → 跳过
        ],
    },
    "genres": ["Action", "RPG"],  # 列表型分类 → 跳过
    "top_sellers": {
        "items": [
            {"id": 814380, "name": "Sekiro", "discount_percent": 75,
             "original_price": 26800, "final_price": 6700,
             "header_image": "http://img/814380.jpg", "type": 1},
            {"id": 570, "name": "Dota 2 dup weaker", "discount_percent": 50,
             "original_price": 14999, "final_price": 7499,
             "header_image": "http://img/570.jpg", "type": 1},
        ],
    },
}


class TestExtractDeals(unittest.TestCase):
    def test_extracts_discounted_items_only(self):
        deals = main.extract_deals(FAKE_PAYLOAD)
        self.assertEqual(len(deals), 2)
        by_id = {d["id"]: d for d in deals}
        self.assertEqual(by_id["570"]["discount_percent"], 80)      # 保留折扣最大
        self.assertEqual(by_id["570"]["final_price"], 2999)
        self.assertEqual(by_id["814380"]["final_price"], 6700)
        self.assertNotIn("730", by_id)   # 无折扣
        self.assertNotIn("999", by_id)   # 坏条目


class TestFetchJson(unittest.TestCase):
    @mock.patch("main.time.sleep")
    @mock.patch("main.urllib.request.urlopen")
    def test_retry_then_success(self, urlopen_mock, sleep_mock):
        urlopen_mock.side_effect = [OSError("boom"), OSError("boom"), mock_urlopen({"ok": 1})]
        self.assertEqual(main.fetch_json("http://x"), {"ok": 1})

    @mock.patch("main.time.sleep")
    @mock.patch("main.urllib.request.urlopen", side_effect=OSError("down"))
    def test_all_retries_fail_raises(self, urlopen_mock, sleep_mock):
        with self.assertRaises(OSError):
            main.fetch_json("http://x")


class TestFetchFeaturedDeals(unittest.TestCase):
    @mock.patch("main.extract_deals", return_value=[make_deal(1, "x", 100, 200, 50)])
    @mock.patch("main.fetch_json", return_value={"specials": {}})
    def test_wiring(self, fetch_json_mock, extract_mock):
        deals = main.fetch_featured_deals()
        fetch_json_mock.assert_called_once_with(main.STEAM_API_URL)
        self.assertEqual(deals, [make_deal(1, "x", 100, 200, 50)])
