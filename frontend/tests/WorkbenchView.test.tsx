import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const apiMock = vi.hoisted(() => ({
  snapshot: vi.fn(),
  current: vi.fn(),
  calculate: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  action: vi.fn(),
}))

const chartProps = vi.hoisted(() => vi.fn())
const marketAnalysisMock = vi.hoisted(() => ({ current: vi.fn() }))

vi.mock('../src/workbench-api', () => ({ workbenchApi: apiMock }))
vi.mock('../src/market-analysis-api', () => ({
  marketAnalysisApi: marketAnalysisMock,
  normalizeMarketAnalysis: (value: unknown) => value,
}))
vi.mock('../src/components/TradingPlanChart', () => ({
  default: (props: Record<string, unknown>) => {
    chartProps(props)
    const change = props.onTimeframeChange as (timeframe: string) => void
    return <div data-testid="workbench-chart-props">
      {['1m', '15m', '1H', '4H'].map(timeframe => <button key={timeframe} type="button" onClick={() => change(timeframe)}>测试切换 {timeframe}</button>)}
    </div>
  },
}))

import WorkbenchView, { mergeRealtimeCandles } from '../src/WorkbenchView'
import {
  actualFill,
  executionRisk,
  executionSummary,
  liquidationEstimate,
  planRecord,
  plannedRisk,
  shortPlan,
  snapshot,
} from './workbench-fixtures'

const createdPlan = planRecord({
  id: 'plan-created',
  state: 'PLANNED',
  plan: shortPlan,
  risk: plannedRisk,
})

