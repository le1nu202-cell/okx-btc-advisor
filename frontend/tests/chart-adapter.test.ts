import { describe, expect, it } from 'vitest'
import { buildActualFillMarkers, buildPlanPriceLines, toChartCandles } from '../src/chart-adapter'
import { actualFill, candles1h, executionRisk, plannedRisk, shortPlan } from './workbench-fixtures'

describe('chart-adapter', () => {
  it('将毫秒 K 线按 UTC 秒排序、去重并排除非法 OHLC', () => {
    const result = toChartCandles([
      candles1h[1],
      { ...candles1h[0], close: 100_100 },
      { ...candles1h[0], close: 100_250 },
      { ...candles1h[0], timestamp: -1 },
      { ...candles1h[0], high: 99_000 },
    ])

    expect(result).toHaveLength(2)
    expect(result.map(row => Number(row.time))).toEqual([1_800_000_000, 1_800_003_600])
    expect(result[0]).toMatchObject({ open: 100_000, high: 100_400, low: 99_800, close: 100_250 })
  })

  it('六条计划线直接使用后端 plannedRisk 字段', () => {
    const lines = buildPlanPriceLines(shortPlan, plannedRisk, null)
    expect(lines.map(line => [line.id, line.price, line.title])).toEqual([
      ['initial-entry', 100_000, '初始开仓价'],
      ['add', 101_000, '第一压力位 / 加仓价'],
      ['stop', 102_000, '第二压力位 / 硬止损价'],
      ['take-profit', 99_000, '止盈价'],
      ['average-entry', plannedRisk.averageEntryPrice, '计划加权均价'],
      ['full-cost-breakeven', plannedRisk.fullCostBreakevenPrice, '计划全成本保本价'],
    ])
  })

  it('有真实成交后使用 executionRisk 权威均价和保本价，不根据 fills 重算', () => {
    const lines = buildPlanPriceLines(shortPlan, plannedRisk, executionRisk)
    const average = lines.find(line => line.id === 'average-entry')
    const breakeven = lines.find(line => line.id === 'full-cost-breakeven')

    expect(average).toMatchObject({ price: 100_777.123456, title: '实际加权均价' })
    expect(breakeven).toMatchObject({ price: 100_910.987654, title: '实际全成本保本价' })
  })

  it('成交标记只由 actualFills 生成，并按成交时间和动作顺序确定', () => {
    const chartCandles = toChartCandles(candles1h)
    expect(buildActualFillMarkers(undefined, chartCandles, 'SHORT')).toEqual([])

    const markers = buildActualFillMarkers({
      add: actualFill(101_050, 0.004, candles1h[1].timestamp),
      initial: actualFill(100_010, 0.002, candles1h[0].timestamp),
      reduce: actualFill(100_800, 0.004, candles1h[1].timestamp),
    }, chartCandles, 'SHORT')

    expect(markers.map(marker => marker.id)).toEqual([
      'actual-fill-initial',
      'actual-fill-add',
      'actual-fill-reduce',
    ])
    expect(markers.map(marker => Number(marker.time))).toEqual([
      1_800_000_000,
      1_800_003_600,
      1_800_003_600,
    ])
    expect(markers[0].text).toContain('人工确认开仓')
  })

  it('成交确认落在两根 K 线之间时固定到之前的已收盘 K 线', () => {
    const chartCandles = toChartCandles(candles1h)
    const betweenBars = candles1h[0].timestamp + 55 * 60_000
    const markers = buildActualFillMarkers({
      initial: actualFill(100_010, 0.002, betweenBars),
    }, chartCandles, 'SHORT')

    expect(Number(markers[0].time)).toBe(1_800_000_000)
  })
})
