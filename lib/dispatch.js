/**
 * 派工单生成：把"差分表 + 对抗审查"的失败项，按责任区（zone）变成可执行的子智能体工单。
 *
 * 为什么单独一层：工单质量决定多智能体协作的成败。真实事故里 4 个子智能体因为
 * 指定了不可用的 provider/model 全部 "failed before it finished"，白等一轮；
 * 也有两个 agent 同时改一个文件互相覆盖。所以这里把两件事写成**硬规则文本**，
 * 每张工单都必须原样带上：
 *   1) 派工前先 list_subagent_models，只用返回列表里的组合；
 *   2) 未显式指定就继承父会话默认模型；
 *   3) 失败回退：继承默认 → 同 provider 另一模型 → 拆小任务重派；
 *   4) 递归深度上限（默认 2：lead → 区代理 → 子区代理），到底自己干；
 *   5) 子代理原样继承这五条。
 *
 * @module dsh-cad-workbench/lib/dispatch
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { displayPath, sanitizeBasename, stamp } from './session.js'

const COL_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
const MAX_PROMPT = 12000
const FENCE = String.fromCharCode(96, 96, 96)

export const MODEL_POLICY_RULES = [
  '派工前必须调 list_subagent_models，只使用它返回列表里的 provider/model 组合；列表里没有的，不要凭空指定。',
  '没有显式指定模型时，继承父会话的默认模型（历史事故：指定了不可用组合，4 个子智能体全部 "failed before it finished"，用户回"注意使用有效的子智能体，比如 ds 官方的"）。',
  '子代理失败时按顺序回退：继承默认 → 列表内同 provider 的另一模型 → 把任务拆小再派；连续两次失败就把结论交回上一级，不要空转。',
  '递归深度上限默认 2（lead → 区代理 → 子区代理）；到上限后自己干，不再下派。',
  '子代理也继承这五条，并在它的工单里原样转述，不许省略。',
]

function round(value, digits = 3) {
  const number = Number(value)
  if (!Number.isFinite(number)) return null
  const factor = 10 ** digits
  return Math.round(number * factor) / factor
}

/** 列序号 → 列名（双射 26 进制：A…Z, AA, AB…；负列用 NEG 前缀，避免与 N 列混淆）。 */
export function columnLabel(index) {
  if (index < 0) return 'NEG' + String(index)
  let value = index + 1
  let label = ''
  while (value > 0) {
    const remainder = (value - 1) % 26
    label = String.fromCharCode(65 + remainder) + label
    value = Math.floor((value - 1) / 26)
  }
  return label
}

/** 列名 → 列序号（columnLabel 的逆）。 */
export function columnIndex(label) {
  const text = String(label || '')
  if (text.startsWith('NEG')) return Number(text.slice(3))
  let value = 0
  for (const character of text) {
    const code = character.charCodeAt(0) - 64
    if (code < 1 || code > 26) return null
    value = value * 26 + code
  }
  return value - 1
}

/** (u,v) 米 → 格号：列 A 起每 u_step、行 1 起每 v_step。 */
export function cellOf(point, grid = {}) {
  const uStep = Number(grid.u_step) || 5
  const vStep = Number(grid.v_step) || 5
  const uOrigin = Number(grid.u_origin) || 0
  const vOrigin = Number(grid.v_origin) || 0
  const u = Number(point?.u ?? point?.x ?? 0)
  const v = Number(point?.v ?? point?.z ?? 0)
  const column = Math.floor((u - uOrigin) / uStep)
  const row = Math.floor((v - vOrigin) / vStep)
  return columnLabel(column) + String(row + 1)
}

