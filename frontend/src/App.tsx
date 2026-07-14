import { useState } from 'react'
import { ChartNoAxesCombined, FlaskConical } from 'lucide-react'
import ResearchView from './ResearchView'
import WorkbenchView from './WorkbenchView'

type View = 'workbench' | 'research'

export default function App() {
  const [view, setView] = useState<View>('workbench')
  return <div className="v04-shell">
    <nav className="mode-nav" aria-label="应用区域">
      <button className="mode-brand" onClick={() => setView('workbench')} aria-label="返回交易工作台">
        <span className="logo">₿</span>
        <span><strong>BTC 分批加仓交易与风险工作台</strong><small>v0.4 · 本机手动确认</small></span>
      </button>
      <div className="mode-tabs" role="tablist" aria-label="工作台与研究区">
        <button role="tab" aria-selected={view === 'workbench'} className={view === 'workbench' ? 'active' : ''} onClick={() => setView('workbench')}><ChartNoAxesCombined/>交易工作台</button>
        <button role="tab" aria-selected={view === 'research'} className={view === 'research' ? 'active' : ''} onClick={() => setView('research')}><FlaskConical/>研究区</button>
      </div>
    </nav>
    {view === 'workbench' ? <WorkbenchView/> : <div className="research-route"><div className="research-warning"><strong>实验研究区</strong><span>旧方向评分与三年回测未通过验证，不用于真实交易计划，也不能保证盈利。</span></div><ResearchView/></div>}
  </div>
}
