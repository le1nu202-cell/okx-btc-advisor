import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  marketAnalysisApi,
  normalizeMarketAnalysis,
  normalizeMarketAnalysisValidation,
  normalizeMarketPlanOverlay,
} from '../src/market-analysis-api'
import { api } from '../src/api'

const quality = { status: 'AVAILABLE', qualityCode: 'OK', details: [] }
const contribution = {
  key: 'emaAlignment', name: 'EMA 排列', weight: 20, score: -12, available: true,
  values: { ema20: 100_000, ema50: 101_000 }, explanation: '后端确定性指标贡献。',
}
const timeframe = (period: string) => ({
  timeframe: period,
  status: 'AVAILABLE',
  bias: period === '1m' ? 'NEUTRAL' : 'BEARISH',
  directionScore: -35,
  regime: 'TREND',
  trendStrength: { score: 68 },
  asOf: 1_900_000_000_000,
  dataQuality: quality,
  contributions: [contribution],
  indicators: { ema20: 100_000, ema50: 101_000, rsi: 42 },
})

const backendPayload = {
  modelVersion: 'indicator-regime-v06',
  decisionAt: 1_900_000_000_000,
  status: 'AVAILABLE',
  overallBias: 'BEARISH',
  directionLabel: '高周期偏空',
  directionScore: -42,
  alignment: { consistency: 75, status: 'MIXED' },
  confidence: 72,
  confidenceDetails: { trendStrength: 68 },
  actionContext: { action: 'NO_CHASE', noChase: true, reasons: ['15m 距离 EMA20 过远'] },
  timeframeAnalyses: {
    '4H': timeframe('4H'),
    '1H': timeframe('1H'),
    '15m': timeframe('15m'),
    '1m': timeframe('1m'),
  },
  keyLevels: {
    nearestSupport: { price: 98_000 },
    nearestResistance: { price: 101_500 },
    supports: [{ id: 's1', price: 98_000, kind: 'SUPPORT', strength: 80, sources: ['SWING_4H'] }],
    resistances: [{ id: 'r1', price: 101_500, kind: 'RESISTANCE', strength: 70, sources: ['VWAP'] }],
  },
  chartSeries: {
    '1H': {
      ema20: [{ timestamp: 1_899_999_000_000, value: 100_000 }],
      ema50: [[1_899_999_000_000, 101_000]],
      ema200: [],
      vwap: [{ time: 1_899_999_000, price: 100_200 }],
    },
  },
  dataQuality: { status: 'AVAILABLE', connectionStatus: 'connected', warnings: ['公共行情存在短时抖动'] },
}

const response = (value: unknown, ok = true, status = 200) => ({
  ok,
  status,
  statusText: ok ? 'OK' : 'ERROR',
  json: vi.fn().mockResolvedValue(value),
}) as unknown as Response

