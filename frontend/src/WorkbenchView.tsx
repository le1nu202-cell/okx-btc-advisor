import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, ChevronRight, LoaderCircle, RefreshCw, Save, ShieldAlert, Wifi, WifiOff } from 'lucide-react'
import TradingPlanChart from './components/TradingPlanChart'
import MarketSummaryCard from './components/MarketSummaryCard'
import type { ChartTimeframe } from './chart-adapter'
import { normalizeCandle } from './api'
import { marketAnalysisApi, normalizeMarketAnalysis } from './market-analysis-api'
import type { MarketAnalysis } from './market-analysis-types'
import { workbenchApi } from './workbench-api'
import { ACTION_LABELS, STATE_LABELS, canonicalExecutionState, isTerminalState, legalActionsForState, prefillForAction, priceOrderError, toInputNumber } from './workbench-utils'
import { DEFAULT_PLAN, normalizeTradePlanDraft } from './workbench-types'
import type { ExecutionRisk, FillAction, LiquidationEstimate, RiskCalculation, TradePlanDraft, TradePlanRecord } from './workbench-types'
import type { Candle, MarketSnapshot, MarketTimeframe } from './types'

const EMPTY_STATUS = {
  '1m': { available: false, stale: true, lastAt: null, gapDetected: false },
  '15m': { available: false, stale: true, lastAt: null, gapDetected: false },
  '1H': { available: false, stale: true, lastAt: null, gapDetected: false },
  '4H': { available: false, stale: true, lastAt: null, gapDetected: false },
}
const EMPTY_SNAPSHOT: MarketSnapshot = {
  instrument: 'BTC-USDT-SWAP', price: null, markPrice: null, markPriceTime: null, updatedAt: null,
  stale: true, connectionStatus: 'disconnected', fundingRate: null, fundingTime: null,
  openInterest: null, openInterestTime: null, candles1m: [], candles15m: [], candles1h: [], candles4h: [],
  candleStatus: EMPTY_STATUS,
}
const EMPTY_RECORD: TradePlanRecord = { id: null, state: 'IDLE', plan: null, risk: null, actualFills: {}, activeReminder: null }

