import { api as existingMarketApi } from './api'
import { workbenchApi } from './workbench-api'
import type { MarketSnapshot } from './types'
import type { TradePlanRecord } from './workbench-types'
import {
  ANALYSIS_TIMEFRAMES,
  emptyChartSeries,
  type AnalysisDataQuality,
  type AnalysisSeriesPoint,
  type AnalysisTimeframe,
  type ChartReferenceLine,
  type IndicatorContribution,
  type IndicatorValue,
  type KeyLevel,
  type MarketAnalysis,
  type MarketAnalysisValidation,
  type MarketPlanOverlay,
  type TimeframeAnalysis,
  type TimeframeChartSeries,
  type ValidationMetric,
} from './market-analysis-types'

const record = (value: unknown): Record<string, unknown> => value != null && typeof value === 'object' && !Array.isArray(value)
  ? value as Record<string, unknown> : {}
const body = (value: unknown): unknown => record(value).data ?? value
const finite = (value: unknown): number | null => value === '' || value == null || !Number.isFinite(Number(value)) ? null : Number(value)
const epoch = (value: unknown): number | null => {
  const numeric = finite(value)
  if (numeric != null) return numeric < 1_000_000_000_000 ? numeric * 1_000 : numeric
  const parsed = Date.parse(String(value))
  return Number.isFinite(parsed) ? parsed : null
}
const texts = (value: unknown): string[] => Array.isArray(value) ? value.map(row => String(row)).filter(Boolean) : []
const timeframe = (value: unknown, fallback: AnalysisTimeframe = '1H'): AnalysisTimeframe =>
  ANALYSIS_TIMEFRAMES.includes(String(value) as AnalysisTimeframe) ? String(value) as AnalysisTimeframe : fallback
const boolean = (value: unknown, fallback = false) => value == null ? fallback : Boolean(value)

const errorMessage = async (response: Response) => {
  const value: unknown = await response.json().catch(() => null)
  const detail = record(value).detail
  return typeof detail === 'string' ? detail : `${response.status} ${response.statusText}`
}

const get = async (path: string): Promise<unknown> => {
  const response = await fetch(path)
  if (!response.ok) throw new Error(await errorMessage(response))
  return body(await response.json())
}

const normalizeQuality = (value: unknown, fallbackStatus = 'UNKNOWN'): AnalysisDataQuality => {
  const raw = record(value)
  const status = String(raw.status ?? raw.qualityCode ?? fallbackStatus)
  const stale = boolean(raw.stale, /STALE|UNAVAILABLE|INVALID|GAP|INSUFFICIENT/i.test(`${status} ${raw.qualityCode ?? ''}`)
    || (raw.connectionStatus != null && !/^connected$/i.test(String(raw.connectionStatus))))
  const fresh = raw.fresh == null ? !stale && !/UNAVAILABLE|INVALID|GAP|INSUFFICIENT/i.test(status) : Boolean(raw.fresh)
  const parseFrames = (candidate: unknown) => texts(candidate).map(row => timeframe(row)).filter((row, index, rows) => rows.indexOf(row) === index)
  return {
    status,
    fresh,
    stale,
    warnings: texts(raw.warnings ?? raw.reasons ?? raw.details),
    missingTimeframes: parseFrames(raw.missingTimeframes ?? raw.missing),
    gapTimeframes: parseFrames(raw.gapTimeframes ?? raw.gaps),
  }
}

const normalizeContribution = (value: unknown, index: number): IndicatorContribution => {
  const raw = record(value)
  const score = finite(raw.score ?? raw.points)
  const available = raw.available == null ? score != null && !/UNAVAILABLE|MISSING/i.test(String(raw.status ?? '')) : Boolean(raw.available)
  return {
    key: String(raw.key ?? raw.id ?? `contribution-${index}`),
    name: String(raw.label ?? raw.name ?? '未命名指标'),
    score,
    maxScore: finite(raw.maxScore ?? raw.maxPoints ?? raw.weight),
    value: finite(raw.value),
    valueText: String(raw.valueText ?? raw.displayValue ?? ''),
    explanation: String(raw.explanation ?? raw.reason ?? ''),
    available,
  }
}

const normalizeIndicator = (value: unknown, index: number): IndicatorValue => {
  const raw = record(value)
  return {
    key: String(raw.key ?? raw.id ?? `indicator-${index}`),
    label: String(raw.label ?? raw.name ?? '指标'),
    value: finite(raw.value),
    valueText: String(raw.valueText ?? raw.displayValue ?? ''),
    status: String(raw.status ?? 'AVAILABLE'),
  }
}

