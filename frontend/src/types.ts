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
  confidence: number; contributions: Contribution[]; explanation: string; triggerPrice: number | null;
  invalidation: string; stopLoss: number | null; targets: number[]; riskReward: number[];
  regime: MarketRegime; dataQuality: DataQuality; configVersion: string; createdAt?: number
}
export interface Settings { equity: number | null; riskPercent: number | null; leverage: number | null; notificationsEnabled: boolean; feeBps?: number; slippageBps?: number; customParameters: Record<string, number> }
export interface BacktestResult { status: string; strategy: string; netReturn: number | null; maxDrawdown: number | null; sharpe: number | null; profitFactor: number | null; winRate: number | null; trades: number | null; validation: string; updatedAt?: number }
