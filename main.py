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
