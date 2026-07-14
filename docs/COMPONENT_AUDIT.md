# Frontend Component Audit

审计日期：2026-07-14

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

## 价格线拖动决策

本版本不实现拖动。Lightweight Charts 5.2.0 的正式 `IPriceLine` API 只有创建、更新和移除，没有完整的 pointer down/move/up 拖动生命周期；官方 primitive 示例也没有可直接复用的稳定拖动价格线机制。实现拖动需要自行编写命中检测与交互状态机，超出本轮“只使用稳定官方方案”的边界。四价继续由表单输入，图表即时同步，后端风险请求维持 250ms 防抖。

## 测试组件

为替换源码正则式验证，本版本增加 Vitest 4.1.10、React Testing Library 16.3.2、Testing Library DOM 10.4.1、user-event 14.6.1 与 jsdom 29.1.1。它们均为 MIT 许可证，与 React 19、Vite 8 和 Node 24 兼容。测试覆盖适配器确定性输出、真实 React 交互、图表实例清理、表单校验和执行状态/提醒状态分离。
