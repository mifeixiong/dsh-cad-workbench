/**
 * 作业台账：workspace/.cad-workbench/jobs/<job_id>/job.json + 不可变 revision。
 *
 * 借自 zhaizhoux/dsh-cad-freecad 的「不可变修订」与 tian-yu200 的
 * delivery/trust 结论：每次记录都留一份 revision-NNNN.json，历史不覆盖。
 *
 * @module dsh-cad-workbench/lib/ledger
 */

import { closeSync, existsSync, openSync, readdirSync, readFileSync, statSync, unlinkSync } from 'node:fs'
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
    throw codedError('BAD_ARGS', `非法 job_id：${value || '(空)'}`)
  }
  return value
}

function codedError(code, message) {
  const error = new Error(message)
  error.code = code
  return error
}

function jobNotFound(jobId) {
  return codedError('JOB_NOT_FOUND', `作业不存在：${jobId}`)
}

/**
 * 作业级文件锁：包住「读 job.json → 改 → 写」，防止并发丢更新/覆盖修订。
 * 锁文件 60s 未释放视为失效。
 */
async function withJobLock(workspace, jobId, fn) {
  const dir = jobDir(workspace, validateJobId(jobId))
  await mkdir(dir, { recursive: true })
  const lockPath = join(dir, '.lock')
  let acquired = false
  for (let attempt = 0; attempt < 400; attempt += 1) {
    try {
      const fd = openSync(lockPath, 'wx')
      closeSync(fd)
      acquired = true
      break
    } catch (error) {
      if (error.code !== 'EEXIST') throw error
      try {
        if (Date.now() - statSync(lockPath).mtimeMs > 60000) unlinkSync(lockPath)
      } catch { /* 锁刚被释放 */ }
      await new Promise((resolve) => setTimeout(resolve, 25))
    }
  }
  if (!acquired) throw codedError('JOB_BUSY', '作业正被另一个进程写入，等待超时：' + jobId)
  try {
    return await fn()
  } finally {
    try { unlinkSync(lockPath) } catch { /* 已释放 */ }
  }
}

/** 新建作业。 */
export async function startJob(workspace, options = {}) {
  const jobId = validateJobId(options.job_id ?? newJobId())
  const dir = jobDir(workspace, jobId)
  await mkdir(dir, { recursive: true })
  if (existsSync(join(dir, 'job.json'))) {
    throw codedError('JOB_EXISTS', `作业已存在（不可覆盖不可变修订）：${jobId}`)
  }
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
    let job = null
    try {
      job = await getJob(workspace, name)
    } catch {
      continue
    }
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
  return withJobLock(workspace, jobId, async () => {
    const job = await getJob(workspace, jobId)
    if (!job) throw jobNotFound(jobId)
    job.events = Array.isArray(job.events) ? job.events : []
    job.events.push({ at: new Date().toISOString(), ...event })
    job.updated_at = new Date().toISOString()
    await persist(workspace, job)
    return job
  })
}

/** 记录产物（同一路径覆盖旧记录）。 */
export async function recordArtifacts(workspace, jobId, artifacts) {
  if (!Array.isArray(artifacts)) throw codedError('BAD_ARGS', 'artifacts 必须是数组')
  return withJobLock(workspace, jobId, async () => {
    const job = await getJob(workspace, jobId)
    if (!job) throw jobNotFound(jobId)
    const current = Array.isArray(job.artifacts) ? job.artifacts : []
    const byPath = new Map(current.map((item) => [item.path, item]))
    for (const artifact of artifacts) {
      if (artifact?.path) byPath.set(artifact.path, { at: new Date().toISOString(), ...artifact })
    }
    job.artifacts = [...byPath.values()]
    job.updated_at = new Date().toISOString()
    await persist(workspace, job)
    return job
  })
}

/** 写入 verification（实测校核结果）。 */
export async function recordVerification(workspace, jobId, verification) {
  return withJobLock(workspace, jobId, async () => {
    const job = await getJob(workspace, jobId)
    if (!job) throw jobNotFound(jobId)
    job.verification = verification
    job.updated_at = new Date().toISOString()
    await persist(workspace, job)
    return job
  })
}

/**
 * 结束作业：按证据计算交付结论。
 * 规则：有 verified 校核 + 至少一个非空产物 → delivered；有产物但未校核 → partial；
 * 否则 failed。
 */
export async function finishJob(workspace, jobId, options = {}) {
  return withJobLock(workspace, jobId, async () => {
    const job = await getJob(workspace, jobId)
    if (!job) throw jobNotFound(jobId)
    const blockers = []
    const artifacts = (job.artifacts ?? []).filter((item) => item && item.path)
    const missing = artifacts.filter((item) => !existsSync(item.path))
    if (!artifacts.length) blockers.push('没有任何产物')
    for (const item of missing) blockers.push(`产物已丢失：${item.path}`)
    const verificationStatus = job.verification?.status ?? 'unverified'
    const verified = verificationStatus === 'verified'
    const mismatch = verificationStatus === 'mismatch'
    if (!verified) {
      const failures = (job.verification?.failures ?? []).slice(0, 5).join('；')
      blockers.push(`校核状态=${verificationStatus}，未通过` + (failures ? '：' + failures : ''))
    }
    const trust = verified ? 'verified' : artifacts.length ? 'measured-only' : 'unverified'
    let status = 'failed'
    if (artifacts.length && !missing.length) status = verified ? 'delivered' : 'partial'
    if (mismatch) status = 'failed'
    if (!mismatch && options.accept_unverified === true && artifacts.length && !missing.length) status = 'delivered'
    job.delivery = { status, trust, blockers, verification_status: verificationStatus, finished_at: new Date().toISOString() }
    job.updated_at = new Date().toISOString()
    await persist(workspace, job)
    return job
  })
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
  const existing = readdirSync(dir)
    .map((name) => /^revision-(\d{4})\.json$/.exec(name))
    .filter(Boolean)
    .map((match) => Number(match[1]))
  let next = (existing.length ? Math.max(...existing) : 0) + 1
  const snapshotBase = {
    at: new Date().toISOString(),
    doc: job.doc,
    verification: job.verification,
    delivery: job.delivery,
    artifacts: job.artifacts,
    events: job.events.slice(-20),
  }
  let revision
  for (;;) {
    revision = String(next).padStart(4, '0')
    try {
      await writeFile(join(dir, `revision-${revision}.json`),
        JSON.stringify({ revision, ...snapshotBase }, null, 2), { encoding: 'utf8', flag: 'wx' })
      break
    } catch (error) {
      if (error.code !== 'EEXIST') throw error
      next += 1
    }
  }
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
