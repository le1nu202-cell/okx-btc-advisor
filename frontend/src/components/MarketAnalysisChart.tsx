import { useEffect, useMemo, useRef, useState } from 'react'
import { CandlestickSeries, ColorType, CrosshairMode, LineSeries, LineStyle, createChart } from 'lightweight-charts'
import type { IChartApi, IPriceLine, ISeriesApi, Time, UTCTimestamp } from 'lightweight-charts'
import { confirmedCandleIsStale, type Candle, type MarketSnapshot } from '../types'
import {
  ANALYSIS_TIMEFRAMES,
  analysisIsStale,
  type AnalysisSeriesPoint,
  type AnalysisTimeframe,
  type MarketAnalysis,
  type MarketPlanOverlay,
} from '../market-analysis-types'

type ChartCandle = { time: UTCTimestamp; open: number; high: number; low: number; close: number }
type ChartPoint = { time: UTCTimestamp; value: number }

const candlesFor = (snapshot: MarketSnapshot | null, timeframe: AnalysisTimeframe): Candle[] => {
  if (!snapshot) return []
  if (timeframe === '1m') return snapshot.candles1m
  if (timeframe === '15m') return snapshot.candles15m
  if (timeframe === '4H') return snapshot.candles4h
  return snapshot.candles1h
}

const chartCandles = (candles: Candle[]): ChartCandle[] => {
  const rows = new Map<number, ChartCandle>()
  for (const candle of candles) {
    if (![candle.timestamp, candle.open, candle.high, candle.low, candle.close].every(Number.isFinite)) continue
    const time = Math.floor(candle.timestamp / 1_000) as UTCTimestamp
    rows.set(Number(time), { time, open: candle.open, high: candle.high, low: candle.low, close: candle.close })
  }
  return [...rows.values()].sort((left, right) => Number(left.time) - Number(right.time))
}

const chartPoints = (points: AnalysisSeriesPoint[]): ChartPoint[] => points.map(point => ({
  time: Math.floor(point.timestamp / 1_000) as UTCTimestamp,
  value: point.value,
}))

const confirmedTime = (value: number | null | undefined) => value == null
  ? '未知'
  : new Date(value).toLocaleString('zh-CN', { hour12: false })

