import type { AdviceAction, AdviceResponse, BacktestJob, BacktestParameters, BacktestResult, Candle, CandleTimeframeStatus, MarketRegime, MarketSnapshot, MarketTimeframe, NewsResponse, RiskEstimate, Settings, SignalAdvice, TechnicalGroup, TechnicalSummary } from './types'
import { normalizeBreakdown, normalizeSeries } from './backtest-utils.ts'
import { confidencePercent } from './ui-contracts.ts'

const num = (v: unknown): number | null => v === '' || v == null || !Number.isFinite(Number(v)) ? null : Number(v)
const epoch = (v: unknown): number | null => { const n = num(v); if (n != null) return n < 1e12 ? n * 1000 : n; const d = Date.parse(String(v)); return Number.isFinite(d) ? d : null }
const body = <T,>(v: unknown): T => ((v as { data?: T })?.data ?? v) as T
const get = async <T,>(path: string): Promise<T> => { const r = await fetch(path); if (!r.ok) throw new Error(`${r.status} ${r.statusText}`); return body<T>(await r.json()) }

export const normalizeCandle = (v: unknown): Candle | null => {
  const x=Array.isArray(v)?{timestamp:v[0],open:v[1],high:v[2],low:v[3],close:v[4],volume:v[5],confirm:String(v[8]??v[6])==='1'}:v&&typeof v==='object'?{timestamp:(v as Record<string,unknown>).timestamp??(v as Record<string,unknown>).ts,open:(v as Record<string,unknown>).open??(v as Record<string,unknown>).o,high:(v as Record<string,unknown>).high??(v as Record<string,unknown>).h,low:(v as Record<string,unknown>).low??(v as Record<string,unknown>).l,close:(v as Record<string,unknown>).close??(v as Record<string,unknown>).c,volume:(v as Record<string,unknown>).volume??(v as Record<string,unknown>).vol,confirm:(v as Record<string,unknown>).confirm}:null
  if(!x)return null
  const timestamp=epoch(x.timestamp),open=num(x.open),high=num(x.high),low=num(x.low),close=num(x.close),volume=num(x.volume)
  if(timestamp==null||timestamp<=0||open==null||high==null||low==null||close==null||volume==null||open<=0||high<=0||low<=0||close<=0||volume<0||low>Math.min(open,close)||high<Math.max(open,close)||low>high)return null
  return {timestamp,open,high,low,close,volume,confirm:x.confirm==null?undefined:typeof x.confirm==='boolean'?x.confirm:Boolean(Number(x.confirm))}
}
const regime = (v: unknown): MarketRegime => ['TREND','RANGE','TRANSITION','STALE'].includes(String(v)) ? String(v) as MarketRegime : 'TRANSITION'
const action = (v: unknown): AdviceAction => ['LONG_CANDIDATE','SHORT_CANDIDATE','WATCH_LONG','WATCH_SHORT','WAIT'].includes(String(v)) ? String(v) as AdviceAction : 'WAIT'
const status = (v: unknown): 'fresh'|'partial'|'unavailable' => ['fresh','partial'].includes(String(v).toLowerCase()) ? String(v).toLowerCase() as 'fresh'|'partial' : 'unavailable'
const strings = (v: unknown): string[] => Array.isArray(v) ? v.map(String) : []
const numbers = (v: unknown): number[] => Array.isArray(v) ? v.map(num).filter((x): x is number => x != null) : []
const safeUrl = (v: unknown): string => { try { const u=new URL(String(v)); return ['http:','https:'].includes(u.protocol) ? u.toString() : '' } catch { return '' } }
const wait = (ms:number,signal?:AbortSignal) => new Promise<void>((resolve,reject)=>{const timer=setTimeout(resolve,ms);signal?.addEventListener('abort',()=>{clearTimeout(timer);reject(new DOMException('已停止等待','AbortError'))},{once:true})})

const MARKET_TIMEFRAMES: readonly MarketTimeframe[] = ['1m', '15m', '1H', '4H']

