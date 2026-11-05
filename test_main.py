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


class TestYuan(unittest.TestCase):
    def test_formats_fen(self):
        self.assertEqual(main.yuan(2999), "29.99")
        self.assertEqual(main.yuan(0), "0.00")
        self.assertEqual(main.yuan(26800), "268.00")


class TestBuildArticles(unittest.TestCase):
    def test_fields_and_format(self):
        deals = [make_deal(814380, "只狼", 6700, 26800, 75)]
        arts = main.build_articles(deals)
        self.assertEqual(arts[0]["title"], "🎮 只狼 -75% ¥268.00→¥67.00")
        self.assertEqual(arts[0]["description"], "原价 ¥268.00 → 现价 ¥67.00")
        self.assertEqual(arts[0]["url"], "https://store.steampowered.com/app/814380/?cc=cn")
        self.assertEqual(arts[0]["picurl"], "http://img/x.jpg")

    def test_long_name_truncated_to_128_bytes(self):
        long_name = "超长游戏名称测试" * 30  # 远超 128 字节
        deals = [make_deal(1, long_name, 1000, 2000, 50)]
        arts = main.build_articles(deals)
        self.assertLessEqual(len(arts[0]["title"].encode("utf-8")), 128)
        self.assertTrue(arts[0]["title"].endswith("→¥10.00"))  # 价格段不被截掉


class TestPostWecom(unittest.TestCase):
    @mock.patch("main.urllib.request.urlopen")
    def test_errcode_zero_true(self, urlopen_mock):
        urlopen_mock.return_value = mock_urlopen({"errcode": 0, "errmsg": "ok"})
        payload = {"msgtype": "news", "news": {"articles": [{"title": "t"}]}}
        self.assertTrue(main.post_wecom("https://hook", payload))

    @mock.patch("main.urllib.request.urlopen")
    def test_errcode_nonzero_false(self, urlopen_mock):
        urlopen_mock.return_value = mock_urlopen({"errcode": 93000, "errmsg": "invalid webhook"})
        payload = {"msgtype": "news", "news": {"articles": [{"title": "t"}]}}
        self.assertFalse(main.post_wecom("https://hook", payload))

    @mock.patch("main.urllib.request.urlopen", side_effect=OSError("refused"))
    def test_request_error_false(self, urlopen_mock):
        payload = {"msgtype": "news", "news": {"articles": [{"title": "t"}]}}
        self.assertFalse(main.post_wecom("https://hook", payload))


class TestSendNews(unittest.TestCase):
    @mock.patch("main.time.sleep")
    @mock.patch("main.urllib.request.urlopen")
    def test_batches_of_eight_all_success(self, urlopen_mock, sleep_mock):
        urlopen_mock.side_effect = [mock_urlopen({"errcode": 0}) for _ in range(2)]
        deals = [make_deal(1000 + i, f"game{i}", 1000, 2000, 50) for i in range(10)]
        pushed = main.send_news("https://hook", deals)
        self.assertEqual(len(pushed), 10)
        calls = urlopen_mock.call_args_list
        self.assertEqual(len(calls), 2)
        first = json.loads(calls[0].args[0].data)
        second = json.loads(calls[1].args[0].data)
        self.assertEqual(len(first["news"]["articles"]), 8)
        self.assertEqual(len(second["news"]["articles"]), 2)

    @mock.patch("main.time.sleep")
    @mock.patch("main.urllib.request.urlopen")
    def test_retry_once_then_fail_returns_none(self, urlopen_mock, sleep_mock):
        bad = mock_urlopen({"errcode": 93000, "errmsg": "invalid webhook"})
        urlopen_mock.side_effect = [bad, bad]
        deals = [make_deal(1, "x", 100, 200, 50)]
        self.assertIsNone(main.send_news("https://hook", deals))
        self.assertEqual(len(urlopen_mock.call_args_list), 2)


class TestSavePushed(unittest.TestCase):
    def test_writes_state_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data", "pushed_games.json")
            pushed = {"570": {"price": 2999, "discount": 80, "when": "2026-11-04"}}
            main.save_pushed(pushed, path)
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
            self.assertEqual(state["version"], 1)
            self.assertEqual(state["pushed"], pushed)
            self.assertIsNotNone(state["last_updated"])


