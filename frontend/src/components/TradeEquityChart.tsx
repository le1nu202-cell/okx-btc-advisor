import { useEffect, useMemo, useRef } from 'react'
import { AreaSeries, ColorType, CrosshairMode, createChart } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts'
import type { TradeEquityPoint } from '../workbench-types'

export default function TradeEquityChart({ points, source }: { points: TradeEquityPoint[]; source: 'live' | 'replay' }) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Area'> | null>(null)
  const data = useMemo(() => {
    const byTime = new Map<number, { time: UTCTimestamp; value: number }>()
    for (const point of points) {
      if (!Number.isFinite(point.timestamp) || point.equity == null || !Number.isFinite(point.equity)) continue
      const seconds = Math.floor(point.timestamp >= 1_000_000_000_000 ? point.timestamp / 1_000 : point.timestamp)
      byTime.set(seconds, { time: seconds as UTCTimestamp, value: point.equity })
    }
    return [...byTime.values()].sort((left, right) => Number(left.time) - Number(right.time))
  }, [points])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const width = Math.max(1, Math.floor(container.clientWidth || 900))
    const chart = createChart(container, {
      width, height: 260,
      layout: { background: { type: ColorType.Solid, color: '#0a1015' }, textColor: '#899692', attributionLogo: true },
      grid: { vertLines: { color: '#172129' }, horzLines: { color: '#172129' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#27323a' },
      timeScale: { borderColor: '#27323a', timeVisible: true, rightOffset: 4 },
      localization: { locale: 'zh-CN' },
    })
    const live = source === 'live'
    const series = chart.addSeries(AreaSeries, {
      lineColor: live ? '#49e7ac' : '#b79cff',
      topColor: live ? '#49e7ac55' : '#b79cff55',
      bottomColor: live ? '#49e7ac05' : '#b79cff05',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    })
    chartRef.current = chart
    seriesRef.current = series
    let observer: ResizeObserver | null = null
    const resize = (nextWidth?: number) => chart.resize(Math.max(1, Math.floor(nextWidth || container.clientWidth || width)), 260)
    const onResize = () => resize()
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(entries => resize(entries[0]?.contentRect.width))
      observer.observe(container)
    } else window.addEventListener('resize', onResize)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', onResize)
      seriesRef.current = null
      chartRef.current = null
      chart.remove()
    }
  }, [source])

  useEffect(() => {
    seriesRef.current?.setData(data)
    if (data.length) chartRef.current?.timeScale().fitContent()
  }, [data])

  return <div className="history-equity-wrap">
    <div ref={containerRef} className="history-equity-chart" aria-label={`${source === 'live' ? '真实记录' : '回放记录'}后端模拟净值曲线`}/>
    {!data.length && <div className="history-equity-empty">后端尚未返回净值曲线。</div>}
    <p>曲线仅使用后端 statistics.equityCurve，不在前端重新计算盈亏。</p>
  </div>
}
