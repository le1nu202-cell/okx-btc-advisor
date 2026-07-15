import type { TimeframeAnalysis } from '../market-analysis-types'

const display = (value: number | null, text: string) => text || (value == null ? '—' : Number(value.toFixed(6)).toString())

export default function IndicatorContributionList({ analysis }: { analysis: TimeframeAnalysis }) {
  if (!analysis.contributions.length && !analysis.indicators.length) return <div className="ma-indicator-empty">{analysis.timeframe} 暂无后端指标明细。</div>
  return <div className="ma-indicator-detail" aria-label={`${analysis.timeframe} 指标贡献`}>
    <div className="ma-contribution-list">
      {analysis.contributions.map(row => <div className={`ma-contribution-row ${row.available ? '' : 'unavailable'}`} key={row.key}>
        <div className="ma-contribution-head"><strong>{row.name}</strong><b>{row.available && row.score != null ? `${row.score >= 0 ? '+' : ''}${row.score}${row.maxScore == null ? '' : ` / ${row.maxScore}`}` : '不可用'}</b></div>
        <p>{row.valueText || (row.value == null ? '' : `指标值 ${display(row.value, '')}`)}</p>
        <p>{row.explanation || (row.available ? '后端未提供补充说明' : '本指标当前不可用')}</p>
      </div>)}
    </div>
    {analysis.indicators.length ? <dl className="ma-indicator-values">
      {analysis.indicators.map(row => <div key={row.key}><dt>{row.label}</dt><dd>{display(row.value, row.valueText)}</dd></div>)}
    </dl> : null}
  </div>
}
