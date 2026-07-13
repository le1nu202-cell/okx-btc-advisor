import type { AdviceAction, SignalAdvice } from './types'

export function confidencePercent(value: unknown): number {
  const parsed=Number(value)
  return Number.isFinite(parsed)?Math.max(0,Math.min(100,parsed)):0
}

export function adviceDirection(action: AdviceAction): 'LONG'|'SHORT'|'WAIT' {
  if(action.includes('LONG'))return 'LONG'
  if(action.includes('SHORT'))return 'SHORT'
  return 'WAIT'
}

export function notificationKey(advice:Pick<SignalAdvice,'instrument'|'strategy'|'action'|'candleCloseAt'>):string {
  return `${advice.instrument}:${advice.strategy}:${adviceDirection(advice.action)}:${advice.candleCloseAt}`
}

export function uniqueMessages(...groups:Array<Array<string|null|undefined>|string|null|undefined>):string[] {
  const values=groups.flatMap(group=>Array.isArray(group)?group:[group])
  return [...new Set(values.filter((value):value is string=>typeof value==='string'&&value.trim().length>0))]
}

export function adviceValidationLabel(configVersion:string):string {
  return configVersion.toLowerCase().includes('unvalidated')?'实验信号 · 未通过严格历史验证':'验证状态未确认'
}

export function adviceIsCurrent(advice:SignalAdvice|null,snapshotStale:boolean,nowMs=Date.now()):boolean {
  if(!advice||advice.candleCloseAt==null||snapshotStale||!advice.dataQuality.fresh||advice.regime==='STALE')return false
  const age=nowMs-advice.candleCloseAt
  return Number.isFinite(age)&&age>=-60_000&&age<=2*3600_000
}

const criteriaLabels:Record<string,string>={
  atLeastTwoOosWindows:'至少 2 个 OOS 窗口',
  allOosWindowsComplete:'全部 OOS 窗口完成',
  oosMetricsComplete:'OOS 指标完整',
  marketDataCoverageAtLeast99Pct:'行情数据覆盖率 ≥ 99%',
  dataCoverageAtLeast99Pct:'行情数据覆盖率 ≥ 99%',
  noMarketDataGapLongerThan8Hours:'无超过 8 小时行情缺口',
  fundingCoverageAtLeast95Pct:'样本外与锁定期资金费率覆盖率 ≥ 95%',
  oosFundingCoverageAtLeast95Pct:'OOS 资金费率覆盖率 ≥ 95%',
  lockedHoldoutFundingCoverageAtLeast95Pct:'锁定期资金费率覆盖率 ≥ 95%',
  positiveOosNetReturn:'OOS 正收益',
  profitFactorAtLeast1_1:'PF ≥ 1.1',
  sharpeAtLeast0_5:'Sharpe ≥ 0.5',
  atLeast100Trades:'交易数 ≥ 100',
  profitableWindowsAtLeast60Pct:'盈利窗口 ≥ 60%',
  positiveAt20bps:'20bp 成本下仍为正',
  positiveLockedHoldout:'锁定期正收益',
  positiveLockedHoldoutAt20bps:'锁定期 20bp 仍为正',
}

export function validationCriterionLabel(key:string):string {
  const known=criteriaLabels[key]
  if(known)return known
  const readable=key.replace(/([a-z\d])([A-Z])/g,'$1 $2').replace(/[_-]+/g,' ').trim().replace(/\b[a-z]/g,letter=>letter.toUpperCase())
  return `未识别条件：${readable||'未命名'}`
}
