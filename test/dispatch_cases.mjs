/**
 * lib/dispatch.js 的契约测试（离线，不需要 SolidWorks/Python）。
 *
 * 用法：node --test test/dispatch_cases.mjs
 */
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

const root = new URL('..', import.meta.url)
const { buildWorkOrders, cellOf, columnLabel, columnIndex, compareCells, groupFindings, renderOrder, policyText, MODEL_POLICY_RULES } =
  await import(new URL('lib/dispatch.js', root))

const ZONES = [
  { code: 'IV', label: '烟囱/主桅', u0: 38, u1: 50, v0: 12, v1: 28, owner: 'super-stack',
    module: 'superstructure.py', write_scope: ['superstructure.py'] },
  { code: 'V', label: '驾驶室', u0: 50, u1: 66, v0: 12, v1: 26, owner: 'super-house',
    module: 'superstructure.py', write_scope: ['superstructure.house.py'] },
  { code: 'VI', label: '首楼（本用例无发现）', u0: 66, u1: 80, v0: 12, v1: 20, owner: 'fore-deck',
    module: 'hull.py', write_scope: ['hull.py'] },
]

const FINDINGS = [
  { id: 'A2-001', angle: 'float', severity: 'P0', subject: 'funnel casing', cell: 'I4',
    expect: '底面贴 13.75', actual: 'z0 = 14.15', delta: 0.4, hint: '把烟囱底面落到甲板' },
  { id: 'A7-001', angle: 'shape', severity: 'P1', subject: 'bridge · dx', cell: 'L5',
    expect: '参考带 12.0~12.9', actual: '实测 15.0', delta: 2.1, hint: '收驾驶室纵向范围' },
]

function brief(extra = {}) {
  return {
    project_root: 'C:/work/project',
    must_read: ['README.md', 'spec.py'],
    frames: [{ id: 'frame_05', path: 'C:/work/frame_05.png', view: 'side' }],
    coordinate_note: 'x=0 艉封板→89.95 艏尖；y 右舷正；z 基线 0；单位 m',
    build_cmd: 'pwsh -NoProfile -File build.ps1 -Parts {module} -Out {prefix}',
    verify_cmds: ['python _profile_check.py out/{prefix}.json'],
    task_board: true,
    ...extra,
  }
}

async function workspace() {
  return mkdtemp(join(tmpdir(), 'cadw-dispatch-'))
}

test('buildWorkOrders turns findings into per-zone work orders', async () => {
  const work = await workspace()
  const result = buildWorkOrders({
    workspace: work, project_root: 'C:/work/project', project_id: 'jidi',
    zones: ZONES, audit: { findings: FINDINGS }, brief: brief(),
    recursion: { allow: true, split_threshold: 6 },
  })
  assert.equal(result.ok, true)
  assert.equal(result.count, 3)
  const stack = result.orders.find((order) => order.owner === 'super-stack')
  const house = result.orders.find((order) => order.owner === 'super-house')
  const fore = result.orders.find((order) => order.owner === 'fore-deck')
  assert.deepEqual(stack.findingCounts, { P0: 1, P1: 0 })
  assert.equal(stack.findings[0].severity, 'P0', 'P0 必须排在最前')
  assert.equal(house.findingCounts.P1, 1, 'L5 落在 V 区')
  assert.equal(fore.findings.length, 0)
  assert.match(fore.markdown, /无未通过项/)
  for (const heading of ['## 1 身份与任务板', '## 2 先看差异', '## 3 环境与写作用域', '## 4 必读',
    '## 5 坐标与单位', '## 6 任务', '## 7 自检与达标线', '## 8 子区拆分与递归', '## 9 模型选择与回报']) {
    assert.ok(stack.markdown.includes(heading), '工单缺少段落 ' + heading)
  }
  assert.match(stack.markdown, /superstructure\.py/)
  assert.match(stack.markdown, /build\.ps1 -Parts superstructure\.py -Out super-stack-v/)
  assert.match(stack.markdown, /team_task_get/)
  assert.ok(result.policy.includes('list_subagent_models'))
})

test('cellOf maps metres to column/row codes', () => {
  assert.equal(cellOf({ u: 52.3, v: 23.1 }, { u_step: 5, v_step: 5 }), 'K5')
  assert.equal(cellOf({ u: 0, v: 0 }, {}), 'A1')
  assert.equal(cellOf({ u: 50, v: 20 }, { u_step: 5, v_step: 5 }), 'K5')
  assert.equal(cellOf({ x: 89.99, z: 35.5 }, { u_step: 5, v_step: 5 }), 'R8')
  assert.equal(typeof cellOf({ u: -3, v: -2 }, { u_step: 5, v_step: 5 }), 'string')
})

