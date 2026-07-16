import { expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../src/WorkbenchView', () => ({
  default: ({ onOpenMarketAnalysis }: { onOpenMarketAnalysis?: () => void }) => <main aria-label="当前交易内容">
    当前交易交互测试页
    <label htmlFor="draft-note">未保存草稿</label>
    <input id="draft-note" defaultValue=""/>
    <button type="button" onClick={onOpenMarketAnalysis}>打开行情判断详情</button>
  </main>,
}))

vi.mock('../src/TradeHistoryView', () => ({
  default: () => <main aria-label="历史记录内容">历史记录交互测试页</main>,
}))

vi.mock('../src/MarketAnalysisView', () => ({
  default: () => <main aria-label="行情判断内容">行情判断交互测试页</main>,
}))

vi.mock('../src/ResearchView', () => ({
  default: () => <main aria-label="研究区内容">研究区交互测试页</main>,
}))

import App from '../src/App'

it('默认进入当前交易，并可真实点击切换行情判断、历史记录和研究区', async () => {
  const user = userEvent.setup()
  render(<App/>)

  expect(screen.getByRole('main', { name: '当前交易内容' })).toBeTruthy()
  expect(screen.getByRole('tab', { name: '当前交易' }).getAttribute('aria-selected')).toBe('true')

  await user.click(screen.getByRole('tab', { name: '行情判断' }))
  expect(screen.getByRole('main', { name: '行情判断内容' })).toBeTruthy()
  expect(screen.getByRole('tab', { name: '行情判断' }).getAttribute('aria-selected')).toBe('true')

  await user.click(screen.getByRole('tab', { name: '历史记录' }))
  expect(screen.getByRole('main', { name: '历史记录内容' })).toBeTruthy()
  expect(screen.getByRole('tab', { name: '历史记录' }).getAttribute('aria-selected')).toBe('true')

  await user.click(screen.getByRole('tab', { name: '研究区' }))
  expect(screen.getByRole('main', { name: '研究区内容' })).toBeTruthy()
  expect(screen.getByText('实验研究区')).toBeTruthy()

  await user.click(screen.getByRole('button', { name: '返回当前交易' }))
  expect(screen.getByRole('main', { name: '当前交易内容' })).toBeTruthy()
})

it('从工作台打开行情判断再返回时保留未保存草稿', async () => {
  const user = userEvent.setup()
  render(<App/>)

  const draft = screen.getByLabelText('未保存草稿') as HTMLInputElement
  await user.type(draft, '保留这份计划')
  await user.click(screen.getByRole('button', { name: '打开行情判断详情' }))
  expect(screen.getByRole('main', { name: '行情判断内容' })).toBeTruthy()

  await user.click(screen.getByRole('tab', { name: '当前交易' }))
  expect((screen.getByLabelText('未保存草稿') as HTMLInputElement).value).toBe('保留这份计划')
})
