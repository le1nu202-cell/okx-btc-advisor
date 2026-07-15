import { DEFAULT_PLAN } from '../src/workbench-types'
import type {
  ActualFill,
  ExecutionRisk,
  ExecutionSummary,
  LiquidationEstimate,
  RiskCalculation,
  TradePlanDraft,
  TradePlanRecord,
} from '../src/workbench-types'
import type { Candle, MarketSnapshot } from '../src/types'

export const shortPlan: TradePlanDraft = {
  ...DEFAULT_PLAN,
  direction: 'SHORT',
  initialEntryPrice: 100_000,
  addPrice: 101_000,
  stopPrice: 102_000,
  takeProfitPrice: 99_000,
}
export const plannedRisk: RiskCalculation = {
  valid: true,
  errors: [],
  warnings: [],
  requestedInitialMargin: 3.2,
  initialMargin: 3.2,
  initialMarginPercent: 4,
  affordableInitialMargin: 80,
  maxInitialMarginByLoss: 1,
  initialNotional: 211.2,
  initialQuantityBtc: 0.002112,
  addMargin: 6.4,
  addNotional: 422.4,
  addQuantityBtc: 0.004182178217821782,
  totalMargin: 9.6,
  totalNotional: 633.6,
  totalQuantityBtc: 0.006294178217821782,
  remainingQuantityAfterPlannedReduce: 0.002112,
  initialFillPrice: 100_000,
  addFillPrice: 101_000,
  averageEntryPrice: 100_664.56494325347,
  grossBreakevenPrice: 100_664.56494325347,
  feeBreakevenPrice: 100_765.27986568898,
  fullCostBreakevenPrice: 100_714.92240448673,
  allInBreakevenPrice: 100_714.92240448673,
  feeAdjustedBreakevenPrice: 100_714.92240448673,
  reduceZonePrice: 100_714.92240448673,
  openingFee: 0.04224,
  addFee: 0.08448,
  estimatedReduceFee: 0.210659,
  estimatedCloseFee: 0.106676,
  totalFeesAtTakeProfit: 0.443396,
  totalFeesAtStop: 0.450098,
  estimatedSlippageAtTakeProfit: 0.311562,
  estimatedSlippageAtStop: 0.321003,
  grossProfitAtTakeProfit: 10.477,
  netProfitAtTakeProfit: 9.722,
  initialOnlyNetProfitAtTakeProfit: 1.9,
  grossLossAtStop: 8.405,
  netLossAtStop: 9.176,
  maxLossEquityPercent: 11.47,
  riskRewardRatio: 1.06,
  stopDistancePercent: 1.34,
  addToStopSpacePercent: 0.99,
  adverseMoveLosses: [],
  riskLevel: '极高',
  lossLimitExceeded: true,
  liquidationWarning: false,
  assumptions: [],
}

export const liquidationEstimate: LiquidationEstimate = {
  status: 'AVAILABLE',
  estimatedLiquidationPrice: 95_000,
  referenceMarkPrice: 100_880,
  referenceMarkTime: 1_800_003_600_000,
  referenceMarkStatus: 'AVAILABLE',
  distanceStatus: 'AVAILABLE',
  distancePercent: 5.83,
  distanceRisk: '充足',
  hardStopSequence: 'STOP_FIRST',
  hardStopBufferPercent: 2.95,
  quantityBtc: plannedRisk.totalQuantityBtc,
  averageEntryPrice: plannedRisk.averageEntryPrice,
  supportingEquityUsdt: 80,
  maintenanceMarginRate: 0.004,
  maintenanceMarginFixedUsdt: 0,
  liquidationFeeRate: 0.001,
  tier: 1,
  contracts: 62.94,
  parameterSource: 'OKX_PUBLIC',
  parametersUpdatedAt: 1_800_000_000_000,
  assumptions: ['测试假设'],
  warnings: [],
  errors: [],
}

