import assert from 'node:assert/strict'
import test from 'node:test'
import { BACKTEST_BENCHMARK_LABELS, normalizeBreakdown, normalizeSeries } from '../src/backtest-utils.ts'
import { normalizeBacktest, normalizeBacktestJob, normalizeCandle } from '../src/api.ts'

test('normalizeSeries rejects missing values and sorts real points',()=>{
  assert.deepEqual(normalizeSeries([[2000,1.1],[1000,1],['bad',2],{value:1.2}]),[{timestamp:1000,value:1},{timestamp:2000,value:1.1}])
})

test('normalizeBreakdown accepts backend object maps without inventing metrics',()=>{
  assert.deepEqual(normalizeBreakdown({'2025':{trades:3,netReturn:.1}}),[{label:'2025',trades:3,netReturn:.1,maxDrawdown:null,sharpe:null,winRate:null,passed:null}])
})

test('normalizeBreakdown unwraps strict validation OOS metrics and labels the window',()=>{
  const [row]=normalizeBreakdown([{index:2,oosStart:1735689600000,oosEnd:1743465600000,oosMetrics:{trades:12,netReturn:.04,sharpe:.8}}])
  assert.equal(row.label,'窗口 2 · OOS 2025-01-01 → 2025-04-01')
  assert.equal(row.trades,12)
  assert.equal(row.netReturn,.04)
  assert.equal(row.sharpe,.8)
})

test('normalizeBacktest exposes strict OOS aggregate and locked holdout without replacing full-history metrics',()=>{
  const raw={netReturn:.2,strictValidation:{windows:[{index:1,oosStart:1735689600000,oosEnd:1743465600000,oosMetrics:{netReturn:.03,trades:9}}],aggregateOos:{netReturn:.04,sharpe:.7,trades:9,windows:1,profitableWindowRatio:1},holdout:{start:1743465600000,end:1775001600000,metrics:{netReturn:-.02,trades:4}},thresholdPass:false,diagnosticsComplete:false,criteria:{positiveOosNetReturn:true,atLeast100Trades:false}}}
  const result=normalizeBacktest(raw)
  assert.equal(result.netReturn,.2)
  assert.equal(result.rollingWindows[0].netReturn,.03)
  assert.equal(result.aggregateOos.netReturn,.04)
  assert.equal(result.lockedHoldout.netReturn,-.02)
  assert.equal(result.thresholdPass,false)
  assert.equal(result.diagnosticsComplete,false)
  const job=normalizeBacktestJob({id:'x',status:'complete',result:raw})
  assert.deepEqual(job.result.validationCriteria,{positiveOosNetReturn:true,atLeast100Trades:false})
})

test('normalizeBacktest preserves unavailable benchmarks as null',()=>{
  const result=normalizeBacktest({benchmarks:{cash:0,buyHold1x:null,ema20_50:'bad'}})
  assert.deepEqual(result.benchmarks,{cash:0,buyHold1x:null,ema20_50:null})
})

test('the result panel contract includes all required benchmarks',()=>{
  assert.deepEqual(BACKTEST_BENCHMARK_LABELS,{cash:'现金基准',buyHold1x:'1x 买入持有基准',ema20_50:'EMA20/50 基准'})
})

test('normalizeBacktest preserves an insufficient-data reason',()=>{
  const result=normalizeBacktest({status:'insufficient_data',reason:'数据不足：只有 120 根 K 线'})
  assert.equal(result.status,'insufficient_data')
  assert.equal(result.reason,'数据不足：只有 120 根 K 线')
})

test('backtest jobs expose requested versus actual history coverage',()=>{
  const job=normalizeBacktestJob({id:'coverage',status:'complete',result:{historyCoverage:{requestedStart:1_700_000_000_000,requestedEnd:1_800_000_000_000,actualStart1H:1_700_000_000_000,actualEnd1H:1_800_000_000_000,actualStart4H:1_700_000_000_000,actualEnd4H:1_800_000_000_000,rows1H:10,rows4H:3,durationCoverage:1,complete:true}}})
  assert.equal(job.result.historyCoverage.complete,true)
  assert.equal(job.result.historyCoverage.rows1H,10)
  assert.equal(job.result.historyCoverage.durationCoverage,1)
})

test('normalizeCandle keeps real zero volume but rejects fabricated or invalid bars',()=>{
  assert.deepEqual(normalizeCandle({timestamp:1735689600000,open:100,high:105,low:95,close:102,volume:0,confirm:true}),{timestamp:1735689600000,open:100,high:105,low:95,close:102,volume:0,confirm:true})
  assert.equal(normalizeCandle({timestamp:1735689600000,open:100,high:99,low:95,close:102,volume:1}),null)
  assert.equal(normalizeCandle({timestamp:1735689600000,open:100,high:105,low:95,close:102}),null)
  assert.equal(normalizeCandle([null,100,105,95,102,1]),null)
})
