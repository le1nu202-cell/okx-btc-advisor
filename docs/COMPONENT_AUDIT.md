# Frontend Component Audit

审计日期：2026-07-15

## TradingView Lightweight Charts

- 组件：`lightweight-charts`
- 固定版本：`5.2.0`
- 上游：[TradingView/lightweight-charts](https://github.com/tradingview/lightweight-charts)
- 稳定版依据：[v5.2.0 release](https://github.com/tradingview/lightweight-charts/releases/tag/v5.2.0)
- 官方 API：[5.2 API reference](https://tradingview.github.io/lightweight-charts/docs/api)
- 许可证：[Apache-2.0（v5.2.0 原文）](https://github.com/tradingview/lightweight-charts/blob/v5.2.0/LICENSE)
- 直接传递依赖：`fancy-canvas@2.1.0`（MIT）

该包是纯 ESM、内置 TypeScript 类型且没有 React peer dependency；与本项目 React 19、TypeScript 7、Vite 8 和 Node 24 工具链兼容。包与依赖均固定在 `frontend/package.json` 和 `frontend/pnpm-lock.yaml`，生产环境从本地构建资源加载，不使用闭源 TradingView Charting Library、iframe 或远程脚本。

## 许可证与署名

上游 NOTICE 与 Apache-2.0 LICENSE 的精确副本分别保存在 `third_party/lightweight-charts-NOTICE.txt` 和 `third_party/lightweight-charts-LICENSE.txt`，同时汇总于 `THIRD_PARTY_NOTICES.md`。图表配置显式保留 `layout.attributionLogo: true`，页面下方也提供 `https://www.tradingview.com/` 的可见链接。

## 组件边界

- `frontend/src/components/TradingPlanChart.tsx` 只负责 Lightweight Charts 生命周期、1m/15m/1H/4H 显示、交互、价格线和成交标记；同一实例在周期间复用，每个周期只在第一次进入时 `fitContent`。
- `frontend/src/chart-adapter.ts` 只负责确定性数据转换和后端字段到图表元素的映射。
- `frontend/src/components/TradeEquityChart.tsx` 使用同一开源包的 `AreaSeries` 展示后端权威累计净值；前端不重新累计单笔盈亏。
- 风险、加权均价、全成本保本价和止损盈亏不在图表中重算；它们来自 `RiskCalculation` / `ExecutionRisk` 后端字段。
- 成交标记只遍历 `actualFills`。`activeReminder` 和行情事件不是成交标记数据源。
- 新确认成交写入可选 `confirmedAt` JSON 元数据；标记只锚定到对应周期最近的已收盘 K 线。旧 SQLite 记录没有该字段时，兼容回退到最后一根已收盘可见 K 线，不需要 SQLite schema migration。

## 生命周期与交互

- 蜡烛图使用 v5 `chart.addSeries(CandlestickSeries)`。
- 价格线使用 `series.createPriceLine`，当前价通过 `IPriceLine.applyOptions` 更新。
- 成交标记使用 `createSeriesMarkers`。
- 毫秒时间戳在适配层转换为升序、去重的 UNIX 秒 `UTCTimestamp`。
- WebSocket 增量 K 线按时间戳覆盖、排序并保持有界；同周期调用 `setData` 不执行 `fitContent`，所以实时刷新不会重置用户缩放和平移。
- 缩放、平移和十字光标使用正式的 `handleScale`、`handleScroll` 与 `CrosshairMode.Normal` 配置。
- 宽度/高度由容器 `ResizeObserver` 同步；组件卸载时断开 observer 并调用一次 `chart.remove()`。

## 首屏与历史加载边界

- SQLite 对图表周期的保留是 1m 最近 7 天、15m 最近 90 天；1H/4H 不参与滚动裁剪，回测同步的长期历史继续留在本机。
- `/api/market/snapshot` 只返回最近 1m 720 根、15m 672 根、1H 200 根、4H 200 根，分别约为 12 小时、7 天、8 天 8 小时和 33 天 8 小时。冷启动异步回补期间实际数量可能更少。
- 前端实时合并上限是 1m 10,080 根、15m 8,640 根、1H/4H 各 2,000 根，但当前 WebSocket 只提供新数据和近期缺口修复；后台完成的更早 1m/15m 回补不会自动成为当前页面的向左历史页。
- 当前后端没有图表用 `before` 游标端点，组件也没有订阅左侧可见逻辑范围触发分页。因此缩放和平移只作用于已经装入组件的数据；这不是“可无限向左加载”。
- 后续实现需要：SQLite `ts < before` 的索引查询、严格枚举周期且限制页大小的只读 API、每周期独立游标/加载/结束状态、`subscribeVisibleLogicalRangeChange` 的防重入触发、旧页按时间戳合并，以及 prepend 后保持可见时间范围的交互测试。分页应只读本地已缓存 K 线，避免一次滚动直接触发不受控的上游网络请求。

本项涉及新的后端契约、冷启动同步状态、图表可见区生命周期和交互回归，不属于当前发布末尾的低风险补丁。保持首屏快照有界并列为后续功能，比把全部本地历史塞入每次快照更稳妥。

## 价格线拖动决策

本版本不实现拖动。Lightweight Charts 5.2.0 的正式 `IPriceLine` API 只有创建、更新和移除，没有完整的 pointer down/move/up 拖动生命周期；官方 primitive 示例也没有可直接复用的稳定拖动价格线机制。实现拖动需要自行编写命中检测与交互状态机，超出本轮“只使用稳定官方方案”的边界。四价继续由表单输入，图表即时同步，后端风险请求维持 250ms 防抖。

## 测试组件

为替换源码正则式验证，本版本增加 Vitest 4.1.10、React Testing Library 16.3.2、Testing Library DOM 10.4.1、user-event 14.6.1 与 jsdom 29.1.1。它们均为 MIT 许可证，与 React 19、Vite 8 和 Node 24 兼容。测试覆盖适配器确定性输出、真实 React 交互、图表实例清理、表单校验和执行状态/提醒状态分离。

## v0.6 市场分析组件

模型版本固定为 `indicator-regime-v06.0.0`。该功能是现有工作台和旧研究区之外的解释层，没有替换风险引擎、人工成交状态机、旧策略或旧回测。

后端边界：

- `backend/market_regime.py`：严格 point-in-time 清洗、七组单周期方向贡献、六项趋势强度、结构和波动状态。
- `backend/market_levels.py`：已确认 swing、前日高低、日/周开盘、日/周 VWAP、POC/VAH/VAL 与 ATR 聚类。
- `backend/market_analysis.py`：4H/1H/15m/1m 聚合、硬冲突、alignment、confidence、NO_CHASE、前端覆盖层和紧凑快照。
- `backend/market_validation.py`：15m 决策网格上的历史重建、60/40 切分、24h purge 和 HAC 探索性统计。
- `backend/db.py`：不可变 `market_analysis_snapshots`，按内容哈希幂等，`LIVE_OBSERVED` 保留 180 天。
- `backend/main.py`：`/api/market-analysis/current`、`history`、`validation` 和 `/ws/live` 的 `marketAnalysis` 推送。

前端边界：

- `frontend/src/MarketAnalysisView.tsx`：完整市场分析页面、四周期卡片、验证声明和历史趋势。
- `frontend/src/components/MarketAnalysisChart.tsx`：复用 Lightweight Charts 显示 MarketSnapshot 蜡烛与后端 EMA/VWAP 覆盖层，不在浏览器重算指标。
- `frontend/src/components/MarketSummaryCard.tsx`：工作台只读摘要，不改变计划、实际成交、提醒或执行状态。
- `frontend/src/components/TimeframeAnalysisCard.tsx`、`IndicatorContributionList.tsx`、`KeyLevelPanel.tsx`、`AnalysisHistoryChart.tsx`：分别负责单周期解释、七组贡献、关键位和已保存观察历史。
- `frontend/src/market-analysis-api.ts` 与 `market-analysis-types.ts`：独立归一化和 TypeScript 契约；无效有限值不会进入图表。

## v0.6 数据与生命周期审计

- 所有指标只使用 `confirm=true` 且 `timestamp + timeframe <= decisionAt` 的 K 线。
- Swing 使用左右各 2 根确认；缺失、过期、冲突重复、非法 OHLCV 和运行时缺口都会降级。
- 单周期七组权重严格为 `30/20/15/10/10/5/10`，多周期权重为 `4H 0.40 / 1H 0.35 / 15m 0.20 / 1m 0.05`。
- 趋势强度六项为 `ADX 30% / EMA斜率 20% / 结构持续性 20% / 突破持续性 10% / 成交量确认 10% / 波动支持 10%`，ADX 不进入方向分。
- 图表覆盖层来自后端已收盘数据；原始 OHLC 继续来自现有 MarketSnapshot，避免出现第二套行情事实源。
- 1m 只保留 7 天、15m 只保留 90 天；180 天仅指紧凑分析快照。早期历史验证缺少 1m 时明确降级。
- NO_CHASE 只提供操作上下文和具体原因，不改方向分，也不触发真实成交或自动交易。
- 验证只有一次 60/40 时间切分和单一 OOS 区间，`validationPass=false`；组件不得展示“已验证”或胜率保证。

## v0.6 依赖审计

本轮没有修改 `requirements.txt`、`frontend/package.json` 或 `frontend/pnpm-lock.yaml`，没有新增运行时或测试依赖。指标继续使用仓库已有 NumPy 和 `backend.indicators`，图表继续使用已审计的 Lightweight Charts 5.2.0。

Jesse（MIT）和 CryptoSignal（MIT）用于研究模块边界与指标解释方式的设计参考；Freqtrade（GPL-3.0）和 VectorBT（Apache-2.0 with Commons Clause）只作为验证、数据质量和回测方法的设计参考。本仓库没有复制这些项目的源代码、没有链接或导入其包，也没有将它们加入依赖锁文件。详见 `THIRD_PARTY_NOTICES.md`。
