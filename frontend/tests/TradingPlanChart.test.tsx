import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const chartMocks = vi.hoisted(() => {
  const priceLines: Array<{ options: Record<string, unknown>; applyOptions: ReturnType<typeof vi.fn> }> = []
  const createPriceLine = vi.fn((options: Record<string, unknown>) => {
    const line = { options, applyOptions: vi.fn() }
    priceLines.push(line)
    return line
  })
  const series = {
    setData: vi.fn(),
    update: vi.fn(),
    applyOptions: vi.fn(),
    createPriceLine,
    removePriceLine: vi.fn(),
  }
  const timeScale = { fitContent: vi.fn() }
  const chart = {
    addSeries: vi.fn(() => series),
    timeScale: vi.fn(() => timeScale),
    applyOptions: vi.fn(),
    resize: vi.fn(),
    remove: vi.fn(),
  }
  const markerApi = { setMarkers: vi.fn(), detach: vi.fn() }
  return {
    priceLines,
    createPriceLine,
    series,
    timeScale,
    chart,
    markerApi,
    createChart: vi.fn(() => chart),
    createSeriesMarkers: vi.fn(() => markerApi),
  }
})

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: Symbol('CandlestickSeries'),
  ColorType: { Solid: 'solid' },
  CrosshairMode: { Normal: 0 },
  LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 },
  createChart: chartMocks.createChart,
  createSeriesMarkers: chartMocks.createSeriesMarkers,
}))

import TradingPlanChart from '../src/components/TradingPlanChart'
import { actualFill, candles1m, candles15m, candles1h, candles4h, executionRisk, plannedRisk, shortPlan, snapshot } from './workbench-fixtures'

const baseProps = {
  candles1m,
  candles15m,
  candles1h,
  candles4h,
  timeframe: '1H' as const,
  currentPrice: 100_900,
  plan: shortPlan,
  plannedRisk,
  executionRisk: null,
  actualFills: {},
  stale: false,
  connectionStatus: 'connected',
  candleStatus: snapshot.candleStatus,
}