export default function MarketAnalysisChart({
  analysis,
  snapshot,
  planOverlay,
  initialTimeframe = '1H',
}: {
  analysis: MarketAnalysis | null
  snapshot: MarketSnapshot | null
  planOverlay: MarketPlanOverlay | null
  initialTimeframe?: AnalysisTimeframe
}) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const ema20Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ema50Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ema200Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const vwapRef = useRef<ISeriesApi<'Line'> | null>(null)
  const priceLinesRef = useRef<IPriceLine[]>([])
  const fittedRef = useRef<Set<AnalysisTimeframe>>(new Set())
  const [timeframe, setTimeframe] = useState<AnalysisTimeframe>(initialTimeframe)
  const [showEma20, setShowEma20] = useState(true)
  const [showEma50, setShowEma50] = useState(true)
  const [showEma200, setShowEma200] = useState(false)
  const [showVwap, setShowVwap] = useState(false)
  const [showKeyLevels, setShowKeyLevels] = useState(true)
  const [showInvalidation, setShowInvalidation] = useState(true)
  const [showPlanned, setShowPlanned] = useState(false)
  const [showActual, setShowActual] = useState(false)

  const candles = useMemo(() => chartCandles(candlesFor(snapshot, timeframe)), [snapshot, timeframe])
  const backendSeries = analysis?.chartSeries[timeframe]

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const initialWidth = Math.max(1, Math.floor(container.clientWidth || 1_000))
    const initialHeight = Math.max(360, Math.floor(container.getBoundingClientRect().height || 500))
    const chart = createChart(container, {
      width: initialWidth,
      height: initialHeight,
      layout: { background: { type: ColorType.Solid, color: '#080f14' }, textColor: '#8d9b98', attributionLogo: true },
      grid: { vertLines: { color: '#16232c' }, horzLines: { color: '#16232c' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#293640', scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: '#293640', timeVisible: true, secondsVisible: false, rightOffset: 4 },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
      localization: { locale: 'zh-CN' },
    })
    candleRef.current = chart.addSeries(CandlestickSeries, {
      upColor: '#4ce0ab', downColor: '#f0697e', borderUpColor: '#4ce0ab', borderDownColor: '#f0697e',
      wickUpColor: '#4ce0ab', wickDownColor: '#f0697e', priceLineVisible: false, lastValueVisible: false,
    })
    ema20Ref.current = chart.addSeries(LineSeries, { color: '#5adbb1', lineWidth: 2, title: 'EMA20', priceLineVisible: false })
    ema50Ref.current = chart.addSeries(LineSeries, { color: '#e3b860', lineWidth: 2, title: 'EMA50', priceLineVisible: false })
    ema200Ref.current = chart.addSeries(LineSeries, { color: '#a98df0', lineWidth: 2, title: 'EMA200', priceLineVisible: false, visible: false })
    vwapRef.current = chart.addSeries(LineSeries, { color: '#66aef5', lineWidth: 2, lineStyle: LineStyle.Dashed, title: 'VWAP', priceLineVisible: false, visible: false })
    chartRef.current = chart

    let observer: ResizeObserver | null = null
    const resize = (width?: number, height?: number) => chart.resize(
      Math.max(1, Math.floor(width || container.clientWidth || initialWidth)),
      Math.max(360, Math.floor(height || container.getBoundingClientRect().height || initialHeight)),
    )
    const onResize = () => resize()
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(entries => resize(entries[0]?.contentRect.width, entries[0]?.contentRect.height))
      observer.observe(container)
    } else window.addEventListener('resize', onResize)

    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', onResize)
      priceLinesRef.current = []
      fittedRef.current.clear()
      candleRef.current = null
      ema20Ref.current = null
      ema50Ref.current = null
      ema200Ref.current = null
      vwapRef.current = null
      chartRef.current = null
      chart.remove()
    }
  }, [])

  useEffect(() => {
    chartRef.current?.applyOptions({ timeScale: { secondsVisible: timeframe === '1m' } })
    candleRef.current?.setData(candles)
    if (candles.length && !fittedRef.current.has(timeframe)) {
      chartRef.current?.timeScale().fitContent()
      fittedRef.current.add(timeframe)
    }
  }, [candles, timeframe])

  useEffect(() => {
    ema20Ref.current?.setData(chartPoints(backendSeries?.ema20 ?? []))
    ema50Ref.current?.setData(chartPoints(backendSeries?.ema50 ?? []))
    ema200Ref.current?.setData(chartPoints(backendSeries?.ema200 ?? []))
    vwapRef.current?.setData(chartPoints(backendSeries?.vwap ?? []))
  }, [backendSeries])

  useEffect(() => { ema20Ref.current?.applyOptions({ visible: showEma20 }) }, [showEma20])
  useEffect(() => { ema50Ref.current?.applyOptions({ visible: showEma50 }) }, [showEma50])
  useEffect(() => { ema200Ref.current?.applyOptions({ visible: showEma200 }) }, [showEma200])
  useEffect(() => { vwapRef.current?.applyOptions({ visible: showVwap }) }, [showVwap])

  useEffect(() => {
    const candleSeries = candleRef.current
    if (!candleSeries) return
    for (const priceLine of priceLinesRef.current) candleSeries.removePriceLine(priceLine)
    priceLinesRef.current = []
    const create = (id: string, title: string, price: number | null, color: string, lineStyle: LineStyle) => {
      if (price == null || !Number.isFinite(price) || price <= 0) return
      priceLinesRef.current.push(candleSeries.createPriceLine({
        id, title, price, color, lineStyle, lineWidth: 1, lineVisible: true, axisLabelVisible: true,
      }))
    }
    if (showKeyLevels) for (const level of analysis?.keyLevels ?? []) create(
      `analysis-level-${level.id}`, level.label || '后端关键位', level.price,
      /RESIST/i.test(level.kind) ? '#dc8d76' : /SUPPORT/i.test(level.kind) ? '#62c99f' : '#a2afb4', LineStyle.Dotted,
    )
    if (showInvalidation) create('analysis-invalidation', '分析失效位', analysis?.invalidationLevel ?? null, '#f15f78', LineStyle.Dashed)
    if (showPlanned) for (const line of planOverlay?.planned ?? []) create(line.id, line.label, line.price, '#8fa9ba', LineStyle.Solid)
    if (showActual) for (const line of planOverlay?.actual ?? []) create(line.id, line.label, line.price, '#4de0aa', LineStyle.Dashed)
    create('analysis-current-price', '当前价格', snapshot?.price ?? snapshot?.markPrice ?? null, '#eef4f2', LineStyle.Dotted)
  }, [analysis, planOverlay, showActual, showInvalidation, showKeyLevels, showPlanned, snapshot?.markPrice, snapshot?.price])

  const selectedStatus = snapshot?.candleStatus[timeframe]
  const confirmedStale = confirmedCandleIsStale(selectedStatus, timeframe)
  const stale = analysisIsStale(analysis) || snapshot?.stale || selectedStatus?.stale
  const warning = !candles.length || selectedStatus?.available === false
    ? `${timeframe} 暂无可用 K 线`
    : selectedStatus?.gapDetected
      ? `${timeframe} 检测到行情缺口，分析仅供观察`
      : confirmedStale
        ? `${timeframe} 已收盘 K 线已过期（最新确认：${confirmedTime(selectedStatus?.lastConfirmedAt)}）；未收盘更新不代表指标数据新鲜`
        : stale
        ? `${timeframe} 分析或行情已过期，请等待后端更新`
        : snapshot?.connectionStatus !== 'connected' ? `公共行情连接状态：${snapshot?.connectionStatus ?? 'unknown'}` : ''

  const toggles = [
    ['EMA20', showEma20, setShowEma20], ['EMA50', showEma50, setShowEma50], ['EMA200', showEma200, setShowEma200],
    ['VWAP', showVwap, setShowVwap], ['关键位', showKeyLevels, setShowKeyLevels], ['失效位', showInvalidation, setShowInvalidation],
    ['计划线', showPlanned, setShowPlanned], ['实际线', showActual, setShowActual],
  ] as const

  return <div className="ma-chart-wrap" data-testid="market-analysis-chart">
    <div className="ma-chart-toolbar">
      <div className="ma-timeframe-tabs" role="tablist" aria-label="市场分析图表周期">
        {ANALYSIS_TIMEFRAMES.map(period => <button key={period} type="button" role="tab" aria-selected={timeframe === period} className={timeframe === period ? 'active' : ''} onClick={() => setTimeframe(period)}>{period}</button>)}
      </div>
      <div className="ma-chart-switches" aria-label="市场分析图表显示开关">
        {toggles.map(([label, enabled, setEnabled]) => <button key={label} type="button" aria-pressed={enabled} className={enabled ? 'active' : ''} onClick={() => setEnabled(value => !value)}>{label}</button>)}
      </div>
    </div>
    <div ref={containerRef} className="ma-main-chart" aria-label={`${timeframe} 后端市场分析图表`}/>
    {warning ? <div className="ma-chart-warning" role="status">{warning}</div> : null}
    <div className="ma-chart-caption">
      <span>EMA、VWAP、关键位和失效位均来自后端；前端只控制显示与隐藏。</span>
      <span>EMA20/50 默认开启，其余图层按需打开，避免交易时信息过载。</span>
      <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView</a>
    </div>
  </div>
}
