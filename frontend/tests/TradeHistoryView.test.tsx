import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { TradeEquityPoint, TradeLog, TradeStats } from '../src/workbench-types'

const apiMock = vi.hoisted(() => ({
  logs: vi.fn(),
  statistics: vi.fn(),
  deleteLog: vi.fn(),
  bulkDeleteLogs: vi.fn(),
  clearLogs: vi.fn(),
  exportLogsUrl: vi.fn((source: string, format: string) => `/api/trade-logs/export?source=${source}&format=${format}`),
}))
const equityProps = vi.hoisted(() => vi.fn())

vi.mock('../src/workbench-api', () => ({ workbenchApi: apiMock }))
vi.mock('../src/components/TradeEquityChart', () => ({
  default: (props: { points: TradeEquityPoint[]; source: string }) => {
    equityProps(props)
    return <div data-testid="equity-chart">{props.source}:{props.points.length}</div>
  },
}))

import TradeHistoryView from '../src/TradeHistoryView'

const makeLog = (overrides: Partial<TradeLog>): TradeLog => ({
  id: 'live-profit',
  planId: 'plan-1',
  source: 'live',
  direction: 'LONG',
  state: 'TAKE_PROFIT',
  planPrices: { initial: 100_000, add: 99_000, stop: 98_000, takeProfit: 102_000 },
  actualPrices: { initial: 100_010, add: null, reduce: null, exit: 102_020 },
  actualQuantitiesBtc: { initial: 0.002, add: null, reduce: null, exit: 0.002 },
  initialQuantityBtc: 0.002,
  addQuantityBtc: 0,
  totalQuantityBtc: 0.002,
  initialMargin: 3.2,
  addMargin: 0,
  leverage: 66,
  fees: 0.2,
  grossPnl: 4.1,
  slippageUsdt: 0.1,
  netPnl: 3.8,
  returnPercent: 4.75,
  mfeUsdt: null,
  maeUsdt: null,
  regime4H: 'TREND',
  addTriggered: false,
  returnedToReduceZone: false,
  notes: '盈利做多记录',
  screenshotPath: null,
  closedAt: Date.UTC(2026, 0, 2, 12),
  ...overrides,
})

const liveLogs = [
  makeLog({}),
  makeLog({
    id: 'live-loss', planId: 'plan-2', direction: 'SHORT', state: 'STOPPED',
    netPnl: -2.4, grossPnl: -2.0, returnPercent: -3, notes: '亏损做空记录',
    closedAt: Date.UTC(2026, 0, 1, 12),
  }),
]
const replayLogs = [makeLog({ id: 'replay-one', planId: 'replay-plan', source: 'replay', notes: '回放记录' })]
const equityCurve = [
  { timestamp: liveLogs[1].closedAt, cumulativeNetPnl: -2.4, equity: 77.6 },
  { timestamp: liveLogs[0].closedAt, cumulativeNetPnl: 1.4, equity: 81.4 },
]
const stats: TradeStats = {
  totalTrades: 2,
  winningTrades: 1,
  losingTrades: 1,
  breakevenTrades: 0,
  winRate: 0.5,
  initialDirectTakeProfitRate: 0.5,
  addTriggerRate: 0,
  returnToReduceZoneRate: 0,
  hardStopAfterAddRate: 0,
  averageProfit: 3.8,
  averageLoss: -2.4,
  profitFactor: 1.58,
  totalFees: 0.4,
  totalSlippage: 0.2,
  feesToGrossProfit: 0.1,
  maxConsecutiveLosses: 1,
  maxDrawdownUsdt: 2.4,
  netPnl: 1.4,
  startingEquity: 80,
  simulatedEquity: 81.4,
  equityCurve,
  byDirection: {},
  byRegime: {},
}

