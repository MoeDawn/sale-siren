# Steam 每日折扣推送企业微信 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每天北京时间 9:00 由 GitHub Actions 自动拉取 Steam 首页精选特惠，把值得推送的游戏（首次出现 / 价格创新低 / 与上次持平）以企业微信 news 图文卡片推送到群机器人，推送记录经 Git 持久化回仓库。

**Architecture:** 单文件 Python 脚本 `main.py`（仅标准库）承载全部逻辑：拉取 Steam `featuredcategories` API → 与 `data/pushed_games.json` 做 ID+价格对比 → 组装 news 图文消息（每条最多 8 篇，自动分批）→ POST 企业微信 webhook → 全部成功后写回状态文件并 git commit+push（每次运行必 commit 保活）。GitHub Actions workflow 提供 cron 触发、Secret 注入、`contents: write` 权限。对应 spec：`docs/superpowers/specs/2026-11-04-steam-wecom-daily-deals-design.md`。

**Tech Stack:** Python 3.12 标准库（urllib.request / json / os / subprocess / datetime / time / logging / unittest / unittest.mock）+ GitHub Actions（actions/checkout@v4、actions/setup-python@v5）+ 企业微信群机器人 webhook（news 消息类型）+ Steam storefront API。

## Global Constraints

- **零第三方依赖**：`main.py` 与 `test_main.py` 只能 import 标准库
- **Python 3.12**（workflow 中 setup-python 固定 `'3.12'`；本地运行用 `py` 命令，Windows 商店 stub `python` 会静默失败）
- **去重规则（spec §5，逐字）**：appid 不在记录中 → 推送；记录 price ≥ 本次 final_price → 推送；记录 price < 本次 final_price → 跳过
- **状态文件**：`data/pushed_games.json`，结构 `{"version": 1, "last_updated": ISO8601 UTC, "pushed": {"<appid>": {"price": int(分), "discount": int, "when": "YYYY-MM-DD"}}}`
- **价格单位一律为分**（int）；显示用 `yuan()` → `"29.99"` 形式
- **news 消息**：每条最多 8 篇；title=`🎮 {name} -{discount}%`、description=`原价 ¥X → 现价 ¥Y`、picurl=header_image、url=`https://store.steampowered.com/app/{appid}/?cc=cn`
- **排序**：discount 降序，同 discount 按 final_price 升序（`select_new_deals` 负责）
- **推送成功判定**：HTTP 200 且响应 JSON `errcode == 0`；失败重试 1 次
- **事务原则（spec §8）**：只有全部消息推送成功才写状态文件并 git commit；任何失败 exit 1 且状态不变
- **保活**：无论是否推送消息，每次运行都更新 `last_updated` 并 commit（message `chore: update pushed records <UTC日期>`）
- **Webhook**：环境变量 `WECOM_WEBHOOK`，未设置时启动即 exit 1
- **Steam API**：`https://store.steampowered.com/api/featuredcategories/?cc=cn&l=schinese`，带 User-Agent，失败重试 2 次（间隔 5s、15s），耗尽 → exit 1
- **无值得推送的条目时**：不发消息、exit 0、仍更新 last_updated 并 commit（保活）
- 本机为 E-SafeNet 加密环境：读文件/跑测试一律用 `py`（白名单内可读明文）；git 命令通过 Git Bash 正常工作

---

### Task 1: 去重核心 — `load_pushed` + `select_new_deals`

**Files:**
- Create: `main.py`
- Create: `test_main.py`

**Interfaces:**
- Produces: `select_new_deals(current: list[dict], pushed: dict) -> list[dict]`
  - `current` 元素：`{"id": str, "name": str, "final_price": int, "original_price": int, "discount_percent": int, "header_image": str}`
  - `pushed`：`{"570": {"price": 2999, "discount": 80, "when": "2026-11-04"}, ...}`
  - 返回"值得推送"子集（首次出现，或 price ≥ 本次价），已按 discount 降序、final_price 升序排序
- Produces: `load_pushed(path: str = STATE_PATH) -> dict` — 返回 pushed 字典；文件缺失/损坏 → `{}`（带日志）
- 模块常量（后续任务复用）：`STATE_PATH`、`STEAM_API_URL`、`RETRY_DELAYS=(5, 15)`、`NEWS_BATCH_SIZE=8`、`STORE_URL_TEMPLATE`、`UA`

