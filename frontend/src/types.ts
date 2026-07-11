export type MarketRegime = 'TREND' | 'RANGE' | 'TRANSITION' | 'STALE'
export type AdviceAction = 'LONG_CANDIDATE' | 'SHORT_CANDIDATE' | 'WATCH_LONG' | 'WATCH_SHORT' | 'WAIT'

export interface Candle { timestamp: number; open: number; high: number; low: number; close: number; volume: number; confirm?: boolean }
export interface MarketSnapshot {
  instrument: string; price: number | null; updatedAt: number | null; stale: boolean; connectionStatus: string;
  fundingRate: number | null; fundingTime: number | null; openInterest: number | null; openInterestTime: number | null;
  candles1h: Candle[]; candles4h: Candle[]
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
export interface Settings { equity: number | null; riskPercent: number | null; leverage: number | null; notificationsEnabled: boolean; feeBps?: number; slippageBps?: number; customParameters: Record<string, number> }
export interface BacktestResult { status: string; strategy: string; netReturn: number | null; maxDrawdown: number | null; sharpe: number | null; profitFactor: number | null; winRate: number | null; trades: number | null; validation: string; updatedAt?: number }

export interface TechnicalMetric { label: string; value: string; tone?: 'positive' | 'negative' | 'neutral' }
export interface TechnicalGroup {
  key: string; label: string; rating1h: string; rating4h: string; summary: string; metrics: TechnicalMetric[]
}
export interface TechnicalSummary {
  asOf: number | null; status: 'fresh' | 'partial' | 'unavailable'; groups: TechnicalGroup[];
  vwap: number | null; poc: number | null; vah: number | null; val: number | null;
  supports: number[]; resistances: number[]; momentum: string; volatilityPhase: string; warnings: string[]
}

export interface NewsItem {
  id: string; title: string; url: string; source: string; publishedAt: number | null; summary: string;
  category: string; importance: number; importanceLabel: string; sentiment: -1 | 0 | 1;
  sentimentLabel: string; relevance: number; reason: string
}
export interface NewsAnalysis {
  asOf: number | null; windowHours: number; score: number; articleCount: number;
  status: 'fresh' | 'partial' | 'unavailable'; warnings: string[]
}
export interface NewsResponse { items: NewsItem[]; analysis: NewsAnalysis }
