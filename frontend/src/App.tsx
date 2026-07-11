import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Activity, AlertTriangle, BarChart3, Bell, BellOff, ChevronRight, CircleDollarSign, Database, Gauge, History, LoaderCircle, Play, RefreshCw, Save, ShieldCheck, Trash2, Wifi, WifiOff } from 'lucide-react'
import { api, normalizeAdvice } from './api'
import type { AdviceAction, BacktestResult, Candle, MarketRegime, MarketSnapshot, Settings, SignalAdvice } from './types'

const emptySnapshot: MarketSnapshot = { instrument: 'BTC-USDT-SWAP', price: null, updatedAt: null, stale: true, connectionStatus: 'disconnected', fundingRate: null, fundingTime: null, openInterest: null, openInterestTime: null, candles1h: [], candles4h: [] }
const emptySettings: Settings = { equity: null, riskPercent: null, leverage: null, notificationsEnabled: false, customParameters: {} }
const labels: Record<AdviceAction, string> = { LONG_CANDIDATE: '做多候选', SHORT_CANDIDATE: '做空候选', WATCH_LONG: '偏多观察', WATCH_SHORT: '偏空观察', WAIT: '等待机会' }
const regimeLabels: Record<MarketRegime, string> = { TREND: '趋势', RANGE: '震荡', TRANSITION: '过渡', STALE: '数据过期' }
const signed = (v: number | null, digits = 2) => v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}`
const money = (v: number | null) => v == null ? '—' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(v)
const pct = (v: number | null) => v == null ? '—' : `${signed(Math.abs(v) <= 1 ? v * 100 : v)}%`
const compact = (v: number | null) => v == null ? '数据不足' : new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 2 }).format(v)
const time = (v: number | null | undefined) => v ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(v) : '—'
const actionTone = (a?: AdviceAction) => a?.includes('LONG') ? 'long' : a?.includes('SHORT') ? 'short' : 'neutral'

function PriceChart({ candles, timeframe }: { candles: Candle[]; timeframe: string }) {
  const data = candles.slice(-90)
  const [hover, setHover] = useState<number | null>(null)
  if (data.length < 2) return <div className="chart-empty"><BarChart3 size={28}/><span>等待 {timeframe} K 线数据</span></div>
  const W = 800, H = 255, top = 15, bottom = 40, right = 12
  const lows = data.map(x => x.low), highs = data.map(x => x.high)
  const min = Math.min(...lows), max = Math.max(...highs), spread = max - min || 1
  const y = (v: number) => top + (max - v) / spread * (H - top - bottom)
  const x = (i: number) => i / Math.max(1, data.length - 1) * (W - right)
  const line = data.map((c, i) => `${x(i)},${y(c.close)}`).join(' ')
  const active = hover == null ? data[data.length - 1] : data[hover]
  return <div className="chart-wrap">
    <div className="chart-head"><span className="timeframe">{timeframe}</span><span>收 {money(active.close)}</span><span className={active.close >= active.open ? 'up' : 'down'}>{active.close >= active.open ? '上涨' : '下跌'}</span><span>量 {compact(active.volume)}</span><span>{time(active.timestamp)}</span></div>
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" onMouseLeave={() => setHover(null)} onMouseMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); setHover(Math.max(0, Math.min(data.length - 1, Math.round((e.clientX - r.left) / r.width * (data.length - 1))))) }}>
      <defs><linearGradient id={`fill-${timeframe}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#42e8a6" stopOpacity=".22"/><stop offset="1" stopColor="#42e8a6" stopOpacity="0"/></linearGradient></defs>
      {[0,.25,.5,.75,1].map(t => <line key={t} x1="0" x2={W} y1={top+t*(H-top-bottom)} y2={top+t*(H-top-bottom)} className="gridline" />)}
      <polygon points={`0,${H-bottom} ${line} ${W-right},${H-bottom}`} fill={`url(#fill-${timeframe})`}/>
      <polyline points={line} fill="none" stroke="#42e8a6" strokeWidth="2" vectorEffect="non-scaling-stroke"/>
      {data.map((c,i) => <rect key={c.timestamp} x={x(i)-1.2} y={H-bottom-Math.min(30, c.volume/Math.max(...data.map(d=>d.volume),1)*28)} width="2.4" height={Math.min(30,c.volume/Math.max(...data.map(d=>d.volume),1)*28)} fill={c.close>=c.open?'#42e8a6':'#ff627d'} opacity=".55"/>)}
      {hover != null && <><line x1={x(hover)} x2={x(hover)} y1={top} y2={H-bottom} className="cross"/><circle cx={x(hover)} cy={y(active.close)} r="4" fill="#e9fff7"/></>}
    </svg>
  </div>
}

