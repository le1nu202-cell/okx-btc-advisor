import type { Candle, MarketSnapshot } from './types'

export type TradeDirection = 'LONG' | 'SHORT'
export type TradeState = 'IDLE' | 'PLANNED' | 'INITIAL_OPEN' | 'APPROACHING_ADD' | 'ADDED' | 'REDUCE_ZONE' | 'PARTIALLY_REDUCED' | 'TAKE_PROFIT' | 'STOPPED' | 'CANCELLED'
export type TradeAction = 'CONFIRM_INITIAL' | 'CONFIRM_ADD' | 'CONFIRM_REDUCE' | 'CONFIRM_TAKE_PROFIT' | 'CONFIRM_STOP' | 'CANCEL'
export type FillAction = Exclude<TradeAction, 'CANCEL'>

export interface TradePlanDraft {
  instrument: 'BTC-USDT-SWAP'
  direction: TradeDirection
  initialEntryPrice: number | null
  addPrice: number | null
  stopPrice: number | null
  takeProfitPrice: number | null
  equity: number
  leverage: number
  initialMargin: number | null
  initialMarginPercent: number
  addMultiplier: number
  crossAvailableEquity: number
  extraMarginUsdt: number
  maintenanceMarginSource: 'AUTO' | 'MANUAL'
  maintenanceMarginRate: number | null
  maintenanceMarginFixedUsdt: number
  liquidationFeeBps: number
  includeUnsettledFunding: boolean
  unsettledFundingUsdt: number
  assumeNoOtherPositions: boolean
  makerFeeBps: number
  takerFeeBps: number
  slippageBps: number
  marginMode: 'ISOLATED' | 'CROSS'
  sizingMode: 'MARGIN' | 'MAX_LOSS'
  maxLossUsdt: number | null
  lossLimitUsdt: number | null
  riskLowMaxPercent: number
  riskMediumMaxPercent: number
  riskHighMaxPercent: number
  approachThresholdPercent: number
  notes: string
  screenshotPath: string | null
}

export interface AdverseMoveLoss { movePercent: number; lossUsdt: number; equityPercent: number }

export type LiquidationStatus = 'AVAILABLE' | 'UNAVAILABLE' | 'NO_OPEN_POSITION'
export type HardStopSequence = 'STOP_FIRST' | 'LIQUIDATION_FIRST' | 'OVERLAP_UNSAFE' | 'UNAVAILABLE'
export interface LiquidationEstimate {
  status: LiquidationStatus
  estimatedLiquidationPrice: number | null
  referenceMarkPrice: number | null
  referenceMarkTime: number | null
  referenceMarkStatus: 'AVAILABLE' | 'MISSING' | 'STALE'
  distanceStatus: 'AVAILABLE' | 'UNAVAILABLE'
  distancePercent: number | null
  distanceRisk: string
  hardStopSequence: HardStopSequence
  hardStopBufferPercent: number | null
  quantityBtc: number | null
  averageEntryPrice: number | null
  supportingEquityUsdt: number | null
  maintenanceMarginRate: number | null
  maintenanceMarginFixedUsdt: number | null
  liquidationFeeRate: number | null
  tier: number | string | null
  contracts: number | null
  parameterSource: 'OKX_PUBLIC' | 'MANUAL' | 'UNAVAILABLE'
  parametersUpdatedAt: number | null
  changeFromPreviousUsdt?: number | null
  assumptions: string[]
  warnings: string[]
  errors: string[]
}

export interface LiquidationScenarios {
  initialOnly: LiquidationEstimate | null
  afterAdd: LiquidationEstimate | null
  afterPlannedReduce: LiquidationEstimate | null
}

