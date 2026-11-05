# sale-siren

每天自动获取 Steam 特惠，把值得推送的游戏（首次出现、价格创新低、或与上次
持平）以图文卡片推送到 `WECOM_WEBHOOK` Secret 里配置的 webhook 地址。

## 使用

1. Fork / push 本仓库
2. 配置 Secret：Settings → Secrets and variables → Actions → `WECOM_WEBHOOK`，
   值为你的 webhook 地址（多个地址用逗号、分号或换行分隔即可，每个都会收到推送）
3. Actions 页手动 Run workflow 验证；之后每天北京时间 9:00 自动运行

## 说明

- 推送记录提交回本仓库（`data/pushed_games.json`）做去重，每天自动 commit 保活
- 改推送时间：编辑 `.github/workflows/daily-push.yml` 的 cron（UTC 时间，北京 = UTC+8）
- 让所有游戏重新推一遍：把 `data/pushed_games.json` 里 `pushed` 清成 `{}` 并提交
- 本地测试：`py -m unittest discover -v`（Windows）/ `python3 -m unittest discover -v`（Linux/macOS）