function pointOf(finding, grid) {
  // 显式坐标优先（findings 可能来自自定义来源，不一定有 bbox）
  const directU = finding?.u ?? finding?.x
  const directV = finding?.v ?? finding?.z
  if (Number.isFinite(Number(directU)) && Number.isFinite(Number(directV))) {
    return { u: Number(directU), v: Number(directV) }
  }
  const box = finding?.evidence?.bbox
  if (Array.isArray(box) && box.length === 6) {
    return { u: (Number(box[0]) + Number(box[3])) / 2, v: (Number(box[2]) + Number(box[5])) / 2 }
  }
  const cell = String(finding?.cell ?? '').trim()
  const match = /^(NEG-?\d+|[A-Z]+)(-?\d+)$/.exec(cell)
  if (!match) return null
  const column = columnIndex(match[1])
  if (column === null) return null
  const uStep = Number(grid?.u_step) || 5
  const vStep = Number(grid?.v_step) || 5
  return { u: (column + 0.5) * uStep, v: (Number(match[2]) - 1 + 0.5) * vStep }
}

function zoneBounds(zone) {
  // 责任区坐标写反（u0>u1）时自动归一，否则该区永远接不到活（v0.4 核对发现）
  const u0 = Math.min(Number(zone.u0), Number(zone.u1))
  const u1 = Math.max(Number(zone.u0), Number(zone.u1))
  const v0 = Math.min(Number(zone.v0), Number(zone.v1))
  const v1 = Math.max(Number(zone.v0), Number(zone.v1))
  return { u0, u1, v0, v1 }
}

function zoneContains(zone, point) {
  if (!point) return false
  const bounds = zoneBounds(zone)
  return point.u >= bounds.u0 && point.u <= bounds.u1 && point.v >= bounds.v0 && point.v <= bounds.v1
}

/**
 * 把发现按 zone 归组；落在所有区之外的进 unassigned。
 * 同时落在多个区（责任区重叠）的发现归第一个区并记进 conflicts ——
 * 这是"分区定义有问题"的信号，派工时给一条 warning，而不是静默吞掉。
 */
export function groupFindings(findings, zones = [], grid = {}) {
  const buckets = new Map()
  for (const zone of zones) buckets.set(String(zone.code), [])
  const unassigned = []
  const conflicts = []
  for (const finding of findings || []) {
    const point = pointOf(finding, grid)
    const hits = zones.filter((item) => zoneContains(item, point))
    if (!hits.length) {
      unassigned.push(finding)
      continue
    }
    buckets.get(String(hits[0].code)).push(finding)
    if (hits.length > 1) {
      conflicts.push({ finding: finding.id || finding.subject || '?', zones: hits.map((item) => String(item.code)) })
    }
  }
  return { buckets, unassigned, conflicts }
}

/** 格号排序：先比列序号再比行号（字符串排序会把 I10 排到 I9 前面）。 */
export function compareCells(left, right) {
  const parse = (cell) => {
    const match = /^(NEG-?\d+|[A-Z]+)(-?\d+)$/.exec(String(cell || ''))
    if (!match) return [Number.MAX_SAFE_INTEGER, 0]
    return [columnIndex(match[1]) ?? Number.MAX_SAFE_INTEGER, Number(match[2])]
  }
  const a = parse(left)
  const b = parse(right)
  return a[0] - b[0] || a[1] - b[1]
}

function timeStamp() {
  const now = new Date()
  const pad = (value) => String(value).padStart(2, '0')
  return String(now.getFullYear()) + pad(now.getMonth() + 1) + pad(now.getDate()) +
    '-' + pad(now.getHours()) + pad(now.getMinutes()) + pad(now.getSeconds())
}

function severityRank(finding) {
  return finding?.severity === 'P0' ? 0 : finding?.severity === 'P1' ? 1 : 2
}

function findingLine(finding) {
  const delta = finding.delta === undefined || finding.delta === null
    ? '-'
    : (Number(finding.delta) >= 0 ? '+' : '') + round(finding.delta)
  return '| ' + (finding.cell || '-') + ' | ' + (finding.angle || finding.type || '-') + ' | ' +
    String(finding.subject || finding.name || '-').replace(/\|/g, '/') + ' | ' +
    String(finding.expect || finding.ref || '-') + ' | ' +
    String(finding.actual || finding.model || '-') + ' | ' + delta + ' | ' +
    (finding.hint || '-').replace(/\|/g, '/') + ' |'
}