- [ ] **Step 1: 创建 `test_main.py`（先写失败测试）**

```python
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
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `py -m unittest test_main -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: 创建 `main.py` 最小实现**

```python
"""Steam 每日折扣推送企业微信。

GitHub Actions 每天运行一次：拉取 Steam 首页精选特惠，与历史推送记录
按「ID + 价格」对比，把值得推送的游戏以 news 图文消息发到企业微信群
机器人，然后把记录写回 data/pushed_games.json 并 git commit（保活）。
"""

import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("steam-push")

STEAM_API_URL = "https://store.steampowered.com/api/featuredcategories/?cc=cn&l=schinese"
STATE_PATH = os.path.join("data", "pushed_games.json")
RETRY_DELAYS = (5, 15)
NEWS_BATCH_SIZE = 8
STORE_URL_TEMPLATE = "https://store.steampowered.com/app/{appid}/?cc=cn"
UA = "Mozilla/5.0 (X11; Linux x86_64) steam-wecom-push/1.0"


def load_pushed(path=STATE_PATH):
    """读取历史推送记录；文件缺失或损坏视为空（首次运行/重新开始）。"""
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        pushed = state.get("pushed")
        if not isinstance(pushed, dict):
            raise ValueError("pushed is not a dict")
        return pushed
    except FileNotFoundError:
        log.info("no state file at %s, first run", path)
        return {}
    except (ValueError, OSError) as e:
        log.warning("state file %s broken (%s), starting fresh", path, e)
        return {}


def select_new_deals(current, pushed):
    """选出值得推送的条目：首次出现，或价格 <= 上次已推价（创新低/持平）。

    返回按 discount 降序、final_price 升序排好的列表。
    """
    worth = []
    for deal in current:
        prev = pushed.get(deal["id"])
        prev_price = prev.get("price") if isinstance(prev, dict) else None
        if not isinstance(prev_price, int) or prev_price >= deal["final_price"]:
            worth.append(deal)
    worth.sort(key=lambda d: (-d["discount_percent"], d["final_price"]))
    return worth
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `py -m unittest test_main -v`
Expected: PASS（10 个测试全绿）

- [ ] **Step 5: Commit**

```bash
git add main.py test_main.py
git commit -m "feat: dedup core (load_pushed + select_new_deals)"
```

---

### Task 2: Steam API 拉取与解析 — `fetch_json` + `extract_deals` + `fetch_featured_deals`

**Files:**
- Modify: `main.py`（文件末尾追加）
- Modify: `test_main.py`（文件末尾追加测试类与 `mock_urlopen` 辅助函数）

**Interfaces:**
- Consumes: 常量 `STEAM_API_URL`、`RETRY_DELAYS`、`UA`（Task 1）
- Produces: `fetch_json(url: str) -> dict` — 带 UA 请求，失败重试（间隔 `RETRY_DELAYS`），全部失败 raise 最后一个异常
- Produces: `extract_deals(payload: dict) -> list[dict]` — 从 featuredcategories 响应提取所有 `discount_percent > 0` 的条目（跨所有 dict 型分类；列表型分类如 `genres` 跳过；坏条目跳过并告警；同 appid 多分类出现时保留折扣最大的一条）
- Produces: `fetch_featured_deals() -> list[dict]` — 两者组合

- [ ] **Step 1: 在 `test_main.py` 末尾追加（先写失败测试）**

在 `import main` 之后（模块级，测试类之前）加入 mock 辅助：

```python
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
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `py -m unittest test_main.TestExtractDeals test_main.TestFetchJson test_main.TestFetchFeaturedDeals -v`
Expected: FAIL — `AttributeError: module 'main' has no attribute 'extract_deals'`

- [ ] **Step 3: 在 `main.py` 末尾追加实现**