function normalizeCandleStatus(value: unknown, candles: Record<MarketTimeframe, Candle[]>): Record<MarketTimeframe, CandleTimeframeStatus> {
  const raw = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  return Object.fromEntries(MARKET_TIMEFRAMES.map(timeframe => {
    const candidate = raw[timeframe]
    const row = candidate && typeof candidate === 'object' ? candidate as Record<string, unknown> : {}
    const latest = candles[timeframe].at(-1)?.timestamp ?? null
    const latestConfirmed = [...candles[timeframe]].reverse().find(candle => candle.confirm === true)?.timestamp ?? null
    const lastConfirmedAt = epoch(row.lastConfirmedAt) ?? latestConfirmed
    return [timeframe, {
      available: row.available == null ? candles[timeframe].length > 0 : Boolean(row.available),
      stale: row.stale == null ? false : Boolean(row.stale),
      lastAt: epoch(row.lastAt) ?? latest,
      lastConfirmedAt,
      confirmedStale: row.confirmedStale == null ? undefined : Boolean(row.confirmedStale),
      gapDetected: Boolean(row.gapDetected),
    }]
  })) as Record<MarketTimeframe, CandleTimeframeStatus>
}

export const api = {
  snapshot: async (): Promise<MarketSnapshot> => {
    const x = await get<Record<string, unknown>>('/api/market/snapshot')
    const readCandles = (value: unknown) => (Array.isArray(value) ? value : []).map(normalizeCandle).filter((row): row is Candle => row != null)
    const candles = {
      '1m': readCandles(x.candles1M ?? x.candles1m),
      '15m': readCandles(x.candles15M ?? x.candles15m),
      '1H': readCandles(x.candles1H ?? x.candles1h),
      '4H': readCandles(x.candles4H ?? x.candles4h),
    } satisfies Record<MarketTimeframe, Candle[]>
    return {
      instrument: String(x.instrument ?? 'BTC-USDT-SWAP'),
      price: num(x.price),
      markPrice: num(x.markPrice),
      markPriceTime: epoch(x.markPriceTime),
      updatedAt: epoch(x.updatedAt),
      stale: Boolean(x.stale),
      connectionStatus: String(x.connectionStatus ?? 'unknown'),
      fundingRate: num(x.fundingRate),
      fundingTime: epoch(x.fundingTime),
      openInterest: num(x.openInterest),
      openInterestTime: epoch(x.openInterestTime),
      candles1m: candles['1m'],
      candles15m: candles['15m'],
      candles1h: candles['1H'],
      candles4h: candles['4H'],
      candleStatus: normalizeCandleStatus(x.candleStatus, candles),
    }
  },
  advice: async (): Promise<AdviceResponse> => { const x = await get<Record<string, unknown>>('/api/advice/current'); return { advice: normalizeAdvice(x.advice ?? x), riskEstimate: normalizeRiskEstimate(x.riskEstimate) } },
  history: async (): Promise<SignalAdvice[]> => { const x = await get<unknown>('/api/advice/history'); const rows = Array.isArray(x) ? x : ((x as { items?: unknown[] }).items ?? []); return rows.map(normalizeAdvice) },
  technicalSummary: async (): Promise<TechnicalSummary> => normalizeTechnical(await get<unknown>('/api/technical/summary')),
  news: async (): Promise<NewsResponse> => normalizeNews(await get<unknown>('/api/news?limit=8')),
  settings: async (): Promise<Settings> => { const x = await get<Record<string, unknown>>('/api/settings'); return { equity: num(x.equity), riskPercent: num(x.riskPercent), leverage: num(x.leverage), notificationsEnabled: Boolean(x.notificationsEnabled), feeBps: num(x.feeBps) ?? 5, slippageBps: num(x.slippageBps) ?? 5, customParameters: (x.customParameters ?? {}) as Record<string, number> } },
  saveSettings: async (settings: Settings): Promise<Settings> => { const r = await fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings) }); if (!r.ok) throw new Error(`${r.status} ${r.statusText}`); return settings },
  clearLocalData: async (): Promise<void> => { const r = await fetch('/api/local-data', { method: 'DELETE' }); if (!r.ok) throw new Error(`${r.status} ${r.statusText}`) },
  startBacktest: async (parameters:BacktestParameters,signal?:AbortSignal):Promise<BacktestJob> => { const r=await fetch('/api/backtests',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(parameters),signal});if(!r.ok)throw new Error(r.status===409?'已有回测正在运行':`${r.status} ${r.statusText}`);const x=body<Record<string,unknown>>(await r.json());return normalizeBacktestJob(x) },
  backtestHistory: async (limit=10,signal?:AbortSignal):Promise<BacktestJob[]> => {const r=await fetch(`/api/backtests?limit=${Math.max(1,Math.min(50,limit))}`,{signal});if(!r.ok)throw new Error(`${r.status} ${r.statusText}`);const x=body<unknown>(await r.json());const rows=Array.isArray(x)?x:x&&typeof x==='object'&&Array.isArray((x as {items?:unknown[]}).items)?(x as {items:unknown[]}).items:null;if(!rows)throw new Error('回测历史响应格式不兼容');return rows.map(normalizeBacktestJob)},
  backtestStatus: async (id:string,signal?:AbortSignal):Promise<BacktestJob> => {const r=await fetch(`/api/backtests/${encodeURIComponent(id)}`,{signal});if(!r.ok)throw new Error(r.status===404?'回测记录已不存在':`${r.status} ${r.statusText}`);return normalizeBacktestJob(body<Record<string,unknown>>(await r.json()))},
  waitBacktest: async (id:string,options:{signal?:AbortSignal;timeoutMs?:number;onProgress?:(job:BacktestJob)=>void}={}):Promise<BacktestResult> => {const started=Date.now(),timeout=options.timeoutMs??6*60_000;for(;;){if(Date.now()-started>timeout)throw new Error('等待超时：后端任务可能仍在运行，请从最近任务恢复');await wait(1500,options.signal);const job=await api.backtestStatus(id,options.signal);options.onProgress?.(job);if(job.status==='failed'||job.status==='interrupted')throw new Error(job.message||(job.status==='interrupted'?'回测已中断':'回测失败'));if(job.status==='complete'){if(!job.result)throw new Error('回测完成但结果为空');return job.result}}
  }
}

