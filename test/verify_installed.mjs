/**
 * 对**已安装副本**做端到端验证（工作区源码不算数）。
 *
 * 直接 import profile/node_modules/dsh-cad-workbench/index.js，用 mock ctx 注册工具，
 * 按 DSH 的 walkJsonValue 规则检查返回值，再真跑 cadw_doctor / cadw_model(dry_run) /
 * cadw_review / cadw_job。需要 SolidWorks 在运行（doctor 会如实报告）。
 *
 * 用法：node test/verify_installed.mjs [安装副本目录]
 */
import assert from 'node:assert/strict'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { mkdtemp, mkdir } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = dirname(HERE)

function discoverInstalled() {
  if (process.env.DSH_PROFILE_DIR) {
    const linked = join(process.env.DSH_PROFILE_DIR, 'node_modules', 'dsh-cad-workbench')
    if (existsSync(linked)) return linked
  }
  const home = process.env.USERPROFILE || process.env.HOME
  for (const candidate of [
    join(home, '.dsh', 'profiles', 'desktop', 'node_modules', 'dsh-cad-workbench'),
    join(home, '.dsh', 'profiles', 'web', 'node_modules', 'dsh-cad-workbench'),
  ]) {
    if (existsSync(candidate)) return candidate
  }
  return undefined
}

const target = resolve(process.argv[2] ?? discoverInstalled() ?? '')
if (!existsSync(target)) {
  console.error('找不到安装副本：' + (target || '(未指定)'))
  process.exit(2)
}

process.env.DSH_CAD_SELFTEST = '1'
const { apply } = await import(pathToFileURL(join(target, 'index.js')).href)

const problems = []
function check(name, condition, detail) {
  if (condition) console.log('  PASS ' + name)
  else {
    problems.push(name + (detail ? '：' + detail : ''))
    console.log('  FAIL ' + name + (detail ? '：' + detail : ''))
  }
}

function losslessProblems(value, path = '$') {
  const found = []
  const ancestors = new Set()
  const walk = (node, at) => {
    if (node === undefined) return void found.push(at + ' 是 undefined')
    if (node === null || typeof node === 'boolean' || typeof node === 'string') return
    if (typeof node === 'number') {
      if (!Number.isFinite(node)) found.push(at + ' 非有限数字')
      else if (Object.is(node, -0)) found.push(at + ' 是 -0')
      return
    }
    if (typeof node !== 'object') return void found.push(at + ' 类型 ' + typeof node)
    if (ancestors.has(node)) return void found.push(at + ' 有环')
    ancestors.add(node)
    if (Array.isArray(node)) {
      if (Object.getPrototypeOf(node) !== Array.prototype) found.push(at + ' 数组原型异常')
      if (Reflect.ownKeys(node).length !== node.length + 1) found.push(at + ' 稀疏数组')
      node.forEach((item, index) => walk(item, at + '[' + index + ']'))
    } else {
      const prototype = Object.getPrototypeOf(node)
      if (prototype !== null && prototype !== Object.prototype) found.push(at + ' 非纯对象')
      for (const key of Reflect.ownKeys(node)) {
        if (typeof key !== 'string') found.push(at + ' 含 symbol 键')
        else walk(node[key], at + '.' + key)
      }
    }
    ancestors.delete(node)
  }
  walk(value, path)
  return found
}

const definitions = new Map()
let skillFactory
const ctx = {
  tools: { register(definition) { definitions.set(definition.name, definition); return () => {} } },
  get(name) {
    if (name === 'skills') return { registerProvider(factory) { skillFactory = factory } }
    return undefined
  },
  effect(fn) { return fn() },
  logger: { warn() {} },
}
apply(ctx)

console.log('安装副本：' + target)
check('tool.count', definitions.size === 9, '实际 ' + definitions.size)
const skills = skillFactory ? await skillFactory().list() : []
check('skill.count', skills.length === 4, '实际 ' + skills.length)

const workspace = await mkdtemp(join(tmpdir(), 'cadw-installed-'))

async function invoke(name, args) {
  const result = await definitions.get(name).execute(args, { agent: null })
  const issues = losslessProblems(result)
  check(name + '.lossless', issues.length === 0, issues.slice(0, 3).join('；'))
  return result
}

console.log('== cadw_doctor ==')
const doctor = await invoke('cadw_doctor', { workspace, probe_com: true })
check('doctor.ok', doctor.ok === true, doctor.error?.message)
check('doctor.com', doctor.capabilities?.com === true, JSON.stringify(doctor.capabilities))
check('doctor.path', doctor.executable?.includes('python'), doctor.executable)

console.log('== cadw_model dry_run ==')
const dry = await invoke('cadw_model', {
  workspace,
  doc: 'unused',
  dry_run: true,
  graph: {
    units: 'mm',
    nodes: [
      { op: 'sketch', plane: 'front', entities: [{ type: 'rect', width: 10, height: 5 }] },
      { op: 'extrude', depth: 2 },
    ],
  },
})
check('model.dry_run', dry.ok === true && dry.dry_run === true, dry.error?.message)
check('model.plan', Array.isArray(dry.plan) && dry.plan.length === 2)

console.log('== cadw_job ==')
const started = await invoke('cadw_job', { action: 'start', name: 'verify', workspace, plan: { expect: {} } })
check('job.start', started.ok === true, started.error?.message)
if (started.ok) {
  const artifact = join(workspace, 'artifact.step')
  writeFileSync(artifact, 'ISO-10303-21;')
  const recorded = await invoke('cadw_job', {
    action: 'record',
    job_id: started.job.job_id,
    workspace,
    artifacts: [{ format: 'step', path: artifact, bytes: 13 }],
    event: { tool: 'verify', ok: true },
  })
  check('job.record', recorded.ok === true && recorded.job.artifacts.length === 1)
  await invoke('cadw_job', {
    action: 'verify', job_id: started.job.job_id, workspace,
    verification: { status: 'verified', checks: [], failures: [] },
  })
  const finished = await invoke('cadw_job', { action: 'finish', job_id: started.job.job_id, workspace })
  check('job.delivered', finished.ok === true && finished.job.delivery.status === 'delivered')
}

console.log('== cadw_review (STL) ==')
const stl = join(workspace, 'tetra.stl')
const header = Buffer.alloc(80)
const body = Buffer.alloc(4)
body.writeUInt32LE(4, 0)
const triangles = [
  [[0, 0, 0], [10, 0, 0], [0, 10, 0]],
  [[0, 0, 0], [10, 0, 0], [0, 0, 10]],
  [[10, 0, 0], [0, 10, 0], [0, 0, 10]],
  [[0, 10, 0], [0, 0, 0], [0, 0, 10]],
]
const chunks = [header, body]
for (const triangle of triangles) {
  const record = Buffer.alloc(50)
  triangle.forEach((point, index) => {
    point.forEach((value, axis) => record.writeFloatLE(value, 12 + index * 12 + axis * 4))
  })
  chunks.push(record)
}
writeFileSync(stl, Buffer.concat(chunks))
const reviewed = await invoke('cadw_review', { workspace, paths: [stl] })
check('review.ok', reviewed.ok === true, reviewed.error?.message)
check('review.verdict', reviewed.verdict === 'PASS', JSON.stringify(reviewed.files?.map((file) => file.verdict)))

console.log('\n结果：失败 ' + problems.length)
for (const item of problems) console.log('  - ' + item)
process.exit(problems.length ? 1 : 0)