function Metric({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: string }) { return <div className="metric"><div className="metric-label">{label}</div><div className={`metric-value ${tone ?? ''}`}>{value}</div>{hint && <div className="metric-hint">{hint}</div>}</div> }

function AdviceCard({ advice, loading }: { advice: SignalAdvice | null; loading: boolean }) {
  const tone = actionTone(advice?.action)
  return <section className={`panel advice-card ${tone}`}>
    <div className="section-title"><span><Gauge size={17}/>当前策略建议</span>{advice && <span className="tag">{advice.strategy === 'range' ? '震荡策略' : advice.strategy === 'trend' ? '趋势策略' : '组合策略'}</span>}</div>
    {loading && !advice ? <div className="big-empty"><LoaderCircle className="spin"/>正在计算已收盘 K 线…</div> : !advice ? <div className="big-empty"><AlertTriangle/>暂无有效建议，等待后端分析</div> : <>
      <div className="advice-main"><div><div className="eyebrow">行动建议</div><h2>{labels[advice.action]}</h2><p>{advice.explanation || '当前条件尚未形成高质量入场信号。'}</p></div><div className="score-ring" style={{'--score': `${Math.abs(advice.directionScore)}%`} as React.CSSProperties}><strong>{signed(advice.directionScore,0)}</strong><span>方向分</span></div></div>
      <div className="confidence"><div><span>置信度</span><strong>{Math.round(advice.confidence <= 1 ? advice.confidence * 100 : advice.confidence)}%</strong></div><div className="bar"><i style={{width: `${Math.min(100, advice.confidence <= 1 ? advice.confidence*100 : advice.confidence)}%`}}/></div></div>
      <div className="trade-levels"><Metric label="触发参考" value={money(advice.triggerPrice)}/><Metric label="参考止损" value={money(advice.stopLoss)}/><Metric label="目标 1 / 2" value={advice.targets.length ? advice.targets.slice(0,2).map(money).join(' / ') : '—'}/><Metric label="风险收益比" value={advice.riskReward.length ? advice.riskReward.slice(0,2).map(v=>`1:${v.toFixed(2)}`).join(' / ') : '—'}/></div>
      <div className="invalidation"><ShieldCheck size={16}/><span><b>失效条件：</b>{advice.invalidation}</span></div>
    </>}
  </section>
}

function Contributions({ advice }: { advice: SignalAdvice | null }) { return <section className="panel"><div className="section-title"><span><Activity size={17}/>指标贡献</span><span className="muted">贡献分</span></div><div className="contrib-list">{advice?.contributions.length ? advice.contributions.map((c,i) => <div className="contrib" key={`${c.name}-${i}`} title={c.explanation}><span>{c.name}<small>{c.explanation}</small></span><div className="bi-bar"><i className={c.score >= 0 ? 'pos' : 'neg'} style={{width:`${Math.min(50,Math.abs(c.score)/2)}%`, [c.score>=0?'left':'right']:'50%'} as React.CSSProperties}/></div><strong className={c.score>=0?'up':'down'}>{signed(c.score,0)}</strong></div>) : <div className="small-empty">指标贡献将在新信号生成后显示</div>}</div></section> }

