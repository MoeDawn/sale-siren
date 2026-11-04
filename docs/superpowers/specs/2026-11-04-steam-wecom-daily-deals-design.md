# Steam 每日折扣推送企业微信 — 设计文档

- 日期：2026-11-04
- 状态：已与用户逐节确认
- 方案：方案 A（单文件 Python 脚本 + 标准库，零第三方依赖）

## 1. 目标

利用 GitHub Actions 每天北京时间早晨自动拉取 Steam 首页精选特惠，将**从未推送过的新折扣**（以游戏 ID + 价格组合判定）以**图文卡片**形式推送到企业微信群机器人，并把推送记录以 Git 提交的方式持久化回仓库，避免重复推送。

### 成功标准

- 每天北京时间 9:00 左右自动运行，无需任何人工干预
- 同一游戏同一价格终生只推一次；价格创新低时再次推送
- 消息带游戏封面图，点击卡片跳转 Steam 商店页
- 完全免费（公共仓库 Actions + Steam 官方 API + 企业微信机器人）
- 推送与状态写入满足事务性：绝不漏推，最坏情况重复推

### 非目标（YAGNI）

- 不做全量折扣扫描（只抓首页精选特惠）
- 不做愿望单/指定游戏监控
- 不做多渠道推送（Discord/Telegram 等）
- 不引入任何第三方 Python 包

## 2. 背景调研结论

