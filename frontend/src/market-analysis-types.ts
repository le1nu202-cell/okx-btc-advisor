import type { MarketSnapshot, MarketTimeframe } from './types'

export const ANALYSIS_TIMEFRAMES = ['1m', '15m', '1H', '4H'] as const satisfies readonly MarketTimeframe[]
export type AnalysisTimeframe = typeof ANALYSIS_TIMEFRAMES[number]

export interface AnalysisDataQuality {
  status: string
  fresh: boolean
  stale: boolean
  warnings: string[]
  missingTimeframes: AnalysisTimeframe[]
  gapTimeframes: AnalysisTimeframe[]
}

export interface IndicatorContribution {
  key: string
  name: string
  score: number | null
  maxScore: number | null
  value: number | null
  valueText: string
  explanation: string
  available: boolean
}

export interface IndicatorValue {
  key: string
  label: string
  value: number | null
  valueText: string
  status: string
}

export interface TimeframeAnalysis {
  timeframe: AnalysisTimeframe
  status: string
  bias: string
  actionContext: string
  regime: string
  structure: string
  volatilityState: string
  score: number | null
  trendStrength: number | null
  confidence: number | null
  summary: string
  primaryReason: string
  asOf: number | null
  dataQuality: AnalysisDataQuality
  indicators: IndicatorValue[]
  contributions: IndicatorContribution[]
}

export interface KeyLevel {
  id: string
  price: number
  kind: string
  label: string
  strength: number | null
  strengthLabel: string
  source: string
  sources: string[]
  timeframe: AnalysisTimeframe | null
  touches: number | null
  distanceAtr: number | null
}

export interface AnalysisSeriesPoint {
  timestamp: number
  value: number
}

export interface TimeframeChartSeries {
  ema20: AnalysisSeriesPoint[]
  ema50: AnalysisSeriesPoint[]
  ema200: AnalysisSeriesPoint[]
  vwap: AnalysisSeriesPoint[]
}

export type MarketAnalysisChartSeries = Record<AnalysisTimeframe, TimeframeChartSeries>

export interface MarketAnalysis {
  overallBias: string
  actionContext: string
  compositeScore: number | null
  trendStrength: number | null
  alignmentScore: number | null
  confidence: number | null
  summary: string
  primaryReason: string
  invalidationLevel: number | null
  nearestSupport: number | null
  nearestResistance: number | null
  timeframeAnalyses: TimeframeAnalysis[]
  keyLevels: KeyLevel[]
  supportingReasons: string[]
  conflictingReasons: string[]
  riskWarnings: string[]
  dataQuality: AnalysisDataQuality
  asOf: number | null
  modelVersion: string
  chartSeries: MarketAnalysisChartSeries
}

export interface ValidationMetric {
  key: string
  label: string
  value: number | null
  valueText: string
  passed: boolean | null
}

export interface MarketAnalysisValidation {
  status: string
  sufficient: boolean
  passed: boolean | null
  sampleSize: number | null
  minimumSampleSize: number | null
  summary: string
  warnings: string[]
  metrics: ValidationMetric[]
  asOf: number | null
  modelVersion: string
}

export interface ChartReferenceLine {
  id: string
  label: string
  price: number
  group: 'planned' | 'actual'
}

export interface MarketPlanOverlay {
  planId: string | null
  planned: ChartReferenceLine[]
  actual: ChartReferenceLine[]
}

export interface MarketAnalysisLoad {
  analysis: MarketAnalysis
  history: MarketAnalysis[]
  validation: MarketAnalysisValidation
  snapshot: MarketSnapshot
  planOverlay: MarketPlanOverlay | null
}

export const emptyChartSeries = (): MarketAnalysisChartSeries => ({
  '1m': { ema20: [], ema50: [], ema200: [], vwap: [] },
  '15m': { ema20: [], ema50: [], ema200: [], vwap: [] },
  '1H': { ema20: [], ema50: [], ema200: [], vwap: [] },
  '4H': { ema20: [], ema50: [], ema200: [], vwap: [] },
})

export const timeframeAnalysis = (analysis: MarketAnalysis | null | undefined, timeframe: AnalysisTimeframe) =>
  analysis?.timeframeAnalyses.find(row => row.timeframe === timeframe) ?? null

export const analysisIsStale = (analysis: MarketAnalysis | null | undefined) => !analysis
  || analysis.dataQuality.stale
  || !analysis.dataQuality.fresh
  || /STALE|UNAVAILABLE|INVALID|GAP|INSUFFICIENT/i.test(analysis.dataQuality.status)

export const biasLabel = (value: string) => {
  const normalized = value.toUpperCase()
  if (normalized.includes('BULL') || normalized.includes('LONG') || normalized === 'UP') return '偏多'
  if (normalized.includes('BEAR') || normalized.includes('SHORT') || normalized === 'DOWN') return '偏空'
  if (normalized.includes('NEUTRAL') || normalized.includes('MIXED') || normalized.includes('RANGE')) return '中性'
  return value || '数据不足'
}

export const actionContextLabel = (value: string) => {
  const normalized = value.toUpperCase()
  if (normalized.includes('NO_CHASE') || normalized.includes('NO CHASE')) return '禁止追价'
  if (normalized.includes('WAIT') || normalized.includes('CAUTION')) return '等待确认'
  if (normalized.includes('CLEAR')) return '未触发追价限制'
  if (normalized.includes('LONG')) return '偏多环境'
  if (normalized.includes('SHORT')) return '偏空环境'
  return value || '数据不足'
}

export const regimeLabel = (value: string) => {
  const normalized = value.toUpperCase()
  if (normalized.includes('TREND')) return '趋势'
  if (normalized.includes('RANGE')) return '震荡'
  if (normalized.includes('TRANSITION')) return '过渡'
  if (normalized.includes('STALE')) return '过期'
  return value || '数据不足'
}
