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
