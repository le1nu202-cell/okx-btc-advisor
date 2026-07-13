import assert from 'node:assert/strict'
import test from 'node:test'
import { normalizeNews } from '../src/api.ts'

test('unavailable news is neutral even if a malformed response carries a score',()=>{
  const result=normalizeNews({analysis:{status:'unavailable',score:15,sourceCoverage:1}})
  assert.equal(result.analysis.score,0)
  assert.equal(result.analysis.status,'unavailable')
})

test('news weights and source health keep a finite, bounded UI contract',()=>{
  const result=normalizeNews({
    items:[{id:'n1',title:'Bitcoin event',url:'https://example.com/a',source:'okx',sources:['okx','coindesk'],sentiment:.4,importance:9,relevance:2,sourceCount:2.9,timeDecay:4,directionConfidence:-1,weightBreakdown:{severity:.9,bad:'NaN'},weightFormula:'formula'}],
    analysis:{status:'partial',score:30,sourceCoverage:.5,sourceStatus:[{source:'okx',ok:true,itemCount:2,observedAt:1_700_000_000_000},{source:'feed',ok:false,itemCount:-2,error:'offline'}]},
  })
  const item=result.items[0]
  assert.equal(item.sentiment,1)
  assert.equal(item.importance,5)
  assert.equal(item.relevance,1)
  assert.equal(item.sourceCount,2)
  assert.equal(item.timeDecay,1)
  assert.equal(item.directionConfidence,0)
  assert.deepEqual(item.weightBreakdown,{severity:.9})
  assert.equal(result.analysis.score,15)
  assert.equal(result.analysis.sourceStatus.filter(source=>source.ok).length,1)
})
