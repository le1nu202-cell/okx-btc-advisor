# OKX BTC 永续合约策略分析器

一个仅在本机运行、只读取 OKX 公共行情的中文策略分析网页。它分析 `BTC-USDT-SWAP`，用 4 小时级别识别趋势或震荡环境，并在 1 小时收盘后生成可解释的候选、观察或等待信号。

> 本项目是行情分析与教育性风险测算工具，不构成个性化投资建议，不承诺收益，也不连接账户或执行交易。

## 安全边界

- 不要求、读取或保存 OKX API Key。
- 不包含下单、改单、撤单或账户查询接口。
- 后端仅监听 `127.0.0.1`，本地设置保存在 SQLite。
- 只在 OKX 标记为已收盘的K线上生成建议；数据过期时停止生成新建议。

## 启动

在 PowerShell 中运行：

```powershell
cd D:\codex\okx-btc-advisor
.\start.ps1
```

脚本会创建本地 Python 虚拟环境、安装依赖、构建前端并打开 `http://127.0.0.1:8765`。首次启动需要联网安装依赖并回填行情，后续可使用 `-SkipInstall` 加快启动：

```powershell
.\start.ps1 -SkipInstall
```

## 使用流程

1. 等待页面显示“数据正常”，查看4H市场状态和1H建议。
2. 浏览器通知需要手动授权；拒绝后仍保留网页内信号记录。
3. 如需仓位示例，自行填写账户权益、每笔风险比例（0.1%～2%）和计划杠杆（1x～2x）。这些数据仅保存在本机。
4. 在回测页填写自己的手续费假设，运行趋势、震荡或组合策略。结果未达到样本外门槛时会标为“实验信号”。

当前版本尚未完成严格 walk-forward、最后12个月锁定、全部基准策略和 PBO/Deflated Sharpe，因此回测结果会统一保持“实验信号”，不会显示为通过历史验证。详见 `PROGRESS.md`。

## 开发与测试

后端测试：

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests
```

前端检查：

```powershell
cd frontend
pnpm build
```

主要接口：`/api/market/snapshot`、`/api/advice/current`、`/api/advice/history`、`/api/settings`、`/api/backtests` 和 `/ws/live`。FastAPI 交互文档位于 `/docs`。

## 数据与方法

行情来自 [OKX 公共 API](https://www.okx.com/docs-v5/en/)。指标实现遵循常见 EMA、RSI、MACD、ATR、ADX、OBV 与布林带定义。资金费率和持仓量只用于置信度与上下文提示，缺失时不会补零。

策略历史表现不能代表未来结果。短周期永续合约还会受到手续费、滑点、资金费率、强平规则、网络延迟和市场跳空影响。
