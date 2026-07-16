import type { KeyLevel, MarketAnalysis } from '../market-analysis-types'

const money = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString('en-US', { maximumFractionDigits: 2 })
const kindLabel = (value: string) => /SUPPORT/i.test(value) ? '支撑' : /RESIST/i.test(value) ? '阻力' : /INVALID/i.test(value) ? '失效位' : '价格区'
const strength = (level: KeyLevel) => level.strengthLabel || (level.strength == null ? '强度未提供' : `强度 ${Number(level.strength.toFixed(2))}`)

export default function KeyLevelPanel({ analysis }: { analysis: MarketAnalysis | null }) {
  return <article className="ma-key-level-panel" aria-label="后端关键价位">
    <h3>关键价位</h3>
    <div className="ma-key-level-summary">
      <div><span>最近支撑</span><strong>{money(analysis?.nearestSupport)} USDT</strong></div>
      <div><span>最近阻力</span><strong>{money(analysis?.nearestResistance)} USDT</strong></div>
      <div><span>结论失效位</span><strong>{money(analysis?.invalidationLevel)} USDT</strong></div>
    </div>
    {analysis?.keyLevels.length ? <ul>{analysis.keyLevels.map(level => <li key={level.id}><strong>{kindLabel(level.kind)} {money(level.price)}</strong> · {level.label} · {strength(level)}{level.distanceAtr == null ? '' : ` · 距当前 ${level.distanceAtr.toFixed(2)} ATR`}</li>)}</ul> : <p>后端尚未形成可靠的关键位聚类。</p>}
  </article>
}
