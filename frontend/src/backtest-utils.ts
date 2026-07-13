import type { BacktestBreakdownRow, BacktestSeriesPoint } from './types'

export const BACKTEST_BENCHMARK_LABELS={cash:'现金基准',buyHold1x:'1x 买入持有基准',ema20_50:'EMA20/50 基准'} as const

const finite = (value: unknown): number | null => value == null || value === '' || !Number.isFinite(Number(value)) ? null : Number(value)

export function normalizeSeries(value: unknown): BacktestSeriesPoint[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((row) => {
    if (Array.isArray(row)) {
      const timestamp=finite(row[0]), point=finite(row[1])
      return timestamp == null || point == null ? [] : [{ timestamp: timestamp < 1e12&&timestamp>10_000_000 ? timestamp*1000 : timestamp, value: point }]
    }
    const item=(row??{}) as Record<string,unknown>
    const rawTime=item.timestamp??item.time??item.date??item.ts
    const parsed=typeof rawTime==='string'&&!Number.isFinite(Number(rawTime)) ? Date.parse(rawTime) : finite(rawTime)
    const point=finite(item.value??item.equity??item.drawdown??item.netValue)
    if (parsed == null || !Number.isFinite(parsed) || point == null) return []
    return [{timestamp:parsed < 1e12&&parsed>10_000_000 ? parsed*1000:parsed,value:point}]
  }).sort((a,b)=>a.timestamp-b.timestamp)
}

export function normalizeBreakdown(value: unknown): BacktestBreakdownRow[] {
  const entries:Array<[string,unknown]>=Array.isArray(value)
    ? value.map((row,index)=>[String((row as Record<string,unknown>)?.label??(row as Record<string,unknown>)?.window??index+1),row])
    : value&&typeof value==='object' ? Object.entries(value as Record<string,unknown>) : []
  return entries.map(([label,row])=>{const item=(row??{}) as Record<string,unknown>;const source=((item.oosMetrics??item.metrics??item)??{}) as Record<string,unknown>;const formatDate=(raw:unknown)=>{const value=finite(raw);return value==null?'?':new Date(value<1e12?value*1000:value).toISOString().slice(0,10)};const range=item.oosStart!=null||item.oosEnd!=null?`OOS ${formatDate(item.oosStart)} → ${formatDate(item.oosEnd)}`:'';const prefix=item.index!=null?`窗口 ${item.index}`:'';return {
    label:String(item.label??item.window??item.year??item.regime??([prefix,range].filter(Boolean).join(' · ')||label)), trades:finite(source.trades),
    netReturn:finite(source.netReturn),maxDrawdown:finite(source.maxDrawdown),sharpe:finite(source.sharpe),winRate:finite(source.winRate),
    passed:source.passed==null&&source.validationPass==null?null:Boolean(source.passed??source.validationPass),
  }})
}