export function normalizeAdvice(v: unknown): SignalAdvice { const x = body<Record<string, unknown>>(v); const rawContrib = (x.contributions ?? []) as unknown[]; const contributions = rawContrib.map((c) => { const z = c as Record<string, unknown>; return { name: String(z.name ?? '指标'), score: num(z.score) ?? 0, value: num(z.value), explanation: String(z.explanation ?? '') } }); const q = (x.dataQuality ?? {}) as Record<string, unknown>; return {
  id: x.id == null ? undefined : String(x.id), instrument: String(x.instrument ?? 'BTC-USDT-SWAP'), strategy: String(x.strategy ?? 'combined'), candleCloseAt: epoch(x.candleCloseAt), action: action(x.action), directionScore: num(x.directionScore) ?? 0, technicalScore: num(x.technicalScore) ?? num(x.directionScore) ?? 0, newsScore: num(x.newsScore) ?? 0,
  confidence: confidencePercent(x.confidence), contributions, explanation: String(x.explanation ?? ''), triggerPrice: num(x.triggerPrice), invalidation: String(x.invalidation ?? '等待下一根已收盘 K 线确认'), stopLoss: num(x.stopLoss), targets: ((x.targets ?? []) as unknown[]).map(num).filter((n): n is number => n != null), riskReward: ((x.riskReward ?? []) as unknown[]).map(num).filter((n): n is number => n != null), regime: regime(x.regime), dataQuality: { fresh: Boolean(q.fresh), lastCandleAt: epoch(q.lastCandleAt), fundingAvailable: Boolean(q.fundingAvailable), openInterestAvailable: Boolean(q.openInterestAvailable), warnings: Array.isArray(q.warnings) ? q.warnings.map(String) : [] }, configVersion: String(x.configVersion ?? 'research-v2-unvalidated'), createdAt: epoch(x.createdAt) ?? undefined }
}
export function normalizeRiskEstimate(v: unknown): RiskEstimate { const x=(v??{}) as Record<string,unknown>; return { equity:num(x.equity),riskPercent:num(x.riskPercent),leverage:num(x.leverage),entryPrice:num(x.entryPrice),stopLoss:num(x.stopLoss),stopDistance:num(x.stopDistance),referenceNotional:num(x.referenceNotional),quantityBtc:num(x.quantityBtc),warnings:strings(x.warnings) } }
export function normalizeBacktest(v: unknown): BacktestResult { const x = body<Record<string, unknown>>(v); const b=(x.benchmarks??{}) as Record<string,unknown>; const stressRaw=(x.stress??{}) as Record<string,unknown>; const strict=(x.strictValidation??{}) as Record<string,unknown>; const aggregate=(strict.aggregateOos??{}) as Record<string,unknown>;const holdout=(strict.holdout??{}) as Record<string,unknown>;const aggregateRow=normalizeBreakdown([{label:'滚动 OOS 汇总',...aggregate}])[0];const holdoutRow=normalizeBreakdown([{label:'最后 12 个月锁定验证',...((holdout.metrics??{}) as Record<string,unknown>)}])[0]; const stress=Object.fromEntries(Object.entries(stressRaw).map(([label,row])=>{const z=(row??{}) as Record<string,unknown>;return [label,{netReturn:num(z.netReturn??(typeof row==='number'?row:null)),maxDrawdown:num(z.maxDrawdown),profitFactor:num(z.profitFactor)}]})); const reason=String(x.reason??'').trim()||undefined; return { status: String(x.status ?? 'complete'), reason, strategy: String(x.strategy ?? 'combined'), netReturn: num(x.netReturn), maxDrawdown: num(x.maxDrawdown), sharpe: num(x.sharpe), sortino:num(x.sortino), calmar:num(x.calmar), profitFactor: num(x.profitFactor), winRate: num(x.winRate), trades: num(x.trades), exposure:num(x.exposure),maxConsecutiveLosses:num(x.maxConsecutiveLosses),averageBarsHeld:num(x.averageBarsHeld),benchmarks:Object.fromEntries(Object.entries(b).map(([k,value])=>[k,num(value)])),holdingRule:String(x.holdingRule??''),limitations:strings(x.limitations), validation: String(x.validationLabel ?? strict.validationLabel ?? (x.validationPass ? '通过历史验证' : '实验信号')),equityCurve:normalizeSeries(x.equityCurve??x.equitySeries??x.series),drawdownCurve:normalizeSeries(x.drawdownCurve??x.drawdownSeries),rollingWindows:normalizeBreakdown(strict.windows??x.rollingWindows??x.walkForwardWindows??x.windows),byYear:normalizeBreakdown(x.byYear),byRegime:normalizeBreakdown(x.byRegime??x.regimePerformance),aggregateOos:Object.keys(aggregate).length&&aggregateRow?{...aggregateRow,profitableWindowRatio:num(aggregate.profitableWindowRatio),windows:num(aggregate.windows)}:undefined,lockedHoldout:Object.keys(holdout).length&&holdoutRow?{...holdoutRow,start:epoch(holdout.start),end:epoch(holdout.end)}:undefined,thresholdPass:strict.thresholdPass==null?null:Boolean(strict.thresholdPass),diagnosticsComplete:strict.diagnosticsComplete==null?null:Boolean(strict.diagnosticsComplete),stress, updatedAt: epoch(x.updatedAt) ?? undefined } }
function attachValidationCriteria(result:BacktestResult,raw:unknown):BacktestResult {
  const x=(raw??{}) as Record<string,unknown>,strict=(x.strictValidation??{}) as Record<string,unknown>,criteria=(strict.criteria??{}) as Record<string,unknown>,coverage=(x.historyCoverage??{}) as Record<string,unknown>
  const entries=Object.entries(criteria).filter((row):row is [string,boolean]=>typeof row[1]==='boolean')
  const historyCoverage=Object.keys(coverage).length?{requestedStart:epoch(coverage.requestedStart),requestedEnd:epoch(coverage.requestedEnd),actualStart1H:epoch(coverage.actualStart1H),actualEnd1H:epoch(coverage.actualEnd1H),actualStart4H:epoch(coverage.actualStart4H),actualEnd4H:epoch(coverage.actualEnd4H),rows1H:num(coverage.rows1H),rows4H:num(coverage.rows4H),durationCoverage:num(coverage.durationCoverage),complete:Boolean(coverage.complete)}:undefined
  return {...result,...(entries.length?{validationCriteria:Object.fromEntries(entries)}:{}),...(historyCoverage?{historyCoverage}:{})}
}
export function normalizeBacktestJob(v:unknown):BacktestJob {const x=body<Record<string,unknown>>(v);const request=(x.request??{}) as Record<string,unknown>;const strategy=String(request.strategy??'');const parameters=['trend','range','combined'].includes(strategy)?{strategy:strategy as BacktestParameters['strategy'],years:num(request.years)??3,feeBps:num(request.feeBps)??5,slippageBps:num(request.slippageBps)??5}:null;return {id:String(x.id??''),status:String(x.status??'unknown'),progress:Math.max(0,Math.min(1,num(x.progress)??0)),message:String(x.message??''),result:x.result?attachValidationCriteria(normalizeBacktest(x.result),x.result):null,parameters,createdAt:epoch(x.createdAt),updatedAt:epoch(x.updatedAt),startedAt:epoch(x.startedAt),finishedAt:epoch(x.finishedAt)}}

