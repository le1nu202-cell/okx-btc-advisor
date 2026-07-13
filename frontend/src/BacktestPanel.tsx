import { useCallback, useEffect, useRef, useState } from 'react'
import { BarChart3, LoaderCircle, Pause, Play, RefreshCw } from 'lucide-react'
import { api } from './api'
import { readBacktestJobs, rememberBacktestJob } from './backtest-storage'
import { BACKTEST_BENCHMARK_LABELS } from './backtest-utils'
import { validationCriterionLabel } from './ui-contracts'
import type { BacktestBreakdownRow, BacktestHistoryCoverage, BacktestJob, BacktestParameters, BacktestResult, BacktestSeriesPoint } from './types'

type RecentRow={job:BacktestJob|null;parameters:BacktestParameters|null;startedAt:number|null}
const percent=(value:number|null,sign=true)=>value==null?'—':`${sign&&value>0?'+':''}${(value*100).toFixed(2)}%`
const number=(value:number|null,digits=2)=>value==null?'—':value.toFixed(digits)
const date=(value:number|null|undefined)=>value==null?'—':new Date(value).toISOString().slice(0,10)
const statusLabel=(status:string)=>({queued:'排队中',running:'运行中',complete:'已完成',failed:'失败',interrupted:'已中断'}[status]??status)
const unavailableReason=(result:BacktestResult)=>result.reason?.trim()||(result.status==='insufficient_data'?'数据不足':'未生成可用的回测结果')

