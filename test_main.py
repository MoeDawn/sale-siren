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