/**
 * 模型选择策略文本：五条硬规则 + 项目级禁用/优先清单。
 * 禁用清单来自真实运维约束（用户明确说过"不要再用某个模型"），
 * 所以它必须写进每张工单，而不是只留在 lead 的脑子里。
 */
export function policyText(modelPolicy = {}) {
  const lines = MODEL_POLICY_RULES.slice()
  const exclude = Array.isArray(modelPolicy.exclude) ? modelPolicy.exclude.filter(Boolean).map(String) : []
  const preferred = Array.isArray(modelPolicy.preferred) ? modelPolicy.preferred.filter(Boolean).map(String) : []
  if (exclude.length) {
    lines.push('禁用清单（本项目/本用户明确要求，不得派给）：' + exclude.join('、') +
      '；若 list_subagent_models 返回的列表里只剩禁用项，直接回报 lead，不要硬派。')
  }
  if (preferred.length) {
    lines.push('优先清单：' + preferred.join('、') + '（仍需先 list_subagent_models 确认可用）。')
  }
  if (modelPolicy.note) lines.push('补充说明：' + String(modelPolicy.note))
  return lines
}

function briefOf(input) {
  const brief = input.brief || {}
  return {
    mustRead: Array.isArray(brief.must_read) ? brief.must_read : [],
    frames: Array.isArray(brief.frames) ? brief.frames : [],
    coordinateNote: brief.coordinate_note || '',
    conventions: Array.isArray(brief.conventions) ? brief.conventions : [],
    buildCmd: brief.build_cmd || '',
    verifyCmds: Array.isArray(brief.verify_cmds) ? brief.verify_cmds : [],
    taskBoard: brief.task_board === true,
    readOnly: Array.isArray(brief.read_only) ? brief.read_only : [],
  }
}

function childrenHint(zone, findings, recursion) {
  if (!recursion.allow || findings.length <= recursion.splitThreshold) return []
  const children = []
  const u0 = Number(zone.u0)
  const u1 = Number(zone.u1)
  const span = (u1 - u0) / 2
  for (let index = 0; index < 2 && children.length < recursion.maxChildren; index += 1) {
    const low = u0 + span * index
    const high = index === 1 ? u1 : low + span
    children.push({
      code: String(zone.code) + '.' + (index + 1),
      label: String(zone.label || '') + ' 子区 ' + (index + 1),
      u0: round(low), u1: round(high), v0: round(zone.v0), v1: round(zone.v1),
      write_scope: [],
      hint: '只读分析该子区的差异并出补丁建议，写操作交本级执行（同一模块文件不允许并发写）',
    })
  }
  return children
}

