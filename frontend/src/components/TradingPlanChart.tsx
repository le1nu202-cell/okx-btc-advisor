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
import { buildActualFillMarkers, buildPlanPriceLines, toChartCandles } from '../chart-adapter'
import type { ChartTimeframe } from '../chart-adapter'
import type { Candle } from '../types'
import type { ActualFill, ActualFillKey, ExecutionRisk, RiskCalculation, TradePlanDraft } from '../workbench-types'

export interface TradingPlanChartProps {
  candles1h: Candle[]
  candles4h: Candle[]
  timeframe: ChartTimeframe
  currentPrice: number | null
  plan: TradePlanDraft
  plannedRisk: RiskCalculation | null
  executionRisk: ExecutionRisk | null
  actualFills: Partial<Record<ActualFillKey, ActualFill>>
  stale: boolean
  connectionStatus: string
}

const CHART_HEIGHT = 430

export default function TradingPlanChart({
  candles1h,
  candles4h,
  timeframe,
  currentPrice,
  plan,
  plannedRisk,
  executionRisk,
  actualFills,
  stale,
  connectionStatus,
}: TradingPlanChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const planLinesRef = useRef<IPriceLine[]>([])
  const currentPriceLineRef = useRef<IPriceLine | null>(null)
  const fittedTimeframeRef = useRef<ChartTimeframe | null>(null)
  const [selectedTimeframe, setSelectedTimeframe] = useState<ChartTimeframe>(timeframe)

  useEffect(() => setSelectedTimeframe(timeframe), [timeframe])

  const selectedCandles = selectedTimeframe === '1H' ? candles1h : candles4h
  const chartCandles = useMemo(() => toChartCandles(selectedCandles), [selectedCandles])
  const priceLineDefinitions = useMemo(
    () => buildPlanPriceLines(plan, plannedRisk, executionRisk),
    [plan, plannedRisk, executionRisk],
  )

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const initialWidth = Math.max(1, Math.floor(container.getBoundingClientRect().width || container.clientWidth || 920))
    const initialHeight = Math.max(320, Math.floor(container.getBoundingClientRect().height || CHART_HEIGHT))
    const chart = createChart(container, {
      width: initialWidth,
      height: initialHeight,
      layout: {
        background: { type: ColorType.Solid, color: '#090e13' },
        textColor: '#87948f',
        attributionLogo: true,
      },
      grid: {
        vertLines: { color: '#172129' },
        horzLines: { color: '#172129' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#26313a', scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: '#26313a', timeVisible: true, secondsVisible: false, rightOffset: 5 },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      localization: { locale: 'zh-CN' },
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#49e7ac',
      downColor: '#ff627d',
      borderUpColor: '#49e7ac',
      borderDownColor: '#ff627d',
      wickUpColor: '#49e7ac',
      wickDownColor: '#ff627d',
      priceLineVisible: false,
      lastValueVisible: false,
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
    } else {
      window.addEventListener('resize', onWindowResize)
    }

    return () => {
      resizeObserver?.disconnect()
      window.removeEventListener('resize', onWindowResize)
      planLinesRef.current = []
      currentPriceLineRef.current = null
      fittedTimeframeRef.current = null
      markersRef.current = null
      seriesRef.current = null
      chartRef.current = null
      chart.remove()
    }
  }, [])

  useEffect(() => {
    const series = seriesRef.current
    const chart = chartRef.current
    if (!series || !chart) return
    series.setData(chartCandles)
    if (chartCandles.length && fittedTimeframeRef.current !== selectedTimeframe) {
      chart.timeScale().fitContent()
      fittedTimeframeRef.current = selectedTimeframe
    }
  }, [chartCandles, selectedTimeframe])

  useEffect(() => {
    const markerApi = markersRef.current
    if (!markerApi) return
    markerApi.setMarkers(buildActualFillMarkers(actualFills, chartCandles, plan.direction))
  }, [actualFills, chartCandles, plan.direction])

  useEffect(() => {
    const series = seriesRef.current
    if (!series) return
    for (const line of planLinesRef.current) series.removePriceLine(line)
    planLinesRef.current = priceLineDefinitions.map(line => series.createPriceLine({
      id: line.id,
      price: line.price,
      title: line.title,
      color: line.color,
      lineStyle: line.lineStyle,
      lineWidth: 1,
      lineVisible: true,
      axisLabelVisible: true,
    }))
  }, [priceLineDefinitions])

  useEffect(() => {
    const series = seriesRef.current
    if (!series) return
    const visualPrices = priceLineDefinitions.map(line => line.price)
    if (currentPrice != null && Number.isFinite(currentPrice) && currentPrice > 0) visualPrices.push(currentPrice)
    const autoscaleInfoProvider: AutoscaleInfoProvider = original => {
      const base = original()
      if (!visualPrices.length) return base
      const visualMin = Math.min(...visualPrices)
      const visualMax = Math.max(...visualPrices)
      if (!base?.priceRange) return { priceRange: { minValue: visualMin, maxValue: visualMax } }
      return {
        ...base,
        priceRange: {
          minValue: Math.min(base.priceRange.minValue, visualMin),
          maxValue: Math.max(base.priceRange.maxValue, visualMax),
        },
      }
    }
    series.applyOptions({
      autoscaleInfoProvider,
    })
  }, [priceLineDefinitions, currentPrice])

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
      id: 'current-market-price',
      price: currentPrice,
      title: '当前价',
      color: '#e8eef2',
      lineStyle: LineStyle.Dotted,
      lineWidth: 1,
      lineVisible: true,
      axisLabelVisible: true,
    })
  }, [currentPrice])

  const disconnected = connectionStatus !== 'connected'
  return <div className="wb-chart-wrap" data-testid="trading-plan-chart">
    <div className="wb-chart-toolbar">
      <div className="wb-timeframe-tabs" role="tablist" aria-label="K 线周期">
        {(['1H', '4H'] as const).map(value => <button key={value} type="button" role="tab" aria-selected={selectedTimeframe === value} className={selectedTimeframe === value ? 'active' : ''} onClick={() => setSelectedTimeframe(value)}>{value}</button>)}
      </div>
      <span>{selectedTimeframe} 已收盘 K 线 · 可滚轮缩放、拖拽平移与十字光标查看</span>
    </div>
    <div className="wb-lightweight-chart" ref={containerRef} aria-label={`${selectedTimeframe} 蜡烛图与六条计划风险价格线`}/>
    {(stale || disconnected || !chartCandles.length) && <div className="wb-chart-status" role="status">
      {!chartCandles.length ? '当前周期暂无可用 K 线。' : stale ? '行情数据已过期，价格线保留但请勿据此确认成交。' : `公共行情连接状态：${connectionStatus}`}
    </div>}
    <div className="wb-chart-caption">
      <span>成交标记仅来自 actualFills；行情提醒不会生成标记。</span>
      <span>价格线由表单与后端权威风险字段同步，本版不支持拖动。</span>
      <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView</a>
    </div>
  </div>
}