describe('TradingPlanChart', () => {
  let disconnect: ReturnType<typeof vi.fn>

  beforeEach(() => {
    chartMocks.priceLines.splice(0)
    disconnect = vi.fn()
    class ResizeObserverMock {
      observe = vi.fn()
      unobserve = vi.fn()
      disconnect = disconnect
    }
    Object.defineProperty(globalThis, 'ResizeObserver', { configurable: true, writable: true, value: ResizeObserverMock })
  })

  it('真实点击切换 1m/15m/1H/4H，并将对应蜡烛数据交给图表', async () => {
    const user = userEvent.setup()
    const onTimeframeChange = vi.fn()
    render(<TradingPlanChart {...baseProps} onTimeframeChange={onTimeframeChange}/>)

    expect(screen.getByRole('tab', { name: '1H' }).getAttribute('aria-selected')).toBe('true')
    expect(chartMocks.series.setData.mock.calls.at(-1)?.[0]).toHaveLength(2)
    expect(chartMocks.timeScale.fitContent).toHaveBeenCalledTimes(1)
    expect(chartMocks.createChart.mock.calls[0]?.[1]).toMatchObject({
      crosshair: { mode: 0 },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale: { mouseWheel: true, pinch: true },
    })

    await user.click(screen.getByRole('tab', { name: '1m' }))
    expect(screen.getByRole('tab', { name: '1m' }).getAttribute('aria-selected')).toBe('true')
    expect(chartMocks.series.setData.mock.calls.at(-1)?.[0].map((row: { time: number }) => Number(row.time))).toEqual([
      1_800_003_480,
      1_800_003_540,
    ])
    expect(chartMocks.chart.applyOptions).toHaveBeenLastCalledWith({ timeScale: { secondsVisible: true } })

    await user.click(screen.getByRole('tab', { name: '15m' }))
    expect(screen.getByRole('tab', { name: '15m' }).getAttribute('aria-selected')).toBe('true')
    expect(chartMocks.series.setData.mock.calls.at(-1)?.[0].map((row: { time: number }) => Number(row.time))).toEqual([
      1_800_001_800,
      1_800_002_700,
    ])

    await user.click(screen.getByRole('tab', { name: '4H' }))

    expect(screen.getByRole('tab', { name: '4H' }).getAttribute('aria-selected')).toBe('true')
    expect(chartMocks.series.setData.mock.calls.at(-1)?.[0].map((row: { time: number }) => Number(row.time))).toEqual([
      1_799_985_600,
      1_800_000_000,
    ])
    expect(chartMocks.timeScale.fitContent).toHaveBeenCalledTimes(4)
    expect(onTimeframeChange.mock.calls.map(call => call[0])).toEqual(['1m', '15m', '4H'])
  })

  it('真实点击图表显示开关分别隐藏计划线、实际线和人工成交标记', async () => {
    const user = userEvent.setup()
    render(<TradingPlanChart
      {...baseProps}
      executionRisk={executionRisk}
      actualFills={{ initial: actualFill(100_010, 0.002, candles1h[0].timestamp) }}
    />)

    const planned = screen.getByRole('button', { name: '计划线' })
    const actual = screen.getByRole('button', { name: '实际线' })
    const fills = screen.getByRole('button', { name: '成交标记' })
    expect(planned.getAttribute('aria-pressed')).toBe('true')
    expect(actual.getAttribute('aria-pressed')).toBe('true')
    expect(fills.getAttribute('aria-pressed')).toBe('true')

    let before = chartMocks.createPriceLine.mock.calls.length
    await user.click(actual)
    const afterActual = chartMocks.createPriceLine.mock.calls.slice(before).map(call => call[0] as { title: string })
    expect(actual.getAttribute('aria-pressed')).toBe('false')
    expect(afterActual.some(line => line.title.startsWith('实际'))).toBe(false)
    expect(afterActual.some(line => line.title.startsWith('计划'))).toBe(true)

    before = chartMocks.createPriceLine.mock.calls.length
    await user.click(planned)
    expect(planned.getAttribute('aria-pressed')).toBe('false')
    expect(chartMocks.createPriceLine.mock.calls.slice(before)).toHaveLength(0)

    await user.click(fills)
    expect(fills.getAttribute('aria-pressed')).toBe('false')
    expect(chartMocks.markerApi.setMarkers).toHaveBeenLastCalledWith([])
  })

  it('同周期行情数据刷新不会重置用户缩放和平移', () => {
    const { rerender } = render(<TradingPlanChart {...baseProps}/>)
    expect(chartMocks.timeScale.fitContent).toHaveBeenCalledTimes(1)

    rerender(<TradingPlanChart {...baseProps} candles1h={[...candles1h]}/>)

    expect(chartMocks.series.setData).toHaveBeenCalledTimes(2)
    expect(chartMocks.timeScale.fitContent).toHaveBeenCalledTimes(1)
  })

  it('价格线使用后端实际风险字段，成交标记仅来自 actualFills', () => {
    render(<TradingPlanChart
      {...baseProps}
      executionRisk={executionRisk}
      actualFills={{
        initial: actualFill(100_010, 0.002, candles1h[0].timestamp),
        add: actualFill(101_050, 0.004, candles1h[1].timestamp),
      }}
    />)

    const definitions = chartMocks.priceLines.map(line => line.options)
    expect(definitions.find(line => line.id === 'actual-average-entry')).toMatchObject({ price: 100_777.123456, title: '实际 · 加权均价' })
    expect(definitions.find(line => line.id === 'actual-full-cost-breakeven')).toMatchObject({ price: 100_910.987654, title: '实际 · 全成本保本价' })

    const markers = chartMocks.markerApi.setMarkers.mock.calls.at(-1)?.[0]
    expect(markers.map((marker: { id: string }) => marker.id)).toEqual(['actual-fill-initial', 'actual-fill-add'])

    const autoscale = chartMocks.series.applyOptions.mock.calls.at(-1)?.[0].autoscaleInfoProvider
    expect(autoscale(() => ({ priceRange: { minValue: 100_000, maxValue: 101_000 } }))).toMatchObject({
      priceRange: { minValue: 99_000, maxValue: 102_000 },
    })
  })

  it('成交标记只锚定到最近已收盘 K 线', () => {
    const candlesWithOpenBar = [candles1h[0], { ...candles1h[1], confirm: false }]
    render(<TradingPlanChart
      {...baseProps}
      candles1h={candlesWithOpenBar}
      actualFills={{ initial: actualFill(100_010, 0.002, candles1h[1].timestamp) }}
    />)

    const markers = chartMocks.markerApi.setMarkers.mock.calls.at(-1)?.[0]
    expect(markers).toHaveLength(1)
    expect(Number(markers[0].time)).toBe(candles1h[0].timestamp / 1_000)
  })

  it('当前价更新不重建图表，卸载时 remove 并断开尺寸观察器', () => {
    const { rerender, unmount } = render(<TradingPlanChart {...baseProps}/>)
    const currentLine = chartMocks.priceLines.find(line => line.options.id === 'current-market-price')

    rerender(<TradingPlanChart {...baseProps} currentPrice={101_111}/>)

    expect(chartMocks.createChart).toHaveBeenCalledTimes(1)
    expect(currentLine?.applyOptions).toHaveBeenLastCalledWith({ price: 101_111 })

    unmount()
    expect(chartMocks.chart.remove).toHaveBeenCalledTimes(1)
    expect(disconnect).toHaveBeenCalledTimes(1)
  })

  it('数据过期或断线时显示明确提示', () => {
    const { rerender } = render(<TradingPlanChart {...baseProps} stale/>)
    expect(screen.getByRole('status').textContent).toContain('1H 行情已过期')

    rerender(<TradingPlanChart {...baseProps} stale={false} connectionStatus="reconnecting"/>)
    expect(screen.getByRole('status').textContent).toContain('reconnecting')
  })

  it('所选周期不可用或有缺口时使用该周期独立状态', async () => {
    const user = userEvent.setup()
    render(<TradingPlanChart {...baseProps} candleStatus={{
      ...snapshot.candleStatus,
      '1m': { available: false, stale: true, lastAt: null, gapDetected: false },
      '15m': { available: true, stale: false, lastAt: candles15m.at(-1)!.timestamp, gapDetected: true },
    }}/>)

    await user.click(screen.getByRole('tab', { name: '1m' }))
    expect(screen.getByRole('status').textContent).toContain('1m 暂无可用 K 线')
    await user.click(screen.getByRole('tab', { name: '15m' }))
    expect(screen.getByRole('status').textContent).toContain('15m 检测到行情缺口')
  })
})