export interface RiskCalculation {
  valid: boolean
  errors: string[]
  warnings: string[]
  requestedInitialMargin: number | null
  initialMargin: number | null
  initialMarginPercent: number | null
  affordableInitialMargin: number | null
  maxInitialMarginByLoss: number | null
  initialNotional: number | null
  initialQuantityBtc: number | null
  addMargin: number | null
  addNotional: number | null
  addQuantityBtc: number | null
  totalMargin: number | null
  totalNotional: number | null
  totalQuantityBtc: number | null
  remainingQuantityAfterPlannedReduce: number | null
  initialFillPrice: number | null
  addFillPrice: number | null
  averageEntryPrice: number | null
  grossBreakevenPrice: number | null
  feeBreakevenPrice: number | null
  fullCostBreakevenPrice: number | null
  /** @deprecated 兼容 v0.4 早期字段。 */
  allInBreakevenPrice?: number | null
  /** @deprecated 兼容旧客户端，值等于 allInBreakevenPrice。 */
  feeAdjustedBreakevenPrice: number | null
  reduceZonePrice: number | null
  openingFee: number | null
  addFee: number | null
  estimatedReduceFee: number | null
  estimatedCloseFee: number | null
  totalFeesAtTakeProfit: number | null
  totalFeesAtStop: number | null
  estimatedSlippageAtTakeProfit: number | null
  estimatedSlippageAtStop: number | null
  grossProfitAtTakeProfit: number | null
  netProfitAtTakeProfit: number | null
  initialOnlyNetProfitAtTakeProfit: number | null
  grossLossAtStop: number | null
  netLossAtStop: number | null
  maxLossEquityPercent: number | null
  riskRewardRatio: number | null
  stopDistancePercent: number | null
  addToStopSpacePercent: number | null
  adverseMoveLosses: AdverseMoveLoss[]
  riskLevel: string
  lossLimitExceeded: boolean
  liquidationWarning: boolean
  liquidationScenarios?: LiquidationScenarios | null
  assumptions: string[]
}

export interface ActualFill {
  price: number
  quantityBtc: number
  /** 成交确认时间；旧 SQLite JSON 记录可能没有该字段。 */
  confirmedAt?: number
}
export type ActualFillKey = 'initial' | 'add' | 'reduce' | 'exit'

export interface ExecutionSummary {
  openedQuantityBtc: number
  remainingQuantityBtc: number
  averageEntryPrice: number | null
  remainingEntryCost: number
  realizedGrossPnl: number
  allocatedEntryFees: number
  exitFees: number
  fees: number
  incurredFees?: number
  slippageUsdt: number
  realizedNetPnl: number
  mfeMaeSupported: boolean
}

export interface ExecutionRisk {
  quantityBtc: number
  averageEntryPrice: number | null
  grossBreakevenPrice: number | null
  feeBreakevenPrice: number | null
  fullCostBreakevenPrice: number | null
  remainingNetLossAtStop?: number | null
  netLossAtStop: number | null
  maxLossEquityPercent: number | null
  riskLevel?: string
  totalNetPnlIfStopped: number | null
  liquidationEstimate?: LiquidationEstimate | null
}

export interface RealizedSegment {
  action: string
  price: number
  quantityBtc: number
  grossPnl: number
  fees: number
  slippageUsdt: number
  netPnl: number
  remainingQuantityBtc: number
}

export interface ActiveReminder {
  type: string
  message: string
  price: number | null
  createdAt: number
}

export interface TradePlanRecord {
  id: string | null
  state: TradeState
  plan: TradePlanDraft | null
  risk: RiskCalculation | null
  addCount?: number
  actualFills?: Partial<Record<ActualFillKey, ActualFill>>
  executionSummary?: ExecutionSummary | null
  execution?: ExecutionSummary | null
  executionRisk?: ExecutionRisk | null
  realizedSegments?: RealizedSegment[]
  activeReminder?: ActiveReminder | null
  events?: Array<{ type: string; price?: number | null; at?: number; message?: string }>
  mfeUsdt?: number | null
  maeUsdt?: number | null
  createdAt?: number
  updatedAt?: number
}

export type TradeActionPayload =
  | { action: 'CANCEL'; note?: string }
  | { action: FillAction; price: number; quantityBtc: number; note: string }

export interface AddCheck {
  status: string
  asOf: number
  bodyClosedBeyond?: boolean
  volumeRatio20?: number | null
  rejectionCandle?: boolean
  regime4H?: string
  vwap?: number | null
  vwapPosition?: string
  recentLow?: number | null
  recentHigh?: number | null
  fundingRate?: number | null
  oiChangePercent?: number | null
  addToStopSpacePercent?: number | null
  lossAtStopAfterAdd?: number | null
  warnings: string[]
}