export function normalizeTechnical(v: unknown): TechnicalSummary {
  const x = body<Record<string, unknown>>(v); const vp = (x.volumeProfile ?? {}) as Record<string, unknown>; const sr = (x.supportResistance ?? {}) as Record<string, unknown>; const vol=(x.volatility??{}) as Record<string,unknown>; const risk=(x.riskOverlay??{}) as Record<string,unknown>
  const groups: TechnicalGroup[] = ((x.groups ?? []) as unknown[]).map((row, i) => { const g = row as Record<string, unknown>; const raw = g.metrics; const metrics = Array.isArray(raw) ? raw.map(m => { const z=m as Record<string,unknown>; const t=String(z.tone??'neutral'); return { label:String(z.label??z.name??'指标'), value:String(z.value??'—'), tone:(['positive','negative'].includes(t)?t:'neutral') as 'positive'|'negative'|'neutral' } }) : Object.entries((raw??{}) as Record<string,unknown>).map(([label,value])=>({label,value:String(value),tone:'neutral' as const})); const summary=String(g.summary??''); const match=summary.match(/([+-]?\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)/); const score=num(g.score)??(match?Number(match[1]):null); const cap=num(g.cap)??(match?Number(match[2]):null); const ratio=score!=null&&cap?score/cap:0; const direction=ratio>=.35?'偏多':ratio<=-.35?'偏空':'中性'; return { key:String(g.key??`group-${i}`), label:String(g.label??g.name??'技术组'), score, cap, direction, summary, metrics } })
  return { asOf:epoch(x.asOf), status:status(x.status), groups, technicalScore:num(x.technicalScore), vwap:num(x.vwap), weeklyVwap:num(x.weeklyVwap), mfi:num(x.mfi), poc:num(x.poc??vp.poc), vah:num(x.vah??vp.vah), val:num(x.val??vp.val), supports:numbers(x.supports??sr.supports), resistances:numbers(x.resistances??sr.resistances), momentum:String(x.momentum??'数据不足'), volatilityPhase:String(x.volatilityPhase??'数据不足'), atrPercentile:num(x.atrPercentile??vol.atrPercentile), bollingerWidthPercentile:num(x.bollingerWidthPercentile??vol.bollingerWidthPercentile), positionScale:num(x.positionScale??risk.positionScale), riskReasons:strings(x.riskReasons??risk.reasons), warnings:strings(x.warnings) }
}

