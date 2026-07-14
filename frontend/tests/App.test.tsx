import { expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../src/WorkbenchView', () => ({
  default: () => <main aria-label="工作台内容">工作台交互测试页</main>,
}))

vi.mock('../src/ResearchView', () => ({
  default: () => <main aria-label="研究区内容">研究区交互测试页</main>,
}))

import App from '../src/App'

it('默认进入工作台，并可真实点击切换研究区再返回', async () => {
  const user = userEvent.setup()
  render(<App/>)

  expect(screen.getByRole('main', { name: '工作台内容' })).toBeTruthy()
  expect(screen.getByRole('tab', { name: '交易工作台' }).getAttribute('aria-selected')).toBe('true')

  await user.click(screen.getByRole('tab', { name: '研究区' }))
  expect(screen.getByRole('main', { name: '研究区内容' })).toBeTruthy()
  expect(screen.getByText('实验研究区')).toBeTruthy()

  await user.click(screen.getByRole('tab', { name: '交易工作台' }))
  expect(screen.getByRole('main', { name: '工作台内容' })).toBeTruthy()
})
