import { useState } from 'react'
import { ChartNoAxesCombined, FlaskConical, History as HistoryIcon } from 'lucide-react'
import ResearchView from './ResearchView'
import TradeHistoryView from './TradeHistoryView'
import WorkbenchView from './WorkbenchView'

type View = 'workbench' | 'history' | 'research'

export default function App() {
  const [view, setView] = useState<View>('workbench')
  return <div className="v04-shell">
    <nav className="mode-nav" aria-label="主导航">
      <button className="mode-brand" onClick={() => setView('workbench')} aria-label="返回当前交易">
        <span className="logo">₿</span>
        <span><strong>BTC 个人手动交易工作台</strong><small>公共行情 · 人工确认 · 本地记录</small></span>
      </button>
      <div className="mode-tabs" role="tablist" aria-label="当前交易、历史记录与研究区">
        <button role="tab" aria-selected={view === 'workbench'} className={view === 'workbench' ? 'active' : ''} onClick={() => setView('workbench')}><ChartNoAxesCombined/><span>当前交易</span></button>
        <button role="tab" aria-selected={view === 'history'} className={view === 'history' ? 'active' : ''} onClick={() => setView('history')}><HistoryIcon/><span>历史记录</span></button>
        <button role="tab" aria-selected={view === 'research'} className={view === 'research' ? 'active' : ''} onClick={() => setView('research')}><FlaskConical/><span>研究区</span></button>
      </div>
    </nav>
    {view === 'workbench' && <WorkbenchView/>}
    {view === 'history' && <TradeHistoryView/>}
    {view === 'research' && <div className="research-route"><div className="research-warning"><strong>实验研究区</strong><span>方向评分、新闻与历史回测不改变实际交易状态，也不保证盈利。</span></div><ResearchView/></div>}
  </div>
}
