import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import { DEFAULT_PLAN } from '../src/workbench-types.ts'
import { LEGAL_ACTIONS, prefillForAction, priceOrderError } from '../src/workbench-utils.ts'

const source = name => readFile(new URL(`../src/${name}`, import.meta.url), 'utf8')

test('App 默认进入交易工作台并保留研究区入口', async () => {
  const app = await source('App.tsx')
  assert.match(app, /useState<View>\('workbench'\)/)
  assert.match(app, /<WorkbenchView\/>/)
  assert.match(app, /<ResearchView\/>/)
  assert.match(app, /交易工作台/)
  assert.match(app, /研究区/)
})

test('v0.4 默认参数符合 80U、66x、4%、2 倍和成本约定', () => {
  assert.equal(DEFAULT_PLAN.instrument, 'BTC-USDT-SWAP')
  assert.equal(DEFAULT_PLAN.equity, 80)
  assert.equal(DEFAULT_PLAN.leverage, 66)
  assert.equal(DEFAULT_PLAN.initialMarginPercent, 4)
  assert.equal(DEFAULT_PLAN.addMultiplier, 2)
  assert.deepEqual([DEFAULT_PLAN.makerFeeBps, DEFAULT_PLAN.takerFeeBps, DEFAULT_PLAN.slippageBps], [2, 5, 5])
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

test('实际 BTC 数量允许后端精确预填值通过浏览器原生校验', async () => {
  const view = await source('WorkbenchView.tsx')
  assert.match(view, /label="实际成交 BTC 数量"[^\n]+step="any"/)
})

test('终态没有加仓减仓动作，部分减仓只允许最终退出', () => {
  assert.deepEqual(LEGAL_ACTIONS.TAKE_PROFIT, [])
  assert.deepEqual(LEGAL_ACTIONS.STOPPED, [])
  assert.deepEqual(LEGAL_ACTIONS.CANCELLED, [])
  assert.deepEqual(LEGAL_ACTIONS.PARTIALLY_REDUCED, ['CONFIRM_TAKE_PROFIT', 'CONFIRM_STOP'])
})

test('字段和动作 API 与后端冻结契约一致', async () => {
  const [types, api] = await Promise.all([source('workbench-types.ts'), source('workbench-api.ts')])
  for (const field of ['fullCostBreakevenPrice', 'totalMargin', 'totalNotional', 'remainingQuantityAfterPlannedReduce', 'feeBreakevenPrice', 'totalFeesAtStop', 'estimatedSlippageAtStop', 'estimatedSlippageAtTakeProfit']) assert.match(types, new RegExp(field))
  for (const field of ['price: number', 'quantityBtc: number', 'realizedNetPnl', 'remainingQuantityBtc', 'mfeMaeSupported', 'activeReminder', 'realizedSegments', 'executionRisk', 'totalNetPnlIfStopped']) assert.match(types, new RegExp(field))
  assert.match(api, /action: \(id: string, payload: TradeActionPayload\)/)
  assert.match(api, /JSON\.stringify\(body\)/)
})

test('页面醒目显示净亏损并区分提醒与真实成交', async () => {
  const view = await source('WorkbenchView.tsx')
  assert.match(view, /到第二压力位预计净亏损/)
  assert.match(view, /初始开仓价/)
  assert.match(view, /第一压力位 \/ 加仓价/)
  assert.match(view, /第二压力位 \/ 硬止损价/)
  assert.match(view, /止盈价/)
  assert.match(view, /实际成交价格/)
  assert.match(view, /实际成交 BTC 数量/)
  assert.match(view, /提醒不是成交/)
  assert.match(view, /仅展示你人工确认的成交/)
  assert.match(view, /剩余 BTC/)
  assert.match(view, /分段减仓后 MFE\/MAE 暂不支持/)
  assert.match(view, /全成本保本价/)
  assert.match(view, /executionRisk\?\.netLossAtStop/)
  assert.match(view, /executionRisk\?\.fullCostBreakevenPrice/)
})
