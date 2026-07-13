import type { BacktestParameters } from './types'

export const BACKTEST_JOBS_STORAGE_KEY='okx-advisor-backtest-jobs-v1'
export type StoredBacktestJob={id:string;parameters:BacktestParameters;startedAt:number}

export function readBacktestJobs():StoredBacktestJob[]{try{const rows=JSON.parse(localStorage.getItem(BACKTEST_JOBS_STORAGE_KEY)??'[]');return Array.isArray(rows)?rows.filter(x=>x&&typeof x.id==='string').slice(0,5):[]}catch{return []}}
export function rememberBacktestJob(row:StoredBacktestJob){const next=[row,...readBacktestJobs().filter(x=>x.id!==row.id)].slice(0,5);localStorage.setItem(BACKTEST_JOBS_STORAGE_KEY,JSON.stringify(next))}
export function clearBacktestJobHistory(storage:Pick<Storage,'removeItem'>=localStorage){storage.removeItem(BACKTEST_JOBS_STORAGE_KEY)}
