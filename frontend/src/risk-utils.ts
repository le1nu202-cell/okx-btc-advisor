import type { Settings } from './types'

export function validateRiskSettings(settings:Pick<Settings,'equity'|'riskPercent'|'leverage'>):string {
  if(settings.equity==null||!Number.isFinite(settings.equity))return '账户权益必须是有限数值'
  if(settings.equity<=0)return '账户权益须大于 0'
  if(settings.riskPercent==null||!Number.isFinite(settings.riskPercent))return '每笔风险必须是有限数值'
  if(settings.riskPercent<.1||settings.riskPercent>2)return '每笔风险须为 0.1%～2%'
  if(settings.leverage==null||!Number.isFinite(settings.leverage))return '计划杠杆必须是有限数值'
  if(settings.leverage<1||settings.leverage>2)return '计划杠杆须为 1x～2x'
  return ''
}

export function riskEstimateLabel(configured:boolean,referenceNotional:number|null|undefined):string {
  if(!configured)return '填写并保存三项参数后计算'
  if(referenceNotional==null)return '等待有效交易触发与止损'
  return `${new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(referenceNotional)} USDT`
}
