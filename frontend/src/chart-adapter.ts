import { LineStyle } from 'lightweight-charts'
import type { CandlestickData, SeriesMarker, UTCTimestamp } from 'lightweight-charts'
import type { Candle } from './types'
import type { ActualFill, ActualFillKey, ExecutionRisk, RiskCalculation, TradeDirection, TradePlanDraft } from './workbench-types'

export const CHART_TIMEFRAMES = ['1m', '15m', '1H', '4H'] as const
export type ChartTimeframe = typeof CHART_TIMEFRAMES[number]
export type PriceLineGroup = 'planned' | 'actual'

export interface PlanPriceLineDefinition {
  id: 'planned-initial-entry' | 'planned-add' | 'planned-stop' | 'planned-take-profit' | 'planned-average-entry' | 'planned-full-cost-breakeven' | 'actual-average-entry' | 'actual-full-cost-breakeven'
  group: PriceLineGroup
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
    byTime.set(Number(time), { time, open: candle.open, high: candle.high, low: candle.low, close: candle.close })
  }
  return [...byTime.values()].sort((left, right) => Number(left.time) - Number(right.time))
}

export function buildPlanPriceLines(
  plan: TradePlanDraft,
  plannedRisk: RiskCalculation | null,
  executionRisk: ExecutionRisk | null,
): PlanPriceLineDefinition[] {
  const plannedBreakeven = plannedRisk?.fullCostBreakevenPrice
    ?? plannedRisk?.allInBreakevenPrice
    ?? plannedRisk?.feeAdjustedBreakevenPrice
  const candidates: Array<PlanPriceLineDefinition | null> = [
    finitePositive(plan.initialEntryPrice) ? { id: 'planned-initial-entry', group: 'planned', title: '计划 · 初始开仓价', price: plan.initialEntryPrice, color: '#829bb0', lineStyle: LineStyle.Solid } : null,
    finitePositive(plan.addPrice) ? { id: 'planned-add', group: 'planned', title: '计划 · 加仓价', price: plan.addPrice, color: '#b89a61', lineStyle: LineStyle.Solid } : null,
    finitePositive(plan.stopPrice) ? { id: 'planned-stop', group: 'planned', title: '计划 · 硬止损价', price: plan.stopPrice, color: '#e0677d', lineStyle: LineStyle.Solid } : null,
    finitePositive(plan.takeProfitPrice) ? { id: 'planned-take-profit', group: 'planned', title: '计划 · 止盈价', price: plan.takeProfitPrice, color: '#6ea5d8', lineStyle: LineStyle.Solid } : null,
    finitePositive(plannedRisk?.averageEntryPrice) ? { id: 'planned-average-entry', group: 'planned', title: '计划 · 加权均价', price: plannedRisk.averageEntryPrice, color: '#9aafc1', lineStyle: LineStyle.Solid } : null,
    finitePositive(plannedBreakeven) ? { id: 'planned-full-cost-breakeven', group: 'planned', title: '计划 · 全成本保本价', price: plannedBreakeven, color: '#a7c2d3', lineStyle: LineStyle.Solid } : null,
    finitePositive(executionRisk?.averageEntryPrice) ? { id: 'actual-average-entry', group: 'actual', title: '实际 · 加权均价', price: executionRisk.averageEntryPrice, color: '#49e7ac', lineStyle: LineStyle.Dashed } : null,
    finitePositive(executionRisk?.fullCostBreakevenPrice) ? { id: 'actual-full-cost-breakeven', group: 'actual', title: '实际 · 全成本保本价', price: executionRisk.fullCostBreakevenPrice, color: '#9af0cf', lineStyle: LineStyle.Dotted } : null,
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
