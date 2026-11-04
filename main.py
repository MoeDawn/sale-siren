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
        if not isinstance(state, dict):
            raise ValueError("state is not a dict")
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
