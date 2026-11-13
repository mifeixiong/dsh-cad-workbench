/**
 * 插件契约自测（离线，不需要 SolidWorks/Python）。
 *
 * 覆盖：9 个工具注册、参数与输出 schema、技能 provider、无损 JSON、
 * 期望—实测比对、路径守卫、Feature Graph 轻量校验、台账不可变修订、
 * 图片附件降级。
 *
 * 用法：node --test test/selftest.mjs
 */
import assert from 'node:assert/strict'
import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

process.env.DSH_CAD_SELFTEST = '1'
const root = new URL('..', import.meta.url)
const { apply } = await import(new URL('index.js', root))
const { toLossless } = await import(new URL('lib/tooling.js', root))
const { compareExpect, axisTolerance } = await import(new URL('lib/plan.js', root))
const { normalizeGraph, graphHash } = await import(new URL('lib/graph.js', root))
const { guardOutputPath } = await import(new URL('lib/session.js', root))
const { startJob, recordEvent, recordArtifacts, recordVerification, finishJob, getJob, listRevisions } =
  await import(new URL('lib/ledger.js', root))
const { asFail, fail, fromScript } = await import(new URL('lib/tooling.js', root))
const { contentFrom } = await import(new URL('lib/images.js', root))

const EXPECTED_TOOLS = [
  'cadw_doctor', 'cadw_session', 'cadw_model', 'cadw_measure', 'cadw_capture',
  'cadw_export', 'cadw_drawing', 'cadw_review', 'cadw_assembly', 'cadw_job',
]

function mockContext() {
  const definitions = new Map()
  let providerFactory
  const ctx = {
    tools: {
      register(definition) {
        definitions.set(definition.name, definition)
        return () => definitions.delete(definition.name)
      },
    },
    get(name) {
      if (name === 'skills') {
        return { registerProvider(factory) { providerFactory = factory } }
      }
      return undefined
    },
    effect(fn) { return fn() },
    logger: { warn() {} },
  }
  return { ctx, definitions, provider: () => providerFactory?.() }
}

async function withWorkspace() {
  return mkdtemp(join(tmpdir(), 'cadw-test-'))
}

test('registers nine cadw_* tools with parameters and output schema', () => {
  const { ctx, definitions } = mockContext()
  apply(ctx)
  assert.deepEqual([...definitions.keys()].sort(), [...EXPECTED_TOOLS].sort())
  for (const [name, definition] of definitions) {
    assert.equal(typeof definition.description, 'string')
    assert.ok(definition.description.length > 20, name + ' 描述太短')
    assert.ok(definition.parameters && typeof definition.parameters === 'object', name + ' 缺 parameters')
    assert.ok(definition.output?.schema, name + ' 缺 output.schema')
    assert.equal(typeof definition.output.render, 'function', name + ' 缺 render')
    assert.equal(typeof definition.execute, 'function', name + ' 缺 execute')
  }
})

test('registers bundled skills through the skills provider', async () => {
  const { ctx, provider } = mockContext()
  apply(ctx)
  const skills = provider()
  assert.ok(skills, '没有注册技能 provider')
  const list = await skills.list()
  assert.deepEqual(list.map((item) => item.name).sort(), [
    'cad-drawing-gb', 'cad-feature-graph', 'cad-modeling-workflow', 'solidworks-com',
  ])
  for (const item of list) {
    const resolved = await skills.get({ name: item.name, provider: item.provider })
    assert.ok(resolved.content.length > 200, item.name + ' 内容太短')
  }
})

test('toLossless removes undefined, NaN, -0 and cycles', () => {
  const value = { a: 1, b: undefined, c: Number.NaN, d: -0, e: [1, undefined, Number.POSITIVE_INFINITY] }
  value.self = value
  const clean = toLossless(value)
  assert.deepEqual(Object.keys(clean).sort(), ['a', 'c', 'd', 'e', 'self'])
  assert.equal(clean.b, undefined)
  assert.equal(clean.c, null)
  assert.ok(Object.is(clean.d, 0))
  assert.deepEqual(clean.e, [1, null, null])
  assert.equal(clean.self, null)
})

test('failure envelope keeps code/message/details', () => {
  const envelope = fail('DOC_BUSY', '忙', { pid: 1 })
  assert.equal(envelope.ok, false)
  assert.equal(envelope.error.code, 'DOC_BUSY')
  assert.deepEqual(envelope.error.details, { pid: 1 })
  const fromError = asFail(new Error('炸了'), 'BUILD_FAILED')
  assert.equal(fromError.error.code, 'BUILD_FAILED')
  assert.equal(fromScript({ ok: true, x: 1 }, { y: 2 }).y, 2)
  assert.equal(fromScript({ ok: false, error: { code: 'X' } }).error.code, 'X')
})

