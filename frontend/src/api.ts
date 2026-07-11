import type { AdviceAction, BacktestResult, Candle, MarketRegime, MarketSnapshot, Settings, SignalAdvice } from './types'

const num = (v: unknown): number | null => v === '' || v == null || !Number.isFinite(Number(v)) ? null : Number(v)
const epoch = (v: unknown): number | null => { const n = num(v); if (n != null) return n < 1e12 ? n * 1000 : n; const d = Date.parse(String(v)); return Number.isFinite(d) ? d : null }
const body = <T,>(v: unknown): T => ((v as { data?: T })?.data ?? v) as T
const get = async <T,>(path: string): Promise<T> => { const r = await fetch(path); if (!r.ok) throw new Error(`${r.status} ${r.statusText}`); return body<T>(await r.json()) }

const candle = (v: unknown): Candle => {
  if (Array.isArray(v)) return { timestamp: epoch(v[0]) ?? 0, open: num(v[1]) ?? 0, high: num(v[2]) ?? 0, low: num(v[3]) ?? 0, close: num(v[4]) ?? 0, volume: num(v[5]) ?? 0, confirm: String(v[8] ?? v[6]) === '1' }
  const x = v as Record<string, unknown>; return { timestamp: epoch(x.timestamp ?? x.ts) ?? 0, open: num(x.open ?? x.o) ?? 0, high: num(x.high ?? x.h) ?? 0, low: num(x.low ?? x.l) ?? 0, close: num(x.close ?? x.c) ?? 0, volume: num(x.volume ?? x.vol) ?? 0, confirm: x.confirm == null ? undefined : Boolean(Number(x.confirm)) }
}
const regime = (v: unknown): MarketRegime => ['TREND','RANGE','TRANSITION','STALE'].includes(String(v)) ? String(v) as MarketRegime : 'TRANSITION'
const action = (v: unknown): AdviceAction => ['LONG_CANDIDATE','SHORT_CANDIDATE','WATCH_LONG','WATCH_SHORT','WAIT'].includes(String(v)) ? String(v) as AdviceAction : 'WAIT'

export const api = {
  snapshot: async (): Promise<MarketSnapshot> => { const x = await get<Record<string, unknown>>('/api/market/snapshot'); return {
    instrument: String(x.instrument ?? 'BTC-USDT-SWAP'), price: num(x.price), updatedAt: epoch(x.updatedAt), stale: Boolean(x.stale), connectionStatus: String(x.connectionStatus ?? 'unknown'),
    fundingRate: num(x.fundingRate), fundingTime: epoch(x.fundingTime), openInterest: num(x.openInterest), openInterestTime: epoch(x.openInterestTime),
    candles1h: ((x.candles1H ?? x.candles1h ?? []) as unknown[]).map(candle), candles4h: ((x.candles4H ?? x.candles4h ?? []) as unknown[]).map(candle) } },
  advice: async (): Promise<SignalAdvice> => { const x = await get<Record<string, unknown>>('/api/advice/current'); return normalizeAdvice(x.advice ?? x) },
  history: async (): Promise<SignalAdvice[]> => { const x = await get<unknown>('/api/advice/history'); const rows = Array.isArray(x) ? x : ((x as { items?: unknown[] }).items ?? []); return rows.map(normalizeAdvice) },
  settings: async (): Promise<Settings> => { const x = await get<Record<string, unknown>>('/api/settings'); return { equity: num(x.equity), riskPercent: num(x.riskPercent), leverage: num(x.leverage), notificationsEnabled: Boolean(x.notificationsEnabled), feeBps: num(x.feeBps) ?? 5, slippageBps: num(x.slippageBps) ?? 5, customParameters: (x.customParameters ?? {}) as Record<string, number> } },
  saveSettings: async (settings: Settings): Promise<Settings> => { const r = await fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings) }); if (!r.ok) throw new Error(`${r.status} ${r.statusText}`); return settings },
  clearLocalData: async (): Promise<void> => { const r = await fetch('/api/local-data', { method: 'DELETE' }); if (!r.ok) throw new Error(`${r.status} ${r.statusText}`) },
  backtest: async (): Promise<BacktestResult> => { const r = await fetch('/api/backtests', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ strategy: 'combined', years: 3, feeBps: 5, slippageBps: 5 }) }); if (!r.ok) throw new Error(`${r.status} ${r.statusText}`); const job = body<Record<string, unknown>>(await r.json()); const id=String(job.id); for (;;) { await new Promise(resolve=>setTimeout(resolve,1500)); const state=await get<Record<string, unknown>>(`/api/backtests/${encodeURIComponent(id)}`); if(state.status==='failed') throw new Error(String(state.message??'回测失败')); if(state.status==='complete'&&state.result) return normalizeBacktest(state.result) } }
}

export function normalizeAdvice(v: unknown): SignalAdvice { const x = body<Record<string, unknown>>(v); const rawContrib = (x.contributions ?? []) as unknown[]; const contributions = rawContrib.map((c) => { const z = c as Record<string, unknown>; return { name: String(z.name ?? '指标'), score: num(z.score) ?? 0, value: num(z.value), explanation: String(z.explanation ?? '') } }); const q = (x.dataQuality ?? {}) as Record<string, unknown>; return {
  id: x.id == null ? undefined : String(x.id), instrument: String(x.instrument ?? 'BTC-USDT-SWAP'), strategy: String(x.strategy ?? 'combined'), candleCloseAt: epoch(x.candleCloseAt), action: action(x.action), directionScore: num(x.directionScore) ?? 0,
  confidence: num(x.confidence) ?? 0, contributions, explanation: String(x.explanation ?? ''), triggerPrice: num(x.triggerPrice), invalidation: String(x.invalidation ?? '等待下一根已收盘 K 线确认'), stopLoss: num(x.stopLoss), targets: ((x.targets ?? []) as unknown[]).map(num).filter((n): n is number => n != null), riskReward: ((x.riskReward ?? []) as unknown[]).map(num).filter((n): n is number => n != null), regime: regime(x.regime), dataQuality: { fresh: Boolean(q.fresh), lastCandleAt: epoch(q.lastCandleAt), fundingAvailable: Boolean(q.fundingAvailable), openInterestAvailable: Boolean(q.openInterestAvailable), warnings: Array.isArray(q.warnings) ? q.warnings.map(String) : [] }, configVersion: String(x.configVersion ?? 'validated-v1'), createdAt: epoch(x.createdAt) ?? undefined }
}
export function normalizeBacktest(v: unknown): BacktestResult { const x = body<Record<string, unknown>>(v); return { status: String(x.status ?? 'complete'), strategy: String(x.strategy ?? 'combined'), netReturn: num(x.netReturn), maxDrawdown: num(x.maxDrawdown), sharpe: num(x.sharpe), profitFactor: num(x.profitFactor), winRate: num(x.winRate), trades: num(x.trades), validation: String(x.validationLabel ?? (x.validationPass ? '通过历史验证' : '实验信号')), updatedAt: epoch(x.updatedAt) ?? undefined } }
