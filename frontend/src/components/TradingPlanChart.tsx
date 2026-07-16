import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  createSeriesMarkers,
} from 'lightweight-charts'
import type { AutoscaleInfoProvider, IChartApi, IPriceLine, ISeriesApi, ISeriesMarkersPluginApi, Time } from 'lightweight-charts'
import { buildActualFillMarkers, buildPlanPriceLines, CHART_TIMEFRAMES, toChartCandles } from '../chart-adapter'
import type { ChartTimeframe } from '../chart-adapter'
import { confirmedCandleIsStale, type Candle, type CandleTimeframeStatus } from '../types'
import type { ActualFill, ActualFillKey, ExecutionRisk, RiskCalculation, TradePlanDraft } from '../workbench-types'

export interface TradingPlanChartProps {
  candles1m?: Candle[]
  candles15m?: Candle[]
  candles1h: Candle[]
  candles4h: Candle[]
  timeframe: ChartTimeframe
  onTimeframeChange?: (timeframe: ChartTimeframe) => void
  currentPrice: number | null
  plan: TradePlanDraft
  plannedRisk: RiskCalculation | null
  executionRisk: ExecutionRisk | null
  actualFills: Partial<Record<ActualFillKey, ActualFill>>
  stale: boolean
  connectionStatus: string
  candleStatus?: Partial<Record<ChartTimeframe, CandleTimeframeStatus>>
}

const CHART_HEIGHT = 470
const confirmedTime = (value: number | null | undefined) => value == null
  ? '未知'
  : new Date(value).toLocaleString('zh-CN', { hour12: false })

