import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Download, LoaderCircle, RefreshCw, Trash2 } from 'lucide-react'
import TradeEquityChart from './components/TradeEquityChart'
import { actualPrice, actualQuantity, backendEquityCurve, DEFAULT_HISTORY_FILTERS, filterTradeLogs } from './history-adapter'
import type { HistoryFilters } from './history-adapter'
import { workbenchApi } from './workbench-api'
import type { TradeLog, TradeStats } from './workbench-types'

type HistorySource = 'live' | 'replay'

const money = (value: number | null | undefined, digits = 2) => value == null || !Number.isFinite(value) ? '—' : new Intl.NumberFormat('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value)
const quantity = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '—' : value.toFixed(8)
const percent = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(2)}%`
const dateTime = (value: number | null | undefined) => value ? new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(value) : '—'
const STATE_LABELS: Record<string, string> = { TAKE_PROFIT: '止盈退出', STOPPED: '硬止损退出', CANCELLED: '已取消' }

function StatCard({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: string }) {
  return <div className="history-stat-card"><span>{label}</span><strong className={tone}>{value}</strong>{hint && <small>{hint}</small>}</div>
}

function FillSegment({ label, price, size }: { label: string; price: number | null; size: number | null }) {
  return <div className="history-fill-segment"><span>{label}</span><strong>{money(price)} USDT</strong><small>{quantity(size)} BTC</small></div>
}

function HistoryCard({ log, selected, busy, onSelect, onDelete }: { log: TradeLog; selected: boolean; busy: boolean; onSelect: (checked: boolean) => void; onDelete: () => void }) {
  const initialSize = actualQuantity(log, 'initial') ?? log.initialQuantityBtc
  const addSize = actualQuantity(log, 'add') ?? log.addQuantityBtc
  return <details className="history-card">
    <summary>
      <label className="history-select" onClick={event => event.stopPropagation()}><input type="checkbox" checked={selected} onChange={event => onSelect(event.target.checked)} aria-label={`选择 ${dateTime(log.closedAt)} 的记录`}/></label>
      <div className="history-card-time"><strong>{dateTime(log.closedAt)}</strong><small>{log.source === 'live' ? '真实记录' : '回放记录'}</small></div>
      <span className={`history-direction ${log.direction.toLowerCase()}`}>{log.direction === 'LONG' ? '做多' : '做空'}</span>
      <span>{STATE_LABELS[log.state] ?? log.state}</span>
      <strong className={log.netPnl >= 0 ? 'positive' : 'danger'}>{money(log.netPnl)} USDT</strong>
      <small>展开详情</small>
    </summary>
    <div className="history-card-body">
      <div className="history-fill-grid">
        <FillSegment label="初始开仓" price={actualPrice(log, 'initial')} size={initialSize}/>
        <FillSegment label="加仓" price={actualPrice(log, 'add')} size={addSize}/>
        <FillSegment label="部分减仓" price={actualPrice(log, 'reduce')} size={actualQuantity(log, 'reduce')}/>
        <FillSegment label="最终退出" price={actualPrice(log, 'exit')} size={actualQuantity(log, 'exit')}/>
      </div>
      <div className="history-detail-grid">
        <StatCard label="实际累计开仓量" value={`${quantity(log.totalQuantityBtc)} BTC`}/>
        <StatCard label="杠杆" value={`${money(log.leverage, 0)}x`}/>
        <StatCard label="毛盈亏" value={`${money(log.grossPnl)} USDT`}/>
        <StatCard label="手续费" value={`${money(log.fees)} USDT`}/>
        <StatCard label="滑点" value={`${money(log.slippageUsdt)} USDT`}/>
        <StatCard label="净盈亏" value={`${money(log.netPnl)} USDT`} tone={log.netPnl >= 0 ? 'positive' : 'danger'}/>
        <StatCard label="账户收益率" value={percent(log.accountReturnPercent ?? log.returnPercent)}/>
        <StatCard label="终态" value={STATE_LABELS[log.state] ?? log.state}/>
      </div>
      <div className="history-notes"><strong>备注</strong><p>{log.notes || '无备注'}</p></div>
      <button className="history-delete-one" type="button" disabled={busy} onClick={onDelete}><Trash2/>删除这条记录</button>
    </div>
  </details>
}

export default function TradeHistoryView() {
  const [source, setSource] = useState<HistorySource>('live')
  const [logs, setLogs] = useState<TradeLog[]>([])
  const [stats, setStats] = useState<TradeStats | null>(null)
  const [filters, setFilters] = useState<HistoryFilters>(DEFAULT_HISTORY_FILTERS)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [clearText, setClearText] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const requestVersion = useRef(0)

  const load = useCallback(async (requestedSource: HistorySource = source) => {
    const version = ++requestVersion.current
    setLoading(true); setError('')
    try {
      const [history, statistics] = await Promise.all([workbenchApi.logs(requestedSource), workbenchApi.statistics(requestedSource)])
      if (version !== requestVersion.current) return
      setLogs(history.items)
      setStats(statistics)
    } catch (reason) {
      if (version === requestVersion.current) setError(reason instanceof Error ? reason.message : '历史记录加载失败')
    } finally { if (version === requestVersion.current) setLoading(false) }
  }, [source])

  useEffect(() => { setSelected(new Set()); setClearText(''); void load(source) }, [source])
  const filtered = useMemo(() => filterTradeLogs(logs, filters), [filters, logs])
  const curve = useMemo(() => backendEquityCurve(stats), [stats])
  const allSelected = filtered.length > 0 && filtered.every(log => selected.has(log.id))

  const reloadAfterMutation = async (message: string) => {
    setSelected(new Set())
    await load(source)
    setNotice(message)
  }
  const deleteOne = async (log: TradeLog) => {
    if (!window.confirm(`确认删除 ${dateTime(log.closedAt)}、净盈亏 ${money(log.netPnl)} USDT 的记录？`)) return
    setBusy(true); setError('')
    try { await workbenchApi.deleteLog(source, log.id); await reloadAfterMutation('记录已删除，列表与统计已刷新。') }
    catch (reason) { setError(reason instanceof Error ? reason.message : '删除失败，原记录已保留。') }
    finally { setBusy(false) }
  }
  const deleteSelected = async () => {
    const ids = [...selected]
    if (!ids.length || !window.confirm(`确认删除已选择的 ${ids.length} 条 ${source === 'live' ? '真实' : '回放'}记录？`)) return
    setBusy(true); setError('')
    try { await workbenchApi.bulkDeleteLogs(source, ids); await reloadAfterMutation(`已删除 ${ids.length} 条记录，列表与统计已刷新。`) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '批量删除失败，原记录已保留。') }
    finally { setBusy(false) }
  }
  const clearAll = async () => {
    if (clearText !== 'DELETE' || !window.confirm(`最后确认：清空全部 ${source === 'live' ? '真实' : '回放'}历史？`)) return
    setBusy(true); setError('')
    try { const result = await workbenchApi.clearLogs(source); setClearText(''); await reloadAfterMutation(`已清空 ${result.deletedCount} 条记录。`) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '清空失败，原记录已保留。') }
    finally { setBusy(false) }
  }
  const toggleAll = (checked: boolean) => {
    setSelected(current => {
      const next = new Set(current)
      for (const log of filtered) checked ? next.add(log.id) : next.delete(log.id)
      return next
    })
  }

  return <main className="history-page" data-source={source}>
    <header className="history-header">
      <div><span>交易日志</span><h1>历史记录</h1><p>实际成交和 Replay 严格分源；统计和净值曲线只使用后端结果。</p></div>
      <div className="history-source-tabs" role="tablist" aria-label="历史来源">
        <button role="tab" aria-selected={source === 'live'} className={source === 'live' ? 'active' : ''} onClick={() => setSource('live')}>真实记录 LIVE</button>
        <button role="tab" aria-selected={source === 'replay'} className={source === 'replay' ? 'active' : ''} onClick={() => setSource('replay')}>回放记录 REPLAY</button>
      </div>
    </header>

    {error && <div className="history-error" role="alert"><AlertTriangle/>{error}<button onClick={() => load(source)}>重试</button></div>}
    {notice && <div className="history-notice" role="status"><CheckCircle2/>{notice}</div>}

    <section className="history-stats" aria-label="后端交易统计">
      <StatCard label="累计净盈亏" value={`${money(stats?.netPnl)} USDT`} tone={(stats?.netPnl ?? 0) >= 0 ? 'positive' : 'danger'}/>
      <StatCard label="模拟账户权益" value={`${money(stats?.simulatedEquity)} USDT`} hint={`首笔记录权益 ${money(stats?.startingEquity)} USDT`}/>
      <StatCard label="总交易次数" value={String(stats?.totalTrades ?? 0)}/>
      <StatCard label="盈利次数" value={String(stats?.winningTrades ?? 0)}/>
      <StatCard label="亏损次数" value={String(stats?.losingTrades ?? 0)} hint={`持平 ${stats?.breakevenTrades ?? 0}`}/>
      <StatCard label="胜率" value={percent(stats?.winRate == null ? null : stats.winRate * 100)}/>
      <StatCard label="平均盈利" value={`${money(stats?.averageProfit)} USDT`}/>
      <StatCard label="平均亏损" value={`${money(stats?.averageLoss)} USDT`}/>
      <StatCard label="Profit Factor" value={money(stats?.profitFactor)}/>
      <StatCard label="总手续费" value={`${money(stats?.totalFees)} USDT`}/>
      <StatCard label="总滑点" value={`${money(stats?.totalSlippage)} USDT`}/>
      <StatCard label="最大回撤" value={`${money(stats?.maxDrawdownUsdt)} USDT`}/>
      <StatCard label="最大连续亏损" value={String(stats?.maxConsecutiveLosses ?? 0)}/>
      <StatCard label="做多累计盈亏" value={`${money(stats?.byDirection?.LONG?.netPnl)} USDT`}/>
      <StatCard label="做空累计盈亏" value={`${money(stats?.byDirection?.SHORT?.netPnl)} USDT`}/>
      <StatCard label="触发加仓次数" value={String(stats?.addTriggeredCount ?? 0)}/>
      <StatCard label="加仓后回到减仓区" value={String(stats?.returnedToReduceZoneCount ?? 0)}/>
      <StatCard label="加仓后止损次数" value={String(stats?.stoppedAfterAddCount ?? 0)}/>
    </section>

    <section className="history-panel history-equity-panel"><div className="history-section-title"><div><span>后端权威曲线</span><h2>模拟净值曲线</h2></div></div><TradeEquityChart points={curve} source={source}/></section>

    <section className="history-panel history-list-panel">
      <div className="history-section-title"><div><span>筛选与记录</span><h2>{source === 'live' ? '真实交易日志' : 'Replay 交易日志'}</h2></div><strong>{filtered.length} / {logs.length} 条</strong></div>
      <div className="history-filters">
        <label><span>方向</span><select value={filters.direction} onChange={event => setFilters({ ...filters, direction: event.target.value as HistoryFilters['direction'] })}><option value="ALL">全部</option><option value="LONG">做多</option><option value="SHORT">做空</option></select></label>
        <label><span>盈亏</span><select value={filters.outcome} onChange={event => setFilters({ ...filters, outcome: event.target.value as HistoryFilters['outcome'] })}><option value="ALL">全部</option><option value="PROFIT">盈利</option><option value="LOSS">亏损</option><option value="BREAKEVEN">持平</option></select></label>
        <label><span>终态</span><select value={filters.state} onChange={event => setFilters({ ...filters, state: event.target.value as HistoryFilters['state'] })}><option value="ALL">全部</option><option value="TAKE_PROFIT">止盈</option><option value="STOPPED">止损</option></select></label>
        <label><span>开始日期</span><input type="date" value={filters.dateFrom} onChange={event => setFilters({ ...filters, dateFrom: event.target.value })}/></label>
        <label><span>结束日期</span><input type="date" value={filters.dateTo} onChange={event => setFilters({ ...filters, dateTo: event.target.value })}/></label>
        <button type="button" onClick={() => setFilters(DEFAULT_HISTORY_FILTERS)}>重置筛选</button>
      </div>
      <div className="history-toolbar">
        <label><input type="checkbox" checked={allSelected} onChange={event => toggleAll(event.target.checked)}/>选择当前筛选结果</label>
        <button type="button" disabled={!selected.size || busy} onClick={deleteSelected}><Trash2/>删除已选（{selected.size}）</button>
        <a href={workbenchApi.exportLogsUrl(source, 'csv')} download><Download/>导出 CSV</a>
        <a href={workbenchApi.exportLogsUrl(source, 'json')} download><Download/>导出 JSON</a>
        <button type="button" onClick={() => load(source)} disabled={loading}>{loading ? <LoaderCircle className="spin"/> : <RefreshCw/>}刷新</button>
      </div>
      {loading ? <div className="history-empty"><LoaderCircle className="spin"/>正在加载历史记录…</div>
        : filtered.length ? <div className="history-list">{filtered.map(log => <HistoryCard key={log.id} log={log} selected={selected.has(log.id)} busy={busy} onSelect={checked => setSelected(current => { const next = new Set(current); checked ? next.add(log.id) : next.delete(log.id); return next })} onDelete={() => deleteOne(log)}/>)}</div>
          : <div className="history-empty">当前来源和筛选条件下没有交易记录。</div>}
      <details className="history-danger-zone"><summary>清空当前来源的全部历史</summary><div><p>输入 <strong>DELETE</strong> 后才可清空；不会影响另一来源。</p><input value={clearText} onChange={event => setClearText(event.target.value)} placeholder="输入 DELETE"/><button type="button" disabled={clearText !== 'DELETE' || busy} onClick={clearAll}><Trash2/>确认清空 {source === 'live' ? 'LIVE' : 'REPLAY'}</button></div></details>
    </section>
  </main>
}
