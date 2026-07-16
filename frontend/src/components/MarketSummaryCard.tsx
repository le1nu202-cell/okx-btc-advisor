import {
  actionContextLabel,
  analysisIsStale,
  biasLabel,
  regimeLabel,
  timeframeAnalysis,
  type MarketAnalysis,
} from '../market-analysis-types'
import '../market-analysis.css'

const percent = (value: number | null) => value == null ? '—' : `${value.toFixed(0)}%`
const updated = (value: number | null) => value == null ? '更新时间未知' : new Intl.DateTimeFormat('zh-CN', {
  month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
}).format(value)

export default function MarketSummaryCard({ analysis, onOpen }: { analysis: MarketAnalysis | null; onOpen: () => void }) {
  const fourHour = timeframeAnalysis(analysis, '4H')
  const oneHour = timeframeAnalysis(analysis, '1H')
  const stale = analysisIsStale(analysis)
  const noChase = /NO_CHASE|NO CHASE/i.test(analysis?.actionContext ?? '')
  return <section className={`market-analysis-summary-card ${stale ? 'stale' : ''}`} aria-label="市场分析摘要">
    <div className="ma-summary-card-head">
      <div><p>市场环境</p><h3>{analysis ? biasLabel(analysis.overallBias) : '等待后端分析'}</h3></div>
      <span className={`ma-summary-action ${stale || noChase ? 'danger' : ''}`}>{stale ? '数据不可用或过期' : actionContextLabel(analysis?.actionContext ?? '')}</span>
    </div>
    <div className="ma-summary-periods">
      <div><span>4H 环境</span><strong>{fourHour ? `${biasLabel(fourHour.bias)} · ${regimeLabel(fourHour.regime)}` : '数据不足'}</strong></div>
      <div><span>1H 环境</span><strong>{oneHour ? `${biasLabel(oneHour.bias)} · ${regimeLabel(oneHour.regime)}` : '数据不足'}</strong></div>
    </div>
    <p className="ma-summary-text">{analysis?.summary || '后端尚未返回可用的多周期市场分析。'}</p>
    <div className="ma-summary-meta"><span>一致度 {percent(analysis?.alignmentScore ?? null)}</span><span>{updated(analysis?.asOf ?? null)}</span></div>
    <button className="ma-summary-open" type="button" onClick={onOpen}>打开完整市场分析</button>
    <p className="ma-summary-disclaimer">只读辅助，不修改计划、不代表行情成功概率，也不会自动下单。</p>
  </section>
}
