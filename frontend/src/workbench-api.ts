import { api as marketApi } from './api'
import type { AddCheck, ReplaySession, RiskCalculation, TradeActionPayload, TradeLog, TradePlanDraft, TradePlanRecord, TradeStats } from './workbench-types'

const errorMessage = (body: unknown, status: number, statusText: string) => {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return detail.map(row => {
      if (!row || typeof row !== 'object') return String(row)
      return String((row as { msg?: unknown }).msg ?? '输入不符合要求')
    }).join('；')
  }
  return `${status} ${statusText}`
}

const json = async <T,>(response: Response): Promise<T> => {
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) throw new Error(errorMessage(body, response.status, response.statusText))
  return body as T
}

const request = async <T,>(path: string, method = 'GET', body?: unknown) => json<T>(await fetch(path, {
  method,
  headers: body == null ? undefined : { 'Content-Type': 'application/json' },
  body: body == null ? undefined : JSON.stringify(body),
}))

export const workbenchApi = {
  snapshot: marketApi.snapshot,
  current: () => request<TradePlanRecord>('/api/trade-plans/current'),
  calculate: (plan: TradePlanDraft) => request<RiskCalculation>('/api/workbench/calculate', 'POST', plan),
  create: (plan: TradePlanDraft) => request<TradePlanRecord>('/api/trade-plans', 'POST', plan),
  update: (id: string, plan: TradePlanDraft) => request<TradePlanRecord>(`/api/trade-plans/${encodeURIComponent(id)}`, 'PUT', plan),
  action: (id: string, payload: TradeActionPayload) => request<{ plan: TradePlanRecord; log: TradeLog | null }>(`/api/trade-plans/${encodeURIComponent(id)}/actions`, 'POST', payload),
  addCheck: () => request<AddCheck>('/api/workbench/add-check'),
  logs: (source: 'live' | 'replay' = 'live') => request<{ items: TradeLog[] }>(`/api/trade-logs?source=${source}&limit=500`),
  statistics: (source: 'live' | 'replay' = 'live') => request<TradeStats>(`/api/trade-logs/statistics?source=${source}`),
  createReplay: (mode: 'random' | 'manual', startAt?: number | null) => request<ReplaySession>('/api/replay/sessions', 'POST', { mode, startAt }),
  setReplayPlan: (id: string, plan: TradePlanDraft) => request<ReplaySession>(`/api/replay/sessions/${encodeURIComponent(id)}/plan`, 'PUT', { plan }),
  replayStep: (id: string, count = 1) => request<ReplaySession>(`/api/replay/sessions/${encodeURIComponent(id)}/step?count=${count}`, 'POST'),
  replayAction: (id: string, payload: TradeActionPayload) => request<ReplaySession>(`/api/replay/sessions/${encodeURIComponent(id)}/actions`, 'POST', payload),
}