class TestGitCommitAndPush(unittest.TestCase):
    @mock.patch("main.run_git")
    def test_success_path(self, run_git_mock):
        ok = mock.Mock(returncode=0, stdout="M  data/pushed_games.json")
        run_git_mock.return_value = ok
        self.assertTrue(main.git_commit_and_push())
        cmds = [c.args for c in run_git_mock.call_args_list]
        self.assertEqual(cmds[0], ("add", main.STATE_PATH))
        self.assertEqual(cmds[1], ("status", "--porcelain", "--", main.STATE_PATH))
        self.assertEqual(cmds[2][:2], ("commit", "-m"))
        self.assertEqual(cmds[3], ("pull", "--rebase"))
        self.assertEqual(cmds[4], ("push",))

    @mock.patch("main.run_git")
    def test_rebase_failure_aborts(self, run_git_mock):
        ok = mock.Mock(returncode=0, stdout="M  data/pushed_games.json")
        bad = mock.Mock(returncode=1, stdout="")
        run_git_mock.side_effect = [ok, ok, ok, bad, ok]  # add, status, commit, pull 失败, rebase --abort
        self.assertFalse(main.git_commit_and_push())
        called = [c.args[0] for c in run_git_mock.call_args_list]
        self.assertNotIn("push", called)
        self.assertIn("rebase", called)
        self.assertEqual(run_git_mock.call_args_list[-1].args, ("rebase", "--abort"))

    @mock.patch("main.run_git")
    def test_push_retried_once(self, run_git_mock):
        ok = mock.Mock(returncode=0, stdout="M  data/pushed_games.json")
        bad = mock.Mock(returncode=1, stdout="")
        run_git_mock.side_effect = [ok, ok, ok, ok, bad, ok]  # add, status, commit, pull, push×2
        self.assertTrue(main.git_commit_and_push())
        pushes = [c for c in run_git_mock.call_args_list if c.args[0] == "push"]
        self.assertEqual(len(pushes), 2)

    @mock.patch("main.run_git")
    def test_nothing_to_commit_still_ok(self, run_git_mock):
        ok = mock.Mock(returncode=0, stdout="")  # status 输出为空 → 无变更
        run_git_mock.return_value = ok
        self.assertTrue(main.git_commit_and_push())
        cmds = [c.args for c in run_git_mock.call_args_list]
        self.assertEqual(len(cmds), 2)
        called = [c.args[0] for c in run_git_mock.call_args_list]
        self.assertNotIn("commit", called)
        self.assertNotIn("push", called)


class TestMainFlow(unittest.TestCase):
    @mock.patch("main.git_commit_and_push", return_value=True)
    @mock.patch("main.save_pushed")
    @mock.patch("main.send_news", return_value=None)
    @mock.patch("main.load_pushed", return_value={})
    @mock.patch("main.fetch_featured_deals")
    @mock.patch.dict(os.environ, {"WECOM_WEBHOOK": "https://hook"})
    def test_push_failure_keeps_state(self, fetch_m, load_m, send_m, save_m, git_m):
        fetch_m.return_value = [make_deal(1, "x", 100, 200, 50)]
        self.assertEqual(main.main(), 1)
        save_m.assert_not_called()
        git_m.assert_not_called()

    @mock.patch("main.git_commit_and_push", return_value=True)
    @mock.patch("main.save_pushed")
    @mock.patch("main.send_news", return_value=[make_deal(570, "Dota 2", 2999, 14999, 80)])
    @mock.patch("main.load_pushed", return_value={})
    @mock.patch("main.fetch_featured_deals")
    @mock.patch.dict(os.environ, {"WECOM_WEBHOOK": "https://hook"})
    def test_push_success_updates_state(self, fetch_m, load_m, send_m, save_m, git_m):
        fetch_m.return_value = [make_deal(570, "Dota 2", 2999, 14999, 80)]
        self.assertEqual(main.main(), 0)
        saved = save_m.call_args.args[0]
        self.assertEqual(saved["570"]["price"], 2999)
        self.assertEqual(saved["570"]["discount"], 80)
        git_m.assert_called_once()

    @mock.patch("main.git_commit_and_push", return_value=True)
    @mock.patch("main.save_pushed")
    @mock.patch("main.send_news", return_value=[])
    @mock.patch("main.load_pushed")
    @mock.patch("main.fetch_featured_deals")
    @mock.patch.dict(os.environ, {"WECOM_WEBHOOK": "https://hook"})
    def test_nothing_worth_still_saves_for_keepalive(self, fetch_m, load_m, send_m, save_m, git_m):
        fetch_m.return_value = [make_deal(570, "Dota 2", 2999, 14999, 80)]
        load_m.return_value = {"570": {"price": 1999, "discount": 87, "when": "2026-11-03"}}
        self.assertEqual(main.main(), 0)
        send_m.assert_not_called()      # 价格回升 → 不打扰
        save_m.assert_called_once()     # 但保活照常
        git_m.assert_called_once()

    @mock.patch("main.fetch_featured_deals")
    def test_missing_webhook_exits_1(self, fetch_m):
        with mock.patch.dict(os.environ):
            os.environ.pop("WECOM_WEBHOOK", None)
            self.assertEqual(main.main(), 1)
        fetch_m.assert_not_called()


class TestSendNewsAll(unittest.TestCase):
    @mock.patch("main.send_news")
    def test_all_success(self, send_mock):
        send_mock.return_value = ["deal"]
        self.assertEqual(main.send_news_all(["https://a", "https://b"], ["deal"]), ["deal"])
        self.assertEqual(send_mock.call_count, 2)

    @mock.patch("main.send_news")
    def test_second_webhook_fails_returns_none(self, send_mock):
        send_mock.side_effect = [["deal"], None]
        self.assertIsNone(main.send_news_all(["https://a", "https://b"], ["deal"]))


class TestParseWebhooks(unittest.TestCase):
    def test_single(self):
        self.assertEqual(main.parse_webhooks("https://a"), ["https://a"])

    def test_comma_separated(self):
        self.assertEqual(main.parse_webhooks("https://a,https://b"), ["https://a", "https://b"])

    def test_mixed_separators_and_spaces(self):
        self.assertEqual(
            main.parse_webhooks("https://a; https://b\nhttps://c ,"),
            ["https://a", "https://b", "https://c"],
        )

    def test_empty(self):
        self.assertEqual(main.parse_webhooks("  ,; "), [])
