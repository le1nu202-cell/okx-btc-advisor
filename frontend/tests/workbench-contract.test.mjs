import assert from 'node:assert/strict'
import { test } from 'node:test'
import { DEFAULT_PLAN, normalizeTradePlanDraft } from '../src/workbench-types.ts'
import { LEGAL_ACTIONS, prefillForAction, priceOrderError } from '../src/workbench-utils.ts'

test('v0.4 默认参数符合 80U、66x、4%、2 倍和成本约定', () => {
  assert.equal(DEFAULT_PLAN.instrument, 'BTC-USDT-SWAP')
  assert.equal(DEFAULT_PLAN.equity, 80)
  assert.equal(DEFAULT_PLAN.leverage, 66)
  assert.equal(DEFAULT_PLAN.initialMarginPercent, 4)
  assert.equal(DEFAULT_PLAN.addMultiplier, 2)
  assert.equal(DEFAULT_PLAN.crossEquityMode, 'FOLLOW_EQUITY')
  assert.equal(DEFAULT_PLAN.liquidationFeeMode, 'FOLLOW_TAKER')
  assert.deepEqual([DEFAULT_PLAN.makerFeeBps, DEFAULT_PLAN.takerFeeBps, DEFAULT_PLAN.slippageBps], [2, 5, 5])
})

test('v0.4 旧计划缺全仓支持余额字段时沿用该计划权益而不是新计划默认 80U', () => {
  const legacy = { ...DEFAULT_PLAN, equity: 123 }
  delete legacy.crossAvailableEquity
  delete legacy.crossEquityMode
  assert.equal(normalizeTradePlanDraft(legacy).crossAvailableEquity, 123)
  assert.equal(normalizeTradePlanDraft(legacy).crossEquityMode, 'FOLLOW_EQUITY')
  const oldIndependent = normalizeTradePlanDraft({ ...legacy, crossAvailableEquity: 45 })
  assert.equal(oldIndependent.crossEquityMode, 'MANUAL')
  assert.equal(oldIndependent.crossAvailableEquity, 45)
})

test('旧强平费率按是否等于 Taker 推断模式，显式手动模式永不被覆盖', () => {
  const legacy = { ...DEFAULT_PLAN }
  delete legacy.liquidationFeeMode
  assert.equal(normalizeTradePlanDraft({ ...legacy, takerFeeBps: 7, liquidationFeeBps: 7 }).liquidationFeeMode, 'FOLLOW_TAKER')
  const oldIndependent = normalizeTradePlanDraft({ ...legacy, takerFeeBps: 7, liquidationFeeBps: 12 })
  assert.equal(oldIndependent.liquidationFeeMode, 'MANUAL')
  assert.equal(oldIndependent.liquidationFeeBps, 12)
  const explicitManual = normalizeTradePlanDraft({ ...DEFAULT_PLAN, takerFeeBps: 7, liquidationFeeBps: 7, liquidationFeeMode: 'MANUAL' })
  assert.equal(explicitManual.liquidationFeeMode, 'MANUAL')
  assert.equal(explicitManual.liquidationFeeBps, 7)
})

test('四价顺序对做多和做空分别给出中文错误', () => {
  const shortPlan = { ...DEFAULT_PLAN, direction: 'SHORT', takeProfitPrice: 90, initialEntryPrice: 100, addPrice: 110, stopPrice: 120 }
  const longPlan = { ...DEFAULT_PLAN, direction: 'LONG', stopPrice: 90, addPrice: 95, initialEntryPrice: 100, takeProfitPrice: 110 }
  assert.equal(priceOrderError(shortPlan), '')
  assert.equal(priceOrderError(longPlan), '')
  assert.match(priceOrderError({ ...shortPlan, stopPrice: 105 }), /做空价格顺序/)
  assert.match(priceOrderError({ ...longPlan, addPrice: 105 }), /做多价格顺序/)
})

test('人工确认动作预填价格和 BTC 数量', () => {
  const plan = { ...DEFAULT_PLAN, direction: 'SHORT', takeProfitPrice: 90, initialEntryPrice: 100, addPrice: 110, stopPrice: 120 }
  const risk = { initialQuantityBtc: .4, addQuantityBtc: .8, totalQuantityBtc: 1.2, reduceZonePrice: 103, fullCostBreakevenPrice: 102 }
  const record = { id: 'p1', state: 'ADDED', plan, risk, execution: { remainingQuantityBtc: 1.1 } }
  assert.deepEqual(prefillForAction('CONFIRM_INITIAL', plan, risk, record), { price: 100, quantityBtc: .4 })
  assert.deepEqual(prefillForAction('CONFIRM_ADD', plan, risk, record), { price: 110, quantityBtc: .8 })
  assert.deepEqual(prefillForAction('CONFIRM_REDUCE', plan, risk, record), { price: 103, quantityBtc: .8 })
  assert.deepEqual(prefillForAction('CONFIRM_STOP', plan, risk, record), { price: 120, quantityBtc: 1.1 })
})

test('终态没有加仓减仓动作，部分减仓只允许最终退出', () => {
  assert.deepEqual(LEGAL_ACTIONS.TAKE_PROFIT, [])
  assert.deepEqual(LEGAL_ACTIONS.STOPPED, [])
  assert.deepEqual(LEGAL_ACTIONS.CANCELLED, [])
  assert.deepEqual(LEGAL_ACTIONS.PARTIALLY_REDUCED, ['CONFIRM_TAKE_PROFIT', 'CONFIRM_STOP'])
})
