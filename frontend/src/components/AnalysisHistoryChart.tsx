import { useEffect, useMemo, useRef } from 'react'
import { ColorType, CrosshairMode, LineSeries, createChart } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts'
import type { MarketAnalysis } from '../market-analysis-types'

type LineData = { time: UTCTimestamp; value: number }

export default function AnalysisHistoryChart({ items }: { items: MarketAnalysis[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const compositeRef = useRef<ISeriesApi<'Line'> | null>(null)
  const trendRef = useRef<ISeriesApi<'Line'> | null>(null)
  const alignmentRef = useRef<ISeriesApi<'Line'> | null>(null)
  const series = useMemo(() => {
    const composite = new Map<number, LineData>()
    const trend = new Map<number, LineData>()
    const alignment = new Map<number, LineData>()
    for (const item of items) {
      if (item.asOf == null || !Number.isFinite(item.asOf)) continue
      const time = Math.floor(item.asOf / 1_000) as UTCTimestamp
      if (item.compositeScore != null && Number.isFinite(item.compositeScore)) composite.set(Number(time), { time, value: item.compositeScore })
      if (item.trendStrength != null && Number.isFinite(item.trendStrength)) trend.set(Number(time), { time, value: item.trendStrength })
      if (item.alignmentScore != null && Number.isFinite(item.alignmentScore)) alignment.set(Number(time), { time, value: item.alignmentScore })
    }
    const sorted = (rows: Map<number, LineData>) => [...rows.values()].sort((left, right) => Number(left.time) - Number(right.time))
    return { composite: sorted(composite), trend: sorted(trend), alignment: sorted(alignment) }
  }, [items])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const initialWidth = Math.max(1, Math.floor(container.clientWidth || 900))
    const chart = createChart(container, {
      width: initialWidth,
      height: 260,
      layout: { background: { type: ColorType.Solid, color: '#091016' }, textColor: '#8d9b98', attributionLogo: true },
      grid: { vertLines: { color: '#17242d' }, horzLines: { color: '#17242d' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#293640' },
      timeScale: { borderColor: '#293640', timeVisible: true, rightOffset: 3 },
      localization: { locale: 'zh-CN' },
    })
    compositeRef.current = chart.addSeries(LineSeries, { color: '#5be0ae', lineWidth: 2, title: '综合分', priceLineVisible: false })
    trendRef.current = chart.addSeries(LineSeries, { color: '#71aef7', lineWidth: 1, title: '趋势强度', priceLineVisible: false })
    alignmentRef.current = chart.addSeries(LineSeries, { color: '#d7b36a', lineWidth: 1, title: '一致度', priceLineVisible: false })
    chartRef.current = chart
    let observer: ResizeObserver | null = null
    const resize = (width?: number) => chart.resize(Math.max(1, Math.floor(width || container.clientWidth || initialWidth)), 260)
    const onResize = () => resize()
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(entries => resize(entries[0]?.contentRect.width))
      observer.observe(container)
    } else window.addEventListener('resize', onResize)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', onResize)
      compositeRef.current = null
      trendRef.current = null
      alignmentRef.current = null
      chartRef.current = null
      chart.remove()
    }
  }, [])

  useEffect(() => {
    compositeRef.current?.setData(series.composite)
    trendRef.current?.setData(series.trend)
    alignmentRef.current?.setData(series.alignment)
    if (series.composite.length || series.trend.length || series.alignment.length) chartRef.current?.timeScale().fitContent()
  }, [series])

  const empty = !series.composite.length && !series.trend.length && !series.alignment.length
  return <div className="ma-history-chart-wrap">
    <div className="ma-history-chart" ref={containerRef} aria-label="后端市场分析历史曲线"/>
    {empty ? <div className="ma-history-chart-empty">后端尚未返回足够的分析历史。</div> : null}
    <p>综合分、趋势强度和一致度均直接来自后端历史快照；曲线不在前端重算指标。</p>
  </div>
}