export const executionRisk: ExecutionRisk = {
  quantityBtc: 0.0059,
  averageEntryPrice: 100_777.123456,
  grossBreakevenPrice: 100_777.123456,
  feeBreakevenPrice: 100_860.2,
  fullCostBreakevenPrice: 100_910.987654,
  remainingNetLossAtStop: 6.2,
  netLossAtStop: 7.4,
  maxLossEquityPercent: 9.25,
  totalNetPnlIfStopped: -7.4,
}

export const executionSummary: ExecutionSummary = {
  openedQuantityBtc: 0.0062,
  remainingQuantityBtc: 0.0059,
  averageEntryPrice: executionRisk.averageEntryPrice,
  remainingEntryCost: 594.585,
  realizedGrossPnl: 0.2,
  allocatedEntryFees: 0.03,
  exitFees: 0.02,
  fees: 0.05,
  slippageUsdt: 0.01,
  realizedNetPnl: 0.14,
  mfeMaeSupported: false,
}

export const candles1h: Candle[] = [
  { timestamp: 1_800_000_000_000, open: 100_000, high: 100_400, low: 99_800, close: 100_200, volume: 10, confirm: true },
  { timestamp: 1_800_003_600_000, open: 100_200, high: 101_200, low: 100_100, close: 100_900, volume: 12, confirm: true },
]

export const candles1m: Candle[] = [
  { timestamp: 1_800_003_480_000, open: 100_700, high: 100_850, low: 100_650, close: 100_800, volume: 2, confirm: true },
  { timestamp: 1_800_003_540_000, open: 100_800, high: 101_000, low: 100_750, close: 100_900, volume: 3, confirm: true },
]

export const candles15m: Candle[] = [
  { timestamp: 1_800_001_800_000, open: 100_300, high: 100_800, low: 100_200, close: 100_700, volume: 6, confirm: true },
  { timestamp: 1_800_002_700_000, open: 100_700, high: 101_000, low: 100_600, close: 100_900, volume: 8, confirm: true },
]

export const candles4h: Candle[] = [
  { timestamp: 1_799_985_600_000, open: 99_600, high: 100_600, low: 99_400, close: 100_200, volume: 40, confirm: true },
  { timestamp: 1_800_000_000_000, open: 100_200, high: 101_300, low: 100_000, close: 100_900, volume: 48, confirm: true },
]

export const snapshot: MarketSnapshot = {
  instrument: 'BTC-USDT-SWAP',
  price: 100_900,
  markPrice: 100_880,
  markPriceTime: Date.now(),
  updatedAt: Date.now(),
  stale: false,
  connectionStatus: 'connected',
  fundingRate: null,
  fundingTime: null,
  openInterest: null,
  openInterestTime: null,
  candles1m,
  candles15m,
  candles1h,
  candles4h,
  candleStatus: {
    '1m': { available: true, stale: false, lastAt: candles1m.at(-1)!.timestamp, lastConfirmedAt: candles1m.at(-1)!.timestamp, confirmedStale: false, gapDetected: false },
    '15m': { available: true, stale: false, lastAt: candles15m.at(-1)!.timestamp, lastConfirmedAt: candles15m.at(-1)!.timestamp, confirmedStale: false, gapDetected: false },
    '1H': { available: true, stale: false, lastAt: candles1h.at(-1)!.timestamp, lastConfirmedAt: candles1h.at(-1)!.timestamp, confirmedStale: false, gapDetected: false },
    '4H': { available: true, stale: false, lastAt: candles4h.at(-1)!.timestamp, lastConfirmedAt: candles4h.at(-1)!.timestamp, confirmedStale: false, gapDetected: false },
  },
}

export const actualFill = (price: number, quantityBtc: number, confirmedAt: number): ActualFill =>
  ({ price, quantityBtc, confirmedAt } as ActualFill)

export function planRecord(overrides: Partial<TradePlanRecord> = {}): TradePlanRecord {
  return {
    id: null,
    state: 'IDLE',
    plan: null,
    risk: null,
    actualFills: {},
    activeReminder: null,
    ...overrides,
  }
}