```python
def fetch_json(url):
    """GET 并解析 JSON；失败按 RETRY_DELAYS 重试，耗尽后抛出最后一个异常。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for attempt, delay in enumerate((0,) + RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except (OSError, ValueError) as e:
            last = e
            log.warning("fetch %s failed (attempt %d): %s", url, attempt + 1, e)
    raise last


def extract_deals(payload):
    """提取所有分类下 discount_percent > 0 的条目，同一游戏保留折扣最大的。"""
    deals = {}
    for category in payload.values():
        if not isinstance(category, dict):
            continue  # genres/layout 等列表型键
        for item in category.get("items", []):
            if not isinstance(item, dict):
                continue
            try:
                if item.get("discount_percent", 0) <= 0:
                    continue
                appid = str(item["id"])
                known = deals.get(appid)
                if known is not None and known["discount_percent"] >= item["discount_percent"]:
                    continue
                deals[appid] = {
                    "id": appid,
                    "name": item["name"],
                    "final_price": int(item["final_price"]),
                    "original_price": int(item["original_price"]),
                    "discount_percent": int(item["discount_percent"]),
                    "header_image": item.get("header_image", ""),
                    "discount_expiration": item.get("discount_expiration"),
                }
            except (KeyError, TypeError, ValueError) as e:
                log.warning("skip malformed item: %s", e)
    return list(deals.values())


def fetch_featured_deals():
    return extract_deals(fetch_json(STEAM_API_URL))
```

- [ ] **Step 4: 运行全部测试，确认通过**

Run: `py -m unittest test_main -v`
Expected: PASS（Task 1 的 10 个 + 本任务 5 个，全绿）

- [ ] **Step 5: Commit**

```bash
git add main.py test_main.py
git commit -m "feat: fetch and parse steam featuredcategories"
```

---

### Task 3: 企业微信消息组装与发送 — `yuan` + `build_articles` + `post_wecom` + `send_news`

**Files:**
- Modify: `main.py`（末尾追加）
- Modify: `test_main.py`（末尾追加测试类）

**Interfaces:**
- Consumes: 常量 `NEWS_BATCH_SIZE`、`STORE_URL_TEMPLATE`（Task 1）
- Produces: `yuan(price_fen: int) -> str` — `"2999" → "29.99"`
- Produces: `build_articles(deals: list[dict]) -> list[dict]` — 每游戏一篇 `{title, description, picurl, url}`，保持传入顺序（排序由 `select_new_deals` 保证）
- Produces: `post_wecom(webhook: str, payload: dict) -> bool` — HTTP 200 且 `errcode == 0` 才 True
- Produces: `send_news(webhook: str, deals: list[dict]) -> list[dict] | None` — 分批发送，**全部成功返回已推送的 deals 列表；任一批重试后仍失败返回 None**（调用方据 None 判定事务失败）

- [ ] **Step 1: 在 `test_main.py` 末尾追加（先写失败测试）**

```python
class TestYuan(unittest.TestCase):
    def test_formats_fen(self):
        self.assertEqual(main.yuan(2999), "29.99")
        self.assertEqual(main.yuan(0), "0.00")
        self.assertEqual(main.yuan(26800), "268.00")


class TestBuildArticles(unittest.TestCase):
    def test_fields_and_format(self):
        deals = [make_deal(814380, "只狼", 6700, 26800, 75)]
        arts = main.build_articles(deals)
        self.assertEqual(arts[0]["title"], "🎮 只狼 -75%")
        self.assertEqual(arts[0]["description"], "原价 ¥268.00 → 现价 ¥67.00")
        self.assertEqual(arts[0]["url"], "https://store.steampowered.com/app/814380/?cc=cn")
        self.assertEqual(arts[0]["picurl"], "http://img/x.jpg")


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
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `py -m unittest test_main.TestYuan test_main.TestBuildArticles test_main.TestPostWecom test_main.TestSendNews -v`
Expected: FAIL — `AttributeError: module 'main' has no attribute 'yuan'`

- [ ] **Step 3: 在 `main.py` 末尾追加实现**

```python
def yuan(price_fen):
    """分 → 人民币字符串，如 2999 → "29.99"。"""
    return f"{price_fen // 100}.{price_fen % 100:02d}"


def build_articles(deals):
    """每个游戏组装一篇企业微信 news 图文，保持传入顺序。"""
    return [
        {
            "title": f"🎮 {d['name']} -{d['discount_percent']}%",
            "description": f"原价 ¥{yuan(d['original_price'])} → 现价 ¥{yuan(d['final_price'])}",
            "url": STORE_URL_TEMPLATE.format(appid=d["id"]),
            "picurl": d["header_image"],
        }
        for d in deals
    ]