const normalizeTimeframe = (value: unknown, fallback: AnalysisTimeframe): TimeframeAnalysis => {
  const raw = record(value)
  const contributions = Array.isArray(raw.contributions) ? raw.contributions.map(normalizeContribution) : []
  const rawIndicators = raw.indicators
  const indicators = Array.isArray(rawIndicators)
    ? rawIndicators.map(normalizeIndicator)
    : Object.entries(record(rawIndicators)).map(([key, candidate], index) => normalizeIndicator({ key, label: key, ...(record(candidate)), value: record(candidate).value ?? candidate }, index))
  const status = String(raw.status ?? record(raw.dataQuality).status ?? 'UNKNOWN')
  const rawStructure = record(raw.structure)
  const rawVolatility = record(raw.volatility)
  return {
    timeframe: timeframe(raw.timeframe ?? raw.period, fallback),
    status,
    bias: String(raw.bias ?? raw.direction ?? raw.overallBias ?? 'UNAVAILABLE'),
    actionContext: String(record(raw.actionContext).action ?? raw.actionContext ?? raw.noChaseStatus ?? ''),
    regime: String(raw.regime ?? raw.marketRegime ?? 'UNKNOWN'),
    structure: String(rawStructure.label ?? rawStructure.direction ?? raw.structure ?? 'UNKNOWN'),
    volatilityState: String(raw.volatilityState ?? rawVolatility.state ?? 'UNKNOWN'),
    score: finite(raw.score ?? raw.compositeScore ?? raw.directionScore),
    trendStrength: finite(record(raw.trendStrength).score ?? raw.trendStrength),
    confidence: finite(raw.confidence),
    summary: String(raw.summary ?? raw.explanation ?? ''),
    primaryReason: String(raw.primaryReason ?? raw.reason ?? ''),
    asOf: epoch(raw.asOf ?? raw.lastClosedAt),
    dataQuality: normalizeQuality(raw.dataQuality, status),
    indicators,
    contributions,
  }
}

const normalizeKeyLevel = (value: unknown, index: number): KeyLevel | null => {
  const raw = record(value)
  const price = finite(raw.price ?? raw.center ?? raw.level)
  if (price == null || price <= 0) return null
  const rawTimeframe = raw.timeframe == null ? null : timeframe(raw.timeframe)
  return {
    id: String(raw.id ?? `level-${index}-${price}`),
    price,
    kind: String(raw.kind ?? raw.type ?? raw.side ?? 'LEVEL'),
    label: String(raw.label ?? raw.name ?? '关键位'),
    strength: finite(raw.strength ?? raw.weight),
    strengthLabel: String(raw.strengthLabel ?? ''),
    source: String(raw.source ?? ''),
    sources: texts(raw.sources),
    timeframe: rawTimeframe,
    touches: finite(raw.touches ?? raw.touchCount),
    distanceAtr: finite(raw.distanceAtr),
  }
}

const taggedLevel = (value: unknown, kind: 'SUPPORT' | 'RESISTANCE', index: number) => {
  const raw = record(value)
  return {
    ...raw,
    id: raw.id ?? `compact-${kind.toLowerCase()}-${index}`,
    price: raw.price ?? value,
    kind: raw.kind ?? kind,
    label: raw.label ?? (kind === 'SUPPORT' ? '历史支撑位' : '历史阻力位'),
  }
}

const levelPrice = (value: unknown) => finite(record(value).price ?? value)

const normalizePoint = (value: unknown): AnalysisSeriesPoint | null => {
  const raw: Record<string, unknown> = Array.isArray(value) ? { timestamp: value[0], value: value[1] } : record(value)
  const timestamp = epoch(raw.timestamp ?? raw.time ?? raw.ts)
  const pointValue = finite(raw.value ?? raw.price ?? raw.close)
  return timestamp == null || pointValue == null ? null : { timestamp, value: pointValue }
}

const normalizePoints = (value: unknown): AnalysisSeriesPoint[] => {
  const rows = (Array.isArray(value) ? value : []).map(normalizePoint).filter((row): row is AnalysisSeriesPoint => row != null)
  const byTime = new Map(rows.map(row => [row.timestamp, row]))
  return [...byTime.values()].sort((left, right) => left.timestamp - right.timestamp)
}