export function normalizeNews(v: unknown): NewsResponse {
  const x=body<Record<string,unknown>>(v)
  const a=(x.analysis??{}) as Record<string,unknown>
  const items=((x.items??[]) as unknown[]).filter(row=>row!=null&&typeof row==='object').map((row,i)=>{
    const n=row as Record<string,unknown>
    const rawSentiment=num(n.sentiment)??0
    const sentiment: -1|0|1=rawSentiment>0?1:rawSentiment<0?-1:0
    const rawBreakdown=n.weightBreakdown!=null&&typeof n.weightBreakdown==='object'?n.weightBreakdown as Record<string,unknown>:{}
    const weightBreakdown=Object.fromEntries(Object.entries(rawBreakdown).map(([key,value])=>[key,num(value)]).filter((entry):entry is [string,number]=>entry[1]!=null))
    const rawSourceCount=num(n.sourceCount)
    const rawDecay=num(n.timeDecay)
    return {
      id:String(n.id??`${n.source??'news'}-${i}`),title:String(n.title??'未命名消息'),url:safeUrl(n.url),source:String(n.source??'未知来源'),sources:strings(n.sources),publishedAt:epoch(n.publishedAt),latestPublishedAt:epoch(n.latestPublishedAt),summary:String(n.summary??''),category:String(n.category??'其他'),
      importance:Math.round(Math.max(1,Math.min(5,num(n.importance)??1))),importanceLabel:String(n.importanceLabel??'低'),sentiment,sentimentLabel:String(n.sentimentLabel??(sentiment>0?'利多':sentiment<0?'利空':'中性')),directionConfidence:num(n.directionConfidence)==null?null:Math.max(0,Math.min(1,num(n.directionConfidence)!)),relevance:Math.max(0,Math.min(1,num(n.relevance)??0)),reason:String(n.reason??''),importanceScore:num(n.importanceScore),effectiveImpact:num(n.effectiveImpact),sourceCount:rawSourceCount==null?null:Math.max(0,Math.floor(rawSourceCount)),ageHours:num(n.ageHours),halfLifeHours:num(n.halfLifeHours),timeDecay:rawDecay==null?null:Math.max(0,Math.min(1,rawDecay)),weightBreakdown,weightFormula:String(n.weightFormula??'')
    }
  })
  const normalizedStatus=status(a.status)
  const rawSources=Array.isArray(a.sourceStatus)?a.sourceStatus:[]
  const sourceStatus=rawSources.filter(row=>row!=null&&typeof row==='object').map(row=>{const source=row as Record<string,unknown>;return {source:String(source.source??'unknown'),ok:Boolean(source.ok),itemCount:Math.max(0,Math.floor(num(source.itemCount)??0)),observedAt:epoch(source.observedAt),error:source.error==null?null:String(source.error)}})
  return {items,analysis:{asOf:epoch(a.asOf),windowHours:num(a.windowHours)??48,score:normalizedStatus==='unavailable'?0:Math.max(-15,Math.min(15,num(a.score)??0)),articleCount:num(a.articleCount)??items.length,status:normalizedStatus,sourceCoverage:Math.max(0,Math.min(1,num(a.sourceCoverage)??0)),sourceStatus,rawImpact:num(a.rawImpact),coverageAdjustedImpact:num(a.coverageAdjustedImpact),reason:String(a.reason??''),warnings:strings(a.warnings)}}
}
