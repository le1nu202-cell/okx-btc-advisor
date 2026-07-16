import type { TradeEquityPoint, TradeLog, TradeStats } from './workbench-types'

export type HistoryDirectionFilter = 'ALL' | 'LONG' | 'SHORT'
export type HistoryOutcomeFilter = 'ALL' | 'PROFIT' | 'LOSS' | 'BREAKEVEN'
export type HistoryStateFilter = 'ALL' | 'TAKE_PROFIT' | 'STOPPED'

export interface HistoryFilters {
  direction: HistoryDirectionFilter
  outcome: HistoryOutcomeFilter
  state: HistoryStateFilter
  dateFrom: string
  dateTo: string
}

export const DEFAULT_HISTORY_FILTERS: HistoryFilters = {
  direction: 'ALL', outcome: 'ALL', state: 'ALL', dateFrom: '', dateTo: '',
}

const startOfDate = (value: string) => value ? new Date(`${value}T00:00:00`).getTime() : null
const endOfDate = (value: string) => value ? new Date(`${value}T23:59:59.999`).getTime() : null

export function filterTradeLogs(logs: readonly TradeLog[], filters: HistoryFilters): TradeLog[] {
  const from = startOfDate(filters.dateFrom)
  const to = endOfDate(filters.dateTo)
  return logs.filter(log => {
    if (filters.direction !== 'ALL' && log.direction !== filters.direction) return false
    if (filters.state !== 'ALL' && log.state !== filters.state) return false
    if (filters.outcome === 'PROFIT' && !(log.netPnl > 0)) return false
    if (filters.outcome === 'LOSS' && !(log.netPnl < 0)) return false
    if (filters.outcome === 'BREAKEVEN' && log.netPnl !== 0) return false
    if (from != null && log.closedAt < from) return false
    if (to != null && log.closedAt > to) return false
    return true
  }).sort((left, right) => right.closedAt - left.closedAt || right.id.localeCompare(left.id))
}

export function backendEquityCurve(stats: TradeStats | null): TradeEquityPoint[] {
  if (!Array.isArray(stats?.equityCurve)) return []
  return stats.equityCurve
    .filter(point => point.equity != null && [point.timestamp, point.cumulativeNetPnl, point.equity].every(Number.isFinite))
    .sort((left, right) => left.timestamp - right.timestamp)
}

export function actualQuantity(log: TradeLog, key: 'initial' | 'add' | 'reduce' | 'exit'): number | null {
  const value = log.actualQuantitiesBtc?.[key]
  return value != null && Number.isFinite(value) ? value : null
}

export function actualPrice(log: TradeLog, key: 'initial' | 'add' | 'reduce' | 'exit'): number | null {
  const value = log.actualPrices?.[key]
  return value != null && Number.isFinite(value) ? value : null
}