def post_wecom(webhook, payload):
    """POST 一条消息；HTTP 200 且 errcode == 0 才算成功。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.load(resp)
    except (OSError, ValueError) as e:
        log.warning("wecom request failed: %s", e)
        return False
    if result.get("errcode") != 0:
        log.warning("wecom returned errcode=%s errmsg=%s",
                    result.get("errcode"), result.get("errmsg"))
        return False
    return True


def send_news(webhook, deals):
    """按 8 篇/条分批发送 news 消息，每批失败重试 1 次。

    全部成功返回传入的 deals；任一批最终失败返回 None（事务失败信号）。
    """
    articles = build_articles(deals)
    batches = [articles[i:i + NEWS_BATCH_SIZE]
               for i in range(0, len(articles), NEWS_BATCH_SIZE)]
    for idx, batch in enumerate(batches, 1):
        payload = {"msgtype": "news", "news": {"articles": batch}}
        if not post_wecom(webhook, payload):
            log.warning("news batch %d/%d failed once, retrying", idx, len(batches))
            if not post_wecom(webhook, payload):
                log.error("news batch %d/%d failed after retry", idx, len(batches))
                return None
        if idx < len(batches):
            time.sleep(1)
    return deals
```

- [ ] **Step 4: 运行全部测试，确认通过**

Run: `py -m unittest test_main -v`
Expected: PASS（累计 21 个测试全绿）

- [ ] **Step 5: Commit**

```bash
git add main.py test_main.py
git commit -m "feat: wecom news message build and send"
```

---

### Task 4: 状态写回与 git 持久化 — `save_pushed` + `run_git` + `git_commit_and_push`

**Files:**
- Modify: `main.py`（末尾追加）
- Modify: `test_main.py`（末尾追加测试类）

**Interfaces:**
- Consumes: 常量 `STATE_PATH`（Task 1）
- Produces: `save_pushed(pushed: dict, path: str = STATE_PATH) -> None` — 写入完整状态信封（version=1、fresh `last_updated`、pushed），自动创建 `data/` 目录
- Produces: `run_git(*args: str) -> subprocess.CompletedProcess`
- Produces: `git_commit_and_push() -> bool` — add → commit → pull --rebase（失败即 False）→ push（失败重试 1 次）；"nothing to commit" 视为成功

- [ ] **Step 1: 在 `test_main.py` 末尾追加（先写失败测试）**

```python
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
        ok = mock.Mock(returncode=0)
        run_git_mock.return_value = ok
        self.assertTrue(main.git_commit_and_push())
        cmds = [c.args for c in run_git_mock.call_args_list]
        self.assertEqual(cmds[0], ("add", main.STATE_PATH))
        self.assertEqual(cmds[1][:2], ("commit", "-m"))
        self.assertEqual(cmds[2], ("pull", "--rebase"))
        self.assertEqual(cmds[3], ("push",))

    @mock.patch("main.run_git")
    def test_rebase_failure_aborts(self, run_git_mock):
        ok = mock.Mock(returncode=0)
        bad = mock.Mock(returncode=1)
        run_git_mock.side_effect = [ok, ok, bad]  # add, commit, pull 失败
        self.assertFalse(main.git_commit_and_push())
        called = [c.args[0] for c in run_git_mock.call_args_list]
        self.assertNotIn("push", called)

    @mock.patch("main.run_git")
    def test_push_retried_once(self, run_git_mock):
        ok = mock.Mock(returncode=0)
        bad = mock.Mock(returncode=1)
        run_git_mock.side_effect = [ok, ok, ok, bad, ok]  # add, commit, pull, push×2
        self.assertTrue(main.git_commit_and_push())
        pushes = [c for c in run_git_mock.call_args_list if c.args[0] == "push"]
        self.assertEqual(len(pushes), 2)

    @mock.patch("main.run_git")
    def test_nothing_to_commit_still_ok(self, run_git_mock):
        ok = mock.Mock(returncode=0)
        nothing = mock.Mock(returncode=1)
        run_git_mock.side_effect = [ok, nothing]  # add 成功, commit 无变更
        self.assertTrue(main.git_commit_and_push())
        called = [c.args[0] for c in run_git_mock.call_args_list]
        self.assertNotIn("push", called)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `py -m unittest test_main.TestSavePushed test_main.TestGitCommitAndPush -v`
Expected: FAIL — `AttributeError: module 'main' has no attribute 'save_pushed'`

- [ ] **Step 3: 在 `main.py` 末尾追加实现**

```python
def save_pushed(pushed, path=STATE_PATH):
    """把合并后的记录写回状态文件（全新 last_updated，保证每次运行有真实变更）。"""
    state = {
        "version": 1,
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pushed": pushed,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


def run_git(*args):
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if result.returncode != 0:
        log.error("git %s failed: %s", " ".join(args), result.stderr.strip())
    return result


def git_commit_and_push():
    """提交状态文件并推送。pull --rebase 失败即放弃；push 失败重试 1 次。"""
    run_git("add", STATE_PATH)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    commit = run_git("commit", "-m", f"chore: update pushed records {date}")
    if commit.returncode != 0:
        log.info("nothing to commit")
        return True
    if run_git("pull", "--rebase").returncode != 0:
        return False
    if run_git("push").returncode != 0:
        log.warning("push failed once, retrying")
        if run_git("push").returncode != 0:
            return False
    return True
```

- [ ] **Step 4: 运行全部测试，确认通过**

Run: `py -m unittest test_main -v`
Expected: PASS（累计 27 个测试全绿）

- [ ] **Step 5: Commit**

```bash
git add main.py test_main.py
git commit -m "feat: state persistence and git commit"
```

---

### Task 5: main 编排入口 — 事务原则落点

**Files:**
- Modify: `main.py`（末尾追加 `main()` 与入口守卫）
- Modify: `test_main.py`（末尾追加 `TestMainFlow`）

**Interfaces:**
- Consumes: Task 1-4 全部函数：`fetch_featured_deals()` / `load_pushed()` / `select_new_deals(current, pushed)` / `send_news(webhook, deals)` / `save_pushed(pushed)` / `git_commit_and_push()`
- Produces: `main() -> int`（0 成功 / 1 失败）；`if __name__ == "__main__": sys.exit(main())`
- 事务语义（测试锁定）：send_news 返回 None → 不写状态、不 commit、返回 1；无值得推送 → 不发消息但照常 save+commit 返回 0；`WECOM_WEBHOOK` 缺失 → 直接返回 1 且不触网

- [ ] **Step 1: 在 `test_main.py` 末尾追加（先写失败测试）**

```python
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
```

注意装饰器顺序：`mock.patch` 自底向上生效，参数签名从最底部的非-dict patch 开始。

- [ ] **Step 2: 运行测试，确认失败**

Run: `py -m unittest test_main.TestMainFlow -v`
Expected: FAIL — `AttributeError: module 'main' has no attribute 'main'`

- [ ] **Step 3: 在 `main.py` 末尾追加实现**

```python
def main():
    webhook = os.environ.get("WECOM_WEBHOOK")
    if not webhook:
        log.error("WECOM_WEBHOOK not set; configure it as a GitHub Secret")
        return 1

    try:
        current = fetch_featured_deals()
    except Exception as e:  # 重试耗尽后的最后异常，任何类型都按拉取失败处理
        log.error("failed to fetch steam deals: %s", e)
        return 1

    pushed = load_pushed()
    worth = select_new_deals(current, pushed)
    log.info("%d deals fetched, %d worth pushing", len(current), len(worth))

    if worth:
        pushed_deals = send_news(webhook, worth)
        if pushed_deals is None:
            return 1  # 事务失败：状态不变，下轮 cron 重推
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for d in pushed_deals:
            pushed[d["id"]] = {
                "price": d["final_price"],
                "discount": d["discount_percent"],
                "when": today,
            }
    else:
        log.info("nothing worth pushing today")

    save_pushed(pushed)  # 无推送也写入（last_updated 变化）以保活
    if not git_commit_and_push():
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行全部测试，确认通过**

Run: `py -m unittest test_main -v`
Expected: PASS（累计 31 个测试全绿）

- [ ] **Step 5: Commit**

```bash
git add main.py test_main.py
git commit -m "feat: main orchestration with transactional push"
```

---

### Task 6: GitHub Actions workflow + README + .gitignore

**Files:**
- Create: `.github/workflows/daily-push.yml`
- Create: `README.md`
- Create: `.gitignore`

**Interfaces:**
- Consumes: `main.py` 入口（Task 5）；spec §11 配置指南全文
- Produces: 可部署的 workflow（cron + workflow_dispatch + `contents: write` + concurrency + git identity 配置）与面向使用者的 README

- [ ] **Step 1: 创建 `.github/workflows/daily-push.yml`**

```yaml
name: Daily Steam Deals Push

on:
  schedule:
    - cron: '0 1 * * *'   # UTC 1:00 = 北京时间 9:00
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: steam-push
  cancel-in-progress: false

jobs:
  push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Configure git identity
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

      - name: Run daily push
        env:
          WECOM_WEBHOOK: ${{ secrets.WECOM_WEBHOOK }}
        run: python main.py
```

- [ ] **Step 2: 创建 `.gitignore`**

```
__pycache__/
```

- [ ] **Step 3: 创建 `README.md`（spec §11 内容落成使用者文档）**

````markdown
# Steam 每日折扣推送

每天北京时间 9:00（UTC 1:00）由 GitHub Actions 自动运行：拉取 Steam 首页精选
特惠，把值得推送的游戏（首次出现、价格创新低、或与上次已推价格持平）以
图文卡片推送到企业微信群机器人，推送记录提交回本仓库做状态持久化。

## 工作原理

1. Steam storefront API（featuredcategories）拉取国区（cc=cn、中文）精选特惠
2. 与 `data/pushed_games.json` 按「游戏 ID + 价格」对比：本次价格 ≤ 上次已推价
   → 推送；高于上次 → 跳过（折扣变浅，不打扰）
3. 按折扣力度排序，组装企业微信 news 图文消息（每条最多 8 篇，自动分批）
4. 推送成功后把新记录写回状态文件并 git commit（每次运行都提交，保活 workflow）

## 配置步骤

### 1. 创建企业微信机器人（获取 Webhook）
1. PC 端企业微信打开目标群聊
2. 右上角群设置（…）→ 群机器人 → 添加机器人 → 新创建一个
3. 复制 Webhook 地址（形如 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx`）
   ⚠️ 该地址即密钥，泄露后任何人都能向群发消息；不要写进代码、不要发到聊天里

### 2. 推送本仓库到 GitHub
公共仓库 Actions 完全免费；私有仓库每月 2000 分钟免费额度，本项目每天约
1 分钟，也够用。

### 3. 配置 Secret
仓库 Settings → Secrets and variables → Actions → New repository secret
- Name：`WECOM_WEBHOOK`
- Secret：粘贴第 1 步复制的完整 Webhook 地址

### 4. 启用并验证
- Actions 页确认 "Daily Steam Deals Push" 已启用（首次 push 后自动启用）
- 手动测试：Actions → Daily Steam Deals Push → Run workflow
- 验证：群里收到图文消息（封面图 + 价格 + 点卡片跳商店页）；
  `data/pushed_games.json` 产生一条新 commit

## 调整推送时间

cron 用 UTC 时间，北京时间 = UTC + 8，编辑 `.github/workflows/daily-push.yml`：

| 目标（北京时间） | cron |
|------------------|------|
| 每天 9:00（默认） | `0 1 * * *` |
| 每天 8:30（Actions 低峰，更准点） | `30 0 * * *` |
| 每天 9:00 与 21:00 各一次 | `0 1,13 * * *` |

## 日常运维

- **让所有游戏重新推一遍**：把 `data/pushed_games.json` 里 `pushed` 清成 `{}` 并提交
- **收到 scheduled workflow 停用邮件**（理论上不会发生，脚本每天有 commit 保活）：
  到 Actions 页重新 Enable
- **换群/换机器人**：新机器人拿到新 webhook 后更新 `WECOM_WEBHOOK` Secret

## 本地测试

```bash
py -m unittest discover -v   # Windows；Linux/macOS 用 python3 -m unittest discover -v
```
````

- [ ] **Step 4: 最终验证**

Run: `py -m py_compile main.py && py -m unittest test_main -v`
Expected: 编译无输出错误；31 个测试全绿

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/daily-push.yml .gitignore README.md
git commit -m "ci: add workflow, README, gitignore"
```

---

## 交付后（用户手工步骤，见 README）

用户把仓库 push 到 GitHub、配置 `WECOM_WEBHOOK` Secret、用 `workflow_dispatch` 手动验证全链路——步骤已写入 README「配置步骤」。
