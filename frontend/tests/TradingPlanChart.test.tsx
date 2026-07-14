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
import { actualFill, candles1h, candles4h, executionRisk, plannedRisk, shortPlan } from './workbench-fixtures'

const baseProps = {
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

  it('真实点击切换 1H/4H，并将对应蜡烛数据交给图表', async () => {
    const user = userEvent.setup()
    render(<TradingPlanChart {...baseProps}/>)

    expect(screen.getByRole('tab', { name: '1H' }).getAttribute('aria-selected')).toBe('true')
    expect(chartMocks.series.setData.mock.calls.at(-1)?.[0]).toHaveLength(2)
    expect(chartMocks.timeScale.fitContent).toHaveBeenCalledTimes(1)
    expect(chartMocks.createChart.mock.calls[0]?.[1]).toMatchObject({
      crosshair: { mode: 0 },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale: { mouseWheel: true, pinch: true },
    })

    await user.click(screen.getByRole('tab', { name: '4H' }))

    expect(screen.getByRole('tab', { name: '4H' }).getAttribute('aria-selected')).toBe('true')
    expect(chartMocks.series.setData.mock.calls.at(-1)?.[0].map((row: { time: number }) => Number(row.time))).toEqual([
      1_799_985_600,
      1_800_000_000,
    ])
    expect(chartMocks.timeScale.fitContent).toHaveBeenCalledTimes(2)
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
    expect(definitions.find(line => line.id === 'average-entry')).toMatchObject({ price: 100_777.123456, title: '实际加权均价' })
    expect(definitions.find(line => line.id === 'full-cost-breakeven')).toMatchObject({ price: 100_910.987654, title: '实际全成本保本价' })

    const markers = chartMocks.markerApi.setMarkers.mock.calls.at(-1)?.[0]
    expect(markers.map((marker: { id: string }) => marker.id)).toEqual(['actual-fill-initial', 'actual-fill-add'])

    const autoscale = chartMocks.series.applyOptions.mock.calls.at(-1)?.[0].autoscaleInfoProvider
    expect(autoscale(() => ({ priceRange: { minValue: 100_000, maxValue: 101_000 } }))).toMatchObject({
      priceRange: { minValue: 99_000, maxValue: 102_000 },
    })
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
    expect(screen.getByRole('status').textContent).toContain('行情数据已过期')

    rerender(<TradingPlanChart {...baseProps} stale={false} connectionStatus="reconnecting"/>)
    expect(screen.getByRole('status').textContent).toContain('reconnecting')
  })
})
