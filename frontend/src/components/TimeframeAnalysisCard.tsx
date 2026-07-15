import type { AnalysisTimeframe, TimeframeAnalysis } from '../market-analysis-types'
import { biasLabel } from '../market-analysis-types'

const value = (input: number | null, suffix = '') => input == null ? '—' : `${input.toFixed(0)}${suffix}`
const structureLabel = (input: string) => ({ HH_HL: '高低点抬高', LH_LL: '高低点下移', RANGE: '区间', TRANSITION: '过渡', UNKNOWN: '未知' }[input] ?? input)
const volatilityLabel = (input: string) => ({ COMPRESSION: '压缩', NORMAL: '正常', EXPANSION: '扩张', EXTREME: '极端', UNKNOWN: '未知' }[input] ?? input)

export default function TimeframeAnalysisCard({ timeframe, analysis }: { timeframe: AnalysisTimeframe; analysis: TimeframeAnalysis | null }) {
  const unavailable = !analysis || /INSUFFICIENT|UNAVAILABLE|INVALID|GAP/i.test(analysis.status)
  return <article className={`ma-timeframe-card ${unavailable ? 'unavailable' : ''}`} aria-label={`${timeframe} 周期分析`}>
    <div className="ma-timeframe-head"><h3>{timeframe}</h3><span>{unavailable ? '数据不足' : analysis.status}</span></div>
    <p className="ma-timeframe-bias">{analysis ? biasLabel(analysis.bias) : '数据不足'}</p>
    <p className="ma-timeframe-summary">{analysis?.summary || analysis?.primaryReason || '后端没有足够的已收盘 K 线形成结论。'}</p>
    <dl className="ma-timeframe-stats">
      <div><dt>趋势强度</dt><dd>{value(analysis?.trendStrength ?? null, '%')}</dd></div>
      <div><dt>市场结构</dt><dd>{analysis ? structureLabel(analysis.structure) : '—'}</dd></div>
      <div><dt>波动状态</dt><dd>{analysis ? volatilityLabel(analysis.volatilityState) : '—'}</dd></div>
    </dl>
    {analysis?.dataQuality.warnings.length ? <small>{analysis.dataQuality.warnings[0]}</small> : null}
  </article>
}
