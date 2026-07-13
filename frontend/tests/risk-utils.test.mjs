import assert from 'node:assert/strict'
import test from 'node:test'
import { riskEstimateLabel, validateRiskSettings } from '../src/risk-utils.ts'

test('risk validation rejects non-finite values before JSON serialization',()=>{
  assert.match(validateRiskSettings({equity:Infinity,riskPercent:1,leverage:1}),/权益.*有限/)
  assert.match(validateRiskSettings({equity:1000,riskPercent:Infinity,leverage:1}),/风险.*有限/)
  assert.match(validateRiskSettings({equity:1000,riskPercent:1,leverage:NaN}),/杠杆.*有限/)
})

test('risk validation accepts any positive finite equity',()=>{
  assert.match(validateRiskSettings({equity:0,riskPercent:1,leverage:1}),/大于 0/)
  assert.equal(validateRiskSettings({equity:1e100,riskPercent:.1,leverage:2}),'')
})

test('configured WAIT advice is not mislabeled as missing risk settings',()=>{
  assert.equal(riskEstimateLabel(false,null),'填写并保存三项参数后计算')
  assert.equal(riskEstimateLabel(true,null),'等待有效交易触发与止损')
  assert.match(riskEstimateLabel(true,1234.5),/1,234\.5 USDT/)
})