export interface TradeLog {
  id: string
  planId: string
  source: 'live' | 'replay'
  direction: TradeDirection
  state: TradeState
  planPrices: Record<string, number | null>
  actualPrices: Record<string, number | null>
  actualQuantitiesBtc?: Record<string, number | null>
  initialQuantityBtc: number
  addQuantityBtc: number
  totalQuantityBtc: number
  initialMargin: number
  addMargin: number
  leverage: number
  makerFeeBps?: number
  takerFeeBps?: number
  slippageBps?: number
  fees: number
  grossPnl: number
  slippageUsdt?: number
  netPnl: number
  accountReturnPercent?: number | null
  returnPercent?: number | null
  execution?: ExecutionSummary | null
  executionRisk?: ExecutionRisk | null
  realizedSegments?: RealizedSegment[]
  mfeUsdt: number | null
  maeUsdt: number | null
  regime4H: string
  addTriggered: boolean
  returnedToReduceZone: boolean
  notes: string
  screenshotPath: string | null
  closedAt: number
}

export interface TradeEquityPoint {
  timestamp: number
  cumulativeNetPnl: number
  equity: number | null
}

export interface TradeStats {
  totalTrades: number
  winningTrades?: number
  losingTrades?: number
  breakevenTrades?: number
  winRate?: number | null
  initialDirectTakeProfitRate: number | null
  addTriggerRate: number | null
  returnToReduceZoneRate: number | null
  hardStopAfterAddRate: number | null
  averageProfit: number | null
  averageLoss: number | null
  profitFactor: number | null
  totalFees: number
  totalSlippage?: number
  feesToGrossProfit: number | null
  maxConsecutiveLosses: number
  maxDrawdownUsdt: number
  netPnl: number
  startingEquity?: number | null
  simulatedEquity?: number | null
  equityCurve?: TradeEquityPoint[]
  addTriggeredCount?: number
  returnedToReduceZoneCount?: number
  stoppedAfterAddCount?: number
  byDirection: Record<string, { trades: number; netPnl: number; winRate: number | null; averagePnl: number | null }>
  byRegime: Record<string, { trades: number; netPnl: number; winRate: number | null; averagePnl: number | null }>
}

export interface ReplaySession extends TradePlanRecord {
  status: 'SELECTED' | 'RUNNING' | 'COMPLETE'
  mode: 'random' | 'manual'
  cursorTs: number
  candles1H: Candle[]
  candles4H: Candle[]
  events: Array<{ type: string; price: number | null; at: number }>
  result: TradeLog | null
}

export interface WorkbenchBootstrap { snapshot: MarketSnapshot; current: TradePlanRecord }

export const DEFAULT_PLAN: TradePlanDraft = {
  instrument: 'BTC-USDT-SWAP',
  direction: 'LONG',
  initialEntryPrice: null,
  addPrice: null,
  stopPrice: null,
  takeProfitPrice: null,
  equity: 80,
  leverage: 66,
  initialMargin: null,
  initialMarginPercent: 4,
  addMultiplier: 2,
  crossAvailableEquity: 80,
  extraMarginUsdt: 0,
  maintenanceMarginSource: 'AUTO',
  maintenanceMarginRate: null,
  maintenanceMarginFixedUsdt: 0,
  liquidationFeeBps: 5,
  includeUnsettledFunding: false,
  unsettledFundingUsdt: 0,
  assumeNoOtherPositions: true,
  makerFeeBps: 2,
  takerFeeBps: 5,
  slippageBps: 5,
  marginMode: 'CROSS',
  sizingMode: 'MARGIN',
  maxLossUsdt: 3,
  lossLimitUsdt: 3,
  riskLowMaxPercent: 3,
  riskMediumMaxPercent: 5,
  riskHighMaxPercent: 8,
  approachThresholdPercent: .25,
  notes: '',
  screenshotPath: null,
}

/**
 * Hydrate additive v0.5 inputs without changing the economics of a v0.4 plan.
 * Legacy rows did not persist crossAvailableEquity; liquidation already treats
 * that as the plan equity, so the editor must use the same value rather than
 * silently injecting today's 80 USDT new-plan default.
 */
export const normalizeTradePlanDraft = (plan: Partial<TradePlanDraft> | null | undefined): TradePlanDraft => {
  const merged = { ...DEFAULT_PLAN, ...(plan ?? {}) }
  return {
    ...merged,
    crossAvailableEquity: typeof plan?.crossAvailableEquity === 'number'
      ? plan.crossAvailableEquity
      : merged.equity,
  }
}
