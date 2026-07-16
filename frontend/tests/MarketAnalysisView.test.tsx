import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const apiMock = vi.hoisted(() => ({
  current: vi.fn(),
  history: vi.fn(),
  validation: vi.fn(),
  snapshot: vi.fn(),
  currentPlanOverlay: vi.fn(),
}))
const historyItemsMock = vi.hoisted(() => vi.fn())

const chartMock = vi.hoisted(() => {
  const series = {
    setData: vi.fn(),
    applyOptions: vi.fn(),
    createPriceLine: vi.fn(() => ({})),
    removePriceLine: vi.fn(),
  }
  const chart = {
    addSeries: vi.fn(() => series),
    applyOptions: vi.fn(),
    resize: vi.fn(),
    remove: vi.fn(),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
  }
  return { chart, series, createChart: vi.fn(() => chart) }
})

vi.mock('../src/market-analysis-api', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/market-analysis-api')>()
  return { ...actual, marketAnalysisApi: apiMock }
})

vi.mock('../src/components/AnalysisHistoryChart', () => ({
  default: ({ items }: { items: MarketAnalysis[] }) => {
    historyItemsMock(items)
    return <div data-testid="analysis-history-items">{items.length}</div>
  },
}))

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: Symbol('CandlestickSeries'),
  LineSeries: Symbol('LineSeries'),
  ColorType: { Solid: 'solid' },
  CrosshairMode: { Normal: 0 },
  LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 },
  createChart: chartMock.createChart,
}))

import MarketAnalysisView from '../src/MarketAnalysisView'
import MarketSummaryCard from '../src/components/MarketSummaryCard'
import { emptyChartSeries, type MarketAnalysis, type MarketAnalysisValidation } from '../src/market-analysis-types'
import type { MarketSnapshot } from '../src/types'

const now = 1_900_000_000_000
const quality = { status: 'AVAILABLE', fresh: true, stale: false, warnings: [] as string[], missingTimeframes: [], gapTimeframes: [] }
const chartSeries = emptyChartSeries()
chartSeries['1H'].ema20 = [{ timestamp: now - 3_600_000, value: 100_000 }]
chartSeries['1H'].ema50 = [{ timestamp: now - 3_600_000, value: 99_800 }]

const analysis: MarketAnalysis = {
  overallBias: 'BEARISH',
  actionContext: 'NO_CHASE',
  compositeScore: -42,
  trendStrength: 68,
  alignmentScore: 75,
  confidence: 72,
  summary: '4H 与 1H 偏空，但 15m 已经远离均线。',
  primaryReason: '高周期结构向下。',
  invalidationLevel: 102_500,
  nearestSupport: 98_000,
  nearestResistance: 101_500,
  timeframeAnalyses: (['1m', '15m', '1H', '4H'] as const).map((timeframe, index) => ({
    timeframe,
    status: 'AVAILABLE',
    bias: index < 2 ? 'NEUTRAL' : 'BEARISH',
    actionContext: index === 1 ? 'NO_CHASE' : 'WATCH_SHORT',
    regime: index < 2 ? 'TRANSITION' : 'TREND',
    score: -15 - index * 10,
    trendStrength: 42 + index * 8,
    confidence: 60,
    summary: `${timeframe} 后端结论`,
    primaryReason: `${timeframe} 主因`,
    asOf: now,
    dataQuality: quality,
    indicators: [{ key: 'ema20', label: 'EMA20', value: 100_000 - index * 100, valueText: '', status: 'AVAILABLE' }],
    contributions: [{ key: 'ema-order', name: 'EMA 排列', score: -12, maxScore: 20, value: null, valueText: '空头排列', explanation: '由后端已收盘 K 线计算。', available: true }],
  })),
  keyLevels: [
    { id: 'support-1', price: 98_000, kind: 'SUPPORT', label: '4H 摆动低点', strength: 80, strengthLabel: '强', source: '', sources: ['SWING_4H'], timeframe: '4H', touches: 2 },
    { id: 'resistance-1', price: 101_500, kind: 'RESISTANCE', label: '成交密集区', strength: 70, strengthLabel: '', source: 'VOLUME_PROFILE', sources: [], timeframe: '1H', touches: null },
  ],
  supportingReasons: ['4H 顺势偏空'],
  conflictingReasons: ['1m 出现短线反弹'],
  riskWarnings: ['数据只来自公共行情'],
  dataQuality: quality,
  asOf: now,
  modelVersion: 'indicator-regime-v06',
  chartSeries,
}

const validation: MarketAnalysisValidation = {
  status: 'INSUFFICIENT_DATA', sufficient: false, passed: null, sampleSize: 12, minimumSampleSize: 100,
  summary: '样本尚不足以得出可靠验证结论。', warnings: [],
  metrics: [{ key: 'direction-hit', label: '方向命中率', value: 0.5, valueText: '50%', passed: null }],
  asOf: now, modelVersion: 'indicator-regime-v06',
}