const normalizeTimeframeSeries = (value: unknown): TimeframeChartSeries => {
  if (Array.isArray(value)) {
    const rows = value.map(record)
    const read = (key: keyof TimeframeChartSeries) => normalizePoints(rows.map(row => ({ timestamp: row.timestamp ?? row.time ?? row.ts, value: row[key] })))
    return { ema20: read('ema20'), ema50: read('ema50'), ema200: read('ema200'), vwap: read('vwap') }
  }
  const raw = record(value)
  return {
    ema20: normalizePoints(raw.ema20 ?? raw.EMA20),
    ema50: normalizePoints(raw.ema50 ?? raw.EMA50),
    ema200: normalizePoints(raw.ema200 ?? raw.EMA200),
    vwap: normalizePoints(raw.vwap ?? raw.VWAP),
  }
}

export const normalizeMarketAnalysis = (value: unknown): MarketAnalysis => {
  const raw = record(body(value))
  const rawFrames = raw.timeframeAnalyses ?? raw.timeframes
  const frameMap = Array.isArray(rawFrames)
    ? new Map(rawFrames.map((row, index) => [timeframe(record(row).timeframe, ANALYSIS_TIMEFRAMES[index] ?? '1H'), row]))
    : new Map(Object.entries(record(rawFrames)).map(([key, row]) => [timeframe(key), { timeframe: key, ...record(row) }]))
  const frames = ANALYSIS_TIMEFRAMES.map(period => normalizeTimeframe(frameMap.get(period) ?? { timeframe: period, status: 'INSUFFICIENT' }, period))
  const rawChart = record(raw.chartSeries)
  const chartSeries = emptyChartSeries()
  for (const period of ANALYSIS_TIMEFRAMES) chartSeries[period] = normalizeTimeframeSeries(rawChart[period])
  const status = String(record(raw.dataQuality).status ?? raw.status ?? 'UNKNOWN')
  const rawAction = record(raw.actionContext)
  const rawAlignment = record(raw.alignment)
  const rawConfidenceDetails = record(raw.confidenceDetails)
  const rawLevels = record(raw.keyLevels ?? raw.levels)
  const rawSupports = Array.isArray(rawLevels.supports) ? rawLevels.supports : []
  const rawResistances = Array.isArray(rawLevels.resistances) ? rawLevels.resistances : []
  const keyLevelRows = Array.isArray(raw.keyLevels)
    ? raw.keyLevels
    : [
        ...rawSupports.map((row, index) => taggedLevel(row, 'SUPPORT', index)),
        ...rawResistances.map((row, index) => taggedLevel(row, 'RESISTANCE', index)),
      ]
  const normalizedKeyLevels = keyLevelRows.map(normalizeKeyLevel).filter((row): row is KeyLevel => row != null)
  const nearestSupport = levelPrice(raw.nearestSupport ?? rawLevels.nearestSupport)
    ?? normalizedKeyLevels.find(row => /SUPPORT/i.test(row.kind))?.price
    ?? null
  const nearestResistance = levelPrice(raw.nearestResistance ?? rawLevels.nearestResistance)
    ?? normalizedKeyLevels.find(row => /RESIST/i.test(row.kind))?.price
    ?? null
  const actionReasons = texts(rawAction.reasons)
  const topWarnings = texts(raw.riskWarnings)
  const qualityWarnings = texts(record(raw.dataQuality).warnings)
  return {
    overallBias: String(raw.overallBias ?? raw.bias ?? 'UNAVAILABLE'),
    actionContext: String(rawAction.action ?? raw.actionContext ?? raw.noChaseStatus ?? ''),
    compositeScore: finite(raw.compositeScore ?? raw.directionScore),
    trendStrength: finite(rawConfidenceDetails.trendStrength ?? raw.trendStrength),
    alignmentScore: finite(rawAlignment.consistency ?? raw.alignmentScore),
    confidence: finite(raw.confidence),
    summary: String(raw.summary ?? raw.directionLabel ?? ''),
    primaryReason: String(raw.primaryReason ?? actionReasons[0] ?? ''),
    invalidationLevel: finite(raw.invalidationLevel ?? rawLevels.invalidationLevel),
    nearestSupport,
    nearestResistance,
    timeframeAnalyses: frames,
    keyLevels: normalizedKeyLevels,
    supportingReasons: texts(raw.supportingReasons),
    conflictingReasons: texts(raw.conflictingReasons),
    riskWarnings: [...topWarnings, ...actionReasons, ...qualityWarnings].filter((row, index, rows) => rows.indexOf(row) === index),
    dataQuality: normalizeQuality(raw.dataQuality, status),
    asOf: epoch(raw.asOf ?? raw.decisionAt),
    modelVersion: String(raw.modelVersion ?? 'indicator-regime-v06'),
    chartSeries,
  }
}