test('groupFindings keeps out-of-zone findings in unassigned and warns', async () => {
  const work = await workspace()
  const outside = { id: 'A9-001', angle: 'naming', severity: 'P2', subject: 'far away', cell: 'R8' }
  const grouped = groupFindings([...FINDINGS, outside], ZONES, { u_step: 5, v_step: 5 })
  assert.equal(grouped.unassigned.length, 1)
  assert.equal(grouped.unassigned[0].id, 'A9-001')
  const result = buildWorkOrders({
    workspace: work, zones: ZONES, audit: { findings: [...FINDINGS, outside] }, brief: brief(),
  })
  assert.ok(result.warnings.some((line) => line.includes('未分配')), result.warnings)
})

test('findings over split_threshold produce child zones without write scope', async () => {
  const work = await workspace()
  const many = []
  for (let index = 0; index < 8; index += 1) {
    many.push({ id: 'A7-' + index, angle: 'shape', severity: 'P1', subject: 'bulk ' + index,
      cell: 'I4', expect: 'x', actual: 'y', delta: 1.2, hint: 'h' })
  }
  const result = buildWorkOrders({
    workspace: work, zones: [ZONES[0]], audit: { findings: many }, brief: brief(),
    recursion: { allow: true, split_threshold: 6, max_children: 2, max_depth: 3 },
  })
  const order = result.orders[0]
  assert.equal(order.children.length, 2)
  assert.equal(order.maxDepth, 3)
  for (const child of order.children) {
    assert.deepEqual(child.write_scope, [], '子区不得与本级争同一文件')
    assert.ok(child.u0 >= 38 && child.u1 <= 50, '子区必须在母区内')
  }
  assert.match(order.markdown, /必须\*\*再拆子区/)
})

test('dispatch pulls FAIL rows out of a diff result', async () => {
  const work = await workspace()
  const diff = {
    rows: [
      { cell: 'I4', name: 'funnel_len', part: 'funnel casing', field: 'dx', ref: [8.5, 9.0], model: 10.4, delta: 1.4, verdict: 'FAIL' },
      { cell: 'I4', name: 'ok', part: 'funnel casing', field: 'dy', ref: [1, 2], model: 1.5, delta: 0, verdict: 'PASS' },
      { cell: 'L5', name: 'missing', part: 'bridge', field: 'dx', ref: [12, 13], model: null, verdict: 'MISSING' },
    ],
    profile_rows: [{ cell: 'K4', ref: { vmax: 22 }, model: { vmax: 24 }, delta_vmax: 2, verdict: 'FAIL' }],
  }
  const result = buildWorkOrders({ workspace: work, zones: ZONES, diff, brief: brief() })
  const stack = result.orders.find((order) => order.owner === 'super-stack')
  const house = result.orders.find((order) => order.owner === 'super-house')
  assert.equal(stack.findingCounts.P1, 1, 'FAIL 行（I4）')
  assert.equal(house.findingCounts.P0, 1, 'MISSING 视为 P0（L5）')
  assert.equal(house.findingCounts.P1, 1, '轮廓行（K4）')
})

test('policy states the five hard rules and children inherit them', async () => {
  const work = await workspace()
  const result = buildWorkOrders({ workspace: work, zones: ZONES, audit: { findings: FINDINGS }, brief: brief() })
  const text = result.policy
  for (const token of ['list_subagent_models', '继承', '回退', '深度上限', '子代理']) {
    assert.ok(text.includes(token), 'policy 缺关键词 ' + token)
  }
  assert.equal(MODEL_POLICY_RULES.length, 5)
  for (const rule of MODEL_POLICY_RULES) assert.ok(result.orders[0].markdown.includes(rule))
})

test('same input yields identical orders apart from batch metadata', async () => {
  const work = await workspace()
  const first = buildWorkOrders({ workspace: work, zones: ZONES, audit: { findings: FINDINGS }, brief: brief() })
  const second = buildWorkOrders({ workspace: work, zones: ZONES, audit: { findings: FINDINGS }, brief: brief() })
  assert.deepEqual(
    first.orders.map((order) => ({ owner: order.owner, markdown: order.markdown, cells: order.cells, counts: order.findingCounts })),
    second.orders.map((order) => ({ owner: order.owner, markdown: order.markdown, cells: order.cells, counts: order.findingCounts })),
  )
})

test('each order is written to disk with the same markdown', async () => {
  const work = await workspace()
  const result = buildWorkOrders({ workspace: work, zones: ZONES, audit: { findings: FINDINGS }, brief: brief() })
  for (const order of result.orders) {
    const text = await readFile(order.path, 'utf8')
    assert.equal(text, order.markdown)
    assert.ok(order.prompt.length > 100)
  }
  assert.ok(result.out_dir.includes('.cad-workbench'))
})

test('missing zones returns a structured failure', () => {
  const result = buildWorkOrders({ zones: [] })
  assert.equal(result.ok, false)
  assert.equal(result.error.code, 'BAD_ARGS')
})

