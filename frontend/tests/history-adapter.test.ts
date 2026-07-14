import { describe, expect, it } from 'vitest'
import { backendEquityCurve } from '../src/history-adapter'
import type { TradeStats } from '../src/workbench-types'

describe('backend equity compatibility', () => {
  it('does not turn an unavailable v0.4 equity baseline into a zero-based curve', () => {
    const stats = {
      equityCurve: [
        { timestamp: 10, cumulativeNetPnl: -4, equity: null },
        { timestamp: 20, cumulativeNetPnl: 3, equity: null },
      ],
    } as TradeStats

    expect(backendEquityCurve(stats)).toEqual([])
  })

  it('retains and orders backend-authoritative absolute equity points', () => {
    const stats = {
      equityCurve: [
        { timestamp: 20, cumulativeNetPnl: 3, equity: 83 },
        { timestamp: 10, cumulativeNetPnl: -4, equity: 76 },
      ],
    } as TradeStats

    expect(backendEquityCurve(stats).map(point => point.equity)).toEqual([76, 83])
  })
})