社区没有可整体复用的现成项目：最接近的 [gapolli/steam-deals-scanner](https://github.com/gapolli/steam-deals-scanner) 依赖 Supabase 云数据库 + Make 中转推送，改造成本高于自写；[HenryLok0/steam-deals-tracker](https://github.com/HenryLok0/steam-deals-tracker) 是纯浏览站无推送能力。因此自研。

## 3. 架构总览

```
GitHub Actions cron (UTC 1:00 = 北京 9:00)
        │
        ▼
main.py（单文件，Python 3.12，仅标准库）
        │
        ├─① GET https://store.steampowered.com/api/featuredcategories/?cc=cn&l=schinese
        │      └─ 提取各分类下带折扣的游戏条目
        │
        ├─② 读取 data/pushed_games.json（不存在 = 首次运行 → 空记录）
        │
        ├─③ 去重对比：(appid, final_price) 组合未见过 或 价格创新低 → 新折扣
        │
        ├─④ 有新折扣 → 组装 news 图文消息 → POST 企业微信 webhook（每 8 篇一条，自动分批）
        │      无新折扣 → 不发送，仅记录日志
        │
        └─⑤ 全部消息推送成功 → 合并记录写回 data/pushed_games.json
               → git commit + push（每次运行必定 commit，用于保活）
```

### 仓库结构

```
repo/
├── .github/workflows/daily-push.yml   # cron 定时 + 手动触发
├── main.py                            # 全部逻辑（拉取/去重/推送/持久化/git 提交）
├── data/pushed_games.json             # 历史推送记录（脚本写回并提交）
└── README.md                          # 配置说明（Secret、添加机器人、启用 Actions）
```

## 4. Steam API 调用

- URL：`https://store.steampowered.com/api/featuredcategories/?cc=cn&l=schinese`
- `cc=cn` 获取国区人民币价格；`l=schinese` 获取中文游戏名
- 扫描响应中各分类（重点 `specials`，同时扫 `top_sellers`、`new_releases`、`coming_soon`、`special_commend_championships` 等所有出现的关键）里 `discount_percent > 0` 的条目
- 每个条目提取：`id`（appid）、`name`、`final_price`（单位：分）、`original_price`、`discount_percent`、`header_image`、折扣截止时间 `discount_expiration`（可空）
- 请求头带常规 User-Agent（Steam 对空 UA 偶发 429）
- 失败重试 2 次，间隔 5s、15s；重试耗尽仍失败 → exit 1（见错误处理）

## 5. 去重语义（ID + 价格组合）

`data/pushed_games.json` 结构：

```jsonc
{
  "version": 1,
  "last_updated": "2026-11-04T01:00:00Z",
  "pushed": {
    "570": { "price": 2999, "discount": 80, "when": "2026-11-04" },
    "730": { "price": 1999, "discount": 50, "when": "2026-11-03" }
  }
}
```

- key = appid（字符串）；value 记录**已推送时的最终价（分）**
- 判定为"新折扣"：appid 不在记录中，**或** 记录中的 price > 本次 final_price（价格创新低）
- 记录 price ≤ 本次 final_price → 跳过
- 记录只增不减；同游戏再次命中时更新 price/discount/when 字段

## 6. 企业微信推送（news 图文）

**为什么用 news 而非 markdown**：markdown 消息类型不支持嵌入图片；news 图文每篇自带封面图（`picurl`），由企业微信服务器自行拉取 Steam CDN 图片，脚本无需下载图片。

每个游戏 = 一篇图文：

| 字段 | 内容 | 示例 |
|------|------|------|
| `title` | 游戏名 + 折扣率 | `🎮 只狼：影逝二度 -75%` |
| `description` | 原价 → 现价（纯文本，news 不支持 markdown） | `原价 ¥268 → 现价 ¥67` |
| `picurl` | Steam API 返回的 `header_image` | （CDN 链接） |
| `url` | 商店页 | `https://store.steampowered.com/app/814380/?cc=cn` |

规则：

- 排序：折扣力度降序，同折扣按现价升序（最吸引眼球的在前；news 第一篇显示大图）
- 每条 news 消息最多 8 篇图文，超出自动拆成多条依次发送
- 企业微信机器人限频 20 条/分钟，分批发送间加 1s 间隔即可
- **无新折扣时不发任何消息**，仅在 Actions 日志中记录
- Webhook 地址从环境变量 `WECOM_WEBHOOK` 读取（GitHub Secrets 注入），绝不写进代码
- 推送判定：HTTP 200 且响应 `errcode == 0` 才算成功

## 7. Workflow（.github/workflows/daily-push.yml）

```yaml
name: Daily Steam Deals Push
on:
  schedule:
    - cron: '0 1 * * *'        # UTC 1:00 = 北京时间 9:00
  workflow_dispatch:            # 手动触发按钮（测试用）
permissions:
  contents: write               # GITHUB_TOKEN 可提交状态文件
concurrency:
  group: steam-push
  cancel-in-progress: false
jobs:
  push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: python main.py
        env:
          WECOM_WEBHOOK: ${{ secrets.WECOM_WEBHOOK }}
```

- 零依赖：不执行任何 `pip install`，脚本仅用标准库（`urllib.request`、`json`、`os`、`subprocess` 等）
- git 提交在 `main.py` 内部完成（`subprocess` 调 git），保证"推送成功才写状态"的事务顺序
- `GITHUB_TOKEN` 的 push 不会触发其他 workflow，无自我触发循环风险
- cron 准点性：GitHub scheduled workflow 有延迟（高峰期几分钟到更久），9:00 上下浮动可接受；如需更准可改 UTC 0:30
- 保活：每次运行结束**必定 commit**——即使状态文件内容无变化，也更新 `last_updated` 字段产生真实变更。仓库每天有 commit，避免 GitHub 对 60 天无活动仓库禁用 scheduled workflow

### git 提交细节

- push 前先 `git pull --rebase`（防极小概率的运行重叠冲突），失败 → exit 1 留给下轮
- commit message：`chore: update pushed records <UTC日期>`
- push 失败重试 1 次

## 8. 错误处理（事务原则）

**核心原则：推送与状态写入是事务——只有企业微信确认成功（HTTP 200 且 errcode=0），才写状态文件并提交；任何失败保持状态不变，靠下轮 cron 重推。宁可重复推，绝不漏推。**

| 故障点 | 行为 |
|--------|------|
| Steam API 请求失败（重试 2 次耗尽） | exit 1，不推送不写状态，Actions 红叉，下轮 cron 重试 |
| API 返回条目缺字段/解析失败 | 跳过该条目，打 warning 日志，不影响整体 |
| 企业微信推送失败（非 200 或 errcode≠0） | 重试 1 次；仍失败 → exit 1，不写状态文件 |
| 消息分批中途失败 | 整批已发送的不回滚（无法回滚），但整体 exit 1 不写状态 → 下轮会重推全部本轮游戏（接受重复） |
| `pushed_games.json` 损坏/非法 JSON | 视为空记录，打 warning，下次成功推送后重建文件 |
| `WECOM_WEBHOOK` 未配置 | 启动即 exit 1，错误信息明确指向 Secrets 配置 |
| git push 冲突 | pull --rebase 后重试 1 次；仍失败 exit 1 |
| 无新折扣 | 正常退出 exit 0，不发消息，仍更新 last_updated 并 commit（保活） |

## 9. 测试策略

- **本地快速验证**：`workflow_dispatch` 手动触发 + 临时指向测试群 webhook，验证 API 拉取、去重、图文渲染、状态写回全链路
- **去重逻辑单测**：纯函数 `diff(pushed, current) -> new_deals` 的单元测试（不触网），覆盖：首次运行、已见同价、价格创新低、价格回升不推、记录文件缺失、记录文件损坏六个场景。测试文件 `test_main.py`，同样仅标准库（`unittest`）
- **消息组装测试**：验证 8 篇/条 的分批边界、排序、字段完整性

## 10. 已知限制与风险

| 风险 | 说明 | 缓解 |
|------|------|------|
| 覆盖面有限 | featuredcategories 只含首页精选（约几十款），不是全量折扣 | 已与用户确认接受；未来可替换数据源（如 HenryLok0 的 RSS） |
| cron 延迟 | GitHub scheduled workflow 不保证准点 | 可接受；可调 cron 表达式 |
| Steam API 无官方 SLA | 接口偶尔 429/超时 | UA + 重试；失败自然留给下轮 |
| news 图文 description 不支持 markdown | 价格只能纯文本 | 已与用户确认的取舍（换图片） |
| 每条消息 8 篇上限、20 条/分钟限频 | 极端大促时消息多 | 分批 + 1s 间隔；精选特惠日常远达不到限频 |