export function renderOrder(order) {
  const lines = []
  const zone = order.zone || {}
  lines.push('# ' + order.owner + ' · ' + (zone.code || '') + ' ' + (zone.label || ''))
  lines.push('')
  lines.push('## 1 身份与任务板')
  lines.push('')
  lines.push('- 角色：' + order.owner + '（负责' + (zone.label || '本区') + '）')
  if (order.taskBoard) {
    lines.push('- 任务板：TASK_ID（占位）。先 team_task_get TASK_ID 再 claim，做完 complete。')
  } else {
    lines.push('- 本工单不需要任务板：完成后 send_message 给 lead 即可。')
  }
  lines.push('')
  lines.push('## 2 先看差异（P0 在前）')
  lines.push('')
  if (order.findings.length) {
    lines.push('| 格号 | 角度/类型 | 对象 | 参考/期望 | 实测 | Δ | 改法 |')
    lines.push('|---|---|---|---|---|---|---|')
    for (const finding of order.findings) lines.push(findingLine(finding))
  } else if (order.hasSource === false) {
    lines.push('未提供差分/审查结果：先自己读参考帧与网格图，沿格号抽查 3 处并给出结论。')
  } else {
    lines.push('本区无未通过项：做增量核对（沿格号抽查 3 处，确认没有新偏差），然后回报"无改动"。')
  }
  lines.push('')
  lines.push('## 3 环境与写作用域')
  lines.push('')
  lines.push('- 项目根：' + order.projectRoot)
  lines.push('- **唯一写作用域**：' + (order.writeScope.length ? order.writeScope.join('、') : '（只读：只出结论与补丁建议）'))
  lines.push('- 只读：' + (order.readOnly.length ? order.readOnly.join('、') : '其余全部文件'))
  lines.push('- 禁止 git commit；禁止跨写作用域改别的模块；禁改真相源（参数表/常量文件）。')
  lines.push('')
  lines.push('## 4 必读')
  lines.push('')
  for (const item of order.mustRead) lines.push('- ' + item)
  for (const frame of order.frames) {
    lines.push('- 参考帧 ' + (frame.id || '') + (frame.view ? '（' + frame.view + '）' : '') + '：' + (frame.path || ''))
  }
  if (!order.mustRead.length && !order.frames.length) lines.push('- （未提供必读清单：先读 README 与参数表）')
  lines.push('')
  lines.push('## 5 坐标与单位')
  lines.push('')
  lines.push(order.coordinateNote || '按项目坐标约定；单位与真相源一致。')
  lines.push('')
  lines.push('## 6 任务（按格号排序）')
  lines.push('')
  if (order.cells.length) lines.push('- 本区格号：' + order.cells.join('、'))
  for (const item of order.tasks) lines.push('- ' + item)
  if (!order.tasks.length) lines.push('- 无待改进项；按第 2 段抽查后回报。')
  lines.push('')
  lines.push('## 7 自检与达标线')
  lines.push('')
  if (order.buildCommand) {
    lines.push(FENCE + 'powershell')
    lines.push(order.buildCommand)
    lines.push(FENCE)
  }
  for (const command of order.verifyCommands) lines.push('- 复测：' + command)
  lines.push('- 达标线：本区 P0 = 0；本区 FAIL 归零；重跑复测后把数字贴回来。')
  lines.push('')
  lines.push('## 8 子区拆分与递归')
  lines.push('')
  if (order.children.length) {
    lines.push('本区可判定项 ' + order.findings.length + ' 条，超过阈值 ' + order.splitThreshold + '：**必须**再拆子区派给下级。')
    for (const child of order.children) {
      lines.push('- 子区 ' + child.code + '（u ' + child.u0 + '~' + child.u1 + '）写作用域：' +
        (child.write_scope.length ? child.write_scope.join('、') : '只读，补丁交本级') + '——' + child.hint)
    }
  } else {
    lines.push('本区规模不需要再拆；自己干完。')
  }
  lines.push('- 递归深度上限 ' + order.maxDepth + '（lead → 区代理 → 子区代理）；到上限后自己干。')
  lines.push('- 下级同样具备本工单能力：可再拆子区、可先 list_subagent_models 选模型、同样受写作用域约束。')
  lines.push('')
  lines.push('## 9 模型选择与回报')
  lines.push('')
  for (const rule of order.policyLines || MODEL_POLICY_RULES) lines.push('- ' + rule)
  lines.push('')
  lines.push('回报格式（表格）：')
  lines.push('')
  lines.push('| 格号 | 零件 | 参考 | 实测 | Δ | 处理 |')
  lines.push('|---|---|---|---|---|---|')
  lines.push('')
  lines.push('外加两项：「仍需 lead 裁决」清单（带你的建议值与依据）、改动文件清单。收尾用 send_message 发给 lead。')
  return lines.join('\n')
}

/**
 * 生成分区工单。
 * @param {object} input 见 docs/reference-modeling.md §6
 * @returns {{ok:true,batch:string,count:number,orders:Array,warnings:Array,policy:string,summary:string}}
 */
