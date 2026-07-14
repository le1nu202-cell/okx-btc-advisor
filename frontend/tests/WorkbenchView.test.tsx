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
    return <div data-testid="workbench-chart-props">图表组件测试替身</div>
  },
}))

import WorkbenchView from '../src/WorkbenchView'
import {
  actualFill,
  executionRisk,
  executionSummary,
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

  it('默认显示工作台，真实填写 SHORT 计划后防抖请求风险并创建计划', async () => {
    const user = userEvent.setup()
    render(<WorkbenchView/>)

    expect(await screen.findByText('尚未创建计划')).toBeTruthy()
    await user.selectOptions(screen.getByLabelText('方向'), 'SHORT')

    const replace = async (label: string, value: string) => {
      const input = screen.getByLabelText(new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
      await user.clear(input)
      await user.type(input, value)
    }
    await replace('初始开仓价', '100000')
    await replace('第一压力位 / 加仓价', '101000')
    await replace('第二压力位 / 硬止损价', '102000')
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
    expect(await screen.findByText('计划已保存，等待开仓')).toBeTruthy()
  })

  it('人工确认必须同时填写实际价格和 BTC 数量', async () => {
    apiMock.current.mockResolvedValue(createdPlan)
    const user = userEvent.setup()
    render(<WorkbenchView/>)

    const price = await screen.findByLabelText(/^实际成交价格/) as HTMLInputElement
    const quantity = screen.getByLabelText(/^实际成交 BTC 数量/) as HTMLInputElement
    await user.clear(price)
    await user.clear(quantity)
    await user.click(within(price.closest('form')!).getByRole('button', { name: '确认已开仓' }))

    expect(price.checkValidity()).toBe(false)
    expect(quantity.checkValidity()).toBe(false)
    expect(quantity.step).toBe('any')
    expect(apiMock.action).not.toHaveBeenCalled()
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
    expect(screen.getByText('计划数据')).toBeTruthy()
    expect(screen.getByText('实际成交数据')).toBeTruthy()
    expect(screen.getByText('实际剩余数量')).toBeTruthy()

    await waitFor(() => expect(chartProps).toHaveBeenCalled())
    const props = chartProps.mock.calls.at(-1)?.[0] as Record<string, unknown>
    expect(props.actualFills).toEqual({ initial })
    expect(props).not.toHaveProperty('activeReminder')
    expect(apiMock.action).not.toHaveBeenCalled()
  })

  it('旧行情阶段状态在实际执行区按 canonical 状态显示', async () => {
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

    expect(await screen.findByText('本计划已结束')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '确认已加仓' })).toBeNull()
    expect(screen.queryByRole('button', { name: '确认已减仓' })).toBeNull()
  })
})