describe('WorkbenchView 真实交互', () => {
  beforeEach(() => {
    apiMock.snapshot.mockReset().mockResolvedValue(snapshot)
    apiMock.current.mockReset().mockResolvedValue(planRecord())
    apiMock.calculate.mockReset().mockResolvedValue(plannedRisk)
    apiMock.create.mockReset().mockResolvedValue(createdPlan)
    apiMock.update.mockReset().mockResolvedValue(createdPlan)
    apiMock.action.mockReset()
    marketAnalysisMock.current.mockReset().mockResolvedValue({
      overallBias: 'BEARISH', actionContext: 'NO_CHASE', compositeScore: -42, trendStrength: 54,
      alignmentScore: 72, confidence: 66, summary: '大周期偏空，但等待 15m 反弹结束。', primaryReason: '4H 与 1H 同向偏空。',
      invalidationLevel: 102_000, nearestSupport: 99_000, nearestResistance: 101_000,
      timeframeAnalyses: [
        { timeframe: '4H', status: 'AVAILABLE', bias: 'BEARISH', actionContext: '', regime: 'TREND', structure: 'LH_LL', volatilityState: 'NORMAL', score: -50, trendStrength: 60, confidence: 70, summary: '4H 偏空', primaryReason: '', asOf: 1_800_000_000_000, dataQuality: { status: 'AVAILABLE', fresh: true, stale: false, warnings: [], missingTimeframes: [], gapTimeframes: [] }, indicators: [], contributions: [] },
        { timeframe: '1H', status: 'AVAILABLE', bias: 'BEARISH', actionContext: '', regime: 'TREND', structure: 'LH_LL', volatilityState: 'NORMAL', score: -40, trendStrength: 50, confidence: 65, summary: '1H 偏空', primaryReason: '', asOf: 1_800_000_000_000, dataQuality: { status: 'AVAILABLE', fresh: true, stale: false, warnings: [], missingTimeframes: [], gapTimeframes: [] }, indicators: [], contributions: [] },
      ],
      keyLevels: [], supportingReasons: [], conflictingReasons: [], riskWarnings: [],
      dataQuality: { status: 'AVAILABLE', fresh: true, stale: false, warnings: [], missingTimeframes: [], gapTimeframes: [] },
      asOf: 1_800_000_000_000, modelVersion: 'indicator-regime-v06.0.0', chartSeries: {},
    })
    chartProps.mockClear()
  })

  it('实时 K 线按时间戳覆盖、排序并保持有界', () => {
    const first = snapshot.candles1m[0]
    const second = snapshot.candles1m[1]
    const corrected = { ...first, close: first.close + 10, confirm: true }
    expect(mergeRealtimeCandles([first, second], [corrected], 2)).toEqual([corrected, second])
    expect(mergeRealtimeCandles([corrected, second], [{ ...corrected, close: 1, confirm: false }], 2)).toEqual([corrected, second])
    expect(mergeRealtimeCandles([first, second], [{ ...second, timestamp: second.timestamp + 60_000 }], 2).map(row => row.timestamp)).toEqual([second.timestamp, second.timestamp + 60_000])
  })

  it('默认显示工作台，真实填写 SHORT 计划后防抖请求风险并创建计划', async () => {
    const user = userEvent.setup()
    render(<WorkbenchView/>)

    expect(await screen.findByText('尚未创建计划')).toBeTruthy()
    await user.selectOptions(screen.getByLabelText('方向'), 'SHORT')

    const replace = async (label: string, value: string) => {
      const input = screen.getByLabelText(new RegExp(`^${label}`))
      await user.clear(input)
      await user.type(input, value)
    }
    await replace('初始开仓价', '100000')
    await replace('加仓价 / 第一压力位', '101000')
    await replace('硬止损价 / 第二压力位', '102000')
    await replace('止盈价', '99000')

    await waitFor(() => expect(apiMock.calculate).toHaveBeenLastCalledWith(expect.objectContaining({
      direction: 'SHORT',
      initialEntryPrice: 100_000,
      addPrice: 101_000,
      stopPrice: 102_000,
      takeProfitPrice: 99_000,
      equity: 80,
      leverage: 66,
      initialMarginPercent: 4,
      addMultiplier: 2,
    })), { timeout: 1_500 })

    await user.click(screen.getByRole('button', { name: '创建交易计划' }))
    await waitFor(() => expect(apiMock.create).toHaveBeenCalledWith(expect.objectContaining({
      direction: 'SHORT',
      initialEntryPrice: 100_000,
      addPrice: 101_000,
      stopPrice: 102_000,
      takeProfitPrice: 99_000,
    })))
    expect(await screen.findByText('计划已创建，等待人工确认真实开仓')).toBeTruthy()
  })

  it('显示只读行情摘要并可打开完整页面，不修改计划或成交状态', async () => {
    const open = vi.fn()
    const user = userEvent.setup()
    render(<WorkbenchView onOpenMarketAnalysis={open}/>)

    expect((await screen.findByRole('region', { name: '市场分析摘要' })).textContent).toContain('大周期偏空，但等待 15m 反弹结束。')
    await user.click(screen.getByRole('button', { name: '打开完整市场分析' }))
    expect(open).toHaveBeenCalledTimes(1)
    expect(apiMock.create).not.toHaveBeenCalled()
    expect(apiMock.update).not.toHaveBeenCalled()
    expect(apiMock.action).not.toHaveBeenCalled()
  })

  it('加载 v0.4 旧计划时用计划权益回填全仓支持余额基准，不静默套用 80U', async () => {
    const legacy = { ...shortPlan, equity: 123 } as Partial<typeof shortPlan>
    delete legacy.crossAvailableEquity
    delete legacy.crossEquityMode
    apiMock.current.mockResolvedValue(planRecord({
      id: 'legacy-equity-plan',
      state: 'PLANNED',
      plan: legacy as typeof shortPlan,
      risk: plannedRisk,
    }))
    render(<WorkbenchView/>)

    const available = await screen.findByRole('spinbutton', { name: /^全仓支持余额基准/ }) as HTMLInputElement
    expect(available.value).toBe('123')
    expect(available.readOnly).toBe(true)
    expect(screen.queryByLabelText(/^全仓可用权益/)).toBeNull()
    await waitFor(() => expect(apiMock.calculate).toHaveBeenLastCalledWith(expect.objectContaining({
      equity: 123,
      crossAvailableEquity: 123,
    })), { timeout: 1_500 })
  })

  it('跟随模式原子同步权益与 Taker，手动模式保留独立值并提示差异', async () => {
    const user = userEvent.setup()
    render(<WorkbenchView/>)
    await screen.findByText('尚未创建计划')
    await user.click(screen.getByText('仓位、强平参数与成本设置'))

    const crossMode = screen.getByLabelText('全仓支持余额基准模式') as HTMLSelectElement
    const feeMode = screen.getByLabelText('强平费率模式') as HTMLSelectElement
    let crossValue = screen.getByRole('spinbutton', { name: /^全仓支持余额基准/ }) as HTMLInputElement
    let liquidationFee = screen.getByRole('spinbutton', { name: /^估算强平费率/ }) as HTMLInputElement
    const equity = screen.getByLabelText(/^账户权益/) as HTMLInputElement
    const taker = screen.getByLabelText(/^Taker 手续费/) as HTMLInputElement

    expect(crossMode.value).toBe('FOLLOW_EQUITY')
    expect(feeMode.value).toBe('FOLLOW_TAKER')
    expect(crossValue.readOnly).toBe(true)
    expect(liquidationFee.readOnly).toBe(true)
    expect(screen.getByText('跟随账户权益')).toBeTruthy()
    expect(screen.getByText('跟随 Taker')).toBeTruthy()

    await user.clear(equity)
    await user.type(equity, '100')
    await user.clear(taker)
    await user.type(taker, '7')
    expect(crossValue.value).toBe('100')
    expect(liquidationFee.value).toBe('7')

    await user.selectOptions(crossMode, 'MANUAL')
    await user.selectOptions(feeMode, 'MANUAL')
    crossValue = screen.getByRole('spinbutton', { name: /^全仓支持余额基准/ }) as HTMLInputElement
    liquidationFee = screen.getByRole('spinbutton', { name: /^估算强平费率/ }) as HTMLInputElement
    expect(crossValue.readOnly).toBe(false)
    expect(liquidationFee.readOnly).toBe(false)
    await user.clear(crossValue)
    await user.type(crossValue, '70')
    await user.clear(liquidationFee)
    await user.type(liquidationFee, '12')
    await user.clear(equity)
    await user.type(equity, '90')
    await user.clear(taker)
    await user.type(taker, '9')

    expect(crossValue.value).toBe('70')
    expect(liquidationFee.value).toBe('12')
    expect(screen.getByText(/手动全仓支持余额基准与账户权益不一致/)).toBeTruthy()
    expect(screen.getByText(/手动估算强平费率与 Taker 手续费不一致/)).toBeTruthy()
    await waitFor(() => expect(apiMock.calculate).toHaveBeenLastCalledWith(expect.objectContaining({
      equity: 90,
      crossEquityMode: 'MANUAL',
      crossAvailableEquity: 70,
      takerFeeBps: 9,
      liquidationFeeMode: 'MANUAL',
      liquidationFeeBps: 12,
    })), { timeout: 1_500 })

    await user.selectOptions(crossMode, 'FOLLOW_EQUITY')
    await user.selectOptions(feeMode, 'FOLLOW_TAKER')
    expect(crossValue.value).toBe('90')
    expect(liquidationFee.value).toBe('9')
  })

  it('明确显示全仓强平假设、支持余额解释与 K 线保留和首屏窗口', async () => {
    const user = userEvent.setup()
    render(<WorkbenchView/>)

    expect(await screen.findByText(/估算假设：只存在 BTC-USDT-SWAP 这一项全仓仓位/)).toBeTruthy()
    expect(screen.getAllByText(/没有其他全仓或逐仓仓位影响账户权益/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/没有待成交挂单占用保证金/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/没有未知账户级费用或资产折算/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/实际强平以 OKX 标记价格和账户页面为准/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/本地保留窗口：/)).toBeTruthy()
    expect(screen.getByText(/1m 最近 7 天（最多 10,080 根）/)).toBeTruthy()
    expect(screen.getByText(/当前首屏加载窗口上限：/)).toBeTruthy()
    expect(screen.getByText(/冷启动时可能更少/)).toBeTruthy()
    expect(screen.getByText(/1m 720 根、15m 672 根、1H 200 根、4H 200 根/)).toBeTruthy()
    expect(screen.getByText(/当前未实现向左分页加载更早数据/)).toBeTruthy()

    await user.click(screen.getByText('仓位、强平参数与成本设置'))
    expect(screen.getByText(/不是 OKX 页面显示的“可用余额”/)).toBeTruthy()
    expect(screen.getByText('这是开仓前用于支撑当前单一全仓仓位的账户余额基准，不是扣除仓位或挂单占用后的可用保证金。当前仓位的未实现盈亏由强平公式单独计算。')).toBeTruthy()
    expect(screen.getByText(/其他仓位、占用保证金的挂单、借币、资金费/)).toBeTruthy()
    expect(screen.getAllByText(/只存在 BTC-USDT-SWAP 这一项全仓仓位/).length).toBeGreaterThanOrEqual(1)
  })

  it('人工确认在实际价格或 BTC 数量缺失时禁用，填写完整后才提交', async () => {
    apiMock.current.mockResolvedValue(createdPlan)
    apiMock.action.mockResolvedValue({ plan: planRecord({ ...createdPlan, state: 'INITIAL_OPEN' }) })
    const user = userEvent.setup()
    render(<WorkbenchView/>)

    await screen.findAllByRole('button', { name: '确认已开仓' })
    const price = await screen.findByLabelText(/^实际成交价格/) as HTMLInputElement
    const quantity = screen.getByLabelText(/^实际成交 BTC 数量/) as HTMLInputElement
    const form = price.closest('form')!
    const submit = within(form).getByRole('button', { name: '确认已开仓' }) as HTMLButtonElement

    await user.clear(price)
    expect(submit.disabled).toBe(true)
    await user.type(price, '100010')
    await user.clear(quantity)
    expect(submit.disabled).toBe(true)
    expect(apiMock.action).not.toHaveBeenCalled()

    await user.type(quantity, '0.002')
    expect(quantity.step).toBe('any')
    expect(submit.disabled).toBe(false)
    await user.click(submit)
    await waitFor(() => expect(apiMock.action).toHaveBeenCalledWith('plan-created', {
      action: 'CONFIRM_INITIAL', price: 100_010, quantityBtc: 0.002, note: '',
    }))
  })

  it('计划模拟与实际成交严格分区，无成交时实际区域不回退到计划字段', async () => {
    apiMock.current.mockResolvedValue(createdPlan)
    render(<WorkbenchView/>)

    const comparison = await screen.findByRole('region', { name: '计划模拟与实际成交对比' })
    const plannedPanel = within(comparison).getByRole('heading', { name: '四价与仓位计划' }).closest('section')!
    const actualPanel = within(comparison).getByRole('heading', { name: '人工确认的真实记录' }).closest('section')!

    expect(within(plannedPanel).getByText('计划总数量')).toBeTruthy()
    expect(within(plannedPanel).getByText('计划加权均价')).toBeTruthy()
    expect(within(actualPanel).getByText('尚无人工确认成交。实际区域不会使用计划数据代替。')).toBeTruthy()
    expect(within(actualPanel).queryByText('实际剩余数量')).toBeNull()
    expect(within(actualPanel).queryByText('实际加权均价')).toBeNull()
  })

  it('行情提醒与实际执行状态分区显示，不会成为图表成交标记', async () => {
    const initial = actualFill(100_010, 0.002, snapshot.candles1h[0].timestamp)
    apiMock.current.mockResolvedValue(planRecord({
      id: 'plan-reminder',
      state: 'INITIAL_OPEN',
      plan: shortPlan,
      risk: plannedRisk,
      actualFills: { initial },
      execution: executionSummary,
      executionRisk,
      activeReminder: {
        type: 'APPROACHING_ADD',
        message: '已接近第一压力位，请检查是否手工加仓',
        price: 100_980,
        createdAt: Date.now(),
      },
    }))
    render(<WorkbenchView/>)

    expect(await screen.findByText('已确认初始开仓')).toBeTruthy()
    expect(screen.getByText('当前行情提醒')).toBeTruthy()
    expect(screen.getByText('已接近第一压力位，请检查是否手工加仓')).toBeTruthy()
    expect(screen.getByText('计划模拟')).toBeTruthy()
    expect(screen.getByText('实际成交')).toBeTruthy()
    expect(screen.getAllByText('实际剩余数量').length).toBeGreaterThan(0)

    await waitFor(() => expect(chartProps).toHaveBeenCalled())
    const props = chartProps.mock.calls.at(-1)?.[0] as Record<string, unknown>
    expect(props.actualFills).toEqual({ initial })
    expect(props.executionRisk).toEqual(executionRisk)
    expect(props).not.toHaveProperty('activeReminder')
    expect(apiMock.action).not.toHaveBeenCalled()
  })

  it('旧行情阶段状态按 canonical 实际状态显示，提醒不改变执行状态', async () => {
    apiMock.current.mockResolvedValue(planRecord({
      id: 'legacy-reminder-state',
      state: 'APPROACHING_ADD',
      plan: shortPlan,
      risk: plannedRisk,
      actualFills: { initial: actualFill(100_010, 0.002, snapshot.candles1h[0].timestamp) },
      execution: executionSummary,
      executionRisk,
      activeReminder: {
        type: 'APPROACHING_ADD',
        message: '旧记录中的加仓提醒',
        price: 100_980,
        createdAt: Date.now(),
      },
    }))
    render(<WorkbenchView/>)

    expect(await screen.findByText('已确认初始开仓')).toBeTruthy()
    expect(screen.getByText('旧记录中的加仓提醒')).toBeTruthy()
    expect(screen.queryByText('接近加仓价，等待人工确认')).toBeNull()
  })

  it('四周期按钮真实更新工作台周期并把选择传回图表', async () => {
    const user = userEvent.setup()
    render(<WorkbenchView/>)

    await screen.findByText('尚未创建计划')
    const status = screen.getByText('当前周期').parentElement!
    expect(status.textContent).toContain('1H')
    for (const timeframe of ['1m', '15m', '4H', '1H']) {
      await user.click(screen.getByRole('button', { name: `测试切换 ${timeframe}` }))
      expect(status.textContent).toContain(timeframe)
      expect(chartProps.mock.calls.at(-1)?.[0]).toMatchObject({ timeframe })
    }
  })

  it('强平和风险解释只显示后端结果，高级风险详情默认折叠', async () => {
    const riskWithLiquidation = {
      ...plannedRisk,
      liquidationScenarios: {
        initialOnly: liquidationEstimate,
        afterAdd: liquidationEstimate,
        afterPlannedReduce: liquidationEstimate,
      },
    }
    apiMock.calculate.mockResolvedValue(riskWithLiquidation)
    const user = userEvent.setup()
    render(<WorkbenchView/>)

    expect((await screen.findAllByText('计划估算强平价')).length).toBeGreaterThanOrEqual(2)
    await waitFor(() => expect(screen.getAllByText('95,000.00 USDT').length).toBeGreaterThan(0))
    expect(screen.getByRole('heading', { name: '损失比例与强平距离是两套指标' })).toBeTruthy()
    expect(screen.getByText(/当前阈值：3% \/ 5% \/ 8%/)).toBeTruthy()
    for (const level of ['低风险', '中风险', '高风险', '极高风险']) expect(screen.getByText(level)).toBeTruthy()
    expect(screen.getByText(/不代表行情成功概率，也不构成盈利保证/)).toBeTruthy()

    const summary = screen.getByText('高级风险与成本详情').closest('summary')!
    const details = summary.closest('details') as HTMLDetailsElement
    expect(details.open).toBe(false)
    await user.click(summary)
    expect(details.open).toBe(true)
    expect(within(details).getByText('计划强平情景')).toBeTruthy()
    expect(within(details).getByText('完成计划加仓')).toBeTruthy()
  })

  it('终态计划不显示加仓或减仓确认动作', async () => {
    apiMock.current.mockResolvedValue(planRecord({
      id: 'plan-stopped',
      state: 'STOPPED',
      plan: shortPlan,
      risk: plannedRisk,
      actualFills: {
        initial: actualFill(100_010, 0.002, snapshot.candles1h[0].timestamp),
        exit: actualFill(102_020, 0.002, snapshot.candles1h[1].timestamp),
      },
      execution: { ...executionSummary, remainingQuantityBtc: 0 },
      executionRisk: null,
    }))
    render(<WorkbenchView/>)

    expect(await screen.findByText(/本计划已结束/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /确认.*加仓/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /确认.*减仓/ })).toBeNull()
  })
})