const money = (value: number | null | undefined, digits = 2) => value == null || !Number.isFinite(value) ? '—' : new Intl.NumberFormat('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value)
const quantity = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '—' : value.toFixed(8)
const percent = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(2)}%`
const time = (value: number | null | undefined) => value ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(value) : '—'
const connectionLabel = (value: string) => value === 'connected' ? '已连接' : value === 'reconnecting' || value === 'starting' ? '重连中' : value === 'degraded' ? '部分可用' : '未连接'
const normalizedPlan = normalizeTradePlanDraft
const riskMessage = (value: string) => value.replaceAll(['全仓', '可用权益'].join(''), '全仓支持余额基准')
const liquidationAvailable = (value: LiquidationEstimate | null | undefined): value is LiquidationEstimate & { estimatedLiquidationPrice: number } => value?.status === 'AVAILABLE' && value.estimatedLiquidationPrice != null
const liquidationDistanceAvailable = (value: LiquidationEstimate | null | undefined): value is LiquidationEstimate & { distancePercent: number } => value?.distanceStatus === 'AVAILABLE' && value.distancePercent != null
const liquidationPrice = (value: LiquidationEstimate | null | undefined) => liquidationAvailable(value) ? `${money(value.estimatedLiquidationPrice)} USDT` : '不可用'
const liquidationDanger = (value: LiquidationEstimate | null | undefined) => value?.hardStopSequence === 'LIQUIDATION_FIRST' || value?.hardStopSequence === 'OVERLAP_UNSAFE' || ['危险', '可能早于止损强平'].includes(value?.distanceRisk ?? '')
const CANDLE_LIMITS: Record<MarketTimeframe, number> = { '1m': 10_080, '15m': 8_640, '1H': 2_000, '4H': 2_000 }
const isMarketTimeframe = (value: unknown): value is MarketTimeframe => value === '1m' || value === '15m' || value === '1H' || value === '4H'
export const mergeRealtimeCandles = (existing: Candle[], incoming: Candle[], limit: number) => {
  const merged = new Map(existing.map(candle => [candle.timestamp, candle]))
  for (const candle of incoming) {
    const previous = merged.get(candle.timestamp)
    if (previous?.confirm === true && candle.confirm !== true) continue
    merged.set(candle.timestamp, candle)
  }
  return [...merged.values()].sort((left, right) => left.timestamp - right.timestamp).slice(-limit)
}
const withRealtimeCandles = (current: MarketSnapshot, timeframe: MarketTimeframe, incoming: Candle[], gapDetected: boolean): MarketSnapshot => {
  const currentRows = timeframe === '1m' ? current.candles1m : timeframe === '15m' ? current.candles15m : timeframe === '1H' ? current.candles1h : current.candles4h
  const rows = mergeRealtimeCandles(currentRows, incoming, CANDLE_LIMITS[timeframe])
  const latest = rows.at(-1)
  const latestConfirmed = [...rows].reverse().find(candle => candle.confirm)
  const candleStatus = { ...current.candleStatus, [timeframe]: { available: rows.length > 0, stale: false, lastAt: latest?.timestamp ?? null, lastConfirmedAt: latestConfirmed?.timestamp ?? null, gapDetected } }
  if (timeframe === '1m') return { ...current, candles1m: rows, candleStatus }
  if (timeframe === '15m') return { ...current, candles15m: rows, candleStatus }
  if (timeframe === '1H') return { ...current, candles1h: rows, candleStatus }
  return { ...current, candles4h: rows, candleStatus }
}

function Metric({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: string }) {
  return <div className="v05-metric"><span>{label}</span><strong className={tone}>{value}</strong>{hint && <small>{hint}</small>}</div>
}

function NumberField({ label, value, onChange, step = 'any', min = 0, disabled = false, readOnly = false, hint, suffix, required = false }: { label: string; value: number | null; onChange: (value: number | null) => void; step?: string | number; min?: number; disabled?: boolean; readOnly?: boolean; hint?: string; suffix?: string; required?: boolean }) {
  return <label className="v05-field"><span>{label}{hint && <small>{hint}</small>}</span><div><input type="number" min={min} step={step} value={value ?? ''} disabled={disabled} readOnly={readOnly} required={required} onChange={event => onChange(toInputNumber(event.target.value))}/>{suffix && <em>{suffix}</em>}</div></label>
}

function SwitchField({ label, checked, onChange, disabled = false, hint }: { label: string; checked: boolean; onChange: (value: boolean) => void; disabled?: boolean; hint?: string }) {
  return <label className="v05-switch-field"><input type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)}/><span><strong>{label}</strong>{hint && <small>{hint}</small>}</span></label>
}

function PanelTitle({ kicker, title, badge }: { kicker: string; title: string; badge?: ReactNode }) {
  return <div className="v05-panel-title"><div><span>{kicker}</span><h2>{title}</h2></div>{badge}</div>
}

function PlanSimulationPanel({ draft, setDraft, record, risk, saving, onSave }: { draft: TradePlanDraft; setDraft: (plan: TradePlanDraft) => void; record: TradePlanRecord; risk: RiskCalculation | null; saving: boolean; onSave: (event: FormEvent) => void }) {
  const state = canonicalExecutionState(record.state)
  const editable = !record.id || state === 'PLANNED' || isTerminalState(state)
  const orderError = priceOrderError(draft)
  const set = <K extends keyof TradePlanDraft>(key: K, value: TradePlanDraft[K]) => setDraft({ ...draft, [key]: value })
  const setEquity = (value: number | null) => {
    const equity = value ?? 0
    setDraft({
      ...draft,
      equity,
      crossAvailableEquity: draft.crossEquityMode === 'FOLLOW_EQUITY' ? equity : draft.crossAvailableEquity,
    })
  }
  const setCrossEquityMode = (mode: TradePlanDraft['crossEquityMode']) => setDraft({
    ...draft,
    crossEquityMode: mode,
    crossAvailableEquity: mode === 'FOLLOW_EQUITY' ? draft.equity : draft.crossAvailableEquity,
  })
  const setTakerFee = (value: number | null) => {
    const takerFeeBps = value ?? 0
    setDraft({
      ...draft,
      takerFeeBps,
      liquidationFeeBps: draft.liquidationFeeMode === 'FOLLOW_TAKER' ? takerFeeBps : draft.liquidationFeeBps,
    })
  }
  const setLiquidationFeeMode = (mode: TradePlanDraft['liquidationFeeMode']) => setDraft({
    ...draft,
    liquidationFeeMode: mode,
    liquidationFeeBps: mode === 'FOLLOW_TAKER' ? draft.takerFeeBps : draft.liquidationFeeBps,
  })
  const crossEquityMismatch = draft.crossEquityMode === 'MANUAL' && Math.abs(draft.crossAvailableEquity - draft.equity) > 1e-9
  const liquidationFeeMismatch = draft.liquidationFeeMode === 'MANUAL' && Math.abs(draft.liquidationFeeBps - draft.takerFeeBps) > 1e-9
  const saveLabel = record.id && state === 'PLANNED' ? '更新当前计划' : isTerminalState(state) ? '创建下一笔计划' : '创建交易计划'
  return <section className="v05-panel v05-plan-panel">
    <PanelTitle kicker="计划模拟" title="四价与仓位计划" badge={<span className="v05-badge planned">计划</span>}/>
    {!editable && <div className="v05-lock"><ShieldAlert/>已有人工确认成交，计划已锁定。</div>}
    <form onSubmit={onSave}>
      <div className="v05-plan-prices">
        <label className="v05-field"><span>方向</span><div><select value={draft.direction} disabled={!editable} onChange={event => set('direction', event.target.value as TradePlanDraft['direction'])}><option value="LONG">做多 LONG</option><option value="SHORT">做空 SHORT</option></select></div></label>
        <NumberField label="初始开仓价" value={draft.initialEntryPrice} onChange={value => set('initialEntryPrice', value)} disabled={!editable} required suffix="USDT"/>
        <NumberField label="加仓价 / 第一压力位" value={draft.addPrice} onChange={value => set('addPrice', value)} disabled={!editable} required suffix="USDT"/>
        <NumberField label="硬止损价 / 第二压力位" value={draft.stopPrice} onChange={value => set('stopPrice', value)} disabled={!editable} required suffix="USDT"/>
        <NumberField label="止盈价" value={draft.takeProfitPrice} onChange={value => set('takeProfitPrice', value)} disabled={!editable} required suffix="USDT"/>
      </div>
      <div className="v05-plan-sizing">
        <NumberField label="账户权益" value={draft.equity} onChange={setEquity} disabled={!editable} required suffix="USDT"/>
        <NumberField label="杠杆" value={draft.leverage} onChange={value => set('leverage', value ?? 0)} disabled={!editable} min={1} step="1" required suffix="x"/>
        <NumberField label="初始保证金比例" value={draft.initialMarginPercent} onChange={value => set('initialMarginPercent', value ?? 0)} disabled={!editable || draft.sizingMode === 'MAX_LOSS'} required suffix="%"/>
        <NumberField label="加仓倍数" value={draft.addMultiplier} onChange={value => set('addMultiplier', value ?? 0)} disabled={!editable} required suffix="倍"/>
        <NumberField label="最大允许亏损" value={draft.maxLossUsdt} onChange={value => setDraft({ ...draft, maxLossUsdt: value, lossLimitUsdt: value })} disabled={!editable} required={draft.sizingMode === 'MAX_LOSS'} suffix="USDT"/>
      </div>
      <details className="v05-form-advanced">
        <summary>仓位、强平参数与成本设置 <ChevronRight/></summary>
        <div className="v05-advanced-grid">
          <label className="v05-field"><span>保证金模式</span><div><select value={draft.marginMode} disabled={!editable} onChange={event => set('marginMode', event.target.value as TradePlanDraft['marginMode'])}><option value="CROSS">全仓 CROSS</option><option value="ISOLATED">逐仓 ISOLATED</option></select></div></label>
          <label className="v05-field"><span>仓位计算方式</span><div><select value={draft.sizingMode} disabled={!editable} onChange={event => set('sizingMode', event.target.value as TradePlanDraft['sizingMode'])}><option value="MARGIN">按初始保证金</option><option value="MAX_LOSS">按最大亏损反推</option></select></div></label>
          <NumberField label="初始保证金金额（可选）" value={draft.initialMargin} onChange={value => set('initialMargin', value)} disabled={!editable || draft.sizingMode === 'MAX_LOSS'} suffix="USDT"/>
          <label className="v05-field"><span>全仓支持余额基准模式</span><div><select value={draft.crossEquityMode} disabled={!editable || draft.marginMode !== 'CROSS'} onChange={event => setCrossEquityMode(event.target.value as TradePlanDraft['crossEquityMode'])}><option value="FOLLOW_EQUITY">跟随账户权益 FOLLOW_EQUITY</option><option value="MANUAL">手动输入 MANUAL</option></select></div></label>
          <NumberField label="全仓支持余额基准" hint={draft.crossEquityMode === 'FOLLOW_EQUITY' ? '跟随账户权益' : '手动估计'} value={draft.crossAvailableEquity} onChange={value => set('crossAvailableEquity', value ?? 0)} disabled={!editable || draft.marginMode !== 'CROSS'} readOnly={draft.crossEquityMode === 'FOLLOW_EQUITY'} suffix="USDT"/>
          <div className="v05-input-explanation"><strong>“全仓支持余额基准”不是 OKX 页面显示的“可用余额”。</strong><span>这是开仓前用于支撑当前单一全仓仓位的账户余额基准，不是扣除仓位或挂单占用后的可用保证金。当前仓位的未实现盈亏由强平公式单独计算。</span><span>默认跟随账户权益；只有你明确知道需要采用不同基准时才切换为手动。其他仓位、占用保证金的挂单、借币、资金费、已实现盈亏或账户余额变化，都会使实际强平结果不同。</span></div>
          {draft.crossEquityMode === 'MANUAL' && <div className="v05-input-mismatch" role="status">{crossEquityMismatch ? '手动全仓支持余额基准与账户权益不一致：' : '正在使用手动全仓支持余额基准：'}计算会保留并使用手动值，不会随账户权益自动覆盖。</div>}
          <NumberField label="追加保证金" value={draft.extraMarginUsdt} onChange={value => set('extraMarginUsdt', value ?? 0)} disabled={!editable} suffix="USDT"/>
          <label className="v05-field"><span>维持保证金参数</span><div><select value={draft.maintenanceMarginSource} disabled={!editable} onChange={event => set('maintenanceMarginSource', event.target.value as TradePlanDraft['maintenanceMarginSource'])}><option value="AUTO">OKX 公共参数 AUTO</option><option value="MANUAL">手动输入 MANUAL</option></select></div></label>
          <NumberField label="维持保证金率" value={draft.maintenanceMarginRate} onChange={value => set('maintenanceMarginRate', value)} disabled={!editable || draft.maintenanceMarginSource !== 'MANUAL'} step="0.0001"/>
          <NumberField label="维持保证金固定额" value={draft.maintenanceMarginFixedUsdt} onChange={value => set('maintenanceMarginFixedUsdt', value ?? 0)} disabled={!editable || draft.maintenanceMarginSource !== 'MANUAL'} suffix="USDT"/>
          <label className="v05-field"><span>强平费率模式</span><div><select value={draft.liquidationFeeMode} disabled={!editable} onChange={event => setLiquidationFeeMode(event.target.value as TradePlanDraft['liquidationFeeMode'])}><option value="FOLLOW_TAKER">跟随 Taker 费率 FOLLOW_TAKER</option><option value="MANUAL">手动输入 MANUAL</option></select></div></label>
          <NumberField label="估算强平费率" hint={draft.liquidationFeeMode === 'FOLLOW_TAKER' ? '跟随 Taker' : '手动估计'} value={draft.liquidationFeeBps} onChange={value => set('liquidationFeeBps', value ?? 0)} disabled={!editable} readOnly={draft.liquidationFeeMode === 'FOLLOW_TAKER'} suffix="bp"/>
          <NumberField label="Maker 手续费" value={draft.makerFeeBps} onChange={value => set('makerFeeBps', value ?? 0)} disabled={!editable} suffix="bp"/>
          <NumberField label="Taker 手续费" value={draft.takerFeeBps} onChange={setTakerFee} disabled={!editable} suffix="bp"/>
          {draft.liquidationFeeMode === 'MANUAL' && <div className="v05-input-mismatch" role="status">{liquidationFeeMismatch ? '手动估算强平费率与 Taker 手续费不一致：' : '正在使用手动估算强平费率：'}计算会保留并使用手动值，不会随 Taker 费率自动覆盖。</div>}
          <NumberField label="预计滑点" value={draft.slippageBps} onChange={value => set('slippageBps', value ?? 0)} disabled={!editable} suffix="bp"/>
          <NumberField label="低风险上限" value={draft.riskLowMaxPercent} onChange={value => set('riskLowMaxPercent', value ?? 0)} disabled={!editable} suffix="%"/>
          <NumberField label="中风险上限" value={draft.riskMediumMaxPercent} onChange={value => set('riskMediumMaxPercent', value ?? 0)} disabled={!editable} suffix="%"/>
          <NumberField label="高风险上限" value={draft.riskHighMaxPercent} onChange={value => set('riskHighMaxPercent', value ?? 0)} disabled={!editable} suffix="%"/>
          <SwitchField label="计入未结资金费" checked={draft.includeUnsettledFunding} onChange={value => set('includeUnsettledFunding', value)} disabled={!editable}/>
          <NumberField label="未结资金费" value={draft.unsettledFundingUsdt} onChange={value => set('unsettledFundingUsdt', value ?? 0)} disabled={!editable || !draft.includeUnsettledFunding} suffix="USDT"/>
          <SwitchField label="确认无其他仓位及占用保证金挂单" checked={draft.assumeNoOtherPositions} onChange={value => set('assumeNoOtherPositions', value)} disabled={!editable} hint="全仓估算的重要前提；不满足时不应输出强平价"/>
          <div className="v05-liquidation-assumptions"><strong>强平估算假设</strong><span>只存在 BTC-USDT-SWAP 这一项全仓仓位；没有其他全仓或逐仓仓位影响账户权益；没有待成交挂单占用保证金；没有未知账户级费用或资产折算；未结资金费仅在你主动勾选并填写后计入。实际强平以 OKX 标记价格和账户页面为准。本工具没有读取账户来核实这些条件。</span></div>
          <label className="v05-field v05-notes"><span>备注（可选）</span><textarea value={draft.notes} disabled={!editable} maxLength={4000} placeholder="入场理由、失效条件或纪律提醒" onChange={event => set('notes', event.target.value)}/></label>
        </div>
      </details>
      {orderError && <div className="v05-error" role="alert"><AlertTriangle/>{orderError}</div>}
      {risk?.errors.map(error => <div className="v05-error" role="alert" key={error}><AlertTriangle/>{error}</div>)}
      {editable && <button className="v05-primary" type="submit" disabled={saving || Boolean(orderError) || risk?.valid === false}>{saving ? <LoaderCircle className="spin"/> : <Save/>}{saveLabel}</button>}
    </form>
    <div className="v05-summary-grid planned">
      <Metric label="计划总数量" value={`${quantity(risk?.totalQuantityBtc)} BTC`}/>
      <Metric label="计划保证金" value={`${money(risk?.totalMargin)} USDT`}/>
      <Metric label="计划名义仓位" value={`${money(risk?.totalNotional)} USDT`}/>
      <Metric label="计划加权均价" value={`${money(risk?.averageEntryPrice)} USDT`}/>
      <Metric label="计划全成本保本价" value={`${money(risk?.fullCostBreakevenPrice ?? risk?.allInBreakevenPrice)} USDT`}/>
      <Metric label="计划止损净亏损" value={`${money(risk?.netLossAtStop)} USDT`}/>
      <Metric label="计划估算强平价" value={liquidationPrice(risk?.liquidationScenarios?.afterAdd)} hint="基于完成一次计划加仓的情景"/>
    </div>
  </section>
}

function ActualExecutionPanel({ record }: { record: TradePlanRecord }) {
  const fills = record.actualFills ?? {}
  const execution = record.executionSummary ?? record.execution
  const executionRisk = record.executionRisk
  const hasFills = Object.values(fills).some(Boolean)
  const fillLabels = { initial: '初始开仓', add: '加仓', reduce: '部分减仓', exit: '最终退出' } as const
  return <section className="v05-panel v05-actual-panel">
    <PanelTitle kicker="实际成交" title="人工确认的真实记录" badge={<span className="v05-badge actual">实际</span>}/>
    {!hasFills ? <div className="v05-actual-empty">尚无人工确认成交。实际区域不会使用计划数据代替。</div> : <>
      <div className="v05-summary-grid actual">
        <Metric label="实际累计开仓数量" value={`${quantity(execution?.openedQuantityBtc)} BTC`}/>
        <Metric label="实际剩余数量" value={`${quantity(execution?.remainingQuantityBtc)} BTC`}/>
        <Metric label="实际加权均价" value={`${money(executionRisk?.averageEntryPrice ?? execution?.averageEntryPrice)} USDT`}/>
        <Metric label="实际已实现盈亏" value={`${money(execution?.realizedNetPnl)} USDT`} tone={(execution?.realizedNetPnl ?? 0) < 0 ? 'danger' : 'positive'}/>
        <Metric label="实际手续费" value={`${money(execution?.incurredFees ?? execution?.fees)} USDT`}/>
        <Metric label="实际全成本保本价" value={`${money(executionRisk?.fullCostBreakevenPrice)} USDT`}/>
        <Metric label="实际到止损总盈亏" value={`${money(executionRisk?.totalNetPnlIfStopped)} USDT`} tone={(executionRisk?.totalNetPnlIfStopped ?? 0) < 0 ? 'danger' : undefined}/>
        <Metric label="实际估算强平价" value={liquidationPrice(executionRisk?.liquidationEstimate)} hint="只使用人工确认成交和实际剩余量"/>
      </div>
      <div className="v05-fill-grid">{Object.entries(fills).map(([key, fill]) => fill && <div key={key}><span>{fillLabels[key as keyof typeof fillLabels] ?? key}</span><strong>{money(fill.price)} USDT</strong><small>{quantity(fill.quantityBtc)} BTC · {time(fill.confirmedAt)}</small></div>)}</div>
      <div className="v05-realized">已实现净盈亏 <strong className={(execution?.realizedNetPnl ?? 0) >= 0 ? 'positive' : 'danger'}>{money(execution?.realizedNetPnl)} USDT</strong></div>
    </>}
  </section>
}

function ManualActions({ record, onChanged }: { record: TradePlanRecord; onChanged: (record: TradePlanRecord, message: string) => void }) {
  const state = canonicalExecutionState(record.state)
  const actions = legalActionsForState(state)
  const fillActions = actions.filter((action): action is FillAction => action !== 'CANCEL')
  const [action, setAction] = useState<FillAction | null>(fillActions[0] ?? null)
  const [price, setPrice] = useState<number | null>(null)
  const [quantityBtc, setQuantityBtc] = useState<number | null>(null)
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const next = fillActions.includes(action as FillAction) ? action : fillActions[0] ?? null
    setAction(next)
    if (!next || !record.plan) {
      setPrice(null)
      setQuantityBtc(null)
      return
    }
    const value = prefillForAction(next, normalizedPlan(record.plan), record.risk, record)
    setPrice(value.price || null)
    setQuantityBtc(value.quantityBtc || null)
  }, [record.id, record.state, fillActions.join('|')])

  const chooseAction = (next: FillAction) => {
    setAction(next)
    if (!record.plan) return
    const value = prefillForAction(next, normalizedPlan(record.plan), record.risk, record)
    setPrice(value.price || null)
    setQuantityBtc(value.quantityBtc || null)
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!record.id || !action || price == null || quantityBtc == null) return
    setSubmitting(true); setError('')
    try {
      const response = await workbenchApi.action(record.id, { action, price, quantityBtc, note })
      onChanged(response.plan, `${ACTION_LABELS[action]}已记录`)
      setNote('')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '人工确认失败') }
    finally { setSubmitting(false) }
  }
  const cancel = async () => {
    if (!record.id || !window.confirm('确认取消这笔尚未开仓的计划？')) return
    setSubmitting(true); setError('')
    try { const response = await workbenchApi.action(record.id, { action: 'CANCEL' }); onChanged(response.plan, '计划已取消') }
    catch (reason) { setError(reason instanceof Error ? reason.message : '取消失败') }
    finally { setSubmitting(false) }
  }

  return <section className="v05-panel v05-actions-panel">
    <PanelTitle kicker="人工操作" title="只显示当前状态允许的动作" badge={<span className="v05-badge actual"><CheckCircle2/>人工确认</span>}/>
    {!record.id ? <div className="v05-empty">先创建计划，随后才能人工确认真实成交。</div>
      : isTerminalState(state) ? <div className="v05-terminal"><CheckCircle2/>本计划已结束，不再显示加仓或减仓动作。</div>
        : !fillActions.length ? <div className="v05-empty">当前没有需要填写成交价格和数量的动作。</div>
          : <>
            <div className="v05-action-tabs">{fillActions.map(value => <button key={value} type="button" className={action === value ? 'active' : ''} onClick={() => chooseAction(value)}>{ACTION_LABELS[value]}</button>)}</div>
            <form className="v05-action-form" onSubmit={submit}>
              <NumberField label="实际成交价格" value={price} onChange={setPrice} required suffix="USDT"/>
              <NumberField label="实际成交 BTC 数量" value={quantityBtc} onChange={setQuantityBtc} required step="any" suffix="BTC"/>
              <label className="v05-field"><span>本次确认备注</span><div><input value={note} maxLength={2000} onChange={event => setNote(event.target.value)}/></div></label>
              <button className="v05-primary" disabled={submitting || price == null || quantityBtc == null}>{submitting ? <LoaderCircle className="spin"/> : <CheckCircle2/>}{action ? ACTION_LABELS[action] : '确认'}</button>
            </form>
          </>}
    {actions.includes('CANCEL') && <button className="v05-cancel" type="button" onClick={cancel} disabled={submitting}>取消尚未开仓的计划</button>}
    {error && <div className="v05-error" role="alert"><AlertTriangle/>{error}</div>}
  </section>
}

function LiquidationScenario({ label, value }: { label: string; value: LiquidationEstimate | null | undefined }) {
  return <div className="v05-liquidation-row"><strong>{label}</strong>{liquidationAvailable(value) ? <>
    <span>{money(value.estimatedLiquidationPrice)} USDT</span>
    <small>距标记价 {percent(value.distancePercent)} · 止损距强平 {percent(value.hardStopBufferPercent)} · {value.hardStopSequence === 'STOP_FIRST' ? '预计止损先触发' : value.hardStopSequence === 'UNAVAILABLE' ? '止损顺序不可用' : '强平可能早于或紧邻止损'}{value.changeFromPreviousUsdt == null ? '' : ` · 较上一情景 ${value.changeFromPreviousUsdt >= 0 ? '+' : ''}${money(value.changeFromPreviousUsdt)} USDT`} · {value.parameterSource} · 参数更新 {time(value.parametersUpdatedAt)}</small>
  </> : <><span>不可用</span><small>{value?.errors?.[0] ?? value?.warnings?.[0] ?? '后端没有返回可用估算'}</small></>}</div>
}

function AdvancedDetails({ risk, record }: { risk: RiskCalculation | null; record: TradePlanRecord }) {
  const execution = record.executionSummary ?? record.execution
  const executionRisk = record.executionRisk
  return <details className="v05-advanced-details">
    <summary><span><strong>高级风险与成本详情</strong><small>手续费、滑点、情景强平和计算假设</small></span><ChevronRight/></summary>
    <div className="v05-advanced-content">
      <div className="v05-detail-grid">
        <Metric label="计划初始保证金" value={`${money(risk?.initialMargin)} USDT`}/>
        <Metric label="计划加仓保证金" value={`${money(risk?.addMargin)} USDT`}/>
        <Metric label="计划总名义仓位" value={`${money(risk?.totalNotional)} USDT`}/>
        <Metric label="计划开仓费" value={`${money(risk?.openingFee)} USDT`}/>
        <Metric label="计划加仓费" value={`${money(risk?.addFee)} USDT`}/>
        <Metric label="计划止损总费用" value={`${money(risk?.totalFeesAtStop)} USDT`}/>
        <Metric label="计划止损滑点" value={`${money(risk?.estimatedSlippageAtStop)} USDT`}/>
        <Metric label="风险回报比" value={risk?.riskRewardRatio == null ? '—' : `1 : ${risk.riskRewardRatio.toFixed(2)}`}/>
        <Metric label="实际累计费用" value={`${money(execution?.incurredFees ?? execution?.fees)} USDT`}/>
        <Metric label="实际累计滑点" value={`${money(execution?.slippageUsdt)} USDT`}/>
        <Metric label="实际已实现净盈亏" value={`${money(execution?.realizedNetPnl)} USDT`}/>
        <Metric label="实际剩余止损净亏损" value={`${money(executionRisk?.remainingNetLossAtStop ?? executionRisk?.netLossAtStop)} USDT`}/>
      </div>
      <div className="v05-liquidation-scenarios">
        <h3>计划强平情景</h3>
        <LiquidationScenario label="仅初始开仓" value={risk?.liquidationScenarios?.initialOnly}/>
        <LiquidationScenario label="完成计划加仓" value={risk?.liquidationScenarios?.afterAdd}/>
        <LiquidationScenario label="计划减仓后" value={risk?.liquidationScenarios?.afterPlannedReduce}/>
        {record.executionRisk?.liquidationEstimate && <LiquidationScenario label="当前实际仓位" value={record.executionRisk.liquidationEstimate}/>}
      </div>
      {risk?.adverseMoveLosses?.length ? <div className="v05-adverse"><h3>计划加仓后的反向移动</h3>{risk.adverseMoveLosses.map(row => <span key={row.movePercent}>反向 {row.movePercent}%：{money(row.lossUsdt)} USDT（权益 {percent(row.equityPercent)}）</span>)}</div> : null}
      {[...(risk?.warnings ?? []), ...(risk?.assumptions ?? [])].map(message => <div className="v05-note" key={message}>{riskMessage(message)}</div>)}
    </div>
  </details>
}

export default function WorkbenchView({ onOpenMarketAnalysis }: { onOpenMarketAnalysis?: () => void } = {}) {
  const [snapshot, setSnapshot] = useState<MarketSnapshot>(EMPTY_SNAPSHOT)
  const [record, setRecord] = useState<TradePlanRecord>(EMPTY_RECORD)
  const [draft, setDraft] = useState<TradePlanDraft>(DEFAULT_PLAN)
  const [risk, setRisk] = useState<RiskCalculation | null>(null)
  const [marketAnalysis, setMarketAnalysis] = useState<MarketAnalysis | null>(null)
  const [timeframe, setTimeframe] = useState<ChartTimeframe>('1H')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [socketConnected, setSocketConnected] = useState(false)
  const [serviceError, setServiceError] = useState('')
  const [calculationError, setCalculationError] = useState('')
  const [toast, setToast] = useState('')
  const latestDraft = useRef(draft)

  const applyRecord = useCallback((next: TradePlanRecord, syncDraft = false) => {
    const normalized = { ...next, plan: next.plan ? normalizedPlan(next.plan) : null }
    setRecord(normalized)
    if (syncDraft && normalized.plan) {
      setDraft(normalized.plan)
      setRisk(normalized.risk)
    }
  }, [])
  const load = useCallback(async () => {
    setLoading(true); setServiceError('')
    try {
      const [market, current] = await Promise.all([workbenchApi.snapshot(), workbenchApi.current()])
      setSnapshot(market); applyRecord(current, true)
    } catch (reason) { setServiceError(reason instanceof Error ? reason.message : '工作台加载失败') }
    finally { setLoading(false) }
  }, [applyRecord])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    let active = true
    marketAnalysisApi.current().then(value => { if (active) setMarketAnalysis(value) }).catch(() => undefined)
    return () => { active = false }
  }, [])
  useEffect(() => {
    latestDraft.current = draft
    const timer = window.setTimeout(() => {
      workbenchApi.calculate(draft).then(value => {
        if (latestDraft.current === draft) { setRisk(value); setCalculationError('') }
      }).catch(reason => { if (latestDraft.current === draft) setCalculationError(reason instanceof Error ? reason.message : '风险计算失败') })
    }, 250)
    return () => window.clearTimeout(timer)
  }, [draft])
  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(''), 4_000)
    return () => window.clearTimeout(timer)
  }, [toast])
  useEffect(() => {
    let socket: WebSocket | null = null
    let retry = 0
    let retryTimer = 0
    let stopped = false
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
            const markPriceTime = Number(message.markPriceTime)
            setSnapshot(current => ({
              ...current,
              price: typeof message.price === 'number' ? message.price : current.price,
              markPrice: typeof message.markPrice === 'number' ? message.markPrice : current.markPrice,
              markPriceTime: Number.isFinite(markPriceTime) ? markPriceTime : current.markPriceTime,
              updatedAt: Number.isFinite(tickerTime) ? tickerTime : current.updatedAt,
              connectionStatus: typeof message.connectionStatus === 'string' ? message.connectionStatus : current.connectionStatus,
              stale: Number.isFinite(tickerTime) ? Date.now() - tickerTime > 30_000 : current.stale,
            }))
          }
          if (message.type === 'candle' && isMarketTimeframe(message.timeframe) && Array.isArray(message.candles)) {
            const candles = message.candles.map(normalizeCandle).filter((candle): candle is Candle => candle != null)
            if (candles.length) setSnapshot(current => withRealtimeCandles(current, message.timeframe as MarketTimeframe, candles, Boolean(message.gapDetected)))
          }
          if (message.type === 'marketAnalysis' && message.analysis && typeof message.analysis === 'object') {
            setMarketAnalysis(normalizeMarketAnalysis(message.analysis))
          }
          if (message.type === 'riskParameters') {
            workbenchApi.calculate(latestDraft.current).then(value => setRisk(value)).catch(() => undefined)
            workbenchApi.current().then(next => applyRecord(next)).catch(() => undefined)
          }
          if (message.type === 'tradePlanAlert') {
            if (message.plan && typeof message.plan === 'object') applyRecord(message.plan as TradePlanRecord)
            else workbenchApi.current().then(next => applyRecord(next)).catch(() => undefined)
            if (typeof message.message === 'string') setToast(message.message)
          }
          if (message.type === 'tradePlanUpdate' && message.plan && typeof message.plan === 'object') {
            const next = message.plan as TradePlanRecord
            applyRecord(next)
            if (canonicalExecutionState(next.state) === 'PLANNED') setRisk(next.risk)
          }
        } catch { /* 下一次 REST 同步会修复损坏帧。 */ }
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
      const state = canonicalExecutionState(record.state)
      const next = record.id && state === 'PLANNED' ? await workbenchApi.update(record.id, draft) : await workbenchApi.create(draft)
      applyRecord(next, true)
      setToast(record.id && state === 'PLANNED' ? '计划已更新' : '计划已创建，等待人工确认真实开仓')
    } catch (reason) { setServiceError(reason instanceof Error ? reason.message : '保存计划失败') }
    finally { setSaving(false) }
  }

  const executionState = canonicalExecutionState(record.state)
  const terminal = isTerminalState(executionState)
  const activeLockedPlan = Boolean(record.id && executionState !== 'PLANNED' && !terminal && record.plan)
  const displayPlan = activeLockedPlan ? normalizedPlan(record.plan) : draft
  const currentRisk = activeLockedPlan ? record.risk : risk
  const hasActualFills = Object.values(record.actualFills ?? {}).some(Boolean)
  const executionRisk: ExecutionRisk | null = hasActualFills ? record.executionRisk ?? null : null
  const execution = record.executionSummary ?? record.execution
  const plannedLiquidation = currentRisk?.liquidationScenarios?.afterAdd ?? currentRisk?.liquidationScenarios?.initialOnly
  const activeLiquidation = executionRisk?.liquidationEstimate ?? plannedLiquidation
  const actualRiskPercent = executionRisk?.maxLossEquityPercent
  const equityPercent = actualRiskPercent ?? currentRisk?.maxLossEquityPercent
  const actualStopPnl = executionRisk?.totalNetPnlIfStopped
  const stopLoss = hasActualFills ? Math.abs(Math.min(0, actualStopPnl ?? -(executionRisk?.netLossAtStop ?? 0))) : currentRisk?.netLossAtStop
  const currentPrice = snapshot.price ?? snapshot.markPrice
  const updatedAt = snapshot.updatedAt ?? snapshot.markPriceTime
  const marketConnected = socketConnected && snapshot.connectionStatus === 'connected' && !snapshot.stale
  const startingNextPlan = Boolean(terminal && record.plan && JSON.stringify(draft) !== JSON.stringify(record.plan))
  const chartActualFills = startingNextPlan ? {} : record.actualFills ?? {}
  const chartExecutionRisk = startingNextPlan ? null : executionRisk
  const displayedRiskLevel = hasActualFills ? executionRisk?.riskLevel : currentRisk?.riskLevel
  const danger = Boolean(currentRisk?.lossLimitExceeded || displayedRiskLevel === '极高' || liquidationDanger(activeLiquidation))
  const reminder = record.activeReminder
  const updateAfterAction = (next: TradePlanRecord, message: string) => { applyRecord(next, true); setToast(message) }
  const timeframeStatus = snapshot.candleStatus?.[timeframe as MarketTimeframe]

  return <main className="v05-workbench">
    {serviceError && <div className="v05-service-error" role="alert"><AlertTriangle/><span>{serviceError}</span><button onClick={load}>重试</button></div>}

    <section className="v05-status-strip" aria-label="当前交易状态">
      <div><span>当前 BTC 价格</span><strong>{currentPrice == null ? '—' : `$${money(currentPrice)}`}</strong><small>标记价 {money(snapshot.markPrice)} USDT</small></div>
      <div><span>当前周期</span><strong>{timeframe}</strong><small>{timeframeStatus?.gapDetected ? '检测到缺口' : timeframeStatus?.stale ? '数据过期' : '可用'}</small></div>
      <div className={marketConnected ? 'connected' : 'disconnected'}><span>公共行情连接</span><strong>{marketConnected ? <Wifi/> : <WifiOff/>}{marketConnected ? '已连接' : connectionLabel(socketConnected ? snapshot.connectionStatus : 'reconnecting')}</strong></div>
      <div><span>更新时间</span><strong>{time(updatedAt)}</strong><small>{updatedAt ? `${Math.max(0, Math.round((Date.now() - updatedAt) / 1000))} 秒前` : '暂无时间'}</small></div>
      <div><span>实际执行状态</span><strong>{STATE_LABELS[executionState]}</strong></div>
      <div className={reminder ? 'reminder' : ''}><span>当前行情提醒</span><strong>{reminder?.message ?? '暂无提醒'}</strong>{reminder && <small>{money(reminder.price)} USDT · {time(reminder.createdAt)}</small>}</div>
      <button className="v05-refresh" aria-label="刷新行情和当前计划" onClick={load} disabled={loading}><RefreshCw className={loading ? 'spin' : ''}/></button>
    </section>

    <section className="v05-core-grid" aria-label="四个核心风险指标">
      <div className={`v05-core-card ${danger ? 'danger' : ''}`}><span>{hasActualFills ? '实际止损净亏损' : '计划止损净亏损'}</span><strong>{money(stopLoss)} USDT</strong><small>包含后端费用与滑点估算</small></div>
      <div className={`v05-core-card ${danger ? 'danger' : ''}`}><span>{hasActualFills ? '实际风险占权益' : '计划风险占权益'}</span><strong>{percent(equityPercent)} · {displayedRiskLevel ?? '等待计算'}</strong><small>当前阈值：{displayPlan.riskLowMaxPercent}% / {displayPlan.riskMediumMaxPercent}% / {displayPlan.riskHighMaxPercent}%</small></div>
      <div className={`v05-core-card ${liquidationDanger(activeLiquidation) ? 'danger' : ''}`}><span>{hasActualFills ? '实际估算强平价' : '计划估算强平价'}</span><strong>{liquidationAvailable(activeLiquidation) ? `${money(activeLiquidation.estimatedLiquidationPrice)} USDT` : '不可用'}</strong><small>{liquidationDistanceAvailable(activeLiquidation) ? `距标记价 ${percent(activeLiquidation.distancePercent)} · ${activeLiquidation.distanceRisk}` : activeLiquidation?.warnings?.find(message => message.includes('标记价格')) ?? activeLiquidation?.errors?.[0] ?? '标记价距离不可用'}</small><small>参数 {activeLiquidation?.parameterSource ?? 'UNAVAILABLE'} · 更新 {time(activeLiquidation?.parametersUpdatedAt)}</small></div>
      <div className="v05-core-card actual"><span>{hasActualFills ? '实际剩余数量' : '计划总数量'}</span><strong>{quantity(hasActualFills ? execution?.remainingQuantityBtc : currentRisk?.totalQuantityBtc)} BTC</strong><small>{hasActualFills ? '来自人工确认成交' : '尚无实际成交，明确显示计划量'}</small></div>
    </section>

    <MarketSummaryCard analysis={marketAnalysis} onOpen={() => onOpenMarketAnalysis?.()}/>

    <p className="v05-estimate-disclaimer">本工具没有读取OKX账户，强平价为基于当前输入和公开规则的估算，以OKX实际显示为准。</p>
    <p className="v05-estimate-assumptions">估算假设：只存在 BTC-USDT-SWAP 这一项全仓仓位；没有其他全仓或逐仓仓位影响账户权益；没有待成交挂单占用保证金；没有未知账户级费用或资产折算。实际强平以 OKX 标记价格和账户页面为准；工具未读取账户来核实这些条件。</p>

    {liquidationDanger(activeLiquidation) && <div className="v05-critical-warning" role="alert"><AlertTriangle/>按当前估算，仓位可能在计划止损生效前进入强平区域。</div>}

    {calculationError && <div className="v05-error v05-global-error" role="alert"><AlertTriangle/>{calculationError}</div>}

    <section className="v05-panel v05-chart-panel">
      <PanelTitle kicker="主图" title="四周期蜡烛图与计划 / 实际价格线" badge={<span className="v05-badge readonly">公共行情 · 只读</span>}/>
      <TradingPlanChart
        candles1m={snapshot.candles1m} candles15m={snapshot.candles15m} candles1h={snapshot.candles1h} candles4h={snapshot.candles4h}
        timeframe={timeframe} onTimeframeChange={setTimeframe} currentPrice={currentPrice} plan={displayPlan}
        plannedRisk={currentRisk} executionRisk={chartExecutionRisk} actualFills={chartActualFills}
        stale={snapshot.stale} connectionStatus={socketConnected ? snapshot.connectionStatus : 'reconnecting'} candleStatus={snapshot.candleStatus}
      />
      <p className="v05-candle-window-note"><strong>本地保留窗口：</strong>1m 最近 7 天（最多 10,080 根）；15m 最近 90 天（最多 8,640 根）；1H、4H 保留已同步的研究历史，不做滚动裁剪。<strong>当前首屏加载窗口上限：</strong>1m 720 根、15m 672 根、1H 200 根、4H 200 根；冷启动时可能更少，随后通过公共 WebSocket 增量更新。当前未实现向左分页加载更早数据。</p>
    </section>

    <section className="v05-comparison" aria-label="计划模拟与实际成交对比">
      <PlanSimulationPanel draft={draft} setDraft={setDraft} record={record} risk={currentRisk} saving={saving} onSave={save}/>
      <ActualExecutionPanel record={record}/>
    </section>

    <section className="v05-workflow-grid">
      <ManualActions record={record} onChanged={updateAfterAction}/>
      <aside className="v05-panel v05-risk-explanation">
        <PanelTitle kicker="风险解释" title="损失比例与强平距离是两套指标"/>
        <div className="v05-risk-tier-grid">
          <div><strong>低风险</strong><span>单笔止损对账户影响较小；连续亏损仍需控制；不代表交易胜率更高。</span></div>
          <div><strong>中风险</strong><span>一次止损会产生明显回撤；连续两三次止损应缩小仓位；不适合临时扩大止损。</span></div>
          <div><strong>高风险</strong><span>会明显影响后续交易空间并可能吃掉多次小额盈利；加仓前须重查强平距离和亏损金额。</span></div>
          <div><strong>极高风险</strong><span>单次失败可能造成严重回撤；高杠杆下滑点或止损失效风险更高；这里只警告，不会替你修改计划。</span></div>
        </div>
        <p>风险等级只描述“到硬止损时预计损失占权益的比例”，不代表行情成功概率，也不构成盈利保证。</p>
        <p><strong>强平距离独立判断：</strong>估算强平价、距离风险和硬止损先后顺序只渲染后端结果；硬止损不等于交易所一定能在强平前成交。</p>
        {activeLiquidation?.warnings?.map(message => <div className="v05-warning" key={message}><AlertTriangle/>{riskMessage(message)}</div>)}
      </aside>
    </section>

    <AdvancedDetails risk={currentRisk} record={record}/>
    <footer className="v05-footer">仅使用 OKX 公共和免鉴权行情；不读取账户、不自动下单。实际状态与成交记录只在你明确确认后改变。</footer>
    {toast && <div className="v05-toast" role="status"><CheckCircle2/>{toast}</div>}
  </main>
}