function Curve({title,points,percentValues=false}:{title:string;points:BacktestSeriesPoint[];percentValues?:boolean}){
  if(points.length<2)return null
  const width=720,height=180,pad=14,values=points.map(x=>x.value),min=Math.min(...values),max=Math.max(...values),spread=max-min||1
  const path=points.map((point,index)=>`${index?'L':'M'} ${(pad+index/(points.length-1)*(width-pad*2)).toFixed(1)} ${(pad+(max-point.value)/spread*(height-pad*2)).toFixed(1)}`).join(' ')
  const format=(value:number)=>percentValues?percent(value,false):number(value,3)
  return <figure className="backtest-curve"><figcaption><strong>{title}</strong><span>最低 {format(min)} · 最高 {format(max)} · {points.length} 个真实数据点</span></figcaption><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}，最低 ${format(min)}，最高 ${format(max)}`} preserveAspectRatio="none"><line x1={pad} x2={width-pad} y1={height-pad} y2={height-pad}/><path d={path}/></svg></figure>
}

function Breakdown({title,rows}:{title:string;rows:BacktestBreakdownRow[]}){
  if(!rows.length)return null
  return <section className="backtest-breakdown"><h4>{title}</h4><div className="table-scroll"><table><caption className="sr-only">{title}</caption><thead><tr><th scope="col">分组</th><th scope="col">交易数</th><th scope="col">净收益</th><th scope="col">最大回撤</th><th scope="col">Sharpe</th><th scope="col">胜率</th></tr></thead><tbody>{rows.map((row,index)=><tr key={`${row.label}-${index}`}><td>{row.label}{row.passed!=null&&<span className={`window-state ${row.passed?'ok':'bad'}`}>{row.passed?'通过':'未通过'}</span>}</td><td>{row.trades??'—'}</td><td className={(row.netReturn??0)>=0?'up':'down'}>{percent(row.netReturn)}</td><td>{percent(row.maxDrawdown,false)}</td><td>{number(row.sharpe)}</td><td>{percent(row.winRate,false)}</td></tr>)}</tbody></table></div></section>
}

function HistoryCoverage({value}:{value:BacktestHistoryCoverage|undefined}){
  if(!value)return null
  return <section className="history-coverage" aria-label="历史数据覆盖"><h4>历史数据覆盖</h4><span>请求 {date(value.requestedStart)} → {date(value.requestedEnd)}</span><span>1H 实际 {date(value.actualStart1H)} → {date(value.actualEnd1H)} · {value.rows1H??'—'} 根</span><span>4H 实际 {date(value.actualStart4H)} → {date(value.actualEnd4H)} · {value.rows4H??'—'} 根</span><b className={value.complete?'up':'down'}>{percent(value.durationCoverage,false)} · {value.complete?'覆盖完整':'覆盖不足'}</b></section>
}

function ResultDetails({result}:{result:BacktestResult}){
  if(result.status!=='complete')return <div className="backtest-results"><div className="backtest-result-unavailable" role="alert"><strong>未生成有效回测指标</strong><span>{unavailableReason(result)}</span><small>请先补足历史行情数据，然后重新运行回测。</small></div><HistoryCoverage value={result.historyCoverage}/></div>
  const metrics=[['净收益',percent(result.netReturn)],['最大回撤',percent(result.maxDrawdown,false)],['年化 Sharpe',number(result.sharpe)],['Sortino',number(result.sortino)],['Calmar',number(result.calmar)],['Profit Factor',number(result.profitFactor)],['胜率',percent(result.winRate,false)],['交易数',result.trades?.toString()??'—'],['市场暴露',percent(result.exposure,false)],['最大连亏',result.maxConsecutiveLosses?.toString()??'—'],['平均持有',result.averageBarsHeld==null?'—':`${result.averageBarsHeld.toFixed(1)} 小时`],...Object.entries(BACKTEST_BENCHMARK_LABELS).map(([key,label])=>[label,percent(result.benchmarks[key]??null)] as [string,string])]
  const stress=Object.entries(result.stress??{})
  return <div className="backtest-results"><div className="backtest-grid">{metrics.map(([label,value])=><div className="metric" key={label}><div className="metric-label">{label}</div><div className="metric-value">{value}</div></div>)}</div>
    <HistoryCoverage value={result.historyCoverage}/>
    {(result.equityCurve?.length??0)>=2&&<div className="curve-grid"><Curve title="全历史模拟净值" points={result.equityCurve!}/>{(result.drawdownCurve?.length??0)>=2&&<Curve title="全历史模拟回撤" points={result.drawdownCurve!} percentValues/>}</div>}
    {stress.length>0&&<section className="stress-grid" aria-label="成本压力测试"><h4>成本压力测试</h4>{stress.map(([label,row])=><div key={label}><strong>{label}</strong><span>净收益 {percent(row.netReturn)}</span><span>回撤 {percent(row.maxDrawdown,false)}</span><span>PF {number(row.profitFactor)}</span></div>)}</section>}
    {(result.aggregateOos||result.lockedHoldout||result.thresholdPass!=null||result.diagnosticsComplete!=null||result.validationCriteria)&&<section className="strict-validation"><h4>严格样本外验证</h4><div>{result.aggregateOos&&<span><b>滚动 OOS 汇总</b>净收益 {percent(result.aggregateOos.netReturn)} · Sharpe {number(result.aggregateOos.sharpe)} · {result.aggregateOos.trades??'—'} 笔 · 盈利窗口 {percent(result.aggregateOos.profitableWindowRatio,false)}</span>}{result.thresholdPass!=null&&<span><b>数值门槛</b>{result.thresholdPass?'通过':'未通过'}</span>}{result.diagnosticsComplete!=null&&<span><b>PBO / DSR 诊断</b>{result.diagnosticsComplete?'完整':'未完整，不授予最强验证标签'}</span>}</div>{result.validationCriteria&&<div className="criteria-list">{Object.entries(result.validationCriteria).map(([key,passed])=><span className={passed?'ok':'bad'} key={key}>{validationCriterionLabel(key)}<b>{passed?'通过':'未通过'}</b></span>)}</div>}</section>}
    <div className="breakdown-grid"><Breakdown title="滚动样本外窗口" rows={result.rollingWindows??[]}/>{result.lockedHoldout&&<Breakdown title={`最后 12 个月锁定验证${result.lockedHoldout.start&&result.lockedHoldout.end?` · ${new Date(result.lockedHoldout.start).toISOString().slice(0,10)} → ${new Date(result.lockedHoldout.end).toISOString().slice(0,10)}`:''}`} rows={[result.lockedHoldout]}/>}<Breakdown title="年度表现" rows={result.byYear??[]}/><Breakdown title="行情状态表现" rows={result.byRegime??[]}/></div>
    {result.holdingRule&&<p className="fineprint">{result.holdingRule}</p>}{result.limitations.length>0&&<div className="backtest-limits"><strong>验证限制</strong>{result.limitations.map(x=><span key={x}>{x}</span>)}</div>}
  </div>
}

export default function BacktestPanel(){
  const [parameters,setParameters]=useState<BacktestParameters>({strategy:'combined',years:3,feeBps:5,slippageBps:5})
  const [result,setResult]=useState<BacktestResult|null>(null),[jobs,setJobs]=useState<RecentRow[]>([])
  const [active,setActive]=useState<BacktestJob|null>(null),[running,setRunning]=useState(false),[feedback,setFeedback]=useState('')
  const controller=useRef<AbortController|null>(null)
  const refreshRecent=useCallback(async()=>{const stored=readBacktestJobs(),local=new Map(stored.map(row=>[row.id,row]));try{const server=await api.backtestHistory(10);setJobs(server.map(job=>{const fallback=local.get(job.id);return {job,parameters:job.parameters??fallback?.parameters??null,startedAt:job.createdAt??job.startedAt??fallback?.startedAt??null}}))}catch{const rows=await Promise.all(stored.map(async item=>{try{return {job:await api.backtestStatus(item.id),parameters:item.parameters,startedAt:item.startedAt}}catch{return {job:null,parameters:item.parameters,startedAt:item.startedAt}}}));setJobs(rows)}},[])
  useEffect(()=>{refreshRecent();return()=>controller.current?.abort()},[refreshRecent])

  const waitFor=async(job:BacktestJob)=>{controller.current?.abort();const next=new AbortController();controller.current=next;setResult(null);setActive(job);setRunning(true);setFeedback('');try{const done=await api.waitBacktest(job.id,{signal:next.signal,onProgress:setActive});setResult(done);setFeedback(done.status==='complete'?'回测已完成，下方只展示后端返回的真实结果。':`回测结束：${unavailableReason(done)}`)}catch(error){setFeedback(error instanceof DOMException&&error.name==='AbortError'?'已停止在本页等待；后端任务未被删除，可从最近任务恢复。':error instanceof Error?error.message:'回测失败')}finally{setRunning(false);controller.current=null;refreshRecent()}}
  const start=async()=>{if(parameters.years<1||parameters.years>5||parameters.feeBps<0||parameters.feeBps>100||parameters.slippageBps<0||parameters.slippageBps>100){setFeedback('请检查年份与成本参数范围。');return}setResult(null);setActive(null);setRunning(true);setFeedback('正在创建回测任务…');try{const job=await api.startBacktest(parameters);rememberBacktestJob({id:job.id,parameters:{...parameters},startedAt:Date.now()});await waitFor(job)}catch(error){setRunning(false);setFeedback(error instanceof Error?error.message:'无法创建回测任务');refreshRecent()}}
  const resume=async(row:RecentRow)=>{setResult(null);setActive(row.job);if(!row.job){setFeedback('该本地记录在后端已不存在。');return}if(row.parameters)setParameters(row.parameters);if(row.job.status==='complete'){if(row.job.result){setResult(row.job.result);setFeedback(row.job.result.status==='complete'?'已载入该次真实回测结果。':`回测结束：${unavailableReason(row.job.result)}`)}else setFeedback('该任务已完成，但后端未返回结果。');return}if(row.job.status==='failed'||row.job.status==='interrupted'){setFeedback(row.job.message||`回测${statusLabel(row.job.status)}`);return}await waitFor(row.job)}
  return <section className="panel backtest"><div className="section-title"><span><BarChart3 size={17}/>回测与样本外验证</span>{result?.status==='complete'&&<span className={`validation ${result.validation.startsWith('通过')?'ok':''}`}>{result.validation}</span>}</div>
    <form className="backtest-form" onSubmit={event=>{event.preventDefault();start()}}><label>策略<select value={parameters.strategy} onChange={e=>setParameters({...parameters,strategy:e.target.value as BacktestParameters['strategy']})}><option value="combined">组合</option><option value="trend">趋势</option><option value="range">震荡</option></select></label><label>历史年份<input type="number" min="1" max="5" step="1" value={parameters.years} onChange={e=>setParameters({...parameters,years:Number(e.target.value)})}/></label><label>手续费 (bps)<input type="number" min="0" max="100" step="0.1" value={parameters.feeBps} onChange={e=>setParameters({...parameters,feeBps:Number(e.target.value)})}/></label><label>基础滑点 (bps)<input type="number" min="0" max="100" step="0.1" value={parameters.slippageBps} onChange={e=>setParameters({...parameters,slippageBps:Number(e.target.value)})}/></label><div className="backtest-actions"><button className="button" disabled={running} type="submit">{running?<LoaderCircle className="spin"/>:<Play/>}运行回测</button>{running&&<button className="button ghost" type="button" onClick={()=>controller.current?.abort()}><Pause/>停止等待</button>}</div></form>
    {active&&<div className="job-progress" role="status" aria-live="polite"><div><strong>{statusLabel(active.status)}</strong><span>{active.message||'等待后端更新'}</span></div><span>{Math.round(active.progress*100)}%</span><progress max="1" value={active.progress}>{Math.round(active.progress*100)}%</progress></div>}
    {feedback&&<div className={feedback.includes('失败')||feedback.includes('超时')||feedback.includes('无法')||feedback.includes('中断')||feedback.includes('回测结束：')?'backtest-feedback error':'backtest-feedback'} role="status">{feedback}</div>}
    {result&&<ResultDetails result={result}/>} {!result&&!running&&<p className="fineprint">曲线、滚动窗口和行情分组只在后端返回对应真实数据时显示，缺失时不推算、不补零。</p>}
    <section className="recent-backtests"><div className="subsection-title"><strong>最近回测任务</strong><button type="button" onClick={refreshRecent}><RefreshCw/>刷新</button></div>{jobs.length?<div className="recent-job-list">{jobs.map((row,index)=>{const terminal=row.job?.status==='failed'||row.job?.status==='interrupted';const unavailable=row.job?.result?.status&&row.job.result.status!=='complete';return <article key={row.job?.id??`lost-${index}`}><div><strong>{statusLabel(row.job?.status??'已丢失')}</strong><span>{row.startedAt?new Date(row.startedAt).toLocaleString('zh-CN',{hour12:false}):'时间未知'} · {row.parameters?`${row.parameters.strategy} · ${row.parameters.years}年 · 费率/滑点 ${row.parameters.feeBps}/${row.parameters.slippageBps} bps`:'参数未知'}</span></div><button className="button secondary" type="button" disabled={running||!row.job} onClick={()=>resume(row)}>{row.job?.status==='complete'?(unavailable?'查看状态':'查看结果'):terminal?'查看状态':'恢复等待'}</button></article>})}</div>:<p className="fineprint">服务端尚无可恢复的回测任务。</p>}</section>
  </section>
}
