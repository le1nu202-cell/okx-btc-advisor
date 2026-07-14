import type { FillAction, RiskCalculation, TradeAction, TradePlanDraft, TradePlanRecord, TradeState } from './workbench-types'

export const TERMINAL_STATES: readonly TradeState[] = ['TAKE_PROFIT', 'STOPPED', 'CANCELLED']

export const STATE_LABELS: Record<TradeState, string> = {
  IDLE: '尚未创建计划',
  PLANNED: '计划已保存，等待开仓',
  INITIAL_OPEN: '已确认初始开仓',
  APPROACHING_ADD: '接近加仓价，等待人工确认',
  ADDED: '已确认加仓',
  REDUCE_ZONE: '进入减仓区，等待人工确认',
  PARTIALLY_REDUCED: '已部分减仓',
  TAKE_PROFIT: '已人工确认止盈',
  STOPPED: '已人工确认止损',
  CANCELLED: '计划已取消',
}

export const ACTION_LABELS: Record<TradeAction, string> = {
  CONFIRM_INITIAL: '确认已开仓',
  CONFIRM_ADD: '确认已加仓',
  CONFIRM_REDUCE: '确认已减仓',
  CONFIRM_TAKE_PROFIT: '确认已止盈',
  CONFIRM_STOP: '确认已止损',
  CANCEL: '取消计划',
}

export const LEGAL_ACTIONS: Record<TradeState, readonly TradeAction[]> = {
  IDLE: [],
  PLANNED: ['CONFIRM_INITIAL', 'CANCEL'],
  INITIAL_OPEN: ['CONFIRM_ADD', 'CONFIRM_TAKE_PROFIT', 'CONFIRM_STOP'],
  APPROACHING_ADD: ['CONFIRM_ADD', 'CONFIRM_TAKE_PROFIT', 'CONFIRM_STOP'],
  ADDED: ['CONFIRM_REDUCE', 'CONFIRM_TAKE_PROFIT', 'CONFIRM_STOP'],
  REDUCE_ZONE: ['CONFIRM_REDUCE', 'CONFIRM_TAKE_PROFIT', 'CONFIRM_STOP'],
  PARTIALLY_REDUCED: ['CONFIRM_TAKE_PROFIT', 'CONFIRM_STOP'],
  TAKE_PROFIT: [],
  STOPPED: [],
  CANCELLED: [],
}

export const isTerminalState = (state: TradeState) => TERMINAL_STATES.includes(state)

export function priceOrderError(plan: TradePlanDraft): string {
  const { initialEntryPrice: initial, addPrice: add, stopPrice: stop, takeProfitPrice: take } = plan
  if ([initial, add, stop, take].some(value => value == null || !Number.isFinite(value) || value <= 0)) return '请填写四个大于 0 的有效价格。'
  if (plan.direction === 'SHORT' && !(take! < initial! && initial! < add! && add! < stop!)) {
    return '做空价格顺序应为：止盈价 < 初始开仓价 < 第一压力位（加仓价） < 第二压力位（硬止损价）。'
  }
  if (plan.direction === 'LONG' && !(stop! < add! && add! < initial! && initial! < take!)) {
    return '做多价格顺序应为：第二压力位（硬止损价） < 第一压力位（加仓价） < 初始开仓价 < 止盈价。'
  }
  return ''
}

export function prefillForAction(action: FillAction, plan: TradePlanDraft, risk: RiskCalculation | null, record: TradePlanRecord) {
  const execution = record.executionSummary ?? record.execution
  const remaining = execution?.remainingQuantityBtc ?? risk?.totalQuantityBtc ?? 0
  if (action === 'CONFIRM_INITIAL') return { price: plan.initialEntryPrice ?? 0, quantityBtc: risk?.initialQuantityBtc ?? 0 }
  if (action === 'CONFIRM_ADD') return { price: plan.addPrice ?? 0, quantityBtc: risk?.addQuantityBtc ?? 0 }
  if (action === 'CONFIRM_REDUCE') return { price: risk?.reduceZonePrice ?? risk?.fullCostBreakevenPrice ?? risk?.allInBreakevenPrice ?? 0, quantityBtc: Math.min(remaining, risk?.addQuantityBtc ?? remaining) }
  if (action === 'CONFIRM_TAKE_PROFIT') return { price: plan.takeProfitPrice ?? 0, quantityBtc: remaining }
  return { price: plan.stopPrice ?? 0, quantityBtc: remaining }
}

export const toInputNumber = (value: string): number | null => value.trim() === '' ? null : Number(value)