export function buildWorkOrders(input = {}) {
  const workspace = String(input.workspace || process.cwd())
  const projectRoot = String(input.project_root || workspace)
  const grid = { u_step: Number(input.grid?.u_step) || 5, v_step: Number(input.grid?.v_step) || 5 }
  const zones = Array.isArray(input.zones) ? input.zones.filter((zone) => zone && zone.code) : []
  if (!zones.length) {
    return { ok: false, error: { code: 'BAD_ARGS', message: 'dispatch 需要 zones：[{code,label,u0,u1,v0,v1,owner,module,write_scope}]' } }
  }
  const agents = new Map()
  for (const agent of Array.isArray(input.agents) ? input.agents : []) {
    if (agent?.owner) agents.set(String(agent.owner), agent)
  }
  const brief = briefOf(input)
  const policy = policyText(input.model_policy || {})
  const recursion = {
    allow: input.recursion?.allow !== false,
    maxDepth: Number(input.recursion?.max_depth) || 2,
    splitThreshold: Number(input.recursion?.split_threshold) || 6,
    maxChildren: Number(input.recursion?.max_children) || 4,
  }
  const findings = [
    ...(Array.isArray(input.audit?.findings)
      ? input.audit.findings.map((item) => ({ ...item, kind: 'audit' }))
      : []),
    ...(Array.isArray(input.diff?.rows)
      ? input.diff.rows
        .filter((row) => row.verdict === 'FAIL' || row.verdict === 'MISSING')
        .map((row) => ({
          angle: 'shape',
          severity: row.verdict === 'MISSING' ? 'P0' : 'P1',
          subject: (row.part || '') + ' · ' + (row.field || ''),
          cell: row.cell,
          expect: '参考带 ' + (row.ref || []).join('~'),
          actual: '实测 ' + row.model,
          delta: row.delta,
          hint: row.hint || '',
          kind: 'diff',
        }))
      : []),
    ...(Array.isArray(input.diff?.profile_rows)
      ? input.diff.profile_rows
        .filter((row) => row.verdict === 'FAIL' || row.verdict === 'MISSING')
        .map((row) => ({
          angle: 'shape',
          severity: 'P1',
          subject: 'profile ' + (row.cell || ''),
          cell: row.cell,
          expect: '|Δ| <= 容差',
          actual: 'Δ ' + (row.delta_vmax ?? row.delta_vmin),
          delta: row.delta_vmax ?? row.delta_vmin,
          hint: row.hint || '',
          kind: 'diff',
        }))
      : []),
  ]
  const hasSource = Boolean(
    (Array.isArray(input.audit?.findings) && input.audit.findings.length) ||
    (Array.isArray(input.diff?.rows) && input.diff.rows.length) ||
    (Array.isArray(input.diff?.profile_rows) && input.diff.profile_rows.length),
  )
  const grouped = groupFindings(findings, zones, grid)
  const batch = 'dispatch-' + timeStamp()
  const outDir = input.out_dir ? String(input.out_dir) : join(workspace, '.cad-workbench', 'dispatch', batch)
  mkdirSync(outDir, { recursive: true })

  const warnings = []
  for (const finding of grouped.unassigned) {
    warnings.push('未分配：' + (finding.subject || finding.angle) + '（格 ' + (finding.cell || '?') + '）不在任何责任区内，扩大 zone 或单独处理')
  }
  if (grouped.conflicts.length) {
    warnings.push('责任区重叠：' + grouped.conflicts.length + ' 条发现同时落在多个区（已归第一个区）：' +
      grouped.conflicts.slice(0, 5).map((item) => item.finding + '→' + item.zones.join('/')).join('；'))
  }
  if (!hasSource) warnings.push('未提供差分/审查结果：本次工单只能做增量核对与人工读图')
  const orders = []
  for (const zone of zones) {
    const owner = String(zone.owner || ('zone-' + zone.code))
    const zoneFindings = (grouped.buckets.get(String(zone.code)) || []).slice()
      .sort((a, b) => severityRank(a) - severityRank(b) || compareCells(a.cell, b.cell))
    const agent = agents.get(owner) || {}
    if (agents.size && !agents.has(owner)) {
      warnings.push(zone.code + '：未在 agents 里登记 ' + owner + '（工单仍会生成，模型沿用父会话默认）')
    }
    const writeScope = Array.isArray(zone.write_scope) ? zone.write_scope.slice()
      : (zone.module ? [String(zone.module)] : [])
    const module = String(zone.module || (writeScope[0] || ''))
    const prefix = sanitizeBasename(owner, 'agent') + '-v' + batch.slice(-4)
    const buildCommand = brief.buildCmd
      ? brief.buildCmd.replace(/\{module\}/g, module).replace(/\{prefix\}/g, prefix)
        .replace(/\{project\}/g, String(input.project_id || ''))
      : ''
    const verifyCommands = brief.verifyCmds.map((command) => command
      .replace(/\{module\}/g, module).replace(/\{prefix\}/g, prefix)
      .replace(/\{project\}/g, String(input.project_id || '')))
    const cells = Array.from(new Set(zoneFindings.map((item) => item.cell).filter(Boolean))).sort(compareCells)
    const tasks = zoneFindings.map((item) => {
      const head = (item.cell ? '格 ' + item.cell + '：' : '') + (item.subject || item.angle || '')
      const detail = [
        item.expect ? '参考/期望 ' + item.expect : '',
        item.actual ? '实测 ' + item.actual : '',
        item.delta !== undefined && item.delta !== null ? 'Δ ' + round(item.delta) : '',
        item.hint ? '做法：' + item.hint : '',
      ].filter(Boolean).join('；')
      return head + (detail ? ' —— ' + detail : '')
    })
    const order = {
      owner,
      module,
      writeScope,
      zone: { code: zone.code, label: zone.label || '' },
      cells,
      findings: zoneFindings,
      findingCounts: {
        P0: zoneFindings.filter((item) => item.severity === 'P0').length,
        P1: zoneFindings.filter((item) => item.severity === 'P1').length,
      },
      projectRoot,
      readOnly: brief.readOnly,
      mustRead: brief.mustRead,
      frames: brief.frames,
      coordinateNote: brief.coordinateNote,
      conventions: brief.conventions,
      tasks,
      buildCommand,
      verifyCommands,
      children: childrenHint(zone, zoneFindings, recursion),
      maxDepth: recursion.maxDepth,
      splitThreshold: recursion.splitThreshold,
      taskBoard: brief.taskBoard,
      hasSource,
      policyLines: policy,
      modelHint: agent.model_hint || null,
    }
    order.markdown = renderOrder(order)
    order.path = join(outDir, sanitizeBasename(owner, 'agent') + '.md')
    // prompt 传给 subagent 时不带一级标题（子智能体自己就是工单主体，标题只会浪费首行）
    const body = order.markdown.replace(/^#[^\n]*\n\n?/, '')
    order.prompt = body.length > MAX_PROMPT
      ? body.slice(0, MAX_PROMPT) + '\n\n（工单过长已截断，完整版见 ' + order.path + '）'
      : body
    writeFileSync(order.path, order.markdown, 'utf8')
    orders.push(order)
    if (!zoneFindings.length) warnings.push(zone.code + '：本区无未通过项，工单为增量核对')
    if (!writeScope.length) warnings.push(zone.code + '：未给写作用域（下级只能只读分析）')
  }
  const policyTextValue = policy.map((rule, index) => String(index + 1) + '. ' + rule).join('\n')
  const totalP0 = orders.reduce((sum, order) => sum + order.findingCounts.P0, 0)
  const totalP1 = orders.reduce((sum, order) => sum + order.findingCounts.P1, 0)
  return {
    ok: true,
    batch,
    generated_at: new Date().toISOString(),
    count: orders.length,
    orders,
    warnings: Array.from(new Set(warnings)),
    policy: policyTextValue,
    out_dir: outDir,
    summary: [
      '工单 ' + orders.length + ' 张（P0 ' + totalP0 + ' / P1 ' + totalP1 + '）',
      '未分配项 ' + grouped.unassigned.length + ' 条',
      '输出目录 ' + displayPath(workspace, outDir),
      '下一步：先 list_subagent_models 校验模型，再把 orders[i].prompt 交给 subagent（写作用域已在工单里写死）',
    ].join('\n'),
  }
}
