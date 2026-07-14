import { LineStyle } from 'lightweight-charts'
import type { CandlestickData, SeriesMarker, UTCTimestamp } from 'lightweight-charts'
import type { Candle } from './types'
import type { ActualFill, ActualFillKey, ExecutionRisk, RiskCalculation, TradeDirection, TradePlanDraft } from './workbench-types'

export type ChartTimeframe = '1H' | '4H'

export interface PlanPriceLineDefinition {
  id: 'initial-entry' | 'add' | 'stop' | 'take-profit' | 'average-entry' | 'full-cost-breakeven'
  title: string
  price: number
  color: string
  lineStyle: LineStyle
}

const finitePositive = (value: number | null | undefined): value is number =>
  value != null && Number.isFinite(value) && value > 0

const utcSeconds = (timestamp: number): UTCTimestamp =>
  Math.floor(timestamp >= 1_000_000_000_000 ? timestamp / 1_000 : timestamp) as UTCTimestamp

export function toChartCandles(candles: readonly Candle[]): CandlestickData<UTCTimestamp>[] {
  const byTime = new Map<number, CandlestickData<UTCTimestamp>>()
  for (const candle of candles) {
    if (
      !Number.isFinite(candle.timestamp) || candle.timestamp <= 0
      || ![candle.open, candle.high, candle.low, candle.close].every(finitePositive)
      || candle.low > Math.min(candle.open, candle.close)
      || candle.high < Math.max(candle.open, candle.close)
      || candle.low > candle.high
    ) continue
    const time = utcSeconds(candle.timestamp)
    byTime.set(Number(time), {
      time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
    })
  }
  return [...byTime.values()].sort((left, right) => Number(left.time) - Number(right.time))
}

export function buildPlanPriceLines(
  plan: TradePlanDraft,
  plannedRisk: RiskCalculation | null,
  executionRisk: ExecutionRisk | null,
): PlanPriceLineDefinition[] {
  const actualAverage = executionRisk?.averageEntryPrice
  const actualBreakeven = executionRisk?.fullCostBreakevenPrice
  const average = finitePositive(actualAverage) ? actualAverage : plannedRisk?.averageEntryPrice
  const breakeven = finitePositive(actualBreakeven)
    ? actualBreakeven
    : plannedRisk?.fullCostBreakevenPrice ?? plannedRisk?.allInBreakevenPrice ?? plannedRisk?.feeAdjustedBreakevenPrice
  const candidates: Array<PlanPriceLineDefinition | null> = [
    finitePositive(plan.initialEntryPrice) ? { id: 'initial-entry', title: '初始开仓价', price: plan.initialEntryPrice, color: '#49e7ac', lineStyle: LineStyle.Solid } : null,
    finitePositive(plan.addPrice) ? { id: 'add', title: '第一压力位 / 加仓价', price: plan.addPrice, color: '#f3b64a', lineStyle: LineStyle.Solid } : null,
    finitePositive(plan.stopPrice) ? { id: 'stop', title: '第二压力位 / 硬止损价', price: plan.stopPrice, color: '#ff627d', lineStyle: LineStyle.Solid } : null,
    finitePositive(plan.takeProfitPrice) ? { id: 'take-profit', title: '止盈价', price: plan.takeProfitPrice, color: '#56a8ff', lineStyle: LineStyle.Solid } : null,
    finitePositive(average) ? { id: 'average-entry', title: finitePositive(actualAverage) ? '实际加权均价' : '计划加权均价', price: average, color: '#c38cff', lineStyle: LineStyle.Dashed } : null,
    finitePositive(breakeven) ? { id: 'full-cost-breakeven', title: finitePositive(actualBreakeven) ? '实际全成本保本价' : '计划全成本保本价', price: breakeven, color: '#f6ef91', lineStyle: LineStyle.Dashed } : null,
  ]
  return candidates.filter((line): line is PlanPriceLineDefinition => line !== null)
}

const FILL_ORDER: readonly ActualFillKey[] = ['initial', 'add', 'reduce', 'exit']
const FILL_LABELS: Record<ActualFillKey, string> = {
  initial: '人工确认开仓',
  add: '人工确认加仓',
  reduce: '人工确认减仓',
  exit: '人工确认退出',
}

function nearestCandleTime(candles: readonly CandlestickData<UTCTimestamp>[], confirmedAt?: number): UTCTimestamp | null {
  if (!candles.length) return null
  if (!finitePositive(confirmedAt)) return candles[candles.length - 1].time
  const target = Number(utcSeconds(confirmedAt))
  let atOrBefore = candles[0]
  if (Number(atOrBefore.time) > target) return atOrBefore.time
  for (const candle of candles) {
    if (Number(candle.time) > target) break
    atOrBefore = candle
  }
  return atOrBefore.time
}

function fillShape(key: ActualFillKey, direction: TradeDirection): Pick<SeriesMarker<UTCTimestamp>, 'shape' | 'color'> {
  if (key === 'initial' || key === 'add') {
    return direction === 'LONG' ? { shape: 'arrowUp', color: '#49e7ac' } : { shape: 'arrowDown', color: '#ff8b9d' }
  }
  if (key === 'reduce') return { shape: 'square', color: '#f3b64a' }
  return direction === 'LONG' ? { shape: 'arrowDown', color: '#56a8ff' } : { shape: 'arrowUp', color: '#56a8ff' }
}

export function buildActualFillMarkers(
  actualFills: Partial<Record<ActualFillKey, ActualFill>> | null | undefined,
  candles: readonly CandlestickData<UTCTimestamp>[],
  direction: TradeDirection,
): SeriesMarker<UTCTimestamp>[] {
  const markers: Array<SeriesMarker<UTCTimestamp> & { order: number }> = []
  for (const [order, key] of FILL_ORDER.entries()) {
    const fill = actualFills?.[key]
    if (!fill || !finitePositive(fill.price) || !finitePositive(fill.quantityBtc)) continue
    const time = nearestCandleTime(candles, fill.confirmedAt)
    if (time == null) continue
    const visual = fillShape(key, direction)
    markers.push({
      id: `actual-fill-${key}`,
      time,
      price: fill.price,
      position: 'atPriceMiddle',
      shape: visual.shape,
      color: visual.color,
      text: `${FILL_LABELS[key]} · ${fill.price.toFixed(2)} · ${fill.quantityBtc.toFixed(8)} BTC`,
      size: 1.2,
      order,
    })
  }
  return markers
    .sort((left, right) => Number(left.time) - Number(right.time) || left.order - right.order)
    .map(({ order: _order, ...marker }) => marker)
}
