import { expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../src/WorkbenchView', () => ({
  default: () => <main aria-label="当前交易内容">当前交易交互测试页</main>,
}))

vi.mock('../src/TradeHistoryView', () => ({
  default: () => <main aria-label="历史记录内容">历史记录交互测试页</main>,
}))

vi.mock('../src/ResearchView', () => ({
  default: () => <main aria-label="研究区内容">研究区交互测试页</main>,
}))

import App from '../src/App'

it('默认进入当前交易，并可真实点击切换历史记录和研究区', async () => {
  const user = userEvent.setup()
  render(<App/>)

  expect(screen.getByRole('main', { name: '当前交易内容' })).toBeTruthy()
  expect(screen.getByRole('tab', { name: '当前交易' }).getAttribute('aria-selected')).toBe('true')

  await user.click(screen.getByRole('tab', { name: '历史记录' }))
  expect(screen.getByRole('main', { name: '历史记录内容' })).toBeTruthy()
  expect(screen.getByRole('tab', { name: '历史记录' }).getAttribute('aria-selected')).toBe('true')

  await user.click(screen.getByRole('tab', { name: '研究区' }))
  expect(screen.getByRole('main', { name: '研究区内容' })).toBeTruthy()
  expect(screen.getByText('实验研究区')).toBeTruthy()

  await user.click(screen.getByRole('button', { name: '返回当前交易' }))
  expect(screen.getByRole('main', { name: '当前交易内容' })).toBeTruthy()
})
