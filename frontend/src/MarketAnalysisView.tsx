import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import AnalysisHistoryChart from './components/AnalysisHistoryChart'
import IndicatorContributionList from './components/IndicatorContributionList'
import KeyLevelPanel from './components/KeyLevelPanel'
import MarketAnalysisChart from './components/MarketAnalysisChart'
import TimeframeAnalysisCard from './components/TimeframeAnalysisCard'
import { marketAnalysisApi, normalizeMarketAnalysis } from './market-analysis-api'
import {
  ANALYSIS_TIMEFRAMES,
  actionContextLabel,
  analysisIsStale,
  biasLabel,
  type MarketAnalysis,
  type MarketAnalysisValidation,
  type MarketPlanOverlay,
} from './market-analysis-types'
import type { MarketSnapshot } from './types'
import './market-analysis.css'

const number = (value: number | null, digits = 1) => value == null ? '—' : value.toFixed(digits)
const time = (value: number | null) => value == null ? '等待后端数据' : new Date(value).toLocaleString('zh-CN', { hour12: false })

export default function MarketAnalysisView() {
  const [analysis, setAnalysis] = useState<MarketAnalysis | null>(null)
  const [history, setHistory] = useState<MarketAnalysis[]>([])
  const [validation, setValidation] = useState<MarketAnalysisValidation | null>(null)
  const [snapshot, setSnapshot] = useState<MarketSnapshot | null>(null)
  const [planOverlay, setPlanOverlay] = useState<MarketPlanOverlay | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [secondaryWarnings, setSecondaryWarnings] = useState<string[]>([])
  const [socketConnected, setSocketConnected] = useState(false)
  const loadSequence = useRef(0)
  const historyRequestSequence = useRef(0)

  const load = useCallback(async () => {
    const sequence = ++loadSequence.current
    setError('')
    setSecondaryWarnings([])
    const currentRequest = marketAnalysisApi.current()
    const historySequence = ++historyRequestSequence.current
    const secondaryRequest = Promise.allSettled([
      marketAnalysisApi.history(120),
      marketAnalysisApi.snapshot(),
      marketAnalysisApi.currentPlanOverlay(),
    ])
    const validationRequest = marketAnalysisApi.validation()
    try {
      const current = await currentRequest
      if (sequence === loadSequence.current) setAnalysis(current)
    } catch (reason) {
      if (sequence === loadSequence.current) setError(reason instanceof Error ? reason.message : '当前分析加载失败')
    } finally {
      if (sequence === loadSequence.current) setLoading(false)
    }
    void secondaryRequest.then(results => {
      if (sequence !== loadSequence.current) return
      const [historyResult, snapshotResult, planResult] = results
      if (historyResult.status === 'fulfilled' && historySequence === historyRequestSequence.current) setHistory(historyResult.value)
      if (snapshotResult.status === 'fulfilled') setSnapshot(snapshotResult.value)
      if (planResult.status === 'fulfilled') setPlanOverlay(planResult.value)
      const labels = ['分析历史', '公开行情', '当前计划']
      setSecondaryWarnings(results.flatMap((result, index) => {
        if (result.status !== 'rejected') return []
        if (index === 0 && historySequence !== historyRequestSequence.current) return []
        return [`${labels[index]}暂不可用`]
      }))
    })
    void validationRequest.then(value => {
      if (sequence === loadSequence.current) setValidation(value)
    }).catch(() => {
      if (sequence === loadSequence.current) setSecondaryWarnings(previous => [...previous, '验证结果暂不可用'])
    })
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 60_000)
    return () => window.clearInterval(timer)
  }, [load])

  useEffect(() => {
    let socket: WebSocket | undefined
    let retryTimer = 0
    let retry = 0
    let stopped = false
    const connect = () => {
      const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
      socket = new WebSocket(`${protocol}://${location.host}/ws/live`)
      socket.onopen = () => { setSocketConnected(true); retry = 0 }
      socket.onclose = () => {
        setSocketConnected(false)
        if (!stopped) retryTimer = window.setTimeout(connect, Math.min(30_000, 1_000 * 2 ** retry++))
      }
      socket.onerror = () => socket?.close()
      socket.onmessage = event => {
        try {
          const message = JSON.parse(event.data) as { type?: string; analysis?: unknown; persisted?: boolean }
          if (message.type !== 'marketAnalysis' || message.analysis == null) return
          const next = normalizeMarketAnalysis(message.analysis)
          setAnalysis(next)
          setSocketConnected(true)
          // WS frames update only the live current value. A backend-confirmed
          // SQLite insert triggers a fresh REST read; the WS payload itself is
          // never inserted into the persisted history curve.
          if (message.persisted === true) {
            const historySequence = ++historyRequestSequence.current
            void marketAnalysisApi.history(120).then(items => {
              if (!stopped && historySequence === historyRequestSequence.current) setHistory(items)
            }).catch(() => {
              if (!stopped && historySequence === historyRequestSequence.current) setSecondaryWarnings(previous => previous.includes('分析历史暂不可用') ? previous : [...previous, '分析历史暂不可用'])
            })
          }
        } catch {
          // Malformed public market frames are ignored; the periodic REST refresh remains authoritative.
        }
      }
    }
    connect()
    return () => {
      stopped = true
      window.clearTimeout(retryTimer)
      socket?.close()
    }
  }, [])

  const stale = analysisIsStale(analysis) || Boolean(snapshot?.stale)
  const action = analysis?.actionContext ?? ''
  const noChase = /NO_CHASE|NO CHASE/i.test(action)
  const reasons = useMemo(() => [
    ...(analysis?.supportingReasons ?? []),
    ...(analysis?.primaryReason ? [analysis.primaryReason] : []),
  ].filter((row, index, rows) => row && rows.indexOf(row) === index), [analysis])

  if (loading && !analysis) return <main className="market-analysis-page"><div className="ma-loading" role="status">正在读取后端市场分析…</div></main>

  return <main className="market-analysis-page">
    <header className="ma-page-header">
      <div>
        <p className="ma-eyebrow">BTC-USDT-SWAP · 公共行情</p>
        <h1>市场分析</h1>
        <p>先看结论，再看周期、图表与证据。所有分数、指标和关键位均由后端给出。</p>
      </div>
      <button type="button" className="ma-refresh" onClick={() => void load()}>刷新分析</button>
    </header>

    {error ? <div className="ma-banner ma-banner-danger" role="alert">当前分析不可用：{error}</div> : null}
    {stale ? <div className="ma-banner ma-banner-danger" role="alert">分析或公开行情已过期，请勿把旧数据当作当前判断。</div> : null}
    {secondaryWarnings.length ? <div className="ma-banner" role="status">{secondaryWarnings.join('；')}</div> : null}

    <section className={`ma-overall ${noChase ? 'ma-overall-no-chase' : ''}`} aria-labelledby="ma-overall-title">
      <div className="ma-overall-copy">
        <p className="ma-layer-label">第一层 · 总体结论</p>
        <h2 id="ma-overall-title">{biasLabel(analysis?.overallBias ?? '')}</h2>
        <span className={`ma-action-pill ${noChase ? 'danger' : ''}`}>{actionContextLabel(action)}</span>
        {noChase ? <p className="ma-no-chase" role="alert">禁止追价：等待价格与结构重新给出合理位置。</p> : null}
        <p className="ma-overall-summary">{analysis?.summary || analysis?.primaryReason || '后端暂未提供总体文字结论，请以周期卡片和数据质量为准。'}</p>
      </div>
      <dl className="ma-score-grid">
        <div><dt>综合分</dt><dd>{number(analysis?.compositeScore ?? null)}</dd></div>
        <div><dt>趋势强度</dt><dd>{number(analysis?.trendStrength ?? null)}</dd></div>
        <div><dt>周期一致度</dt><dd>{number(analysis?.alignmentScore ?? null)}%</dd></div>
        <div><dt>置信度</dt><dd>{number(analysis?.confidence ?? null)}%</dd></div>
      </dl>
      <div className="ma-meta">
        <span>模型 {analysis?.modelVersion || '—'}</span>
        <span>更新 {time(analysis?.asOf ?? null)}</span>
        <span>实时通道 {socketConnected ? '已连接' : '重连中'}</span>
      </div>
    </section>

    <section aria-labelledby="ma-timeframes-title">
      <div className="ma-section-heading"><div><p className="ma-layer-label">第二层 · 多周期结构</p><h2 id="ma-timeframes-title">四个周期一眼对齐</h2></div></div>
      <div className="ma-timeframe-grid">
        {ANALYSIS_TIMEFRAMES.map(period => <TimeframeAnalysisCard key={period} timeframe={period} analysis={analysis?.timeframeAnalyses.find(row => row.timeframe === period) ?? null}/>) }
      </div>
    </section>

    <section className="ma-panel ma-chart-panel" aria-labelledby="ma-chart-title">
      <div className="ma-section-heading">
        <div><p className="ma-layer-label">第三层 · 价格结构</p><h2 id="ma-chart-title">K 线与后端指标</h2></div>
        <p>默认只显示 EMA20/50 与关键位；按需打开其他图层。</p>
      </div>
      <MarketAnalysisChart analysis={analysis} snapshot={snapshot} planOverlay={planOverlay}/>
    </section>

    <section aria-labelledby="ma-evidence-title">
      <div className="ma-section-heading"><div><p className="ma-layer-label">第四层 · 证据与风险</p><h2 id="ma-evidence-title">为什么这样判断</h2></div></div>
      <div className="ma-evidence-grid">
        <article className="ma-reason-card supportive"><h3>支持证据</h3>{reasons.length ? <ul>{reasons.map((reason, index) => <li key={`${index}-${reason}`}>{reason}</li>)}</ul> : <p>后端暂无支持证据。</p>}</article>
        <article className="ma-reason-card conflicting"><h3>冲突证据</h3>{analysis?.conflictingReasons.length ? <ul>{analysis.conflictingReasons.map((reason, index) => <li key={`${index}-${reason}`}>{reason}</li>)}</ul> : <p>后端未报告显著冲突。</p>}</article>
        <article className="ma-reason-card warning"><h3>风险警告</h3>{analysis?.riskWarnings.length ? <ul>{analysis.riskWarnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul> : <p>后端未报告额外警告。</p>}</article>
        <KeyLevelPanel analysis={analysis}/>
      </div>
    </section>

    <section className="ma-panel" aria-labelledby="ma-details-title">
      <details className="ma-details">
        <summary id="ma-details-title"><span><span className="ma-layer-label">第五层 · 指标明细</span>展开后端指标贡献</span><span aria-hidden="true">＋</span></summary>
        <div className="ma-details-content">
          {ANALYSIS_TIMEFRAMES.map(period => {
            const row = analysis?.timeframeAnalyses.find(item => item.timeframe === period) ?? null
            return <section key={period} aria-label={`${period} 指标明细`}><h3>{period}</h3>{row ? <IndicatorContributionList analysis={row}/> : <p>数据不足</p>}</section>
          })}
        </div>
      </details>
    </section>

    <section className="ma-panel" aria-labelledby="ma-validation-title">
      <div className="ma-section-heading">
        <div><p className="ma-layer-label">第六层 · 历史与验证</p><h2 id="ma-validation-title">分析稳定性</h2></div>
        <p>仅展示后端历史快照与验证结果。</p>
      </div>
      <AnalysisHistoryChart items={history}/>
      <div className={`ma-validation ${validation?.sufficient ? '' : 'insufficient'}`}>
        <div>
          <h3>{!validation ? '正在加载历史验证' : validation.sufficient ? '验证样本可用' : '验证样本不足'}</h3>
          <p>{validation?.summary || '验证在后台独立计算；当前结构结论不会等待它，也不会因此被包装成已验证策略。'}</p>
          {validation ? <span>样本 {number(validation.sampleSize, 0)} / 最低 {number(validation.minimumSampleSize, 0)}</span> : null}
        </div>
        {validation?.metrics.length ? <dl>{validation.metrics.map(metric => <div key={metric.key}><dt>{metric.label}</dt><dd>{metric.valueText || number(metric.value)}</dd></div>)}</dl> : null}
      </div>
    </section>

    <p className="ma-disclaimer">市场分析只基于公共行情和确定性指标，用于辅助人工观察；不读取账户、不自动交易，不构成投资建议或盈利保证。</p>
  </main>
}
