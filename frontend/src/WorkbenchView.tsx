import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { AlertOctagon, AlertTriangle, Calculator, CheckCircle2, LoaderCircle, RefreshCw, Save, ShieldAlert, Wifi, WifiOff } from 'lucide-react'
import TradingPlanChart from './components/TradingPlanChart'
import { workbenchApi } from './workbench-api'
import { ACTION_LABELS, STATE_LABELS, canonicalExecutionState, isTerminalState, legalActionsForState, prefillForAction, priceOrderError, toInputNumber } from './workbench-utils'
import { DEFAULT_PLAN } from './workbench-types'
import type { ActiveReminder, ExecutionRisk, FillAction, RiskCalculation, TradePlanDraft, TradePlanRecord } from './workbench-types'
import type { MarketSnapshot } from './types'

const EMPTY_SNAPSHOT: MarketSnapshot = { instrument: 'BTC-USDT-SWAP', price: null, updatedAt: null, stale: true, connectionStatus: 'disconnected', fundingRate: null, fundingTime: null, openInterest: null, openInterestTime: null, candles1h: [], candles4h: [] }
const EMPTY_RECORD: TradePlanRecord = { id: null, state: 'IDLE', plan: null, risk: null, actualFills: {}, activeReminder: null }

const money = (value: number | null | undefined, digits = 2) => value == null || !Number.isFinite(value) ? '—' : new Intl.NumberFormat('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value)
const quantity = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '—' : value.toFixed(8)
const percent = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(2)}%`
const time = (value: number | null | undefined) => value ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(value) : '—'
const connectionLabel = (value: string) => value === 'connected' ? '公共行情已连接' : value === 'reconnecting' || value === 'starting' ? '公共行情重连中' : value === 'degraded' ? '公共行情部分可用' : '公共行情未连接'
const actualRiskLevel = (value: number | null | undefined, plan: TradePlanDraft) => value == null ? '等待计算' : value <= plan.riskLowMaxPercent ? '低' : value <= plan.riskMediumMaxPercent ? '中' : value <= plan.riskHighMaxPercent ? '高' : '极高'

function Metric({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: string }) {
  return <div className="wb-metric"><span>{label}</span><strong className={tone}>{value}</strong>{hint && <small>{hint}</small>}</div>
}

function NumberField({ label, value, onChange, step = 'any', min = 0, disabled = false, suffix, required = false }: { label: string; value: number | null; onChange: (value: number | null) => void; step?: string | number; min?: number; disabled?: boolean; suffix?: string; required?: boolean }) {
  return <label className="wb-field"><span>{label}</span><div><input type="number" min={min} step={step} value={value ?? ''} disabled={disabled} required={required} onChange={event => onChange(toInputNumber(event.target.value))}/>{suffix && <em>{suffix}</em>}</div></label>
}

function PlanForm({ draft, setDraft, record, risk, saving, onSave }: { draft: TradePlanDraft; setDraft: (plan: TradePlanDraft) => void; record: TradePlanRecord; risk: RiskCalculation | null; saving: boolean; onSave: (event: FormEvent) => void }) {
  const executionState = canonicalExecutionState(record.state)
  const terminal = isTerminalState(executionState)
  const editable = !record.id || executionState === 'PLANNED' || terminal
  const orderError = priceOrderError(draft)
  const set = <K extends keyof TradePlanDraft>(key: K, value: TradePlanDraft[K]) => setDraft({ ...draft, [key]: value })
  const marginInputMode = draft.initialMargin == null ? 'PERCENT' : 'AMOUNT'
  return <section className="wb-panel wb-plan-section">
    <div className="wb-section-head"><div><span className="wb-kicker">四价交易计划</span><h2>先写清楚计划，再等待人工成交</h2></div><span className={`wb-direction ${draft.direction.toLowerCase()}`}>{draft.direction === 'LONG' ? '做多计划' : '做空计划'}</span></div>
    {!editable && <div className="wb-locked"><ShieldAlert/>计划已有真实成交，价格和仓位已锁定；请用下方人工确认动作继续。</div>}
    <form onSubmit={onSave}>
      <div className="wb-form-grid wb-four-prices">
        <label className="wb-field"><span>方向</span><div><select value={draft.direction} disabled={!editable} onChange={event => set('direction', event.target.value as TradePlanDraft['direction'])}><option value="LONG">做多 LONG</option><option value="SHORT">做空 SHORT</option></select></div></label>
        <NumberField label="初始开仓价" value={draft.initialEntryPrice} onChange={value => set('initialEntryPrice', value)} disabled={!editable} required suffix="USDT"/>
        <NumberField label="第一压力位 / 加仓价" value={draft.addPrice} onChange={value => set('addPrice', value)} disabled={!editable} required suffix="USDT"/>
        <NumberField label="第二压力位 / 硬止损价" value={draft.stopPrice} onChange={value => set('stopPrice', value)} disabled={!editable} required suffix="USDT"/>
        <NumberField label="止盈价" value={draft.takeProfitPrice} onChange={value => set('takeProfitPrice', value)} disabled={!editable} required suffix="USDT"/>
      </div>
      <div className="wb-form-divider"><span>仓位与成本</span></div>
      <div className="wb-form-grid wb-settings-grid">
        <NumberField label="账户权益" value={draft.equity} onChange={value => set('equity', value ?? 0)} disabled={!editable} required suffix="USDT"/>
        <NumberField label="杠杆" value={draft.leverage} onChange={value => set('leverage', value ?? 0)} disabled={!editable} min={1} step="1" required suffix="x"/>
        <label className="wb-field"><span>初始保证金输入</span><div><select value={marginInputMode} disabled={!editable || draft.sizingMode === 'MAX_LOSS'} onChange={event => set('initialMargin', event.target.value === 'AMOUNT' ? draft.equity * draft.initialMarginPercent / 100 : null)}><option value="PERCENT">按权益比例</option><option value="AMOUNT">按 USDT 金额</option></select></div></label>
        {marginInputMode === 'PERCENT' ? <NumberField label="初始保证金比例" value={draft.initialMarginPercent} onChange={value => set('initialMarginPercent', value ?? 0)} disabled={!editable || draft.sizingMode === 'MAX_LOSS'} required suffix="%"/> : <NumberField label="初始保证金金额" value={draft.initialMargin} onChange={value => set('initialMargin', value)} disabled={!editable || draft.sizingMode === 'MAX_LOSS'} required suffix="USDT"/>}
        <NumberField label="加仓倍数（最多一次）" value={draft.addMultiplier} onChange={value => set('addMultiplier', value ?? 0)} disabled={!editable} required suffix="倍"/>
        <label className="wb-field"><span>仓位计算方式</span><div><select value={draft.sizingMode} disabled={!editable} onChange={event => set('sizingMode', event.target.value as TradePlanDraft['sizingMode'])}><option value="MARGIN">按初始保证金</option><option value="MAX_LOSS">按最大亏损反推</option></select></div></label>
        <NumberField label="最大允许亏损" value={draft.maxLossUsdt} onChange={value => setDraft({ ...draft, maxLossUsdt: value, lossLimitUsdt: value })} disabled={!editable} required={draft.sizingMode === 'MAX_LOSS'} suffix="USDT"/>
        <label className="wb-field"><span>保证金模式</span><div><select value={draft.marginMode} disabled={!editable} onChange={event => set('marginMode', event.target.value as TradePlanDraft['marginMode'])}><option value="ISOLATED">逐仓 ISOLATED</option><option value="CROSS">全仓 CROSS</option></select></div></label>
        <NumberField label="Maker 手续费" value={draft.makerFeeBps} onChange={value => set('makerFeeBps', value ?? 0)} disabled={!editable} suffix="bp"/>
        <NumberField label="Taker 手续费" value={draft.takerFeeBps} onChange={value => set('takerFeeBps', value ?? 0)} disabled={!editable} suffix="bp"/>
        <NumberField label="预计滑点" value={draft.slippageBps} onChange={value => set('slippageBps', value ?? 0)} disabled={!editable} suffix="bp"/>
        <label className="wb-field wb-notes"><span>备注（可选）</span><textarea value={draft.notes} disabled={!editable} maxLength={4000} placeholder="写下入场理由、失效条件或需要克制的临场冲动" onChange={event => set('notes', event.target.value)}/></label>
      </div>
      {orderError && <div className="wb-inline-error" role="alert"><AlertTriangle/>{orderError}</div>}
      {risk?.errors.map(error => <div className="wb-inline-error" role="alert" key={error}><AlertTriangle/>{error}</div>)}
      {editable && <button className="wb-primary" type="submit" disabled={saving || Boolean(orderError) || risk?.valid === false}>{saving ? <LoaderCircle className="spin"/> : <Save/>}{record.id && executionState === 'PLANNED' ? '更新当前计划' : terminal ? '创建下一笔计划' : '创建交易计划'}</button>}
    </form>
  </section>
}

function RiskDetails({ risk, plan, executionRisk, record }: { risk: RiskCalculation | null; plan: TradePlanDraft; executionRisk?: ExecutionRisk | null; record: TradePlanRecord }) {
  if (!risk) return <section className="wb-panel wb-risk-details"><div className="wb-section-head"><div><span className="wb-kicker">加仓后风险详情</span><h2>等待风险计算</h2></div></div><div className="wb-chart-empty"><Calculator/>填写四价后由后端统一计算。</div></section>
  const plannedFullCost = risk.fullCostBreakevenPrice ?? risk.allInBreakevenPrice ?? risk.feeAdjustedBreakevenPrice
  const plannedRows: Array<[string, string]> = [
    ['初始保证金', `${money(risk.initialMargin)} USDT`], ['加仓保证金', `${money(risk.addMargin)} USDT`], ['总使用保证金', `${money(risk.totalMargin)} USDT`],
    ['初始名义仓位', `${money(risk.initialNotional)} USDT`], ['加仓名义仓位', `${money(risk.addNotional)} USDT`], ['总名义仓位', `${money(risk.totalNotional)} USDT`],
    ['计划初始 BTC 数量', `${quantity(risk.initialQuantityBtc)} BTC`], ['计划加仓 BTC 数量', `${quantity(risk.addQuantityBtc)} BTC`], ['计划总 BTC 数量', `${quantity(risk.totalQuantityBtc)} BTC`],
    ['计划减仓后剩余', `${quantity(risk.remainingQuantityAfterPlannedReduce)} BTC`], ['计划加权均价', `${money(risk.averageEntryPrice)} USDT`], ['计划毛保本价', `${money(risk.grossBreakevenPrice)} USDT`],
    ['计划手续费保本价', `${money(risk.feeBreakevenPrice)} USDT`], ['计划全成本保本价', `${money(plannedFullCost)} USDT`], ['止盈位毛利润', `${money(risk.grossProfitAtTakeProfit)} USDT`],
    ['止盈位预计净利润', `${money(risk.netProfitAtTakeProfit)} USDT`], ['止损位毛亏损', `${money(risk.grossLossAtStop)} USDT`], ['计划止损净亏损', `${money(risk.netLossAtStop)} USDT`],
    ['开仓手续费', `${money(risk.openingFee)} USDT`], ['加仓手续费', `${money(risk.addFee)} USDT`], ['减仓手续费', `${money(risk.estimatedReduceFee)} USDT`],
    ['最终平仓手续费', `${money(risk.estimatedCloseFee)} USDT`], ['止损总手续费', `${money(risk.totalFeesAtStop)} USDT`], ['止损预计滑点', `${money(risk.estimatedSlippageAtStop)} USDT`],
    ['止盈预计滑点', `${money(risk.estimatedSlippageAtTakeProfit)} USDT`], ['盈亏比', risk.riskRewardRatio == null ? '—' : `1 : ${risk.riskRewardRatio.toFixed(2)}`], ['反推最大初始保证金', `${money(risk.maxInitialMarginByLoss)} USDT`],
  ]
  const execution = record.executionSummary ?? record.execution
  const fills = record.actualFills ?? {}
  const actualRows: Array<[string, string]> = execution && executionRisk ? [
    ['实际初始成交数量', `${quantity(fills.initial?.quantityBtc)} BTC`],
    ['实际加仓成交数量', `${quantity(fills.add?.quantityBtc)} BTC`],
    ['实际累计开仓数量', `${quantity(execution.openedQuantityBtc)} BTC`],
    ['实际剩余数量', `${quantity(execution.remainingQuantityBtc)} BTC`],
    ['实际加权均价', `${money(executionRisk.averageEntryPrice)} USDT`],
    ['实际毛保本价', `${money(executionRisk.grossBreakevenPrice)} USDT`],
    ['实际手续费保本价', `${money(executionRisk.feeBreakevenPrice)} USDT`],
    ['实际全成本保本价', `${money(executionRisk.fullCostBreakevenPrice)} USDT`],
    ['实际剩余仓位到止损净亏损', `${money(executionRisk.remainingNetLossAtStop)} USDT`],
    ['实际到止损总盈亏', `${money(executionRisk.totalNetPnlIfStopped)} USDT`],
    ['实际止损风险占权益', percent(executionRisk.maxLossEquityPercent)],
    ['实际已实现净盈亏', `${money(execution.realizedNetPnl)} USDT`],
  ] : []
  return <section className="wb-panel wb-risk-details">
    <div className="wb-section-head"><div><span className="wb-kicker">风险详情</span><h2>计划数据与实际成交数据严格分区</h2></div><span className="wb-risk-pill">{executionRisk ? `实际风险：${actualRiskLevel(executionRisk.maxLossEquityPercent, plan)}` : `计划风险：${risk.riskLevel}`}</span></div>
    <div className="wb-risk-subhead"><strong>计划数据</strong><span>由保存的计划和后端风险计算返回</span></div>
    <div className="wb-risk-grid">{plannedRows.map(([label, value]) => <Metric key={label} label={label} value={value}/>)}</div>
    {actualRows.length > 0 && <div className="wb-actual-risk-block">
      <div className="wb-risk-subhead actual"><strong>实际成交数据</strong><span>仅由人工确认的 actualFills 和后端 executionRisk 返回</span></div>
      <div className="wb-risk-grid actual">{actualRows.map(([label, value]) => <Metric key={label} label={label} value={value}/>)}</div>
    </div>}
    <div className="wb-adverse"><strong>加仓后继续反向移动</strong>{risk.adverseMoveLosses.map(row => <span key={row.movePercent}>反向 {row.movePercent}%：预计亏损 {money(row.lossUsdt)} USDT（权益 {percent(row.equityPercent)}）</span>)}</div>
    {[...risk.warnings, ...(risk.liquidationWarning ? ['当前计划存在强平风险提示，请缩小仓位或调整止损。'] : []), ...(risk.lossLimitExceeded ? [`预计亏损超过你设定的 ${money(plan.maxLossUsdt)} USDT 上限。`] : [])].map(message => <div className="wb-warning" key={message}><AlertTriangle/>{message}</div>)}
  </section>
}

function ExecutionPanel({ record }: { record: TradePlanRecord }) {
  const execution = record.executionSummary ?? record.execution
  const fills = record.actualFills ?? {}
  const fillLabels = { initial: '初始开仓', add: '加仓', reduce: '部分减仓', exit: '最终退出' } as const
  return <section className="wb-panel wb-execution">
    <div className="wb-section-head"><div><span className="wb-kicker">真实执行记录</span><h2>仅展示你人工确认的成交</h2></div><span className="wb-manual-badge"><CheckCircle2/>人工确认</span></div>
    {Object.keys(fills).length ? <div className="wb-fill-list">{Object.entries(fills).map(([key, fill]) => fill && <div key={key}><span>{fillLabels[key as keyof typeof fillLabels] ?? key}</span><strong>{money(fill.price)} USDT</strong><small>{quantity(fill.quantityBtc)} BTC</small></div>)}</div> : <div className="wb-empty-small">尚无人工确认成交。行情触价不会写入这里。</div>}
    {execution && <div className="wb-execution-summary"><Metric label="已实现净盈亏" value={`${money(execution.realizedNetPnl)} USDT`} tone={execution.realizedNetPnl >= 0 ? 'up' : 'down'}/><Metric label="剩余 BTC" value={`${quantity(execution.remainingQuantityBtc)} BTC`}/><Metric label="实际加权均价" value={`${money(execution.averageEntryPrice)} USDT`}/><Metric label="剩余入场成本" value={`${money(execution.remainingEntryCost)} USDT`}/><Metric label="累计费用" value={`${money(execution.fees)} USDT`}/><Metric label="累计滑点" value={`${money(execution.slippageUsdt)} USDT`}/></div>}
    {execution && !execution.mfeMaeSupported && <div className="wb-info">分段减仓后 MFE/MAE 暂不支持，已隐藏可能误导的数值。</div>}
  </section>
}

function ActionsPanel({ record, onChanged }: { record: TradePlanRecord; onChanged: (record: TradePlanRecord, message: string) => void }) {
  const executionState = canonicalExecutionState(record.state)
  const legal = legalActionsForState(executionState)
  const fillActions = legal.filter((action): action is FillAction => action !== 'CANCEL')
  const [selected, setSelected] = useState<FillAction | null>(fillActions[0] ?? null)
  const [price, setPrice] = useState<number | null>(null), [quantityBtc, setQuantity] = useState<number | null>(null), [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false), [error, setError] = useState('')
  useEffect(() => { const next = fillActions[0] ?? null; setSelected(next) }, [executionState])
  useEffect(() => { if (!selected || !record.plan) return; const values = prefillForAction(selected, record.plan, record.risk, record); setPrice(values.price || null); setQuantity(values.quantityBtc || null); setError('') }, [selected, record.id, executionState])
  if (!record.id) return <section className="wb-panel wb-actions"><div className="wb-section-head"><div><span className="wb-kicker">当前可执行动作</span><h2>先创建计划</h2></div></div><div className="wb-empty-small">创建计划后，这里只会显示当前阶段允许的人工确认动作。</div></section>
  if (isTerminalState(executionState)) return <section className="wb-panel wb-actions"><div className="wb-section-head"><div><span className="wb-kicker">当前可执行动作</span><h2>本计划已结束</h2></div></div><div className="wb-terminal"><CheckCircle2/>终态不再允许加仓或减仓。可在上方修改四价并创建下一笔计划。</div></section>
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!selected || !record.id || price == null || price <= 0 || quantityBtc == null || quantityBtc <= 0) return setError('实际成交价格和实际 BTC 数量都必须大于 0。')
    setSubmitting(true); setError('')
    try { const response = await workbenchApi.action(record.id, { action: selected, price, quantityBtc, note }); onChanged(response.plan, `${ACTION_LABELS[selected]}已记录`) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '提交失败') }
    finally { setSubmitting(false) }
  }
  const cancel = async () => {
    if (!record.id || !window.confirm('确定取消这笔尚未成交的计划？')) return
    setSubmitting(true); setError('')
    try { const response = await workbenchApi.action(record.id, { action: 'CANCEL', note }); onChanged(response.plan, '计划已取消') }
    catch (reason) { setError(reason instanceof Error ? reason.message : '取消失败') }
    finally { setSubmitting(false) }
  }
  return <section className="wb-panel wb-actions">
    <div className="wb-section-head"><div><span className="wb-kicker">当前可执行动作</span><h2>真实成交必须由你填写并确认</h2></div><span className="wb-manual-badge"><CheckCircle2/>不会自动下单</span></div>
    {fillActions.length > 0 && <div className="wb-action-tabs">{fillActions.map(action => <button key={action} type="button" className={selected === action ? 'active' : ''} onClick={() => setSelected(action)}>{ACTION_LABELS[action]}</button>)}</div>}
    {selected && <form className="wb-action-form" onSubmit={submit}>
      <NumberField label="实际成交价格" value={price} onChange={setPrice} required suffix="USDT"/>
      <NumberField label="实际成交 BTC 数量" value={quantityBtc} onChange={setQuantity} step="any" required suffix="BTC"/>
      <label className="wb-field"><span>本次成交备注（可选）</span><div><input value={note} maxLength={2000} onChange={event => setNote(event.target.value)}/></div></label>
      <button className="wb-primary" type="submit" disabled={submitting}>{submitting ? <LoaderCircle className="spin"/> : <CheckCircle2/>}{ACTION_LABELS[selected]}</button>
    </form>}
    {legal.includes('CANCEL') && <button type="button" className="wb-cancel" onClick={cancel} disabled={submitting}>取消计划</button>}
    {error && <div className="wb-inline-error" role="alert"><AlertTriangle/>{error}</div>}
  </section>
}

function ReminderPanel({ reminder }: { reminder: ActiveReminder | null | undefined }) {
  return <section className={`wb-panel wb-reminder ${reminder ? 'active' : ''}`}>
    <div className="wb-section-head"><div><span className="wb-kicker">当前行情提醒</span><h2>{reminder ? '需要你检查并决定是否操作' : '当前没有待处理提醒'}</h2></div><span className="wb-not-fill"><AlertOctagon/>提醒不是成交</span></div>
    {reminder ? <div className="wb-reminder-body"><strong>{reminder.message}</strong><span>触发参考价 {money(reminder.price)} USDT · {time(reminder.createdAt)}</span><p>行情触及价格只会显示提醒，不会改变真实成交状态、不会写入 actualFills，也不会创建最终交易日志。</p></div> : <div className="wb-empty-small">系统会在接近开仓、加仓、减仓、止盈或止损区域时提醒；仍需你在上方手工确认实际价格和 BTC 数量。</div>}
  </section>
}

export default function WorkbenchView() {
  const [snapshot, setSnapshot] = useState(EMPTY_SNAPSHOT), [record, setRecord] = useState<TradePlanRecord>(EMPTY_RECORD)
  const [draft, setDraft] = useState<TradePlanDraft>({ ...DEFAULT_PLAN }), [risk, setRisk] = useState<RiskCalculation | null>(null)
  const [loading, setLoading] = useState(true), [saving, setSaving] = useState(false), [serviceError, setServiceError] = useState(''), [calculationError, setCalculationError] = useState(''), [toast, setToast] = useState('')
  const [socketConnected, setSocketConnected] = useState(false)
  const didRestore = useRef(false), latestDraft = useRef(draft)
  latestDraft.current = draft
  const applyRecord = useCallback((next: TradePlanRecord, restoreDraft = false) => {
    const normalized = { ...next, state: canonicalExecutionState(next.state) }
    setRecord(normalized); if (normalized.risk) setRisk(normalized.risk)
    if (normalized.plan && (restoreDraft || normalized.state === 'PLANNED')) setDraft(normalized.plan)
  }, [])
  const load = useCallback(async () => {
    const results = await Promise.allSettled([workbenchApi.snapshot(), workbenchApi.current()])
    if (results[0].status === 'fulfilled') setSnapshot(results[0].value)
    if (results[1].status === 'fulfilled') { applyRecord(results[1].value, !didRestore.current); didRestore.current = true }
    setServiceError(results.some(result => result.status === 'rejected') ? '本地服务部分接口暂不可用，正在保留已显示内容并重试。' : '')
    setLoading(false)
  }, [applyRecord])
  useEffect(() => { load(); const timer = window.setInterval(load, 60_000); return () => window.clearInterval(timer) }, [load])
  useEffect(() => {
    const timer = window.setTimeout(() => { workbenchApi.calculate(draft).then(value => { if (latestDraft.current === draft) { setRisk(value); setCalculationError('') } }).catch(reason => { if (latestDraft.current === draft) setCalculationError(reason instanceof Error ? reason.message : '风险计算失败') }) }, 250)
    return () => window.clearTimeout(timer)
  }, [draft])
  useEffect(() => {
    let socket: WebSocket | null = null, retry = 0, retryTimer = 0, stopped = false
    const connect = () => {
      const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
      socket = new WebSocket(`${protocol}://${location.host}/ws/live`)
      socket.onopen = () => setSocketConnected(true)
      socket.onclose = () => { setSocketConnected(false); if (!stopped) retryTimer = window.setTimeout(connect, Math.min(30_000, 1_000 * 2 ** retry++)) }
      socket.onerror = () => socket?.close()
      socket.onmessage = event => {
        try {
          const message = JSON.parse(event.data) as Record<string, unknown>
          if (message.type === 'ready') { retry = 0; setSocketConnected(true) }
          if (message.type === 'market') {
            const tickerTime = Number(message.tickerTime)
            setSnapshot(current => ({ ...current, price: typeof message.price === 'number' ? message.price : current.price, updatedAt: Number.isFinite(tickerTime) ? tickerTime : current.updatedAt, connectionStatus: typeof message.connectionStatus === 'string' ? message.connectionStatus : current.connectionStatus, stale: Number.isFinite(tickerTime) ? Date.now() - tickerTime > 30_000 : current.stale }))
          }
          if (message.type === 'tradePlanAlert') {
            if (message.plan && typeof message.plan === 'object') applyRecord(message.plan as TradePlanRecord)
            else workbenchApi.current().then(next => applyRecord(next)).catch(() => undefined)
            if (typeof message.message === 'string') setToast(message.message)
          }
        } catch { /* 忽略损坏的实时帧，下一次 REST 同步会恢复。 */ }
      }
    }
    connect()
    return () => { stopped = true; window.clearTimeout(retryTimer); socket?.close() }
  }, [applyRecord])
  const save = async (event: FormEvent) => {
    event.preventDefault()
    const localError = priceOrderError(draft)
    if (localError) return setCalculationError(localError)
    setSaving(true); setServiceError('')
    try {
      const executionState = canonicalExecutionState(record.state)
      const next = record.id && executionState === 'PLANNED' ? await workbenchApi.update(record.id, draft) : await workbenchApi.create(draft)
      applyRecord(next, true); setToast(record.id && executionState === 'PLANNED' ? '计划已更新' : '计划已创建，等待你确认真实开仓')
    } catch (reason) { setServiceError(reason instanceof Error ? reason.message : '保存计划失败') }
    finally { setSaving(false) }
  }
  const executionState = canonicalExecutionState(record.state)
  const startingNextPlan = Boolean(record.id && isTerminalState(executionState) && record.plan && JSON.stringify(draft) !== JSON.stringify(record.plan))
  const currentRisk = record.id && executionState !== 'PLANNED' && !isTerminalState(executionState) ? record.risk : risk
  const hasActualFills = Object.keys(record.actualFills ?? {}).length > 0
  const executionRisk: ExecutionRisk | null = hasActualFills && !startingNextPlan ? record.executionRisk ?? null : null
  const direction = record.plan?.direction ?? draft.direction
  const equityPercent = executionRisk?.maxLossEquityPercent ?? currentRisk?.maxLossEquityPercent
  const netLoss = Math.abs(executionRisk?.netLossAtStop ?? currentRisk?.netLossAtStop ?? 0)
  const age = snapshot.updatedAt == null ? '尚无行情时间' : `${Math.max(0, Math.round((Date.now() - snapshot.updatedAt) / 1000))} 秒前`
  const marketConnected = socketConnected && snapshot.connectionStatus === 'connected' && !snapshot.stale
  const chartPlan = record.id && executionState !== 'PLANNED' && (!isTerminalState(executionState) || !startingNextPlan) && record.plan ? record.plan : draft
  const chartActualFills = startingNextPlan ? {} : record.actualFills ?? {}
  const riskTone = currentRisk?.lossLimitExceeded || currentRisk?.liquidationWarning || (chartPlan.maxLossUsdt != null && netLoss > chartPlan.maxLossUsdt) ? 'danger' : 'normal'
  const updateAfterAction = (next: TradePlanRecord, message: string) => { applyRecord(next, true); setToast(message) }
  const primaryStatus = useMemo(() => STATE_LABELS[executionState], [executionState])
  return <main className="workbench-page">
    {serviceError && <div className="wb-service-error" role="alert"><AlertTriangle/><span>{serviceError}</span><button onClick={load}>重试</button></div>}
    <section className="wb-market-status">
      <div className="wb-price"><span>{snapshot.instrument}</span><strong>{snapshot.price == null ? '—' : `$${money(snapshot.price)}`}</strong><small>{age}</small></div>
      <div className={`wb-connection ${marketConnected ? 'online' : ''}`}>{marketConnected ? <Wifi/> : <WifiOff/>}<span><strong>{connectionLabel(snapshot.connectionStatus)}</strong><small>{socketConnected ? '本地实时通道已连接' : '本地实时通道重连中'}</small></span></div>
      <div className="wb-state"><span>实际执行状态</span><strong>{primaryStatus}</strong><small>{direction === 'LONG' ? '当前计划：做多' : '当前计划：做空'} · 只由人工成交确认改变</small></div>
      <button className="wb-refresh" aria-label="刷新行情和当前计划" onClick={load} disabled={loading}><RefreshCw className={loading ? 'spin' : ''}/></button>
    </section>
    <section className={`wb-risk-hero ${riskTone}`}>
      <div><span>最重要的风险数字</span><h1>{executionRisk ? '实际仓位到第二压力位整笔预计净亏损' : '计划到第二压力位预计净亏损'} <strong>{currentRisk?.valid ? money(netLoss) : '—'} USDT</strong></h1><p>这是手续费和预计滑点后的估算，不是“使用 4% 保证金就只承担 4% 风险”。</p></div>
      <div className="wb-risk-hero-side"><Metric label="占账户权益" value={percent(equityPercent)}/><Metric label="风险等级" value={executionRisk ? actualRiskLevel(equityPercent, chartPlan) : currentRisk?.riskLevel ?? '等待计算'}/><Metric label="最大允许亏损" value={`${money(chartPlan.maxLossUsdt)} USDT`}/><Metric label="反推最大初始保证金" value={`${money(currentRisk?.maxInitialMarginByLoss)} USDT`}/></div>
    </section>
    {calculationError && <div className="wb-inline-error wb-global-error" role="alert"><AlertTriangle/>{calculationError}</div>}
    <PlanForm draft={draft} setDraft={setDraft} record={record} risk={risk} saving={saving} onSave={save}/>
    <section className="wb-panel wb-chart-section"><div className="wb-section-head"><div><span className="wb-kicker">1H / 4H K 线与计划价格线</span><h2>四价、后端加权均价与全成本保本价</h2></div><span className="wb-live-label">公共行情 · 只读</span></div><TradingPlanChart candles1h={snapshot.candles1h} candles4h={snapshot.candles4h} timeframe="1H" currentPrice={snapshot.price} plan={chartPlan} plannedRisk={currentRisk} executionRisk={executionRisk} actualFills={chartActualFills} stale={snapshot.stale} connectionStatus={socketConnected ? snapshot.connectionStatus : 'reconnecting'}/></section>
    <RiskDetails risk={currentRisk} plan={chartPlan} executionRisk={executionRisk} record={record}/>
    <ActionsPanel record={record} onChanged={updateAfterAction}/>
    <ReminderPanel reminder={record.activeReminder}/>
    <ExecutionPanel record={record}/>
    <section className="wb-research-entry"><span>需要查看旧方向评分、新闻和三年回测？</span><strong>请使用页面顶部“研究区”入口。它们是实验信号，三年回测未通过，不会改变本计划。</strong></section>
    <footer className="wb-footer"><span>仅使用 OKX 公共和免鉴权行情，不读取账户、不自动下单。</span><span>真实状态、实际成交和最终日志只在你明确确认后记录。</span></footer>
    {toast && <div className="wb-toast" role="status" onAnimationEnd={() => setToast('')}><CheckCircle2/>{toast}</div>}
  </main>
}
