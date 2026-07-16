import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Activity, AlertTriangle, BarChart3, Bell, BellOff, CircleDollarSign, Database, ExternalLink, Gauge, History, Layers3, LoaderCircle, Newspaper, RefreshCw, Save, ShieldCheck, Trash2, Wifi, WifiOff } from 'lucide-react'
import { api, normalizeAdvice } from './api'
import BacktestPanel from './BacktestPanel'
import HelpGuide from './HelpGuide'
import { riskEstimateLabel, validateRiskSettings } from './risk-utils'
import { clearBacktestJobHistory } from './backtest-storage'
import { adviceIsCurrent, adviceValidationLabel, confidencePercent, notificationKey, uniqueMessages } from './ui-contracts'
import type { AdviceAction, Candle, MarketRegime, MarketSnapshot, NewsResponse, RiskEstimate, Settings, SignalAdvice, TechnicalSummary } from './types'

const emptyCandleStatus = {
  '1m': { available: false, stale: true, lastAt: null, gapDetected: false },
  '15m': { available: false, stale: true, lastAt: null, gapDetected: false },
  '1H': { available: false, stale: true, lastAt: null, gapDetected: false },
  '4H': { available: false, stale: true, lastAt: null, gapDetected: false },
}
const emptySnapshot: MarketSnapshot = { instrument: 'BTC-USDT-SWAP', price: null, markPrice: null, markPriceTime: null, updatedAt: null, stale: true, connectionStatus: 'disconnected', fundingRate: null, fundingTime: null, openInterest: null, openInterestTime: null, candles1m: [], candles15m: [], candles1h: [], candles4h: [], candleStatus: emptyCandleStatus }
const emptySettings: Settings = { equity: null, riskPercent: null, leverage: null, notificationsEnabled: false, customParameters: {} }
const labels: Record<AdviceAction, string> = { LONG_CANDIDATE: '做多候选', SHORT_CANDIDATE: '做空候选', WATCH_LONG: '偏多观察', WATCH_SHORT: '偏空观察', WAIT: '等待机会' }
const regimeLabels: Record<MarketRegime, string> = { TREND: '趋势', RANGE: '震荡', TRANSITION: '过渡', STALE: '数据过期' }
const signed = (v: number | null, digits = 2) => v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}`
const money = (v: number | null) => v == null ? '—' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(v)
const pct = (v: number | null) => v == null ? '—' : `${signed(Math.abs(v) <= 1 ? v * 100 : v)}%`
const compact = (v: number | null) => v == null ? '数据不足' : new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 2 }).format(v)
const time = (v: number | null | undefined) => v ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(v) : '—'
const actionTone = (a?: AdviceAction) => a?.includes('LONG') ? 'long' : a?.includes('SHORT') ? 'short' : 'neutral'
const scoreTone = (score?: number) => score != null && score > 0 ? 'long' : score != null && score < 0 ? 'short' : 'neutral'
const connectionLabel = (value:string) => value==='connected'?'实时连接':value==='degraded'?'部分连接':value==='reconnecting'?'正在重连':value==='starting'?'正在启动':'连接异常'

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
    <svg className="chart" role="img" aria-label={`${timeframe} 收盘价与成交量走势，最新收盘 ${money(active.close)}`} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" onMouseLeave={() => setHover(null)} onMouseMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); setHover(Math.max(0, Math.min(data.length - 1, Math.round((e.clientX - r.left) / r.width * (data.length - 1))))) }}>
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
  const tone = advice?.action==='WAIT' ? scoreTone(advice.directionScore) : actionTone(advice?.action)
  const confidence=confidencePercent(advice?.confidence)
  return <section className={`panel advice-card ${tone}`}>
    <div className="section-title"><span><Gauge size={17}/>当前策略建议</span>{advice && <span><span className="tag amber">{adviceValidationLabel(advice.configVersion)}</span><span className="tag">{advice.strategy === 'range' ? '震荡策略' : advice.strategy === 'trend' ? '趋势策略' : '组合策略'}</span></span>}</div>
    {loading && !advice ? <div className="big-empty"><LoaderCircle className="spin"/>正在计算已收盘 K 线…</div> : !advice ? <div className="big-empty"><AlertTriangle/>暂无有效建议，等待后端分析</div> : <>
      <div className="advice-main"><div><div className="eyebrow">行动建议</div><h2>{labels[advice.action]}</h2><p>{advice.explanation || '当前条件尚未形成高质量入场信号。'}</p></div><div className="score-ring" style={{'--score': `${Math.abs(advice.directionScore)}%`} as React.CSSProperties}><strong>{signed(advice.directionScore,0)}</strong><span>方向分</span></div></div>
      <div className="score-breakdown"><Metric label="策略技术分" value={`${signed(advice.technicalScore,0)} / 85`} tone={advice.technicalScore>0?'up':advice.technicalScore<0?'down':''}/><span>+</span><Metric label="本 K 线采用新闻分" value={`${signed(advice.newsScore,0)} / 15`} hint={Math.abs(advice.technicalScore)<30?'技术分不足 30，未采用新闻产生信号':`决策时间 ${time(advice.candleCloseAt)}`} tone={advice.newsScore>0?'up':advice.newsScore<0?'down':''}/><span>=</span><Metric label="综合方向" value={`${signed(advice.directionScore,0)} / 100`} tone={advice.directionScore>0?'up':advice.directionScore<0?'down':''}/></div>
      <div className="confidence"><div><span>置信度</span><strong>{Math.round(confidence)}%</strong></div><div className="bar"><i style={{width: `${confidence}%`}}/></div></div>
      <div className="trade-levels"><Metric label="触发参考" value={money(advice.triggerPrice)}/><Metric label="参考止损" value={money(advice.stopLoss)}/><Metric label="目标 1 / 2" value={advice.targets.length ? advice.targets.slice(0,2).map(money).join(' / ') : '—'}/><Metric label="风险收益比" value={advice.riskReward.length ? advice.riskReward.slice(0,2).map(v=>`1:${v.toFixed(2)}`).join(' / ') : '—'}/></div>
      <div className="invalidation"><ShieldCheck size={16}/><span><b>失效条件：</b>{advice.invalidation}</span></div>
    </>}
  </section>
}

function Contributions({ advice }: { advice: SignalAdvice | null }) { return <section className="panel"><div className="section-title"><span><Activity size={17}/>指标贡献</span><span className="muted">贡献分</span></div><div className="contrib-list">{advice?.contributions.length ? advice.contributions.map((c,i) => <div className="contrib" key={`${c.name}-${i}`} title={c.explanation}><span>{c.name}<small>{c.explanation}</small></span><div className="bi-bar"><i className={c.score >= 0 ? 'pos' : 'neg'} style={{width:`${Math.min(50,Math.abs(c.score)/2)}%`, [c.score>=0?'left':'right']:'50%'} as React.CSSProperties}/></div><strong className={c.score>=0?'up':'down'}>{signed(c.score,0)}</strong></div>) : <div className="small-empty">指标贡献将在新信号生成后显示</div>}</div></section> }

function RiskPanel({ settings, estimate, onSave }: { settings: Settings; estimate: RiskEstimate | null; onSave: (v: Settings)=>Promise<void> }) {
  const [draft,setDraft] = useState(settings); const [saving,setSaving] = useState(false); const [error,setError] = useState('')
  useEffect(()=>setDraft(settings),[settings])
  const complete=draft.equity!=null&&draft.riskPercent!=null&&draft.leverage!=null
  const submit = async () => { const validationError=validateRiskSettings(draft); if(validationError)return setError(validationError); setError(''); setSaving(true); try { await onSave(draft) } catch { setError('保存失败，请确认本地服务已启动') } finally { setSaving(false) } }
  const clear = async () => { if (!window.confirm('清除本机保存的设置、信号和缓存行情？此操作无法撤销。')) return; setSaving(true); try { await api.clearLocalData(); clearBacktestJobHistory(); window.location.reload() } catch { setError('清除失败，请确认本地服务已启动') } finally { setSaving(false) } }
  return <section className="panel risk-panel legacy-risk-panel"><div className="section-title"><span><CircleDollarSign size={17}/>旧研究模型仓位测算</span><span className="tag amber">1–2x · 不用于真实计划</span></div><div className="risk-inputs"><label>账户权益 (USDT)<input type="number" min="0" placeholder="请自行填写" value={draft.equity??''} onChange={e=>setDraft({...draft,equity:e.target.value===''?null:Number(e.target.value)})}/></label><label>每笔风险 (%)<input type="number" min=".1" max="2" step=".1" placeholder="0.1 ～ 2" value={draft.riskPercent??''} onChange={e=>setDraft({...draft,riskPercent:e.target.value===''?null:Number(e.target.value)})}/></label><label>计划杠杆 (x)<input type="number" min="1" max="2" step=".1" placeholder="1 ～ 2" value={draft.leverage??''} onChange={e=>setDraft({...draft,leverage:e.target.value===''?null:Number(e.target.value)})}/></label></div>
    <div className="estimate"><span>参考名义仓位</span><strong>{riskEstimateLabel(complete,estimate?.referenceNotional)}</strong><small>{estimate?.stopDistance==null ? '需要有效触发价与止损价' : `止损距离 ${money(estimate.stopDistance)} USDT · ${estimate.quantityBtc==null?'—':estimate.quantityBtc.toFixed(6)} BTC`}</small></div>{estimate?.warnings.map(w=><div className="risk-warning" key={w}>{w}</div>)}{error && <div className="form-error" role="alert">{error}</div>}<div className="risk-actions"><button className="button secondary" onClick={submit} disabled={saving}>{saving?<LoaderCircle className="spin"/>:<Save/>}保存旧研究设置</button><button className="button ghost" onClick={clear} disabled={saving}><Trash2/>清除本地数据</button></div><p className="fineprint">这是保留的 1–2x 教育性旧模型，未计入实际手续费和滑点，不会写入 v0.4 真实交易计划。请勿与 66x 手动交易工作台的风险计算混用。</p>
  </section>
}

const ratingTone = (value: string) => /多|强|上|bull|buy/i.test(value) ? 'up' : /空|弱|下|bear|sell/i.test(value) ? 'down' : ''

function TechnicalPanel({ data, error }: { data: TechnicalSummary|null; error: string }) {
  const messages=data?uniqueMessages(error,data.warnings,data.riskReasons):uniqueMessages(error)
  return <section className="panel wide-panel technical-panel"><div className="section-title"><span><Layers3 size={17}/>辅助技术共振 <small className="title-note">不直接等于策略技术分</small></span><span className={`module-status ${data?.status??'unavailable'}`}>{data?.status==='fresh'?'数据新鲜':data?.status==='partial'?'部分数据':'暂不可用'} · {time(data?.asOf)}</span></div>
    {error&&!data?<div className="module-empty"><AlertTriangle/>{error}</div>:!data?<div className="module-empty"><LoaderCircle className="spin"/>正在汇总多周期技术指标…</div>:<>
      <div className="technical-groups">{data.groups.slice(0,5).map(g=><article className="technical-group" key={g.key}><div className="tech-group-head"><strong>{g.label}</strong><div><span className={ratingTone(g.direction)}>{g.direction}</span></div></div><p>{g.score==null?g.summary:`独立贡献 ${signed(g.score,1)} / ${g.cap??'—'}`}</p><div className="tech-metrics">{g.metrics.slice(0,4).map((m,i)=><span key={`${m.label}-${i}`}><small>{m.label}</small><b className={m.tone==='positive'?'up':m.tone==='negative'?'down':''}>{m.value}</b></span>)}</div></article>)}</div>
      <div className="technical-levels"><Metric label="日 VWAP" value={money(data.vwap)}/><Metric label="周 VWAP" value={money(data.weeklyVwap)}/><Metric label="MFI" value={data.mfi==null?'数据暂未暴露':data.mfi.toFixed(1)}/><Metric label="ATR / BB 宽度分位" value={`${data.atrPercentile==null?'—':`${data.atrPercentile.toFixed(0)}%`} / ${data.bollingerWidthPercentile==null?'—':`${data.bollingerWidthPercentile.toFixed(0)}%`}`}/><Metric label="风险建议仓位系数" value={data.positionScale==null?'—':`${Math.round(data.positionScale*100)}%`}/><Metric label="动量 / 波动阶段" value={`${data.momentum} / ${data.volatilityPhase}`}/><Metric label="成交量 POC" value={money(data.poc)} hint="K 线近似成交密集价"/><Metric label="VAH / VAL" value={`${money(data.vah)} / ${money(data.val)}`} hint="价值区上下沿"/><Metric label="支撑" value={data.supports.length?data.supports.slice(0,3).map(money).join(' · '):'—'}/><Metric label="阻力" value={data.resistances.length?data.resistances.slice(0,3).map(money).join(' · '):'—'}/></div>
      {messages.length>0&&<div className="warning-row"><AlertTriangle/>{messages.join('；')}</div>}
    </>}
  </section>
}

function NewsPanel({ data, error }: { data: NewsResponse|null; error: string }) {
  const score=data?.analysis.score??0
  const sourceTotal=data?.analysis.sourceStatus.length??0, sourceOk=data?.analysis.sourceStatus.filter(source=>source.ok).length??0
  return <section className="panel wide-panel news-panel"><div className="section-title"><span><Newspaper size={17}/>消息面新闻</span><div className="news-summary"><b className={score>0?'up':score<0?'down':''}>当前新闻窗口分 {signed(score,1)} / 15</b><span>{data?.analysis.windowHours??48}h 窗口 · {time(data?.analysis.asOf)}</span>{sourceTotal>0&&<span>来源 {sourceOk}/{sourceTotal} · 覆盖率 {Math.round((data?.analysis.sourceCoverage??0)*100)}%</span>}<span className={`module-status ${data?.analysis.status??'unavailable'}`}>{data?.analysis.status==='fresh'?'新鲜':data?.analysis.status==='partial'?'部分来源异常':'暂不可用'}</span></div></div>
    {error&&!data?<div className="module-empty"><AlertTriangle/>{error}，技术分析仍可正常使用</div>:!data?<div className="module-empty"><LoaderCircle className="spin"/>正在载入最近消息面…</div>:<>
      <div className="news-grid">{data.items.length?data.items.map(item=><article className="news-card" key={item.id}><div className="news-meta"><span className={`importance i${Math.round(item.importance)}`}>{item.importanceLabel} {item.importanceScore==null?'':item.importanceScore}</span><span className={item.sentiment>0?'positive':item.sentiment<0?'negative':'neutral'}>{item.sentimentLabel}</span><span>{item.category}</span></div><h3>{item.url?<a href={item.url} target="_blank" rel="noopener noreferrer" aria-label={`${item.title}（新窗口打开）`}>{item.title}<ExternalLink aria-hidden="true"/></a>:item.title}</h3>{item.summary&&<p>{item.summary}</p>}<div className="news-foot"><span>{item.source} · {time(item.publishedAt)}</span><span>相关度 {Math.round(item.relevance*100)}%</span></div><div className="news-weight"><span>影响 {signed(item.effectiveImpact,3)}</span><span>{item.sourceCount==null?'来源数未知':`${item.sourceCount} 个独立来源`}</span><span>时效系数 {item.timeDecay==null?'—':item.timeDecay.toFixed(2)}{item.halfLifeHours==null?'':`（半衰期 ${item.halfLifeHours}h）`}</span></div>{(item.reason||item.weightFormula)&&<div className="news-reason">{item.reason&&<>方向依据：{item.reason}</>}{item.weightFormula&&<small>{item.weightFormula}</small>}{Object.keys(item.weightBreakdown).length>0&&<small>事件严重度 {Math.round((item.weightBreakdown.severity??0)*100)}% · 来源质量 {Math.round((item.weightBreakdown.sourceQuality??0)*100)}% · 独立印证 {Math.round((item.weightBreakdown.corroboration??0)*100)}% · 方向置信度 {Math.round((item.weightBreakdown.directionConfidence??0)*100)}%</small>}</div>}</article>):<div className="module-empty">最近 {data.analysis.windowHours} 小时暂无合资格新闻</div>}</div>
      {(error||data.analysis.warnings.length>0)&&<div className="warning-row"><AlertTriangle/>{[error,...data.analysis.warnings].filter(Boolean).join('；')}</div>}
      <p className="fineprint">这里是当前新闻窗口的来源覆盖修正分；本 K 线决策实际采用分请以上方建议卡为准。新闻不能单独产生交易信号，来源全部失败或状态过期时严格归零。</p>
    </>}
  </section>
}

export default function ResearchView() {
  const [snapshot,setSnapshot]=useState(emptySnapshot), [advice,setAdvice]=useState<SignalAdvice|null>(null), [history,setHistory]=useState<SignalAdvice[]>([]), [settings,setSettings]=useState(emptySettings)
  const [riskEstimate,setRiskEstimate]=useState<RiskEstimate|null>(null)
  const [technical,setTechnical]=useState<TechnicalSummary|null>(null), [news,setNews]=useState<NewsResponse|null>(null), [technicalError,setTechnicalError]=useState(''), [newsError,setNewsError]=useState('')
  const [loading,setLoading]=useState(true), [connected,setConnected]=useState(false), [error,setError]=useState(''), [adviceError,setAdviceError]=useState(''), [tab,setTab]=useState<'1H'|'4H'>('1H'), [toast,setToast]=useState('')
  const lastNotice=useRef<string>('')
  const liveMarketReady=useRef(false)
  const load=useCallback(async()=>{ setLoading(true); const results=await Promise.allSettled([api.snapshot(),api.advice(),api.history(),api.settings(),api.technicalSummary(),api.news()]); if(results[0].status==='fulfilled') setSnapshot(results[0].value); else setSnapshot(s=>({...s,stale:true,connectionStatus:'disconnected'})); if(results[1].status==='fulfilled'){setAdvice(results[1].value.advice);setRiskEstimate(results[1].value.riskEstimate);setAdviceError('')}else{setAdvice(null);setRiskEstimate(null);setAdviceError('当前策略建议获取失败，已隐藏旧建议')} if(results[2].status==='fulfilled') setHistory(results[2].value); if(results[3].status==='fulfilled') setSettings(results[3].value); if(results[4].status==='fulfilled'){setTechnical(results[4].value);setTechnicalError('')}else{setTechnical(null);setTechnicalError('技术共振摘要暂时无法获取')} if(results[5].status==='fulfilled'){setNews(results[5].value);setNewsError('')}else{setNews(null);setNewsError('消息面数据暂时无法获取')} const criticalFailed=results[0].status==='rejected'||results[1].status==='rejected'; setError(criticalFailed?'关键行情或建议接口不可用，旧交易建议已安全隐藏。':''); setLoading(false) },[])
  useEffect(()=>{load(); const timer=setInterval(load,60000); return()=>clearInterval(timer)},[load])
  const notify=useCallback((a:SignalAdvice)=>{ if(a.action==='WAIT'||!a.dataQuality.fresh||!liveMarketReady.current)return; const key=notificationKey(a); if(lastNotice.current===key)return; lastNotice.current=key; const msg=`${labels[a.action]} · 方向分 ${signed(a.directionScore,0)}`; if(settings.notificationsEnabled&&Notification.permission==='granted') new Notification(`BTC 策略提醒`,{body:msg,tag:key,requireInteraction:a.action.includes('CANDIDATE')}); else setToast(msg) },[settings.notificationsEnabled])
  useEffect(()=>{ let ws:WebSocket|undefined, retry:number, stopped=false, delay=1000; const connect=()=>{ const protocol=location.protocol==='https:'?'wss':'ws'; ws=new WebSocket(`${protocol}://${location.host}/ws/live`); ws.onopen=()=>setConnected(false); ws.onclose=()=>{setConnected(false);if(!stopped){retry=window.setTimeout(connect,delay);delay=Math.min(delay*2,30000)}}; ws.onerror=()=>ws?.close(); ws.onmessage=e=>{try{const m=JSON.parse(e.data); if(m.type==='ready'){setConnected(true);delay=1000;setSnapshot(s=>({...s,connectionStatus:m.connectionStatus??s.connectionStatus}))} else if(m.type==='signal'&&m.advice){const a=normalizeAdvice(m.advice);setAdvice(a);setRiskEstimate(null);setHistory(h=>[a,...h.filter(x=>`${x.instrument}${x.strategy}${x.action}${x.candleCloseAt}`!==`${a.instrument}${a.strategy}${a.action}${a.candleCloseAt}`)].slice(0,100));notify(a);api.advice().then(v=>{setAdvice(v.advice);setRiskEstimate(v.riskEstimate)}).catch(()=>{})} else if(m.type==='market'){setConnected(true);delay=1000;const ticker=Number(m.tickerTime);setSnapshot(s=>({...s,price:m.price??s.price,connectionStatus:m.connectionStatus??s.connectionStatus,updatedAt:Number.isFinite(ticker)?ticker:s.updatedAt,stale:Number.isFinite(ticker)?Date.now()-ticker>30000:s.stale})); if(!m.price)load()}}catch{/* ignore malformed live frame */}}}; connect(); return()=>{stopped=true;clearTimeout(retry);ws?.close()}},[load,notify])
  const enableNotifications=async()=>{ if(!('Notification'in window))return setToast('当前浏览器不支持系统通知，将使用网页提醒'); const p=await Notification.requestPermission(); const next={...settings,notificationsEnabled:p==='granted'}; setSettings(next); try{await api.saveSettings(next)}catch{/* permission remains local */} setToast(p==='granted'?'系统通知已开启':'未授权，将使用网页提醒') }
  const adviceCurrent=adviceIsCurrent(advice,snapshot.stale)
  const safeAdvice=adviceCurrent?advice:null
  const stale=snapshot.stale||Boolean(adviceError)||(advice!=null&&!adviceCurrent)||(snapshot.updatedAt!=null&&Date.now()-snapshot.updatedAt>2*60*60*1000)
  liveMarketReady.current=!stale&&/^connected$/i.test(snapshot.connectionStatus)&&snapshot.updatedAt!=null&&Date.now()-snapshot.updatedAt<=30_000
  const age=snapshot.updatedAt==null?'未知':`${Math.max(0,Math.round((Date.now()-snapshot.updatedAt)/60000))} 分钟前`
  const candles=tab==='1H'?snapshot.candles1h:snapshot.candles4h
  const upstreamConnected=/^connected$/i.test(snapshot.connectionStatus)
  const notificationsActive=settings.notificationsEnabled&&('Notification' in window)&&Notification.permission==='granted'
  return <div className="app"><header><div className="brand"><div className="logo">₿</div><div><strong>BTC 策略观察台</strong><span>OKX 永续合约 · 本地分析</span></div></div><div className="header-actions"><button className="icon-button" aria-label="刷新全部数据" title="刷新" onClick={load}><RefreshCw className={loading?'spin':''}/></button><button className="notify-button" onClick={enableNotifications}>{notificationsActive?<Bell/>:<BellOff/>}{notificationsActive?'通知已开启':'开启提醒'}</button><div className="connection-stack" aria-live="polite"><div className={`connection ${connected?'online':''}`}>{connected?<Wifi/>:<WifiOff/>}本地 {connected?'已连接':'重连中'}</div><div className={`connection ${upstreamConnected?'online':''}`}>{upstreamConnected?<Wifi/>:<WifiOff/>}OKX {upstreamConnected?'已连接':'异常'}</div></div></div></header>
    {stale&&<div className="stale-banner" role="alert"><AlertTriangle/><div><strong>行情数据已过期，已暂停生成新建议</strong><span>最近更新：{age}。系统会自动重连并回补缺口。</span></div></div>}
    {error&&<div className="service-banner" role="alert"><Database/><span>{error}</span><button onClick={load}>重试</button></div>}
    <main><section className="market-strip"><div className="instrument"><span>{snapshot.instrument}</span><strong>{snapshot.price==null?'—':`$${money(snapshot.price)}`}</strong><em>{connectionLabel(snapshot.connectionStatus)}</em></div><Metric label="市场状态" value={regimeLabels[stale?'STALE':(safeAdvice?.regime??'TRANSITION')]} hint="由 4H 建议引擎判定" tone={safeAdvice?.regime==='TREND'?'up':''}/><Metric label="资金费率" value={pct(snapshot.fundingRate)} hint={`计划结算 ${time(snapshot.fundingTime)}`}/><Metric label="公开持仓量" value={compact(snapshot.openInterest)} hint={`更新于 ${time(snapshot.openInterestTime)}`}/><Metric label="数据新鲜度" value={age} hint={`快照 ${time(snapshot.updatedAt)}`}/></section><HelpGuide/>
      <div className="dashboard-grid"><div className="primary-column"><section className="panel chart-panel"><div className="chart-tabs"><div><h3>BTC / USDT 价格</h3><span>仅已收盘 K 线参与策略计算</span></div><div role="tablist" aria-label="K 线周期">{(['1H','4H'] as const).map(x=><button key={x} role="tab" aria-selected={tab===x} className={tab===x?'active':''} onClick={()=>setTab(x)}>{x}</button>)}</div></div><PriceChart candles={candles} timeframe={tab}/></section><AdviceCard advice={safeAdvice} loading={loading}/><BacktestPanel/></div>
        <aside><Contributions advice={safeAdvice}/><RiskPanel settings={settings} estimate={safeAdvice?riskEstimate:null} onSave={async v=>{await api.saveSettings(v);setSettings(v);const fresh=await api.advice();setAdvice(fresh.advice);setRiskEstimate(fresh.riskEstimate);setToast('风险设置已保存')}}/><section className="panel status-panel"><div className="section-title"><span><ShieldCheck size={17}/>策略状态</span></div><div className="status-row"><span>配置版本</span><strong>{safeAdvice?.configVersion??'安全等待'}</strong></div><div className="status-row"><span>数据质量</span><strong>{safeAdvice?.dataQuality.fresh?'新鲜':'等待/过期'}</strong></div><div className="status-row"><span>信号 K 线</span><strong>{time(safeAdvice?.candleCloseAt)}</strong></div>{safeAdvice?.dataQuality.warnings.map(w=><div className="custom-warning" key={w}><AlertTriangle/>{w}</div>)}{Object.keys(settings.customParameters).length>0&&<div className="custom-warning"><AlertTriangle/>自定义参数，未经历史验证</div>}</section></aside></div>
      <TechnicalPanel data={technical} error={technicalError}/>
      <NewsPanel data={news} error={newsError}/>
      <section className="panel history-panel"><div className="section-title"><span><History size={17}/>信号记录</span><span className="muted">候选与观察信号均记录</span></div>{history.length?<div className="table-scroll"><table><caption className="sr-only">最近交易观察信号</caption><thead><tr><th scope="col">时间</th><th scope="col">策略</th><th scope="col">行动</th><th scope="col">方向分</th><th scope="col">置信度</th><th scope="col">触发参考</th></tr></thead><tbody>{history.slice(0,20).map((h,i)=><tr key={h.id??`${h.candleCloseAt}-${i}`}><td>{time(h.candleCloseAt)}</td><td>{h.strategy}</td><td><span className={`action-pill ${actionTone(h.action)}`}>{labels[h.action]}</span></td><td className={h.directionScore>=0?'up':'down'}>{signed(h.directionScore,0)}</td><td>{Math.round(confidencePercent(h.confidence))}%</td><td>{money(h.triggerPrice)}</td></tr>)}</tbody></table></div>:<div className="big-empty"><History/>尚无历史信号</div>}</section>
    </main><footer><span>仅供策略研究与教育用途，不构成个性化投资建议。加密资产波动剧烈，请独立判断并控制风险。</span><span>所有设置与权益数据仅保存在本机</span></footer><div className="sr-only" aria-live="polite">{connected?'本地服务已连接':'本地服务重连中'}，{upstreamConnected?'OKX 行情已连接':'OKX 行情异常'}</div>{toast&&<div className="toast" role="status" aria-live="polite" onAnimationEnd={()=>setToast('')}><Bell/>{toast}</div>}</div>
}
