# v0.6 指标定义

适用模型：`indicator-regime-v06.0.0`

本文记录当前代码的确定性指标语义。所有指标只接收在 `decision_at` 前已经收盘、通过严格校验且位于最长连续后缀中的 K 线；最多使用 400 根，完整评分至少需要 200 根。

## 方向贡献总览

单周期原始方向分为七组可用贡献之和：

```text
rawDirectionScore = swingStructure
                  + ema
                  + macd
                  + rsi
                  + dmiDirection
                  + vwap
                  + obvRelativeVolume
```

| 组 | 最大绝对贡献 | 当前规则 |
|---|---:|---|
| `swingStructure` | 30 | 最近两个已确认 swing 高点方向 15 分，最近两个已确认 swing 低点方向 15 分 |
| `ema` | 20 | 收盘/EMA20、EMA20/EMA50、EMA20 五根斜率、EMA50 五根斜率，各 5 分 |
| `macd` | 15 | MACD 线高于信号线为 +15，低于为 -15，相等为 0 |
| `rsi` | 10 | `clip((RSI14-50)/20,-1,1) × 10` |
| `dmiDirection` | 10 | `(+DI--DI)/(+DI+-DI) × 10`，分母为零时为 0 |
| `vwap` | 5 | 收盘高于当前 UTC 日 VWAP 为 +5，低于为 -5 |
| `obvRelativeVolume` | 10 | OBV 最近 10 根变化最多 5 分；最新价格方向按相对量强度再贡献最多 5 分 |

每组只有在所需输入可计算时才计入 `availableWeight`：

```text
coverage = availableWeight / 100
```

coverage 低于 `0.80` 时不发布 `directionScore`，即使其余指标看起来方向一致，也返回 `INSUFFICIENT_DATA / LOW_INDICATOR_COVERAGE`。

## Swing 结构

Swing 固定使用 `left=2, right=2`：

- Swing High：中心 K 线高点严格高于左右各两根高点。
- Swing Low：中心 K 线低点严格低于左右各两根低点。
- `confirmedAt` 是右侧第 2 根 K 线的收盘时间，必须不晚于 `decision_at`。

最近高点和低点同时抬高为 `HH_HL / BULLISH`，同时降低为 `LH_LL / BEARISH`；方向相反为 `TRANSITION / MIXED`。缺少两个已确认高点或两个已确认低点时，本组不可用，不以局部未确认拐点补足。

## EMA

使用现有 `backend.indicators.ema`，周期为 20、50、200。EMA200 作为展示字段，不直接增加本组方向票。

方向贡献由四个等权的 5 分票组成：

1. 最新收盘相对 EMA20；
2. EMA20 相对 EMA50；
3. EMA20 相对五根前的斜率，先除以当前 ATR14；
4. EMA50 相对五根前的斜率，先除以当前 ATR14。

零差值贡献 0。标准化斜率同时用于趋势强度，但方向贡献和趋势强度是两套独立输出。

## MACD

使用现有 `backend.indicators.macd` 的 `12/26/9` 参数：

- `line > signal`：+15；
- `line < signal`：-15；
- 相等：0。

Histogram 及其是否上升作为解释字段输出，但当前不额外增加方向分。

## RSI14

使用 Wilder 平滑。项目在 v0.6 模块中明确修正平盘语义：平均上涨和平均下跌同时为零时，RSI 为 50，而不是 0。

```text
rsiScore = clip((RSI14 - 50) / 20, -1, 1) × 10
```

因此 RSI30 对应 -10，RSI50 对应 0，RSI70 对应 +10。该方向映射描述动量，不等于超买必跌或超卖必涨。

## DMI 与 ADX

TR、+DM、-DM 使用现有 `atr` 和 `wilder` 平滑。模块同时输出 `ADX / +DI / -DI`。

```text
dmiScore = (+DI - -DI) / (+DI + -DI) × 10
```

ADX 不进入方向分。ADX 仅用于独立趋势强度和 `TREND/RANGE/TRANSITION` 环境描述，从而避免把“趋势很强”和“方向向上”混为一件事。

## VWAP

日/周 VWAP 使用典型价：

```text
typicalPrice = (high + low + close) / 3
VWAP = sum(typicalPrice × volume) / sum(volume)
```

日锚点按 UTC 自然日，周锚点按 ISO 年和周。只有日 VWAP 相对位置进入 5 分方向贡献，周 VWAP作为解释和关键位来源。

## OBV 与相对成交量

OBV 使用现有 `backend.indicators.obv`。方向贡献分为：

- 当前 OBV 相对 10 根前：`-5 / 0 / +5`；
- 最新收盘方向乘以相对成交量强度，最多 `±5`。

相对成交量为当前成交量除以前 20 根、不包含当前的平均成交量；强度按 `clip(relativeVolume,0,2)/2` 截断。前 20 根平均量为零时，本组不可用。

## ATR 与波动分位

ATR14 使用现有 `backend.indicators.atr`。波动分位比较 `ATR/close`，历史基准最多取此前 100 个有限值，明确不包含当前 K 线：

```text
atrPercentile = count(prior ATR/close <= current ATR/close) / baselineCount × 100
```

当前波动状态：

- `<20`：`COMPRESSION`
- `20..70`：`NORMAL`
- `>70..90`：`EXPANSION`
- `>90`：`EXTREME`

收盘相对 EMA20 的距离达到 `+1.5 ATR` 为 `EXTENDED_UP`，达到 `-1.5 ATR` 为 `EXTENDED_DOWN`，否则为 `NOT_EXTENDED`。

## 趋势强度六项

趋势强度为 `0..100`，当前固定公式：

```text
trendStrength = 0.30 × adxStrength
              + 0.20 × emaSlopeStrength
              + 0.20 × structurePersistence
              + 0.10 × breakoutPersistence
              + 0.10 × volumeConfirmation
              + 0.10 × volatilitySupport
```

| 分项 | 归一化 |
|---|---|
| `adxStrength` | `clip(ADX/50×100,0,100)`；同时输出 ADX 相对五根前是否上升 |
| `emaSlopeStrength` | EMA20、EMA50 五根 ATR 标准化斜率绝对值的较大者，乘 100 后截断到 100 |
| `structurePersistence` | 明确多/空结构 100，混合 40，中性 10，未知 0 |
| `breakoutPersistence` | 最近 5 根中，沿近 5 根方向持续收于此前 20 根高点之上或低点之下的比例 |
| `volumeConfirmation` | `clip(relativeVolume/1.5×100,0,100)` |
| `volatilitySupport` | ATR 分位 30..90 为 100；15..97 为 50；其余或未知为 10 |

环境分类还要求：

- ADX `>=25` 且趋势强度 `>=50`：`TREND`；
- ADX `<=18` 且 EMA20/EMA50 分离强度不高于 25：`RANGE`；
- 其他：`TRANSITION`。

趋势强度不改变方向标签，也不能作为未来收益概率使用。

## 四周期聚合

```text
4H  = 0.40
1H  = 0.35
15m = 0.20
1m  = 0.05
```

综合分只使用已发布的单周期分数，并按可用周期权重归一。4H 与 1H 有效方向相反时不发布综合分。1m 若单独导致标签跨档，则启用 1m guard 并保持不含 1m 的主要标签。

综合置信度是解释性组合：

```text
confidence = 0.40 × dataCoverage
           + 0.35 × alignment
           + 0.25 × trendStrength
```

硬冲突时置信度封顶 20；整体状态不是 `AVAILABLE` 时置信度为 0。该值不是经过校准的命中概率。