test('renderOrder keeps nine sections even for an empty zone', () => {
  const markdown = renderOrder({
    owner: 'solo', zone: { code: 'I', label: '艉部' }, findings: [], cells: [], tasks: [],
    writeScope: [], readOnly: [], mustRead: [], frames: [], coordinates: '', coordinateNote: '',
    buildCommand: '', verifyCommands: [], children: [], maxDepth: 2, splitThreshold: 6, taskBoard: false,
  })
  assert.ok(markdown.includes('## 9 模型选择与回报'))
  assert.ok(markdown.includes('本区无未通过项'))
  assert.ok(markdown.includes('唯一写作用域'))
})
test('column labels go past Z and round-trip', () => {
  assert.equal(columnLabel(0), 'A')
  assert.equal(columnLabel(25), 'Z')
  assert.equal(columnLabel(26), 'AA')
  assert.equal(columnLabel(27), 'AB')
  assert.equal(cellOf({ u: 130, v: 0 }, { u_step: 5, v_step: 5 }), 'AA1')
  for (const index of [0, 5, 25, 26, 51, 52, 700]) {
    assert.equal(columnIndex(columnLabel(index)), index)
  }
  assert.ok(cellOf({ u: -5, v: -2 }, { u_step: 5, v: 5 }).startsWith('NEG'))
})

test('groupFindings accepts explicit u/v points and multi-letter cells', () => {
  const zones = [{ code: 'A', u0: 0, u1: 10, v0: 0, v1: 10 }]
  const direct = groupFindings([{ u: 5, v: 5, subject: 'direct' }], zones, { u_step: 5, v_step: 5 })
  assert.equal(direct.buckets.get('A').length, 1)
  // AA 列 = columnIndex 26 → u 130~135；行 4 → v 15~20
  const cellZones = [{ code: 'X', u0: 130, u1: 135, v0: 15, v1: 20 }]
  const byCell = groupFindings([{ cell: 'AA4', severity: 'P0' }], cellZones, { u_step: 5, v_step: 5 })
  assert.equal(byCell.buckets.get('X').length, 1)
})

test('overlapping zones are reported as conflicts instead of silently swallowed', () => {
  const zones = [
    { code: 'A', u0: 0, u1: 10, v0: 0, v1: 10 },
    { code: 'B', u0: 10, u1: 20, v0: 0, v1: 10 },
  ]
  const grouped = groupFindings([{ subject: 'b', evidence: { bbox: [8, 0, 4, 12, 0, 6] } }], zones, { u_step: 5, v_step: 5 })
  assert.equal(grouped.conflicts.length, 1)
  assert.deepEqual(grouped.conflicts[0].zones, ['A', 'B'])
})

test('reversed zone bounds are normalised', () => {
  const zones = [{ code: 'IV', u0: 50, u1: 38, v0: 28, v1: 12 }]
  const grouped = groupFindings([{ cell: 'I4', severity: 'P0' }], zones, { u_step: 5, v_step: 5 })
  assert.equal(grouped.buckets.get('IV').length, 1)
})

test('cells sort by column then row, not lexicographically', () => {
  assert.deepEqual(['I10', 'I9', 'J1'].sort(compareCells), ['I9', 'I10', 'J1'])
  assert.ok(compareCells('I10', 'I9') > 0)
})

test('batch name and prompt shape follow the contract', async () => {
  const work = await workspace()
  const result = buildWorkOrders({ workspace: work, zones: ZONES, audit: { findings: FINDINGS }, brief: brief() })
  assert.match(result.batch, /^dispatch-\d{8}-\d{6}$/)
  for (const order of result.orders) {
    assert.ok(!/^# /.test(order.prompt), 'prompt 不应带一级标题')
    assert.ok(order.prompt.includes('## 1 身份与任务板'))
    assert.match(order.path, /\.md$/)
  }
})

test('missing diff and audit is called out instead of pretending all clean', async () => {
  const work = await workspace()
  const result = buildWorkOrders({ workspace: work, zones: [ZONES[2]], brief: brief() })
  assert.ok(result.warnings.some((line) => line.includes('未提供差分')), result.warnings)
  assert.match(result.orders[0].markdown, /未提供差分\/审查结果/)
})
test('model policy carries project-level exclusions into every order', async () => {
  const work = await workspace()
  const result = buildWorkOrders({
    workspace: work, zones: ZONES, audit: { findings: FINDINGS }, brief: brief(),
    model_policy: { exclude: ['deepseek-account/deepseek-v4-pro'], preferred: ['deepseek-account/deepseek-flash'], note: '用户要求' },
  })
  assert.ok(result.policy.includes('deepseek-account/deepseek-v4-pro'), result.policy)
  assert.ok(result.policy.includes('禁用清单'), result.policy)
  assert.ok(result.policy.includes('优先清单'), result.policy)
  for (const order of result.orders) {
    assert.ok(order.markdown.includes('不得派给'), '每张工单都要带禁用清单')
    assert.ok(order.markdown.includes('deepseek-flash'))
  }
  assert.deepEqual(policyText({}).length, MODEL_POLICY_RULES.length)
  assert.ok(result.warnings.every((line) => typeof line === 'string'))
})
