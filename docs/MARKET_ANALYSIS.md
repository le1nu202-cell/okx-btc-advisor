# v0.6 市场分析模型

当前模型版本：`indicator-regime-v06.0.0`

## 定位与边界

v0.6 市场分析把四个公共 K 线周期整理成一个可解释的市场环境快照，回答的是“当前已收盘数据呈现什么结构、方向和追价风险”，不是“下一笔交易一定应该怎么做”。它与现有模块保持以下边界：

- 不替换 `backend/strategy.py` 的旧研究策略，也不改变冻结的三年回测结果。
- 不修改工作台风险公式、估算强平价、部分减仓成本池或人工成交确认流程。
- 不读取 OKX API Key、账户余额、真实仓位、订单或成交；数据仍来自 OKX 公共免鉴权行情。
- 不下单、改单或撤单。`WATCH_LONG`、`WATCH_SHORT` 和 `NO_CHASE` 都只是解释性上下文。
- 不把方向分解释为上涨或下跌概率，不承诺收益，也不构成个性化投资建议。

详细指标定义见 [INDICATOR_DEFINITIONS.md](./INDICATOR_DEFINITIONS.md)，验证边界见 [MARKET_ANALYSIS_VALIDATION.md](./MARKET_ANALYSIS_VALIDATION.md)。

## Point-in-time 数据契约

每次计算必须显式传入 `decision_at`。每个周期只允许使用同时满足下列条件的 K 线：

1. `confirm=true`；
2. `timestamp + timeframe_ms <= decision_at`；
3. 时间戳与周期边界对齐；
4. OHLCV 全部为有限数值；
5. 价格为正、成交量非负，并满足合法 OHLC 高低关系。

清洗顺序固定为“先排除未确认和未来 K 线，再严格校验剩余数据”。这样历史重建时，决策时点之后才出现的坏数据不会污染过去的结论。

相同时间戳且字段完全一致的记录会去重；相同时间戳出现不同 OHLCV 时，不猜测哪一条正确，该周期返回 `INSUFFICIENT_DATA / CONFLICTING_DUPLICATE`。指标只使用最长连续后缀，最多 400 根。完整单周期方向评分至少需要 200 根连续已收盘 K 线。

### 数据质量状态

| 状态 | 含义 |
|---|---|
| `AVAILABLE` | 连续数据、时效和指标覆盖达到发布条件 |
| `INSUFFICIENT_DATA` | 缺失、冲突重复、非法数据、连续根数不足、指标 coverage 低于 0.80，或运行时报告缺口 |
| `STALE` | 最近应当已经收盘的 K 线缺失，或公共行情连接不再处于 `connected` |

`qualityCode` 会进一步说明原因，例如 `OK`、`DEGRADED_TIMEFRAME_COVERAGE`、`OK_WITH_OLDER_GAPS`、`INSUFFICIENT_CONTIGUOUS_CANDLES`、`LOW_INDICATOR_COVERAGE`、`LATEST_CANDLE_STALE`、`REQUIRED_TIMEFRAME_STALE`、`EXTERNAL_GAP_DETECTED` 或 `CONNECTION_NOT_CONNECTED`。缺失、过期和缺口都必须降级，不以旧值、零值或未来数据回填。4H 或 1H 的最后确认 K 线过期时，综合结果直接进入 `STALE` 且方向不可用；1m 或 15m 过期时，该周期不参与聚合、coverage 与置信度下降，并显示具体警告。

## 单周期与多周期聚合

每个周期由七组方向贡献形成 `-100..100` 分数，固定权重为：

| 方向组 | 权重 |
|---|---:|
| 已确认 swing 结构 | 30 |
| EMA 排列与斜率 | 20 |
| MACD | 15 |
| RSI14 | 10 |
| DMI 方向 | 10 |
| 日内 VWAP | 5 |
| OBV 与相对成交量 | 10 |

`coverage = availableWeight / 100`，只有 coverage 不低于 `0.80` 才能发布单周期方向分。ADX 不进入方向分，而是进入独立的趋势强度。

四周期聚合权重固定为：

| 周期 | 权重 | 作用 |
|---|---:|---|
| 4H | 0.40 | 主要市场环境 |
| 1H | 0.35 | 主要方向与结构 |
| 15m | 0.20 | 近期确认和执行环境 |
| 1m | 0.05 | 微观确认 |

1m 可以小幅影响加权分与一致性，但不能单独跨越主要方向标签；触发保护时返回 `alignment.oneMinuteGuardApplied=true`。当 4H 与 1H 分别形成相反的有效多空方向时，视为硬冲突：`alignment.hardConflict=true`、`overallBias=CONFLICT`、综合方向分不可用，操作上下文退回等待。

方向标签阈值固定为：

- `>= 60`：`STRONG_BULLISH`（强多）
- `25 .. <60`：`BULLISH`（多）
- `>-25 .. <25`：`NEUTRAL`（中性）
- `>-60 .. <=-25`：`BEARISH`（空）
- `<= -60`：`STRONG_BEARISH`（强空）

`alignment.consistency` 表示可用周期与综合方向的一致程度；`confidence` 综合数据覆盖、一致性和趋势强度。它们是内部解释指标，不是统计胜率。