const normalizeValidationMetric = (value: unknown, index: number): ValidationMetric => {
  const raw = record(value)
  return {
    key: String(raw.key ?? raw.id ?? `metric-${index}`),
    label: String(raw.label ?? raw.name ?? '验证指标'),
    value: finite(raw.value),
    valueText: String(raw.valueText ?? raw.displayValue ?? ''),
    passed: raw.passed == null ? null : Boolean(raw.passed),
  }
}

export const normalizeMarketAnalysisValidation = (value: unknown): MarketAnalysisValidation => {
  const raw = record(body(value))
  const status = String(raw.status ?? 'UNAVAILABLE')
  const sampleSize = finite(raw.sampleSize ?? raw.samples)
  const minimumSampleSize = finite(raw.minimumSampleSize ?? raw.requiredSamples)
  const sufficient = raw.sufficient == null
    ? !/INSUFFICIENT|UNAVAILABLE/i.test(status) && (sampleSize == null || minimumSampleSize == null || sampleSize >= minimumSampleSize)
    : Boolean(raw.sufficient)
  const rawMetrics = Array.isArray(raw.metrics)
    ? raw.metrics
    : Object.entries(record(raw.metrics)).map(([key, metric]) => {
      const rawMetric = record(metric)
      return { key, ...rawMetric, label: rawMetric.label ?? rawMetric.name ?? key, value: rawMetric.value ?? metric }
    })
  return {
    status,
    sufficient,
    passed: raw.passed == null ? null : Boolean(raw.passed),
    sampleSize,
    minimumSampleSize,
    summary: String(raw.summary ?? raw.message ?? ''),
    warnings: texts(raw.warnings),
    metrics: rawMetrics.map(normalizeValidationMetric),
    asOf: epoch(raw.asOf ?? raw.updatedAt),
    modelVersion: String(raw.modelVersion ?? 'indicator-regime-v06'),
  }
}

const line = (id: string, label: string, price: unknown, group: ChartReferenceLine['group']): ChartReferenceLine | null => {
  const numeric = finite(price)
  return numeric != null && numeric > 0 ? { id, label, price: numeric, group } : null
}

export const normalizeMarketPlanOverlay = (value: TradePlanRecord | null | undefined): MarketPlanOverlay | null => {
  if (!value?.plan) return null
  const plan = value.plan
  const risk = value.risk
  const executionRisk = value.executionRisk
  const planned = [
    line('planned-initial', '计划 · 初始价', plan.initialEntryPrice, 'planned'),
    line('planned-add', '计划 · 加仓价', plan.addPrice, 'planned'),
    line('planned-stop', '计划 · 硬止损', plan.stopPrice, 'planned'),
    line('planned-target', '计划 · 止盈价', plan.takeProfitPrice, 'planned'),
    line('planned-average', '计划 · 加权均价', risk?.averageEntryPrice, 'planned'),
    line('planned-breakeven', '计划 · 全成本保本价', risk?.fullCostBreakevenPrice ?? risk?.allInBreakevenPrice, 'planned'),
  ].filter((row): row is ChartReferenceLine => row != null)
  const actual = [
    line('actual-average', '实际 · 加权均价', executionRisk?.averageEntryPrice, 'actual'),
    line('actual-breakeven', '实际 · 全成本保本价', executionRisk?.fullCostBreakevenPrice, 'actual'),
  ].filter((row): row is ChartReferenceLine => row != null)
  return { planId: value.id, planned, actual }
}

const currentPlanOverlay = async (): Promise<MarketPlanOverlay | null> => {
  try {
    return normalizeMarketPlanOverlay(await workbenchApi.current())
  } catch (reason) {
    if (reason instanceof Error && /(^|\s)404(\s|$)|不存在|not found/i.test(reason.message)) return null
    throw reason
  }
}

export const marketAnalysisApi = {
  current: async () => normalizeMarketAnalysis(await get('/api/market-analysis/current')),
  history: async (limit = 120) => {
    const safeLimit = Math.max(1, Math.min(500, Math.floor(limit)))
    const response = await get(`/api/market-analysis/history?limit=${safeLimit}`)
    const rows = Array.isArray(response) ? response : Array.isArray(record(response).items) ? record(response).items as unknown[] : []
    return rows.map(normalizeMarketAnalysis)
  },
  validation: async () => normalizeMarketAnalysisValidation(await get('/api/market-analysis/validation')),
  snapshot: (): Promise<MarketSnapshot> => existingMarketApi.snapshot(),
  currentPlanOverlay,
}