export default function TradingPlanChart({
  candles1m = [],
  candles15m = [],
  candles1h,
  candles4h,
  timeframe,
  onTimeframeChange,
  currentPrice,
  plan,
  plannedRisk,
  executionRisk,
  actualFills,
  stale,
  connectionStatus,
  candleStatus,
}: TradingPlanChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const priceLinesRef = useRef<IPriceLine[]>([])
  const currentPriceLineRef = useRef<IPriceLine | null>(null)
  const fittedTimeframesRef = useRef<Set<ChartTimeframe>>(new Set())
  const [selectedTimeframe, setSelectedTimeframe] = useState<ChartTimeframe>(timeframe)
  const [showPlanned, setShowPlanned] = useState(true)
  const [showActual, setShowActual] = useState(true)
  const [showFills, setShowFills] = useState(true)

  useEffect(() => setSelectedTimeframe(timeframe), [timeframe])

  const candlesByTimeframe = useMemo<Record<ChartTimeframe, Candle[]>>(() => ({
    '1m': candles1m,
    '15m': candles15m,
    '1H': candles1h,
    '4H': candles4h,
  }), [candles1m, candles15m, candles1h, candles4h])
  const chartCandles = useMemo(() => toChartCandles(candlesByTimeframe[selectedTimeframe]), [candlesByTimeframe, selectedTimeframe])
  const closedMarkerCandles = useMemo(() => toChartCandles(candlesByTimeframe[selectedTimeframe].filter(candle => candle.confirm === true)), [candlesByTimeframe, selectedTimeframe])
  const allPriceLines = useMemo(() => buildPlanPriceLines(plan, plannedRisk, executionRisk), [plan, plannedRisk, executionRisk])
  const visiblePriceLines = useMemo(
    () => allPriceLines.filter(line => line.group === 'planned' ? showPlanned : showActual),
    [allPriceLines, showActual, showPlanned],
  )

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const initialWidth = Math.max(1, Math.floor(container.getBoundingClientRect().width || container.clientWidth || 920))
    const initialHeight = Math.max(320, Math.floor(container.getBoundingClientRect().height || CHART_HEIGHT))
    const chart = createChart(container, {
      width: initialWidth,
      height: initialHeight,
      layout: { background: { type: ColorType.Solid, color: '#090e13' }, textColor: '#8d9996', attributionLogo: true },
      grid: { vertLines: { color: '#172129' }, horzLines: { color: '#172129' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#26313a', scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: '#26313a', timeVisible: true, secondsVisible: selectedTimeframe === '1m', rightOffset: 5 },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      localization: { locale: 'zh-CN' },
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#49e7ac', downColor: '#ff627d', borderUpColor: '#49e7ac', borderDownColor: '#ff627d',
      wickUpColor: '#49e7ac', wickDownColor: '#ff627d', priceLineVisible: false, lastValueVisible: false,
    })
    const markers = createSeriesMarkers(series, [])
    chartRef.current = chart
    seriesRef.current = series
    markersRef.current = markers

    let resizeObserver: ResizeObserver | null = null
    const resize = (width?: number, height?: number) => chart.resize(
      Math.max(1, Math.floor(width || container.clientWidth || initialWidth)),
      Math.max(320, Math.floor(height || container.getBoundingClientRect().height || initialHeight)),
    )
    const onWindowResize = () => resize()
    if (typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver(entries => resize(entries[0]?.contentRect.width, entries[0]?.contentRect.height))
      resizeObserver.observe(container)
    } else window.addEventListener('resize', onWindowResize)

    return () => {
      resizeObserver?.disconnect()
      window.removeEventListener('resize', onWindowResize)
      priceLinesRef.current = []
      currentPriceLineRef.current = null
      fittedTimeframesRef.current.clear()
      markersRef.current = null
      seriesRef.current = null
      chartRef.current = null
      chart.remove()
    }
  }, [])

  useEffect(() => {
    chartRef.current?.applyOptions({ timeScale: { secondsVisible: selectedTimeframe === '1m' } })
  }, [selectedTimeframe])

  useEffect(() => {
    const series = seriesRef.current
    const chart = chartRef.current
    if (!series || !chart) return
    series.setData(chartCandles)
    if (chartCandles.length && !fittedTimeframesRef.current.has(selectedTimeframe)) {
      chart.timeScale().fitContent()
      fittedTimeframesRef.current.add(selectedTimeframe)
    }
  }, [chartCandles, selectedTimeframe])

  useEffect(() => {
    markersRef.current?.setMarkers(showFills ? buildActualFillMarkers(actualFills, closedMarkerCandles, plan.direction) : [])
  }, [actualFills, closedMarkerCandles, plan.direction, showFills])

  useEffect(() => {
    const series = seriesRef.current
    if (!series) return
    for (const line of priceLinesRef.current) series.removePriceLine(line)
    priceLinesRef.current = visiblePriceLines.map(line => series.createPriceLine({
      id: line.id, price: line.price, title: line.title, color: line.color, lineStyle: line.lineStyle,
      lineWidth: 1, lineVisible: true, axisLabelVisible: true,
    }))
  }, [visiblePriceLines])

  useEffect(() => {
    const series = seriesRef.current
    if (!series) return
    const visualPrices = visiblePriceLines.map(line => line.price)
    if (currentPrice != null && Number.isFinite(currentPrice) && currentPrice > 0) visualPrices.push(currentPrice)
    const autoscaleInfoProvider: AutoscaleInfoProvider = original => {
      const base = original()
      if (!visualPrices.length) return base
      const visualMin = Math.min(...visualPrices)
      const visualMax = Math.max(...visualPrices)
      if (!base?.priceRange) return { priceRange: { minValue: visualMin, maxValue: visualMax } }
      return { ...base, priceRange: { minValue: Math.min(base.priceRange.minValue, visualMin), maxValue: Math.max(base.priceRange.maxValue, visualMax) } }
    }
    series.applyOptions({ autoscaleInfoProvider })
  }, [visiblePriceLines, currentPrice])

  useEffect(() => {
    const series = seriesRef.current
    if (!series) return
    if (currentPrice == null || !Number.isFinite(currentPrice) || currentPrice <= 0) {
      if (currentPriceLineRef.current) series.removePriceLine(currentPriceLineRef.current)
      currentPriceLineRef.current = null
      return
    }
    if (currentPriceLineRef.current) {
      currentPriceLineRef.current.applyOptions({ price: currentPrice })
      return
    }
    currentPriceLineRef.current = series.createPriceLine({
      id: 'current-market-price', price: currentPrice, title: '当前价格', color: '#eef3f6',
      lineStyle: LineStyle.Dotted, lineWidth: 1, lineVisible: true, axisLabelVisible: true,
    })
  }, [currentPrice])

  const selectTimeframe = (value: ChartTimeframe) => {
    setSelectedTimeframe(value)
    onTimeframeChange?.(value)
  }
  const selectedStatus = candleStatus?.[selectedTimeframe]
  const confirmedStale = confirmedCandleIsStale(selectedStatus, selectedTimeframe)
  const disconnected = connectionStatus !== 'connected'
  const unavailable = selectedStatus?.available === false || !chartCandles.length
  const dataWarning = unavailable
    ? `${selectedTimeframe} 暂无可用 K 线`
    : selectedStatus?.gapDetected
      ? `${selectedTimeframe} 检测到行情缺口，请谨慎参考`
      : confirmedStale
        ? `${selectedTimeframe} 已收盘 K 线已过期（最新确认：${confirmedTime(selectedStatus?.lastConfirmedAt)}）；未收盘更新不代表可确认成交`
        : stale || selectedStatus?.stale
        ? `${selectedTimeframe} 行情已过期，请勿据此确认成交`
        : disconnected ? `公共行情连接状态：${connectionStatus}` : ''

  return <div className="wb-chart-wrap" data-testid="trading-plan-chart">
    <div className="wb-chart-toolbar">
      <div className="wb-timeframe-tabs" role="tablist" aria-label="K 线周期">
        {CHART_TIMEFRAMES.map(value => <button key={value} type="button" role="tab" aria-selected={selectedTimeframe === value} className={selectedTimeframe === value ? 'active' : ''} onClick={() => selectTimeframe(value)}>{value}</button>)}
      </div>
      <div className="wb-chart-switches" aria-label="图表显示开关">
        <button type="button" aria-pressed={showPlanned} className={showPlanned ? 'active planned' : ''} onClick={() => setShowPlanned(value => !value)}>计划线</button>
        <button type="button" aria-pressed={showActual} className={showActual ? 'active actual' : ''} onClick={() => setShowActual(value => !value)}>实际线</button>
        <button type="button" aria-pressed={showFills} className={showFills ? 'active fills' : ''} onClick={() => setShowFills(value => !value)}>成交标记</button>
      </div>
    </div>
    <div className="wb-lightweight-chart" ref={containerRef} aria-label={`${selectedTimeframe} 蜡烛图、计划与实际价格线`}/>
    {dataWarning && <div className="wb-chart-status" role="status">{dataWarning}</div>}
    <div className="wb-chart-caption">
      <span>滚轮缩放、拖拽平移、十字光标查看；同周期实时更新不会重置视图。</span>
      <span>计划线来自表单和后端计划风险；实际线与成交标记只来自后端实际成交字段。</span>
      <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView</a>
    </div>
  </div>
}
