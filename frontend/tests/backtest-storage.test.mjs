import assert from 'node:assert/strict'
import test from 'node:test'

import {
  BACKTEST_JOBS_STORAGE_KEY,
  clearBacktestJobHistory,
} from '../src/backtest-storage.ts'

test('clearBacktestJobHistory removes the shared backtest history key', () => {
  const removed = []

  clearBacktestJobHistory({ removeItem: key => removed.push(key) })

  assert.deepEqual(removed, [BACKTEST_JOBS_STORAGE_KEY])
})