describe('market-analysis API adapter', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()))
  afterEach(() => vi.unstubAllGlobals())

  it('兼容后端嵌套契约并保持指标、关键位和行动语义为后端权威', () => {
    const value = normalizeMarketAnalysis(backendPayload)

    expect(value.overallBias).toBe('BEARISH')
    expect(value.actionContext).toBe('NO_CHASE')
    expect(value.compositeScore).toBe(-42)
    expect(value.trendStrength).toBe(68)
    expect(value.alignmentScore).toBe(75)
    expect(value.summary).toBe('高周期偏空')
    expect(value.asOf).toBe(1_900_000_000_000)
    expect(value.timeframeAnalyses.map(row => row.timeframe)).toEqual(['1m', '15m', '1H', '4H'])
    expect(value.timeframeAnalyses.find(row => row.timeframe === '4H')?.trendStrength).toBe(68)
    expect(value.timeframeAnalyses.find(row => row.timeframe === '4H')?.indicators.find(row => row.key === 'rsi')?.value).toBe(42)
    expect(value.nearestSupport).toBe(98_000)
    expect(value.nearestResistance).toBe(101_500)
    expect(value.keyLevels).toHaveLength(2)
    expect(value.riskWarnings).toEqual(['15m 距离 EMA20 过远', '公共行情存在短时抖动'])
    expect(value.chartSeries['1H'].ema20).toEqual([{ timestamp: 1_899_999_000_000, value: 100_000 }])
    expect(value.chartSeries['1H'].ema50).toEqual([{ timestamp: 1_899_999_000_000, value: 101_000 }])
    expect(value.chartSeries['1H'].vwap).toEqual([{ timestamp: 1_899_999_000_000, value: 100_200 }])
  })

  it('缺失周期会生成明确的不足占位，不拿其他周期替代', () => {
    const value = normalizeMarketAnalysis({
      ...backendPayload,
      timeframeAnalyses: { '4H': timeframe('4H') },
    })
    expect(value.timeframeAnalyses).toHaveLength(4)
    expect(value.timeframeAnalyses.find(row => row.timeframe === '1m')?.status).toBe('INSUFFICIENT')
    expect(value.timeframeAnalyses.find(row => row.timeframe === '4H')?.status).toBe('AVAILABLE')
  })

  it('正规化真实 compact history 契约，保留四周期与价格位', () => {
    const compact = normalizeMarketAnalysis({
      instrument: 'BTC-USDT-SWAP',
      modelVersion: 'indicator-regime-v06',
      decisionAt: 1_900_000_000_000,
      asOf: 1_900_000_000_000,
      status: 'AVAILABLE',
      overallBias: 'BEARISH',
      compositeScore: -38,
      trendStrength: 61,
      alignmentScore: 75,
      confidence: 70,
      summary: '持久化 compact 快照',
      primaryReason: '4H 与 1H 同向偏空',
      invalidationLevel: 102_500,
      nearestSupport: 98_000,
      nearestResistance: 101_500,
      alignment: { status: 'ALIGNED', hardConflict: false, consistency: 75 },
      actionContext: { action: 'WATCH_SHORT', noChase: false, reasons: [] },
      timeframes: {
        '4H': { status: 'AVAILABLE', qualityCode: 'OK', directionScore: -50, bias: 'BEARISH', regime: 'TREND', trendStrength: 72 },
        '1H': { status: 'AVAILABLE', qualityCode: 'OK', directionScore: -42, bias: 'BEARISH', regime: 'TREND', trendStrength: 65 },
        '15m': { status: 'AVAILABLE', qualityCode: 'OK', directionScore: -20, bias: 'NEUTRAL', regime: 'TRANSITION', trendStrength: 44 },
        '1m': { status: 'AVAILABLE', qualityCode: 'OK', directionScore: 5, bias: 'NEUTRAL', regime: 'RANGE', trendStrength: 25 },
      },
      levels: { supports: [98_000, 97_500], resistances: [101_500, 102_000] },
      dataQuality: { status: 'AVAILABLE', qualityCode: 'OK', warnings: [] },
      snapshotSource: 'LIVE_OBSERVED',
    })

    expect(compact.timeframeAnalyses.map(row => [row.timeframe, row.status, row.score, row.trendStrength])).toEqual([
      ['1m', 'AVAILABLE', 5, 25],
      ['15m', 'AVAILABLE', -20, 44],
      ['1H', 'AVAILABLE', -42, 65],
      ['4H', 'AVAILABLE', -50, 72],
    ])
    expect(compact.keyLevels.map(row => [row.kind, row.price])).toEqual([
      ['SUPPORT', 98_000], ['SUPPORT', 97_500], ['RESISTANCE', 101_500], ['RESISTANCE', 102_000],
    ])
    expect(compact.nearestSupport).toBe(98_000)
    expect(compact.nearestResistance).toBe(101_500)
    expect(compact.timeframeAnalyses.every(row => row.status !== 'INSUFFICIENT')).toBe(true)
  })

  it('请求固定端点并把历史 limit 限制在 1..500', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(response({ data: backendPayload }))
      .mockResolvedValueOnce(response({ items: [backendPayload] }))
      .mockResolvedValueOnce(response({
        status: 'INSUFFICIENT_DATA', sampleSize: 12, minimumSampleSize: 100,
        summary: '样本不足', metrics: { hitRate: { label: '命中率', value: 0.5 } },
      }))

    await expect(marketAnalysisApi.current()).resolves.toMatchObject({ overallBias: 'BEARISH' })
    await expect(marketAnalysisApi.history(999)).resolves.toHaveLength(1)
    await expect(marketAnalysisApi.validation()).resolves.toMatchObject({ sufficient: false, sampleSize: 12 })
    expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
      '/api/market-analysis/current',
      '/api/market-analysis/history?limit=500',
      '/api/market-analysis/validation',
    ])
  })

  it('验证对象支持 map 指标并明确标记样本不足', () => {
    const value = normalizeMarketAnalysisValidation({
      status: 'INSUFFICIENT_DATA', samples: 20, requiredSamples: 100,
      metrics: { accuracy: { name: '方向准确率', value: 0.55, displayValue: '55%' } },
    })
    expect(value.sufficient).toBe(false)
    expect(value.metrics).toEqual([expect.objectContaining({ key: 'accuracy', label: '方向准确率', valueText: '55%' })])
  })

  it('snapshot 解析后端已确认 K 线时间与独立过期标志', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(response({
      instrument: 'BTC-USDT-SWAP', price: 100_000, stale: false, connectionStatus: 'connected',
      candles1H: [{ timestamp: 1_900_000_000_000, open: 100_000, high: 100_100, low: 99_900, close: 100_010, volume: 1, confirm: true }],
      candleStatus: {
        '1H': { available: true, stale: false, lastAt: 1_900_003_600_000, lastConfirmedAt: 1_900_000_000_000, confirmedStale: true, gapDetected: false },
      },
    }))

    const value = await api.snapshot()
    expect(value.candleStatus['1H']).toMatchObject({
      available: true,
      stale: false,
      lastAt: 1_900_003_600_000,
      lastConfirmedAt: 1_900_000_000_000,
      confirmedStale: true,
    })
  })

  it('计划和实际价格线只映射持久化风险字段，不在前端重算', () => {
    const overlay = normalizeMarketPlanOverlay({
      id: 'plan-1',
      plan: { initialEntryPrice: 100_000, addPrice: 101_000, stopPrice: 102_000, takeProfitPrice: 98_000 },
      risk: { averageEntryPrice: 100_667, fullCostBreakevenPrice: 100_720 },
      executionRisk: { averageEntryPrice: 100_800, fullCostBreakevenPrice: 100_850 },
    } as never)
    expect(overlay?.planned.map(row => row.price)).toEqual([100_000, 101_000, 102_000, 98_000, 100_667, 100_720])
    expect(overlay?.actual.map(row => row.price)).toEqual([100_800, 100_850])
  })
})