const candle = { timestamp: now - 60_000, open: 100_000, high: 100_100, low: 99_900, close: 100_020, volume: 10, confirm: true }
const candleStatus = { available: true, stale: false, lastAt: candle.timestamp, lastConfirmedAt: candle.timestamp, confirmedStale: false, gapDetected: false }
const snapshot: MarketSnapshot = {
  instrument: 'BTC-USDT-SWAP', price: 100_020, updatedAt: now, stale: false, connectionStatus: 'connected',
  fundingRate: null, fundingTime: null, openInterest: null, openInterestTime: null,
  candles1m: [candle], candles15m: [candle], candles1h: [candle], candles4h: [candle],
  markPrice: 100_018, markPriceTime: now,
  candleStatus: { '1m': candleStatus, '15m': candleStatus, '1H': candleStatus, '4H': candleStatus },
}

describe('MarketAnalysisView 真实交互', () => {
  beforeEach(() => {
    apiMock.current.mockReset().mockResolvedValue(analysis)
    apiMock.history.mockReset().mockResolvedValue([{ ...analysis, asOf: now - 60_000 }, analysis])
    apiMock.validation.mockReset().mockResolvedValue(validation)
    apiMock.snapshot.mockReset().mockResolvedValue(snapshot)
    apiMock.currentPlanOverlay.mockReset().mockResolvedValue({
      planId: 'plan-1',
      planned: [{ id: 'plan-entry', label: '计划初始价', price: 100_000, group: 'planned' }],
      actual: [{ id: 'actual-average', label: '实际均价', price: 100_200, group: 'actual' }],
    })
    chartMock.createChart.mockClear()
    historyItemsMock.mockClear()
  })

  it('五秒内展示总体、四周期、证据、关键位、NO_CHASE 和样本不足', async () => {
    render(<MarketAnalysisView/>)

    expect(await screen.findByRole('heading', { name: '市场分析', level: 1 })).toBeTruthy()
    expect(screen.getAllByText('禁止追价').length).toBeGreaterThan(0)
    expect(screen.getByText(/等待价格与结构重新给出合理位置/)).toBeTruthy()
    for (const timeframe of ['1m', '15m', '1H', '4H']) expect(screen.getByRole('article', { name: `${timeframe} 周期分析` })).toBeTruthy()
    expect(screen.getByText('4H 顺势偏空')).toBeTruthy()
    expect(screen.getByText('1m 出现短线反弹')).toBeTruthy()
    expect(screen.getByText('数据只来自公共行情')).toBeTruthy()
    expect(screen.getByText(/98,000 USDT/)).toBeTruthy()
    expect(screen.getByRole('heading', { name: '验证样本不足' })).toBeTruthy()
    expect(screen.getByText(/不读取账户、不自动交易/)).toBeTruthy()
  })

  it('指标明细默认折叠，点击后展开后端贡献', async () => {
    const user = userEvent.setup()
    const { container } = render(<MarketAnalysisView/>)
    await screen.findByRole('heading', { name: '四个周期一眼对齐' })
    const details = container.querySelector('details') as HTMLDetailsElement
    expect(details.open).toBe(false)
    await user.click(screen.getByText('展开后端指标贡献'))
    expect(details.open).toBe(true)
    expect(screen.getAllByText('EMA 排列')).toHaveLength(4)
    expect(screen.getAllByText('由后端已收盘 K 线计算。')).toHaveLength(4)
  })

  it('四周期和图层开关可真实切换，非核心图层默认关闭', async () => {
    const user = userEvent.setup()
    render(<MarketAnalysisView/>)
    const chart = await screen.findByTestId('market-analysis-chart')
    const oneHour = within(chart).getByRole('tab', { name: '1H' })
    const oneMinute = within(chart).getByRole('tab', { name: '1m' })
    expect(oneHour.getAttribute('aria-selected')).toBe('true')
    await user.click(oneMinute)
    expect(oneMinute.getAttribute('aria-selected')).toBe('true')

    for (const name of ['EMA200', 'VWAP', '计划线', '实际线']) {
      const toggle = within(chart).getByRole('button', { name })
      expect(toggle.getAttribute('aria-pressed')).toBe('false')
      await user.click(toggle)
      expect(toggle.getAttribute('aria-pressed')).toBe('true')
    }
    expect(within(chart).getByRole('button', { name: 'EMA20' }).getAttribute('aria-pressed')).toBe('true')
    expect(within(chart).getByRole('button', { name: 'EMA50' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('过期状态醒目显示且卸载时清理 Lightweight Charts 实例', async () => {
    apiMock.current.mockResolvedValue({ ...analysis, dataQuality: { ...quality, fresh: false, stale: true, status: 'STALE' } })
    const { unmount } = render(<MarketAnalysisView/>)
    expect(await screen.findByText(/分析或公开行情已过期/)).toBeTruthy()
    await waitFor(() => expect(chartMock.createChart).toHaveBeenCalled())
    unmount()
    expect(chartMock.chart.remove).toHaveBeenCalled()
  })

  it('未收盘行情新鲜但已确认 K 线过期时图表明确告警', async () => {
    apiMock.snapshot.mockResolvedValue({
      ...snapshot,
      candleStatus: {
        ...snapshot.candleStatus,
        '1H': { ...candleStatus, stale: false, confirmedStale: true, lastConfirmedAt: now - 8 * 3_600_000 },
      },
    })
    render(<MarketAnalysisView/>)

    const warning = await screen.findByText(/1H 已收盘 K 线已过期/)
    expect(warning.textContent).toContain('未收盘更新不代表指标数据新鲜')
  })

  it('摘要卡展示 4H/1H 并只通过 onOpen 打开完整页面', async () => {
    const user = userEvent.setup()
    const onOpen = vi.fn()
    render(<MarketSummaryCard analysis={analysis} onOpen={onOpen}/>)
    expect(screen.getByText('4H 环境')).toBeTruthy()
    expect(screen.getByText('1H 环境')).toBeTruthy()
    expect(screen.getByText('一致度 75%')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '打开完整市场分析' }))
    expect(onOpen).toHaveBeenCalledOnce()
    expect(screen.getByText(/不修改计划/)).toBeTruthy()
  })

  it('WS 只更新 current，persisted 历史重拉只接受最新请求结果', async () => {
    const originalWebSocket = globalThis.WebSocket
    let socket: {
      onmessage: ((event: MessageEvent) => void) | null
      close: () => void
    } | null = null
    class CapturingWebSocket {
      onopen: ((event: Event) => void) | null = null
      onclose: ((event: CloseEvent) => void) | null = null
      onerror: ((event: Event) => void) | null = null
      onmessage: ((event: MessageEvent) => void) | null = null
      constructor(_url: string | URL) { socket = this }
      close() {}
    }
    Object.defineProperty(globalThis, 'WebSocket', { configurable: true, writable: true, value: CapturingWebSocket })
    const view = render(<MarketAnalysisView/>)
    try {
      await screen.findByText('4H 顺势偏空')
      await waitFor(() => expect(historyItemsMock.mock.calls.at(-1)?.[0]).toHaveLength(2))

      const liveOnly = { ...analysis, summary: 'WS 临时观察，不是持久历史', asOf: now + 60_000 }
      act(() => socket?.onmessage?.(new MessageEvent('message', {
        data: JSON.stringify({ type: 'marketAnalysis', analysis: liveOnly, persisted: false }),
      })))
      expect(await screen.findByText('WS 临时观察，不是持久历史')).toBeTruthy()
      expect(apiMock.history).toHaveBeenCalledTimes(1)
      expect(historyItemsMock.mock.calls.at(-1)?.[0]).toHaveLength(2)

      const slowOldHistory = [{ ...analysis, summary: '慢速旧历史', asOf: now + 120_000 }]
      const fastNewHistory = [{ ...analysis, summary: '快速新历史', asOf: now + 180_000 }]
      let resolveSlow!: (items: MarketAnalysis[]) => void
      let resolveFast!: (items: MarketAnalysis[]) => void
      const slowRequest = new Promise<MarketAnalysis[]>(resolve => { resolveSlow = resolve })
      const fastRequest = new Promise<MarketAnalysis[]>(resolve => { resolveFast = resolve })
      apiMock.history.mockImplementationOnce(() => slowRequest).mockImplementationOnce(() => fastRequest)

      act(() => socket?.onmessage?.(new MessageEvent('message', {
        data: JSON.stringify({ type: 'marketAnalysis', analysis: slowOldHistory[0], persisted: true }),
      })))
      act(() => socket?.onmessage?.(new MessageEvent('message', {
        data: JSON.stringify({ type: 'marketAnalysis', analysis: fastNewHistory[0], persisted: true }),
      })))
      expect(apiMock.history).toHaveBeenCalledTimes(3)

      await act(async () => { resolveFast(fastNewHistory); await fastRequest })
      await waitFor(() => expect(historyItemsMock.mock.calls.at(-1)?.[0]).toEqual(fastNewHistory))

      await act(async () => { resolveSlow(slowOldHistory); await slowRequest })
      expect(historyItemsMock.mock.calls.at(-1)?.[0]).toEqual(fastNewHistory)
    } finally {
      view.unmount()
      Object.defineProperty(globalThis, 'WebSocket', { configurable: true, writable: true, value: originalWebSocket })
    }
  })

})