describe('TradeHistoryView 真实交互', () => {
  beforeEach(() => {
    apiMock.logs.mockReset().mockImplementation((source: string) => Promise.resolve({ items: source === 'live' ? liveLogs : replayLogs }))
    apiMock.statistics.mockReset().mockResolvedValue(stats)
    apiMock.deleteLog.mockReset().mockResolvedValue({ deleted: true, deletedCount: 1, source: 'live', id: 'live-profit' })
    apiMock.bulkDeleteLogs.mockReset().mockResolvedValue({ deletedCount: 1, source: 'live' })
    apiMock.clearLogs.mockReset().mockResolvedValue({ deletedCount: 2, source: 'live' })
    apiMock.exportLogsUrl.mockClear()
    equityProps.mockClear()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })

  it('渲染后端统计和净值曲线，并真实筛选方向、盈亏、终态和日期', async () => {
    const user = userEvent.setup()
    render(<TradeHistoryView/>)

    expect(await screen.findByText('2 / 2 条')).toBeTruthy()
    const statistics = screen.getByRole('region', { name: '后端交易统计' })
    expect(within(statistics).getByText('50.00%')).toBeTruthy()
    expect(within(statistics).getByText('81.40 USDT')).toBeTruthy()
    expect(equityProps).toHaveBeenLastCalledWith({ points: equityCurve, source: 'live' })

    await user.selectOptions(screen.getByLabelText('方向'), 'SHORT')
    await user.selectOptions(screen.getByLabelText('盈亏'), 'LOSS')
    await user.selectOptions(screen.getByLabelText('终态'), 'STOPPED')
    expect(screen.getByText('1 / 2 条')).toBeTruthy()
    expect(screen.getByText('亏损做空记录')).toBeTruthy()
    expect(screen.queryByText('盈利做多记录')).toBeNull()

    await user.type(screen.getByLabelText('开始日期'), '2026-01-02')
    expect(screen.getByText('0 / 2 条')).toBeTruthy()
    expect(screen.getByText('当前来源和筛选条件下没有交易记录。')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '重置筛选' }))
    expect(screen.getByText('2 / 2 条')).toBeTruthy()

    expect(screen.getByRole('link', { name: '导出 CSV' }).getAttribute('href')).toContain('source=live&format=csv')
    expect(screen.getByRole('link', { name: '导出 JSON' }).getAttribute('href')).toContain('source=live&format=json')
  })

  it('LIVE 与 REPLAY 严格分源并重新请求后端统计', async () => {
    const user = userEvent.setup()
    render(<TradeHistoryView/>)
    await screen.findByText('2 / 2 条')

    await user.click(screen.getByRole('tab', { name: '回放记录 REPLAY' }))
    expect(await screen.findByText('1 / 1 条')).toBeTruthy()
    expect(screen.getAllByText('回放记录').length).toBeGreaterThan(0)
    expect(screen.queryByText('盈利做多记录')).toBeNull()
    expect(apiMock.logs).toHaveBeenLastCalledWith('replay')
    expect(apiMock.statistics).toHaveBeenLastCalledWith('replay')
    expect(equityProps).toHaveBeenLastCalledWith({ points: equityCurve, source: 'replay' })
  })

  it('单条删除与批量删除都要求确认、调用分源 API 并刷新列表统计', async () => {
    const user = userEvent.setup()
    render(<TradeHistoryView/>)
    await screen.findByText('2 / 2 条')

    await user.click(screen.getAllByText('展开详情')[0].closest('summary')!)
    await user.click(screen.getAllByRole('button', { name: '删除这条记录' })[0])
    await waitFor(() => expect(apiMock.deleteLog).toHaveBeenCalledWith('live', 'live-profit'))
    expect(await screen.findByText('记录已删除，列表与统计已刷新。')).toBeTruthy()

    await user.click(screen.getAllByRole('checkbox', { name: /选择 .* 的记录/ })[0])
    await user.click(screen.getByRole('button', { name: '删除已选（1）' }))
    await waitFor(() => expect(apiMock.bulkDeleteLogs).toHaveBeenCalledWith('live', ['live-profit']))
    expect(screen.getByText('已删除 1 条记录，列表与统计已刷新。')).toBeTruthy()
    expect(apiMock.statistics.mock.calls.filter(call => call[0] === 'live').length).toBeGreaterThanOrEqual(3)
  })

  it('清空必须输入 DELETE，随后仅清空当前来源并刷新统计', async () => {
    const user = userEvent.setup()
    render(<TradeHistoryView/>)
    await screen.findByText('2 / 2 条')

    await user.click(screen.getByText('清空当前来源的全部历史'))
    const clear = screen.getByRole('button', { name: '确认清空 LIVE' }) as HTMLButtonElement
    expect(clear.disabled).toBe(true)
    await user.type(screen.getByPlaceholderText('输入 DELETE'), 'DELETE')
    expect(clear.disabled).toBe(false)
    await user.click(clear)

    await waitFor(() => expect(apiMock.clearLogs).toHaveBeenCalledWith('live'))
    expect(await screen.findByText('已清空 2 条记录。')).toBeTruthy()
    expect(apiMock.statistics.mock.calls.filter(call => call[0] === 'live').length).toBeGreaterThanOrEqual(2)
  })
})
