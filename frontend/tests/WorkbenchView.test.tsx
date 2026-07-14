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

vi.mock('../src/workbench-api', () => ({ workbenchApi: apiMock }))
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
    chartProps.mockClear()
  })

  it('实时 K 线按时间戳覆盖、排序并保持有界', () => {
    const first = snapshot.candles1m[0]
    const second = snapshot.candles1m[1]
    const corrected = { ...first, close: first.close + 10, confirm: true }
    expect(mergeRealtimeCandles([first, second], [corrected], 2)).toEqual([corrected, second])
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

  it('加载 v0.4 旧计划时用计划权益回填全仓可用权益，不静默套用 80U', async () => {
    const legacy = { ...shortPlan, equity: 123 } as Partial<typeof shortPlan>
    delete legacy.crossAvailableEquity
    apiMock.current.mockResolvedValue(planRecord({
      id: 'legacy-equity-plan',
      state: 'PLANNED',
      plan: legacy as typeof shortPlan,
      risk: plannedRisk,
    }))
    render(<WorkbenchView/>)

    const available = await screen.findByLabelText(/^全仓可用权益/) as HTMLInputElement
    expect(available.value).toBe('123')
    await waitFor(() => expect(apiMock.calculate).toHaveBeenLastCalledWith(expect.objectContaining({
      equity: 123,
      crossAvailableEquity: 123,
    })), { timeout: 1_500 })
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
