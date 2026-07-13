import assert from 'node:assert/strict'
import test from 'node:test'

import {
  adviceValidationLabel,
  adviceIsCurrent,
  confidencePercent,
  notificationKey,
  uniqueMessages,
  validationCriterionLabel,
} from '../src/ui-contracts.ts'

test('confidence remains on the backend 0 to 100 scale and is clamped',()=>{
  assert.equal(confidencePercent(1),1)
  assert.equal(confidencePercent(67.4),67.4)
  assert.equal(confidencePercent(-5),0)
  assert.equal(confidencePercent(120),100)
  assert.equal(confidencePercent(Infinity),0)
})

test('notification key deduplicates watch and candidate actions by direction',()=>{
  const base={instrument:'BTC-USDT-SWAP',strategy:'trend',candleCloseAt:123}
  assert.equal(
    notificationKey({...base,action:'WATCH_LONG'}),
    notificationKey({...base,action:'LONG_CANDIDATE'}),
  )
  assert.equal(
    notificationKey({...base,action:'WATCH_SHORT'}),
    notificationKey({...base,action:'SHORT_CANDIDATE'}),
  )
  assert.notEqual(notificationKey({...base,action:'WATCH_LONG'}),notificationKey({...base,action:'WATCH_SHORT'}))
})

test('technical messages are deduplicated across warning and risk groups',()=>{
  assert.deepEqual(
    uniqueMessages('', ['波动极端', '技术冲突'], ['波动极端', null], undefined),
    ['波动极端', '技术冲突'],
  )
})

test('unvalidated advice is labeled prominently and never implied to be verified',()=>{
  assert.equal(adviceValidationLabel('research-v2-unvalidated'),'实验信号 · 未通过严格历史验证')
  assert.equal(adviceValidationLabel('unknown'),'验证状态未确认')
})

test('old candidate cannot remain actionable after snapshot or advice failure',()=>{
  const now=1_800_000_000_000
  const candidate={regime:'TREND',candleCloseAt:now-3_600_000,dataQuality:{fresh:true}}
  assert.equal(adviceIsCurrent(candidate,false,now),true)
  assert.equal(adviceIsCurrent(candidate,true,now),false)
  assert.equal(adviceIsCurrent({...candidate,dataQuality:{fresh:false}},false,now),false)
  assert.equal(adviceIsCurrent({...candidate,candleCloseAt:now-2*3_600_000-1},false,now),false)
  assert.equal(adviceIsCurrent(null,false,now),false)
})

test('validation criteria use Chinese labels and readable fallback text',()=>{
  assert.equal(validationCriterionLabel('marketDataCoverageAtLeast99Pct'),'行情数据覆盖率 ≥ 99%')
  assert.equal(validationCriterionLabel('fundingCoverageAtLeast95Pct'),'样本外与锁定期资金费率覆盖率 ≥ 95%')
  assert.equal(validationCriterionLabel('unknownValidationRule'),'未识别条件：Unknown Validation Rule')
})