## 趋势强度

趋势强度为独立的 `0..100` 指标，不向方向分投票。当前六项固定权重为：

| 分项 | 权重 |
|---|---:|
| ADX 强度 | 30% |
| EMA 斜率强度 | 20% |
| swing 结构持续性 | 20% |
| 突破持续性 | 10% |
| 成交量确认 | 10% |
| 波动环境支持 | 10% |

趋势强度只描述当前趋势特征是否明显；高趋势强度不等于方向预测更准确。详细归一化方法见指标定义文档。

## 关键位

关键位只从决策时点可见的数据生成，候选来源包括：

- 4H 和 1H 已确认 swing 高低点；
- 前一 UTC 日高点、低点；
- 当前 UTC 日开盘和 ISO 周开盘；
- 日 VWAP、周 VWAP；
- 复用现有 `technical._volume_profile`、并只对截断后数据计算的 POC、VAH、VAL。

Swing 使用左右各 2 根确认，只有右侧第 2 根也已在 `decision_at` 前收盘时才可用。成交量分布使用最近最多 120 根基础周期 K 线、32 个价格桶和 `candle_range_uniform` 近似，不是逐笔成交量分布。

候选价按下列阈值做 ATR 聚类：

```text
clusterThreshold = max(currentPrice × 0.0005, ATR14 × 0.35)
```

聚类价格按来源强度加权，聚类强度来自合并后的来源权重；当前价下方归为支撑、上方归为阻力，分别只返回距离当前价最近的 3 个。前日高低、开盘价、VWAP 和成交量分布仍保留在 `keyLevels.references`，便于解释来源。

## NO_CHASE

`NO_CHASE` 只使用最近已收盘且质量为 `AVAILABLE` 的 15m 数据，并且只在综合方向已经形成时评估。满足任意一项便给出带实际数值的具体原因：

- 收盘沿综合方向偏离 EMA20 至少 `1.50 ATR`；
- 收盘沿综合方向偏离日 VWAP 至少 `1.50 ATR`；
- 最新同向 15m K 线高低振幅至少 `2.00 ATR`；
- 做多时 RSI14 `>=70`，或做空时 RSI14 `<=30`；
- 前方最近阻力/支撑沿交易方向只剩不超过 `0.35 ATR`。

触发后 `actionContext.action=NO_CHASE`，但 `directionScore` 和 `overallBias` 不被重写。它表示“当前方向解释仍在，但位置不适合追价”，不是自动禁止用户操作。

## API、WebSocket 与快照

| 接口或事件 | 用途 |
|---|---|
| `GET /api/market-analysis/current` | 只读返回当前完整分析，不因页面轮询写入历史 |
| `GET /api/market-analysis/history?limit=...` | 返回当前模型的 `LIVE_OBSERVED` 紧凑快照，最多 500 条 |
| `GET /api/market-analysis/validation` | 返回探索性历史重建报告 |
| `/ws/live` 的 `marketAnalysis` 事件 | 已收盘周期、连接或缺口状态变化时推送分析；`persisted=true` 才表示本次新增了 SQLite 快照 |

每个周期计算输入最多 400 根。`chartSeries` 只包含 EMA20/50/200 和日 VWAP 覆盖层；OHLC 蜡烛仍来自现有 `MarketSnapshot`，避免重复传输另一份行情事实源。

SQLite `market_analysis_snapshots` 保存不可变、按语义内容哈希幂等的紧凑 `LIVE_OBSERVED` 快照。触发来源属于审计元数据，不会把同一时点、同一分析保存成重复观察。常规持久化发生在 15m/1H/4H 新确认收盘或综合判断显著变化时；普通 current GET、未确认更新和没有新确认 K 线的 REST 对账不会制造历史。历史保留窗口为 180 天；这是分析观察快照的保留时间，不是 1m K 线的历史长度。`HISTORICAL_RECONSTRUCTED` 与实时观察来源严格分开，历史验证不会冒充当时真实保存过的快照。

## K 线保留与早期覆盖

- 1m：SQLite 仅保留最近 7 天。
- 15m：SQLite 仅保留最近 90 天。
- 1H、4H：保持现有研究和回测历史，不参与上述滚动裁剪。

因此对较早时段做历史重建时，1m 特征必然缺失；系统会记录覆盖率并将该周期降级，不补零、不借用未来 1m，也不把早期报告描述为四周期完整验证。15m 超过 90 天的早期区间同样受本地数据保留限制。

## 关键实现边界

- `backend/market_regime.py`：严格点时清洗、单周期指标、方向和趋势强度。
- `backend/market_levels.py`：关键位候选、成交量分布复用和 ATR 聚类。
- `backend/market_analysis.py`：四周期聚合、冲突、一致性、置信度、NO_CHASE、前端契约。
- `backend/market_validation.py`：历史 point-in-time 重建和探索性统计验证。
- `backend/db.py`：紧凑分析快照持久化与 180 天保留。
- `backend/main.py`：REST、WebSocket、运行时缓存和持久化触发。
- `frontend/src/MarketAnalysisView.tsx`：独立市场分析页面。
- `frontend/src/components/MarketSummaryCard.tsx`：工作台紧凑摘要；不改写计划或实际成交数据。
