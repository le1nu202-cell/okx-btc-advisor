# OKX BTC 永续合约策略观察台

这是一个只在本机运行、只读取 OKX 公共行情的中文分析网页。它分析 `BTC-USDT-SWAP`：用 4H 判断趋势、震荡或过渡环境，再用已收盘的 1H K 线生成候选、观察或等待建议。

> 本项目是行情分析与教育性风险测算工具，不构成个性化投资建议，不承诺收益，不连接账户，也不会自动下单。当前冻结预设的严格三年回测均未通过，页面因此统一标记为“实验信号”。

## 直接打开

桌面快捷方式：`C:\Users\Administrator\Desktop\OKX BTC 策略观察台.lnk`

双击后会通过 `launch.ps1` 启动本地服务并打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。启动器只复用身份和版本都匹配的本应用服务；冷启动时会校验或安装项目依赖并重新生成生产构建，失败时会显示说明并把独立诊断日志写入 `logs/`。连续双击会复用同一个服务。

如快捷方式被移动或删除，可在项目目录重新安装：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-shortcut.ps1
```

也可直接启动：

```powershell
cd D:\codex\okx-btc-advisor
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

首次运行会准备依赖并构建前端；以后可用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -SkipInstall
```

同一份 SQLite 数据库只应运行一个服务实例。如需在另一端口隔离测试，请同时设置不同的 `OKX_ADVISOR_DB` 路径。

## 页面各部分

- **顶部行情**：当前价格、4H 市场状态、资金费率、公开持仓量、数据时间和新鲜度。任一实时通道断开、行情超过 30 秒或近期 K 线有缺口时，系统强制等待。
- **1H / 4H 图表**：展示实时行情；只有 OKX `confirm=1` 的已收盘 K 线进入策略，未收盘 K 线只用于图表。
- **当前策略建议**：显示动作、策略技术分、该根 K 线实际采用的新闻分、综合方向分、置信度及数据质量。趋势策略在 `±60` 以上才成为候选，`±30～59` 为观察，中间区域等待。
- **触发、失效与目标**：趋势策略显示结构/ATR 中更保守的止损及 1R、2R 目标；震荡策略显示布林中轨和对侧轨目标。所有价格都是研究参考，不是成交保证。
- **指标贡献**：解释 EMA、ADX、MACD、RSI、OBV、量能和 20 根突破等规则如何贡献技术分。
- **教育性仓位测算**：只有用户自行填写账户权益、每笔风险和计划杠杆后才计算。风险限制为 `0.1%～2%`，杠杆限制为 `1x～2x`；手续费、资金费率和滑点需另行考虑。
- **辅助技术共振**：独立展示多周期趋势、Donchian 结构、日/周 VWAP、近似成交密集区、MFI、Stoch RSI、Williams %R 和波动分位。这是研究层，不会暗中改变冻结策略分数。
- **消息面新闻**：聚合 OKX 公告、CoinDesk、Cointelegraph Bitcoin 和 Decrypt；展示事件重要度、方向、相关性、跨源确认、来源覆盖和时间衰减。新闻最多修正方向分 `±15`，技术分不足时不能单独制造候选；来源失败或新闻过期时严格归零。
- **回测与样本外验证**：可选择趋势、震荡或组合策略，查看任务进度、历史覆盖、净值/回撤、年度和行情状态、5/10/20bp 滑点压力、滚动窗口、锁定样本及三项基准。
- **信号记录与提醒**：候选和观察信号按品种、策略、方向与 K 线时间去重。实时数据不健康时不会保存或通知新的可交易信号；浏览器通知被拒绝时保留网页内记录。
- **设置与本地数据**：设置、账户权益、K 线、新闻、信号和回测只保存在本机 SQLite。清除数据后会自动重新同步近期 K 线和资金费率。当前自定义策略参数尚未接入引擎，非空自定义参数会被拒绝，避免产生“已生效”的假象。

页面内还有可折叠的“如何阅读本页面”，可以直接查看主要概念。

## 策略与回测口径

4H 趋势状态要求 ADX(14) 较强且 EMA20/50 间距相对 ATR 足够明显；震荡状态要求 ADX 较低且均线收敛；中间状态强制 `WAIT`。趋势策略使用 EMA、MACD、RSI、OBV、量能和 20 根突破；震荡策略使用价格离开布林带后重新进入、且尚未穿过中轨的 RSI 反转。

回测信号只能在 K 线收盘后产生，最早按下一根 1H 开盘进入。趋势交易沿用信号的结构/ATR 止损与 2R 目标；震荡交易使用布林中轨/对侧轨目标。逐根 K 线检查，同根止盈止损同时触发时保守地先按止损；最长持有 24 小时，不允许重叠持仓。成本包含历史资金费率、可配置手续费及 5/10/20bp 滑点压力。

严格验证使用 UTC 自然月：18 个月训练/校准、随后 3 个月样本外，每 3 个月滚动，最后 12 个月锁定。三年请求还必须通过实际历史覆盖门槛，否则返回 `insufficient_data`。CPU 密集型回测在单独进程运行，避免阻塞实时页面。

只有样本外净期望为正、Profit Factor ≥ 1.1、净 Sharpe ≥ 0.5、至少 100 笔交易、至少 60% 样本外窗口盈利且成本压力下仍为正，才可能显示“通过历史验证”。当前只有一个冻结配置，没有预先声明的多配置试验账本，因此 PBO 和 Deflated Sharpe 会诚实显示“不可用”，不会误报通过。历史 OI 和 point-in-time 三年新闻库不可得，所以它们不进入历史回测。

本轮冻结预设的真实三年结果均为负：组合 `-34.12%`、趋势 `-28.33%`、震荡 `-8.08%`，全部 `validationPass=false`。完整指标和限制见 [PROGRESS.md](./PROGRESS.md)。

## 数据与安全边界

- 不要求、读取或保存 OKX API Key；源码不包含账户、下单、改单、撤单或私有交易接口。
- 服务固定监听 `127.0.0.1`，并限制 Host、WebSocket Origin、连接数和安全响应头。
- OKX 公共 `/public` WebSocket 更新 ticker、资金费率和持仓量；未鉴权 `/business` WebSocket 更新 1H/4H K 线。REST 每 5 分钟对账，发现缺口或断线后深度回补并指数退避重连。
- K 线按时间排序、去重，已确认状态不可被迟到的未确认数据降级；时间倒退、坏数据和数据缺口会进入保护状态。
- 新闻以整批来源完成后的提交时点为可见时间，避免慢请求把事后新闻错误回填到过去的 K 线决策。
- 设置、信号、行情和回测只写入本机 SQLite；API 禁止缓存，页面不主动加载第三方字体或脚本。

## 验证与开发

一条命令运行后端编译、后端测试、前端测试与生产构建：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\verify.ps1
```

当前版本 `0.3.0` 的最终验证结果为：后端 `128 passed`、前端 `21 passed`、Python 编译、PowerShell 语法检查、TypeScript 检查和 Vite 生产构建全部通过。

主要本地接口：

- `GET /api/health`
- `GET /api/market/snapshot`
- `GET /api/advice/current`
- `GET /api/advice/history`
- `GET /api/technical/summary`
- `GET /api/news`
- `GET/PUT /api/settings`
- `POST/GET /api/backtests`
- `GET/DELETE /api/backtests/{id}`
- `/ws/live`

方法参考：[OKX 公共 API](https://www.okx.com/docs-v5/en/)、[TA-Lib 指标列表](https://ta-lib.github.io/ta-lib-python/funcs.html)、[The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253) 和 [The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)。
