export type MarketRegime = 'TREND' | 'RANGE' | 'TRANSITION' | 'STALE'
export type AdviceAction = 'LONG_CANDIDATE' | 'SHORT_CANDIDATE' | 'WATCH_LONG' | 'WATCH_SHORT' | 'WAIT'

export interface Candle { timestamp: number; open: number; high: number; low: number; close: number; volume: number; confirm?: boolean }
export type MarketTimeframe = '1m' | '15m' | '1H' | '4H'
export interface CandleTimeframeStatus {
  available: boolean
  stale: boolean
  lastAt: number | null
  lastConfirmedAt?: number | null
  confirmedStale?: boolean
  gapDetected: boolean
}

const CANDLE_DURATION_MS: Record<MarketTimeframe, number> = {
  '1m': 60_000,
  '15m': 15 * 60_000,
  '1H': 60 * 60_000,
  '4H': 4 * 60 * 60_000,
}

/** Match the backend rule: a confirmed candle is stale after two further
 * timeframe durations have elapsed beyond that candle's close. Explicit
 * backend state wins; the timestamp fallback also covers local WS merges. */
export const confirmedCandleIsStale = (
  status: CandleTimeframeStatus | null | undefined,
  timeframe: MarketTimeframe,
  now = Date.now(),
) => {
  if (!status || status.available === false) return false
  if (status.confirmedStale != null) return status.confirmedStale
  if (status.lastConfirmedAt == null) return true
  const duration = CANDLE_DURATION_MS[timeframe]
  return now - (status.lastConfirmedAt + duration) > 2 * duration
}
export interface MarketSnapshot {
  instrument: string; price: number | null; updatedAt: number | null; stale: boolean; connectionStatus: string;
  fundingRate: number | null; fundingTime: number | null; openInterest: number | null; openInterestTime: number | null;
  candles1m: Candle[]; candles15m: Candle[]; candles1h: Candle[]; candles4h: Candle[]
  markPrice: number | null; markPriceTime: number | null
  candleStatus: Record<MarketTimeframe, CandleTimeframeStatus>
}
export interface Contribution { name: string; score: number; value: number | null; explanation: string }
export interface DataQuality { fresh: boolean; lastCandleAt: number | null; fundingAvailable: boolean; openInterestAvailable: boolean; warnings: string[] }
export interface SignalAdvice {
  id?: string; instrument: string; strategy: string; candleCloseAt: number | null; action: AdviceAction; directionScore: number;
  technicalScore: number; newsScore: number;
  confidence: number; contributions: Contribution[]; explanation: string; triggerPrice: number | null;
  invalidation: string; stopLoss: number | null; targets: number[]; riskReward: number[];
  regime: MarketRegime; dataQuality: DataQuality; configVersion: string; createdAt?: number
}
export interface RiskEstimate {
  equity: number | null; riskPercent: number | null; leverage: number | null;
  entryPrice: number | null; stopLoss: number | null; stopDistance: number | null;
  referenceNotional: number | null; quantityBtc: number | null; warnings: string[]
}
export interface AdviceResponse { advice: SignalAdvice; riskEstimate: RiskEstimate }
export interface Settings { equity: number | null; riskPercent: number | null; leverage: number | null; notificationsEnabled: boolean; feeBps?: number; slippageBps?: number; customParameters: Record<string, number> }
export interface BacktestParameters { strategy: 'trend'|'range'|'combined'; years: number; feeBps: number; slippageBps: number }
export interface BacktestSeriesPoint { timestamp: number; value: number }
export interface BacktestBreakdownRow { label: string; trades: number | null; netReturn: number | null; maxDrawdown: number | null; sharpe: number | null; winRate: number | null; passed?: boolean | null }
export interface BacktestHistoryCoverage { requestedStart:number|null; requestedEnd:number|null; actualStart1H:number|null; actualEnd1H:number|null; actualStart4H:number|null; actualEnd4H:number|null; rows1H:number|null; rows4H:number|null; durationCoverage:number|null; complete:boolean }
export interface BacktestResult { status: string; reason?: string; strategy: string; netReturn: number | null; maxDrawdown: number | null; sharpe: number | null; sortino: number | null; calmar: number | null; profitFactor: number | null; winRate: number | null; trades: number | null; exposure: number | null; maxConsecutiveLosses: number | null; averageBarsHeld: number | null; benchmarks: Record<string,number|null>; holdingRule: string; limitations: string[]; validation: string; historyCoverage?:BacktestHistoryCoverage; equityCurve?: BacktestSeriesPoint[]; drawdownCurve?: BacktestSeriesPoint[]; rollingWindows?: BacktestBreakdownRow[]; byYear?: BacktestBreakdownRow[]; byRegime?: BacktestBreakdownRow[]; aggregateOos?: BacktestBreakdownRow & { profitableWindowRatio:number|null; windows:number|null }; lockedHoldout?: BacktestBreakdownRow & { start:number|null; end:number|null }; thresholdPass?: boolean|null; diagnosticsComplete?: boolean|null; validationCriteria?: Record<string,boolean>; stress?: Record<string,{netReturn:number|null;maxDrawdown:number|null;profitFactor:number|null}>; updatedAt?: number }
export interface BacktestJob { id: string; status: string; progress: number; message: string; result: BacktestResult | null; parameters?: BacktestParameters|null; createdAt?: number|null; updatedAt?: number|null; startedAt?: number|null; finishedAt?: number|null }

export interface TechnicalMetric { label: string; value: string; tone?: 'positive' | 'negative' | 'neutral' }
export interface TechnicalGroup {
  key: string; label: string; score: number | null; cap: number | null; direction: string; summary: string; metrics: TechnicalMetric[]
}
export interface TechnicalSummary {
  asOf: number | null; status: 'fresh' | 'partial' | 'unavailable'; groups: TechnicalGroup[];
  technicalScore: number | null; vwap: number | null; weeklyVwap: number | null; mfi: number | null;
  poc: number | null; vah: number | null; val: number | null; supports: number[]; resistances: number[];
  momentum: string; volatilityPhase: string; atrPercentile: number | null; bollingerWidthPercentile: number | null;
  positionScale: number | null; riskReasons: string[]; warnings: string[]
}

export interface NewsItem {
  id: string; title: string; url: string; source: string; sources: string[]; publishedAt: number | null; latestPublishedAt: number | null; summary: string;
  category: string; importance: number; importanceLabel: string; sentiment: -1 | 0 | 1;
  sentimentLabel: string; directionConfidence: number | null; relevance: number; reason: string; importanceScore: number | null;
  effectiveImpact: number | null; sourceCount: number | null; ageHours: number | null; halfLifeHours: number | null;
  timeDecay: number | null; weightBreakdown: Record<string,number>; weightFormula: string
}
export interface NewsSourceStatus { source: string; ok: boolean; itemCount: number; observedAt: number | null; error: string | null }
export interface NewsAnalysis {
  asOf: number | null; windowHours: number; score: number; articleCount: number;
  status: 'fresh' | 'partial' | 'unavailable'; sourceCoverage: number; sourceStatus: NewsSourceStatus[];
  rawImpact: number | null; coverageAdjustedImpact: number | null; reason: string; warnings: string[]
}
export interface NewsResponse { items: NewsItem[]; analysis: NewsAnalysis }