test('compareExpect honours max(1 mm, 2%) per axis', () => {
  assert.equal(axisTolerance(10), 1)
  assert.equal(axisTolerance(1000), 20)
  const verified = compareExpect({ bbox_mm: [120, 80, 10] }, { bbox_mm: [120.4, 79.6, 10.2], feature_count: 3 })
  assert.equal(verified.status, 'verified')
  const mismatch = compareExpect({ bbox_mm: [120, 80, 10] }, { bbox_mm: [130, 80, 10] })
  assert.equal(mismatch.status, 'mismatch')
  assert.ok(mismatch.failures[0].includes('X'))
  assert.equal(compareExpect({}, { bbox_mm: [1, 2, 3] }).status, 'measured-only')
  const volume = compareExpect({ volume_mm3: 1000 }, { volume_mm3: 1050 })
  assert.equal(volume.status, 'mismatch')
  const volumeOk = compareExpect({ volume_mm3: 1000 }, { volume_mm3: 1010 })
  assert.equal(volumeOk.status, 'verified')
})

test('normalizeGraph rejects bad shape and unknown ops', () => {
  assert.throws(() => normalizeGraph({ units: 'in', nodes: [] }), /mm/)
  assert.throws(() => normalizeGraph({ units: 'mm', nodes: [] }), /非空/)
  assert.throws(() => normalizeGraph({ units: 'mm', nodes: [{ op: 'shell', thickness: 2 }] }), /不支持/)
  const graph = normalizeGraph({ nodes: [{ op: 'sketch', plane: 'front', entities: [{ type: 'rect', width: 1, height: 1 }] }] })
  assert.equal(graph.units, 'mm')
  assert.equal(graphHash(graph), graphHash(graph))
  assert.notEqual(graphHash(graph), graphHash({ nodes: [{ op: 'sketch' }] }))
})

test('guardOutputPath confines writes to the workspace', async () => {
  const workspace = await withWorkspace()
  const inside = guardOutputPath(workspace, 'cad-output/a.step')
  assert.ok(inside.startsWith(workspace))
  const { mkdir } = await import('node:fs/promises')
  await mkdir(join(workspace, 'cad-output'), { recursive: true })
  assert.throws(() => guardOutputPath(workspace, 'C:\\Windows\\x.step'), /PATH_DENIED|系统/)
  assert.throws(() => guardOutputPath(workspace, join(tmpdir(), 'outside.step')), /工作区/)
  await writeFile(inside, 'x')
  assert.throws(() => guardOutputPath(workspace, 'cad-output/a.step'), /已存在/)
  assert.ok(guardOutputPath(workspace, 'cad-output/a.step', { overwrite: true }))
})

test('ledger records immutable revisions and delivery verdicts', async () => {
  const workspace = await withWorkspace()
  const job = await startJob(workspace, { name: 'plate', plan: { expect: { bbox_mm: [10, 10, 1] } } })
  assert.match(job.job_id, /^(job|cad)-/)
  await recordEvent(workspace, job.job_id, { tool: 'cadw_model', ok: true })
  const artifactPath = join(workspace, 'part.step')
  await writeFile(artifactPath, 'ISO-10303-21;')
  await recordArtifacts(workspace, job.job_id, [{ format: 'step', path: artifactPath, bytes: 13 }])
  await recordVerification(workspace, job.job_id, { status: 'verified', checks: [], failures: [] })
  const finished = await finishJob(workspace, job.job_id)
  assert.equal(finished.delivery.status, 'delivered')
  assert.equal(finished.delivery.trust, 'verified')
  const revisions = listRevisions(workspace, job.job_id)
  assert.ok(revisions.length >= 1)
  const reread = await getJob(workspace, job.job_id)
  assert.equal(reread.artifacts.length, 1)
})

test('job tool runs without Python', async () => {
  const { ctx, definitions } = mockContext()
  apply(ctx)
  const workspace = await withWorkspace()
  const start = await definitions.get('cadw_job').execute(
    { action: 'start', name: 't', plan: {}, workspace }, { agent: null },
  )
  assert.equal(start.ok, true)
  const get = await definitions.get('cadw_job').execute(
    { action: 'get', job_id: start.job.job_id, workspace }, { agent: null },
  )
  assert.equal(get.ok, true)
  const list = await definitions.get('cadw_job').execute({ action: 'list', workspace }, { agent: null })
  assert.equal(list.jobs.length, 1)
})

test('contentFrom assembles text and image blocks', () => {
  const blocks = contentFrom({ imageNote: 'n1', image: { attachmentId: 'a' }, extraImage: { attachmentId: 'b' } }, 'hello')
  assert.equal(blocks[0].type, 'text')
  assert.equal(blocks[1].text, '（n1）')
  assert.equal(blocks[2].type, 'image')
  assert.equal(blocks[3].type, 'image')
  assert.equal(contentFrom({}, 'x').length, 1)
})