function RiskPanel({ settings, advice, onSave }: { settings: Settings; advice: SignalAdvice | null; onSave: (v: Settings)=>Promise<void> }) {
  const [draft,setDraft] = useState(settings); const [saving,setSaving] = useState(false); const [error,setError] = useState('')
  useEffect(()=>setDraft(settings),[settings])
  const distance = advice?.triggerPrice != null && advice.stopLoss != null ? Math.abs(advice.triggerPrice-advice.stopLoss) : null
  const nominal = draft.equity && draft.riskPercent && distance && advice?.triggerPrice ? (draft.equity*(draft.riskPercent/100)/distance)*advice.triggerPrice : null
  const submit = async () => { if (draft.riskPercent != null && (draft.riskPercent < .1 || draft.riskPercent > 2)) return setError('每笔风险须为 0.1%～2%'); if (draft.leverage != null && (draft.leverage < 1 || draft.leverage > 2)) return setError('计划杠杆须为 1x～2x'); setError(''); setSaving(true); try { await onSave(draft) } catch { setError('保存失败，请确认本地服务已启动') } finally { setSaving(false) } }
  const clear = async () => { if (!window.confirm('清除本机保存的设置、信号和缓存行情？此操作无法撤销。')) return; setSaving(true); try { await api.clearLocalData(); window.location.reload() } catch { setError('清除失败，请确认本地服务已启动') } finally { setSaving(false) } }
  return <section className="panel risk-panel"><div className="section-title"><span><CircleDollarSign size={17}/>教育性仓位测算</span><span className="tag amber">本机保存</span></div><div className="risk-inputs"><label>账户权益 (USDT)<input type="number" min="0" placeholder="请自行填写" value={draft.equity??''} onChange={e=>setDraft({...draft,equity:e.target.value===''?null:Number(e.target.value)})}/></label><label>每笔风险 (%)<input type="number" min=".1" max="2" step=".1" placeholder="0.1 ～ 2" value={draft.riskPercent??''} onChange={e=>setDraft({...draft,riskPercent:e.target.value===''?null:Number(e.target.value)})}/></label><label>计划杠杆 (x)<input type="number" min="1" max="2" step=".1" placeholder="1 ～ 2" value={draft.leverage??''} onChange={e=>setDraft({...draft,leverage:e.target.value===''?null:Number(e.target.value)})}/></label></div>
    <div className="estimate"><span>参考名义仓位</span><strong>{nominal == null ? '填写参数后计算' : `${money(nominal)} USDT`}</strong><small>{distance == null ? '需要有效触发价与止损价' : `止损距离 ${money(distance)} USDT`}</small></div>{error && <div className="form-error">{error}</div>}<div className="risk-actions"><button className="button secondary" onClick={submit} disabled={saving}>{saving?<LoaderCircle className="spin"/>:<Save/>}保存风险设置</button><button className="button ghost" onClick={clear} disabled={saving}><Trash2/>清除本地数据</button></div><p className="fineprint">未计入资金费率、手续费和滑点；该结果不是实际成交数量，也不构成投资建议。</p>
  </section>
}

function Backtest({ result, running, run }: { result: BacktestResult|null; running:boolean; run:()=>void }) { const rows = [['净收益',pct(result?.netReturn??null)],['最大回撤',pct(result?.maxDrawdown??null)],['Sharpe',result?.sharpe?.toFixed(2)??'—'],['Profit Factor',result?.profitFactor?.toFixed(2)??'—'],['胜率',pct(result?.winRate??null)],['交易数',result?.trades?.toString()??'—']]; return <section className="panel backtest"><div className="section-title"><span><BarChart3 size={17}/>回测摘要</span>{result&&<span className={`validation ${result.validation.includes('通过')?'ok':''}`}>{result.validation}</span>}</div><div className="backtest-grid">{rows.map(([a,b])=><Metric key={a} label={a} value={b}/>)}</div><button className="button" disabled={running} onClick={run}>{running?<LoaderCircle className="spin"/>:<Play/>}{running?'正在运行…':'运行组合策略回测'}</button><p className="fineprint">回测使用下一根开盘成交及保守的同 K 线止损顺序。历史表现不代表未来结果。</p></section> }

