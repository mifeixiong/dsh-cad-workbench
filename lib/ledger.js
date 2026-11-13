/**
 * 作业台账：workspace/.cad-workbench/jobs/<job_id>/job.json + 不可变 revision。
 *
 * 借自 zhaizhoux/dsh-cad-freecad 的「不可变修订」与 tian-yu200 的
 * delivery/trust 结论：每次记录都留一份 revision-NNNN.json，历史不覆盖。
 *
 * @module dsh-cad-workbench/lib/ledger
 */

import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { mkdir, readdir, rename, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { createHash } from 'node:crypto'

export const LEDGER_DIR = '.cad-workbench'

export function jobsRoot(workspace) {
  return join(workspace, LEDGER_DIR, 'jobs')
}

export function jobDir(workspace, jobId) {
  return join(jobsRoot(workspace), String(jobId))
}

/** 生成 job_id：时间戳 + 4 位随机。 */
export function newJobId(prefix = 'job') {
  const now = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)
  const rand = Math.random().toString(36).slice(2, 6)
  return `${prefix}-${now}-${rand}`
}

function validateJobId(jobId) {
  const value = String(jobId ?? '')
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$/.test(value)) {
    throw new Error(`非法 job_id：${value || '(空)'}`)
  }
  return value
}

/** 新建作业。 */
export async function startJob(workspace, options = {}) {
  const jobId = validateJobId(options.job_id ?? newJobId())
  const dir = jobDir(workspace, jobId)
  await mkdir(dir, { recursive: true })
  const job = {
    job_id: jobId,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    name: options.name ?? null,
    workspace,
    plan: options.plan ?? null,
    doc: options.doc ?? null,
    events: [],
    artifacts: [],
    verification: { status: 'unverified', checks: [] },
    delivery: { status: 'draft', trust: 'unverified', blockers: [] },
    revisions: [],
  }
  await persist(workspace, job)
  return job
}

/** 读取作业；不存在返回 null。 */
export async function getJob(workspace, jobId) {
  const file = join(jobDir(workspace, validateJobId(jobId)), 'job.json')
  if (!existsSync(file)) return null
  try {
    return JSON.parse(readFileSync(file, 'utf8'))
  } catch {
    return null
  }
}

/** 列出作业摘要（最新在前）。 */
export async function listJobs(workspace, limit = 20) {
  const root = jobsRoot(workspace)
  if (!existsSync(root)) return []
  const names = await readdir(root).catch(() => [])
  const jobs = []
  for (const name of names.sort().reverse().slice(0, Math.max(1, Math.min(200, limit)))) {
    const job = await getJob(workspace, name)
    if (job) {
      jobs.push({
        job_id: job.job_id,
        created_at: job.created_at,
        name: job.name,
        delivery: job.delivery,
        artifacts: (job.artifacts ?? []).length,
        events: (job.events ?? []).length,
      })
    }
  }
  return jobs
}

/**
 * 追加一次事件，并把当前状态存成不可变 revision。
 * @param {string} workspace
 * @param {string} jobId
 * @param {object} event
 */
export async function recordEvent(workspace, jobId, event) {
  const job = await getJob(workspace, jobId)
  if (!job) throw new Error(`作业不存在：${jobId}`)
  job.events = Array.isArray(job.events) ? job.events : []
  job.events.push({ at: new Date().toISOString(), ...event })
  job.updated_at = new Date().toISOString()
  await persist(workspace, job)
  return job
}

/** 记录产物（同一路径覆盖旧记录）。 */
export async function recordArtifacts(workspace, jobId, artifacts) {
  const job = await getJob(workspace, jobId)
  if (!job) throw new Error(`作业不存在：${jobId}`)
  const current = Array.isArray(job.artifacts) ? job.artifacts : []
  const byPath = new Map(current.map((item) => [item.path, item]))
  for (const artifact of artifacts ?? []) {
    if (artifact?.path) byPath.set(artifact.path, { at: new Date().toISOString(), ...artifact })
  }
  job.artifacts = [...byPath.values()]
  job.updated_at = new Date().toISOString()
  await persist(workspace, job)
  return job
}

/** 写入 verification（实测校核结果）。 */
export async function recordVerification(workspace, jobId, verification) {
  const job = await getJob(workspace, jobId)
  if (!job) throw new Error(`作业不存在：${jobId}`)
  job.verification = verification
  job.updated_at = new Date().toISOString()
  await persist(workspace, job)
  return job
}

/**
 * 结束作业：按证据计算交付结论。
 * 规则：有 verified 校核 + 至少一个非空产物 → delivered；有产物但未校核 → partial；
 * 否则 failed。
 */
export async function finishJob(workspace, jobId, options = {}) {
  const job = await getJob(workspace, jobId)
  if (!job) throw new Error(`作业不存在：${jobId}`)
  const blockers = []
  const artifacts = (job.artifacts ?? []).filter((item) => item && item.path)
  const missing = artifacts.filter((item) => !existsSync(item.path))
  if (!artifacts.length) blockers.push('没有任何产物')
  for (const item of missing) blockers.push(`产物已丢失：${item.path}`)
  const verified = job.verification?.status === 'verified'
  const trust = verified ? 'verified' : artifacts.length ? 'measured-only' : 'unverified'
  let status = 'failed'
  if (artifacts.length && !missing.length) status = verified ? 'delivered' : 'partial'
  if (options.accept_unverified === true && artifacts.length && !missing.length) status = 'delivered'
  job.delivery = { status, trust, blockers, finished_at: new Date().toISOString() }
  job.updated_at = new Date().toISOString()
  await persist(workspace, job)
  return job
}

/** 读全部 revision 文件名。 */
export function listRevisions(workspace, jobId) {
  const dir = jobDir(workspace, validateJobId(jobId))
  if (!existsSync(dir)) return []
  return readdirSync(dir).filter((name) => /^revision-\d{4}\.json$/.test(name)).sort()
}

async function persist(workspace, job) {
  const dir = jobDir(workspace, job.job_id)
  await mkdir(dir, { recursive: true })
  const revision = String((job.revisions?.length ?? 0) + 1).padStart(4, '0')
  const snapshot = {
    revision,
    at: new Date().toISOString(),
    doc: job.doc,
    verification: job.verification,
    delivery: job.delivery,
    artifacts: job.artifacts,
    events: job.events.slice(-20),
  }
  await writeFile(join(dir, `revision-${revision}.json`), JSON.stringify(snapshot, null, 2), 'utf8')
  job.revisions = [...(job.revisions ?? []), revision]
  await atomicWrite(join(dir, 'job.json'), JSON.stringify(job, null, 2))
  return job
}

async function atomicWrite(path, content) {
  const tmp = `${path}.tmp-${process.pid}-${Date.now()}`
  await writeFile(tmp, content, 'utf8')
  await rename(tmp, path)
}

/** 文件 sha256（产物指纹；文件不存在返回 null）。 */
export function fileSha256(path) {
  if (!existsSync(path)) return null
  const hash = createHash('sha256')
  hash.update(readFileSync(path))
  return hash.digest('hex')
}