export default function App() {
  const [snapshot,setSnapshot]=useState(emptySnapshot), [advice,setAdvice]=useState<SignalAdvice|null>(null), [history,setHistory]=useState<SignalAdvice[]>([]), [settings,setSettings]=useState(emptySettings), [backtest,setBacktest]=useState<BacktestResult|null>(null)
  const [loading,setLoading]=useState(true), [connected,setConnected]=useState(false), [error,setError]=useState(''), [tab,setTab]=useState<'1H'|'4H'>('1H'), [running,setRunning]=useState(false), [toast,setToast]=useState('')
  const lastNotice=useRef<string>('')
  const load=useCallback(async()=>{ setLoading(true); const results=await Promise.allSettled([api.snapshot(),api.advice(),api.history(),api.settings()]); if(results[0].status==='fulfilled') setSnapshot(results[0].value); if(results[1].status==='fulfilled') setAdvice(results[1].value); if(results[2].status==='fulfilled') setHistory(results[2].value); if(results[3].status==='fulfilled') setSettings(results[3].value); const failed=results.filter(r=>r.status==='rejected').length; setError(failed>=3?'无法连接本地分析服务，请先启动后端。':''); setLoading(false) },[])
  useEffect(()=>{load(); const timer=setInterval(load,60000); return()=>clearInterval(timer)},[load])
  const notify=useCallback((a:SignalAdvice)=>{ if(a.action==='WAIT')return; const key=`${a.instrument}:${a.strategy}:${a.action}:${a.candleCloseAt}`; if(lastNotice.current===key)return; lastNotice.current=key; const msg=`${labels[a.action]} · 方向分 ${signed(a.directionScore,0)}`; if(settings.notificationsEnabled&&Notification.permission==='granted') new Notification(`BTC 策略提醒`,{body:msg,tag:key,requireInteraction:a.action.includes('CANDIDATE')}); else setToast(msg) },[settings.notificationsEnabled])
  useEffect(()=>{ let ws:WebSocket|undefined, retry:number, stopped=false, delay=1000; const connect=()=>{ const protocol=location.protocol==='https:'?'wss':'ws'; ws=new WebSocket(`${protocol}://${location.host}/ws/live`); ws.onopen=()=>{setConnected(true);delay=1000}; ws.onclose=()=>{setConnected(false);if(!stopped){retry=window.setTimeout(connect,delay);delay=Math.min(delay*2,30000)}}; ws.onerror=()=>ws?.close(); ws.onmessage=e=>{try{const m=JSON.parse(e.data); if(m.type==='signal'&&m.advice){const a=normalizeAdvice(m.advice);setAdvice(a);setHistory(h=>[a,...h.filter(x=>`${x.instrument}${x.strategy}${x.action}${x.candleCloseAt}`!==`${a.instrument}${a.strategy}${a.action}${a.candleCloseAt}`)].slice(0,100));notify(a)} else if(m.type==='market'){setSnapshot(s=>({...s,price:m.price??s.price,connectionStatus:m.connectionStatus??s.connectionStatus,updatedAt:m.tickerTime??s.updatedAt})); if(!m.price)load()}}catch{/* ignore malformed live frame */}}}; connect(); return()=>{stopped=true;clearTimeout(retry);ws?.close()}},[load,notify])
  const enableNotifications=async()=>{ if(!('Notification'in window))return setToast('当前浏览器不支持系统通知，将使用网页提醒'); const p=await Notification.requestPermission(); const next={...settings,notificationsEnabled:p==='granted'}; setSettings(next); try{await api.saveSettings(next)}catch{/* permission remains local */} setToast(p==='granted'?'系统通知已开启':'未授权，将使用网页提醒') }
  const run=async()=>{setRunning(true);try{setBacktest(await api.backtest())}catch{setToast('回测启动失败，请检查后端数据状态')}finally{setRunning(false)}}
  const stale=snapshot.stale||advice?.regime==='STALE'||(snapshot.updatedAt!=null&&Date.now()-snapshot.updatedAt>2*60*60*1000)
  const age=snapshot.updatedAt==null?'未知':`${Math.max(0,Math.round((Date.now()-snapshot.updatedAt)/60000))} 分钟前`
  const candles=tab==='1H'?snapshot.candles1h:snapshot.candles4h
  return <div className="app"><header><div className="brand"><div className="logo">₿</div><div><strong>BTC 策略观察台</strong><span>OKX 永续合约 · 本地分析</span></div></div><div className="header-actions"><button className="icon-button" title="刷新" onClick={load}><RefreshCw className={loading?'spin':''}/></button><button className="notify-button" onClick={enableNotifications}>{settings.notificationsEnabled?<Bell/>:<BellOff/>}{settings.notificationsEnabled?'通知已开启':'开启提醒'}</button><div className={`connection ${connected?'online':''}`}>{connected?<Wifi/>:<WifiOff/>}{connected?'实时连接':'正在重连'}</div></div></header>
    {stale&&<div className="stale-banner"><AlertTriangle/><div><strong>行情数据已过期，已暂停生成新建议</strong><span>最近更新：{age}。系统会自动重连并回补缺口。</span></div></div>}
    {error&&<div className="service-banner"><Database/><span>{error}</span><button onClick={load}>重试</button></div>}
    <main><section className="market-strip"><div className="instrument"><span>{snapshot.instrument}</span><strong>{snapshot.price==null?'—':`$${money(snapshot.price)}`}</strong><em>{snapshot.connectionStatus}</em></div><Metric label="市场状态" value={regimeLabels[stale?'STALE':(advice?.regime??'TRANSITION')]} hint="由 4H 建议引擎判定" tone={advice?.regime==='TREND'?'up':''}/><Metric label="资金费率" value={pct(snapshot.fundingRate)} hint={`更新于 ${time(snapshot.fundingTime)}`}/><Metric label="公开持仓量" value={compact(snapshot.openInterest)} hint={`更新于 ${time(snapshot.openInterestTime)}`}/><Metric label="数据新鲜度" value={age} hint={`快照 ${time(snapshot.updatedAt)}`}/></section>
      <div className="dashboard-grid"><div className="primary-column"><section className="panel chart-panel"><div className="chart-tabs"><div><h3>BTC / USDT 价格</h3><span>仅已收盘 K 线参与策略计算</span></div><div>{(['1H','4H'] as const).map(x=><button key={x} className={tab===x?'active':''} onClick={()=>setTab(x)}>{x}</button>)}</div></div><PriceChart candles={candles} timeframe={tab}/></section><AdviceCard advice={advice} loading={loading}/><Backtest result={backtest} running={running} run={run}/></div>
        <aside><Contributions advice={advice}/><RiskPanel settings={settings} advice={advice} onSave={async v=>{await api.saveSettings(v);setSettings(v);setToast('风险设置已保存')}}/><section className="panel status-panel"><div className="section-title"><span><ShieldCheck size={17}/>策略状态</span></div><div className="status-row"><span>配置版本</span><strong>{advice?.configVersion??'default'}</strong></div><div className="status-row"><span>数据质量</span><strong>{advice?.dataQuality.fresh?'新鲜':'等待/过期'}</strong></div><div className="status-row"><span>信号 K 线</span><strong>{time(advice?.candleCloseAt)}</strong></div>{advice?.dataQuality.warnings.map(w=><div className="custom-warning" key={w}><AlertTriangle/>{w}</div>)}{Object.keys(settings.customParameters).length>0&&<div className="custom-warning"><AlertTriangle/>自定义参数，未经历史验证</div>}</section></aside></div>
      <section className="panel history-panel"><div className="section-title"><span><History size={17}/>信号记录</span><span className="muted">候选与观察信号均记录</span></div>{history.length?<div className="table-scroll"><table><thead><tr><th>时间</th><th>策略</th><th>行动</th><th>方向分</th><th>置信度</th><th>触发参考</th><th>状态</th></tr></thead><tbody>{history.slice(0,20).map((h,i)=><tr key={h.id??`${h.candleCloseAt}-${i}`}><td>{time(h.candleCloseAt)}</td><td>{h.strategy}</td><td><span className={`action-pill ${actionTone(h.action)}`}>{labels[h.action]}</span></td><td className={h.directionScore>=0?'up':'down'}>{signed(h.directionScore,0)}</td><td>{Math.round(h.confidence)}%</td><td>{money(h.triggerPrice)}</td><td><span className="row-link">查看 <ChevronRight/></span></td></tr>)}</tbody></table></div>:<div className="big-empty"><History/>尚无历史信号</div>}</section>
    </main><footer><span>仅供策略研究与教育用途，不构成个性化投资建议。加密资产波动剧烈，请独立判断并控制风险。</span><span>所有设置与权益数据仅保存在本机</span></footer>{toast&&<div className="toast" onAnimationEnd={()=>setToast('')}><Bell/>{toast}</div>}</div>
}
