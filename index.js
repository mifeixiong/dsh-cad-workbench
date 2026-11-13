/**
 * dsh-cad-workbench —— 机械 CAD 工作台（DeepSeek Harness 插件）。
 *
 * 定位：把「模型说人话」和「CAD 做确定的事」分开。
 *   * 模型只提交声明式 Feature Graph（单位 mm），不直接拼 COM 调用；
 *   * 插件负责建模、实测、截图、出图、导出与作业台账；
 *   * 每个"做好了"的结论都必须有实测证据（verified），否则只能报 partial。
 *
 * 工具前缀 cadw_：避开生态里已占用的 cad_* / solidworks_*（DSH 重名会拒绝启动）。
 *
 * 设计约束：
 *   1. 失败返回 { ok:false, error:{ code, message, details? } }，不抛原始 traceback；
 *   2. 返回值统一过 toLossless（DSH 的无损 JSON 校验会整次拒绝 undefined/NaN）；
 *   3. 图片经附件服务内联，服务缺失时只给路径，工具不因此失败；
 *   4. Python 子进程 stdout/stderr 写文件而不是管道（受限沙箱里管道会 EPERM）。
 *
 * @module dsh-cad-workbench
 */

import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { contentFrom, saveImageRef } from './lib/images.js'
import { normalizeGraph, graphHash } from './lib/graph.js'
import {
  fileSha256,
  finishJob,
  getJob,
  listJobs,
  listRevisions,
  newJobId,
  recordArtifacts,
  recordEvent,
  recordVerification,
  startJob,
} from './lib/ledger.js'
import { compareExpect } from './lib/plan.js'
import { pythonCacheInfo, runScript } from './lib/python.js'
import {
  defaultOutputDir,
  displayPath,
  ensureDir,
  guardOutputPath,
  sanitizeBasename,
  stamp,
  workspaceOf,
} from './lib/session.js'
import { asFail, fail, fromScript, toLossless } from './lib/tooling.js'

const PROVIDER_NAME = 'dsh-cad-workbench'
const OUTPUT_SCHEMA = { type: 'object', additionalProperties: true }
const SKILL_NAMES = [
  'cad-modeling-workflow',
  'solidworks-com',
  'cad-feature-graph',
  'cad-drawing-gb',
]

let rawDefineTool = (options) => options
let defineToolError
try {
  const mod = await import('@deepseek-ai/dsh-tools')
  if (typeof mod.defineTool === 'function') rawDefineTool = mod.defineTool
  else defineToolError = new Error('模块已加载但没有导出 defineTool')
} catch (error) {
  defineToolError = error
}

/**
 * 注册处统一过 toLossless。返回图片的工具最容易踩 undefined 的坑：
 * 附件服务不可用时 saveImageRef 只给 note，若返回语句还写着 image，
 * { image: undefined } 会让整次调用失败。
 */
function defineTool(definition) {
  const execute = definition?.execute
  if (typeof execute !== 'function') return rawDefineTool(definition)
  return rawDefineTool({
    ...definition,
    execute: async (...args) => {
      try {
        return toLossless(await execute(...args))
      } catch (error) {
        return toLossless(fail(error?.code || 'INTERNAL', String(error?.message ?? error)))
      }
    },
  })
}

function errorSummary(value) {
  if (!value || typeof value !== 'object') return String(value)
  if (value.ok === false && value.error) return '失败：' + value.error.code + ' — ' + value.error.message
  if (typeof value.summary === 'string') return value.summary
  return JSON.stringify(value, null, 2).slice(0, 4000)
}

const renderResult = (_args, value) => contentFrom(value, errorSummary(value))

/** 统一的脚本调用 + 异常信封化。 */
async function invoke(script, spec, options = {}) {
  try {
    return await runScript(script, spec, options)
  } catch (error) {
    return asFail(error, options.fallbackCode ?? 'INTERNAL')
  }
}

/** 把工具工作区内的目录参数解析成绝对路径（允许目录已存在）。 */
function resolveOutDir(workspace, requested, fallback) {
  if (!requested) return ensureDir(fallback)
  const absolute = resolve(workspace, String(requested))
  guardOutputPath(workspace, absolute, { overwrite: true })
  return ensureDir(absolute)
}

/** 模型/作业结果的通用摘要。 */
function modelSummary(result) {
  if (!result || result.ok === false) return errorSummary(result)
  const lines = []
  if (result.dry_run) {
    lines.push('图校验通过（dry_run，未建模）：' + (result.graph?.nodes?.length ?? 0) + ' 个节点')
  } else {
    lines.push('建模完成：创建 ' + (result.created_count ?? 0) + ' 个特征，特征数 ' +
      (result.features_before ?? '?') + ' → ' + (result.features_after ?? '?'))
  }
  const m = result.measurements ?? {}
  if (Array.isArray(m.bbox_mm)) lines.push('实测包围盒：' + m.bbox_mm.map((v) => round3(v)).join(' × ') + ' mm')
  if (Number.isFinite(m.volume_mm3)) lines.push('体积：' + round3(m.volume_mm3) + ' mm³（' + (m.volume_source ?? 'unknown') + '）')
  const v = result.verification ?? {}
  if (v.status) {
    lines.push('校核：' + v.status + (v.failures?.length ? ' — ' + v.failures.join('；') : ''))
  }
  if (result.rollback?.attempted) lines.push('回滚：' + (result.rollback.done ? '成功' : '失败') + '（特征数 ' + (result.rollback.restored_count ?? '?') + '）')
  for (const warning of result.warnings ?? []) lines.push('警告：' + warning)
  return lines.join('\n')
}

function round3(value) {
  const number = Number(value)
  return Number.isFinite(number) ? Math.round(number * 1000) / 1000 : value
}

function registerTools(ctx) {
  // 1) 环境与能力
  ctx.tools.register(defineTool({
    name: 'cadw_doctor',
    description:
      'CAD 环境自检（只读）：探测 Windows/Python 解释器（标准库、Pillow、pywin32、ezdxf）、' +
      'SolidWorks 注册/运行/版本/零件-装配-工程图模板、FreeCAD 是否存在、状态目录可写与 SW 锁状态，' +
      '给出能力矩阵和修复建议。建模前先跑它；缺什么一目了然，不会假装可用。',
    parameters: {
      probe_com: { type: 'boolean', description: '是否实际连接 SolidWorks 取版本（默认 true；不会新建/修改文档）' },
      workspace: { type: 'string', description: '工作区根目录；缺省用会话当前目录' },
    },
    timeoutMs: 180000,
    output: { schema: OUTPUT_SCHEMA, render: renderResult },
    async execute(args, exec) {
      const workspace = workspaceOf(args, exec, ctx)
      const result = await invoke('cad_doctor.py', {
        workspace,
        probe_com: args.probe_com !== false,
      }, { requirement: 'base', timeoutMs: 150000 })
      if (result.ok === false) return result
      result.python_cache = pythonCacheInfo()
      result.summary = [
        '平台：' + result.platform,
        'SolidWorks：' + (result.solidworks?.revision ?? '未连接') + (result.solidworks?.running ? '（运行中）' : ''),
        '模板：零件 ' + (result.solidworks?.templates?.part ? '有' : '缺') +
          '，装配 ' + (result.solidworks?.templates?.assembly ? '有' : '缺') +
          '，工程图 ' + (result.solidworks?.templates?.drawing ? '有' : '缺'),
        '模块：' + Object.entries(result.modules ?? {}).map(([name, ok]) => name + '=' + (ok ? 'ok' : '缺')).join('，'),
        '能力：' + Object.entries(result.capabilities ?? {}).map(([name, ok]) => name + '=' + (ok ? 'yes' : 'no')).join('，'),
        (result.suggestions ?? []).length ? '建议：' + result.suggestions.join('；') : '',
      ].filter(Boolean).join('\n')
      return result
    },
  }))

  // 2) 文档会话
  ctx.tools.register(defineTool({
    name: 'cadw_session',
    description:
      'SolidWorks 文档会话：status / new_part / new_assembly / open / activate / save / save_as / close / close_all / list。' +
      '每个文档都返回 doc_id；后续写操作必须带这个 doc 令牌，插件会在写入前复核文档身份，' +
      '防止把特征画到用户正在编辑的另一个文档上。关闭/覆盖已有文件/丢弃未保存修改需要 confirm=true。',
    parameters: {
      action: { type: 'string', required: true, description: 'status | new_part | new_assembly | open | activate | save | save_as | close | close_all | list' },
      path: { type: 'string', description: 'open 的源文件，或 save_as 的目标路径（工作区内）' },
      template: { type: 'string', description: '可选：模板绝对路径；缺省自动探测' },
      doc: { type: 'string', description: '文档令牌 doc_id（close/activate/save 用）' },
      confirm: { type: 'boolean', description: '危险动作确认：覆盖已有文件、丢弃未保存修改、close_all' },
      visual: { type: 'boolean', description: '是否把 SolidWorks 前台置前并显示等轴测（默认 true）' },
      workspace: { type: 'string', description: '工作区根目录；缺省用会话当前目录' },
      allow_outside: { type: 'boolean', description: '允许保存到工作区外（默认拒绝）' },
    },
    timeoutMs: 240000,
    output: { schema: OUTPUT_SCHEMA, render: renderResult },
    async execute(args, exec) {
      const workspace = workspaceOf(args, exec, ctx)
      const spec = {
        workspace,
        action: args.action,
        template: args.template ?? null,
        doc: args.doc ?? null,
        confirm: args.confirm === true,
        visual: args.visual !== false,
      }
      if (args.path) {
        if (args.action === 'save_as' || args.action === 'save') {
          const target = guardOutputPath(workspace, resolve(workspace, String(args.path)), {
            allowOutside: args.allow_outside === true,
            overwrite: args.confirm === true,
          })
          spec.path = target
        } else {
          spec.path = resolve(workspace, String(args.path))
        }
      }
      const result = await invoke('sw_session.py', spec, { requirement: 'com', timeoutMs: 210000 })
      if (result.ok === false) return result
      const doc = result.doc
      result.summary = result.summary ?? [
        'action：' + args.action,
        doc ? '文档：' + doc.title + '（' + doc.type + '，doc_id=' + doc.doc_id + '）' : '',
        doc?.path ? '路径：' + displayPath(workspace, doc.path) : '',
        result.closed ? '已关闭：' + result.closed : '',
        (result.docs ?? []).length ? '已打开 ' + result.docs.length + ' 个文档' : '',
      ].filter(Boolean).join('\n')
      return result
    },
  }))

  // 3) 声明式建模
  ctx.tools.register(defineTool({
    name: 'cadw_model',
    description:
      '按声明式 Feature Graph 在指定文档上建模（单位 mm）。节点：sketch（rect/circle/slot/rounded_rect/polygon/polyline/arc/centerline）、' +
      'extrude、cut、revolve、hole、fillet、chamfer、sweep、linear_pattern、circular_pattern。先做计划期校验（dry_run 可只看校验与计划），' +
      '再在 Undo 事务内逐节点执行；失败自动回滚并复验特征数。传 expect（包围盒/体积/特征数）时，' +
      '建完按实测逐轴比对，容差 max(1 mm, 2%)，不符返回 PLAN_MISMATCH。',
    parameters: {
      doc: { type: 'string', description: '文档令牌 doc_id（来自 cadw_session；dry_run 时可省略）' },
      graph: { type: 'json', required: true, description: 'Feature Graph：{units:"mm", nodes:[{op:"sketch",...},{op:"extrude",depth:10}]}' },
      dry_run: { type: 'boolean', description: '只校验与生成计划，不碰 SolidWorks（默认 false）' },
      visual: { type: 'boolean', description: '每步特征后置前并展示等轴测（默认 false；慢）' },
      expect: { type: 'json', description: '期望值：{bbox_mm:[x,y,z], volume_mm3?, feature_count?}' },
      rollback_on_fail: { type: 'boolean', description: '失败时回滚已建特征（默认 true）' },
      job_id: { type: 'string', description: '可选：把本次建模与校核记进作业台账' },
      workspace: { type: 'string', description: '工作区根目录；缺省用会话当前目录' },
    },
    timeoutMs: 900000,
    output: { schema: OUTPUT_SCHEMA, render: renderResult },
    async execute(args, exec) {
      const workspace = workspaceOf(args, exec, ctx)
      if (args.dry_run !== true && !args.doc) {
        return fail('BAD_ARGS', 'cadw_model 需要 doc（来自 cadw_session）；只有 dry_run=true 可以省略。')
      }
      let graph
      try {
        graph = normalizeGraph(args.graph)
      } catch (error) {
        return fail('GRAPH_INVALID', String(error?.message ?? error))
      }
      let expect = args.expect ?? null
      if (typeof expect === 'string') {
        try { expect = JSON.parse(expect) } catch { return fail('GRAPH_INVALID', 'expect 不是合法 JSON') }
      }
      const spec = {
        workspace,
        doc: args.doc ?? null,
        graph,
        dry_run: args.dry_run === true,
        visual: args.visual === true,
        expect,
        rollback_on_fail: args.rollback_on_fail !== false,
      }
      const result = await invoke('sw_build.py', spec, {
        requirement: spec.dry_run ? 'base' : 'com',
        timeoutMs: 880000,
      })
      if (result.ok === false) return result
      // graph_hash 以 Python 校验后规范化图为准；原文 hash 另存，避免同一张图两个值。
      result.graph_hash_input = graphHash(graph)
      if (!spec.dry_run) {
        const measured = {
          ...(result.measurements ?? {}),
          feature_count: result.features_after,
        }
        result.verification = compareExpect(expect, measured)
      }
      if (args.job_id) {
        const job = await getJob(workspace, args.job_id)
        if (!job) return fail('JOB_NOT_FOUND', '作业不存在：' + args.job_id)
        result.job_id = job.job_id
        await recordEvent(workspace, job.job_id, {
          tool: 'cadw_model',
          ok: result.verification?.status !== 'mismatch',
          dry_run: spec.dry_run,
          graph_hash: result.graph_hash,
          graph_hash_input: result.graph_hash_input,
          node_count: graph.nodes.length,
          features_before: result.features_before ?? null,
          features_after: result.features_after ?? null,
        })
        if (!spec.dry_run && result.verification) {
          await recordVerification(workspace, job.job_id, {
            status: result.verification.status,
            checks: result.verification.checks,
            failures: result.verification.failures,
          })
        }
      }
      if (!spec.dry_run && result.verification?.status === 'mismatch') {
        return fail('PLAN_MISMATCH', '实测与 expect 不符：' + (result.verification.failures ?? []).join('；'), {
          verification: result.verification,
          measurements: result.measurements,
          created: result.created,
          graph_hash: result.graph_hash,
        })
      }
      result.summary = modelSummary(result)
      return result
    },
  }))

  // 4) 测量
  ctx.tools.register(defineTool({
    name: 'cadw_measure',
    description:
      '读取指定 SolidWorks 文档的几何事实：包围盒（mm）、体积/表面积/质心（需 with_massprops=true，' +
      'massprops 在部分机器上可能卡死，默认关闭）、实体/面/边数量、特征树、草图段数/约束状态（全定义审计，进入编辑模式只读）、' +
      '基准面世界轴（查 IRefPlane 变换，查不到才退回经验表并标注 source）。',
    parameters: {
      doc: { type: 'string', required: true, description: '文档令牌 doc_id' },
      scope: { type: 'string', description: 'summary | mass | sketches | planes | tree | all（默认 summary）' },
      with_massprops: { type: 'boolean', description: '是否读取质量属性（默认 false；可能卡住 SolidWorks）' },
      deep_sketches: { type: 'boolean', description: 'scope 含 sketches 时是否进入草图编辑模式读约束状态/段数（默认 true；只读，不修改几何）' },
      workspace: { type: 'string', description: '工作区根目录' },
    },
    timeoutMs: 240000,
    output: { schema: OUTPUT_SCHEMA, render: renderResult },
    async execute(args, exec) {
      const workspace = workspaceOf(args, exec, ctx)
      const result = await invoke('sw_measure.py', {
        workspace,
        doc: args.doc,
        scope: args.scope ?? 'summary',
        with_massprops: args.with_massprops === true,
        deep_sketches: args.deep_sketches !== false,
      }, { requirement: 'com', timeoutMs: 210000 })
      if (result.ok === false) return result
      const bbox = result.bbox_mm
      result.summary = [
        bbox ? '包围盒：' + bbox.map((v) => round3(v)).join(' × ') + ' mm' : '',
        result.volume_mm3 ? '体积：' + round3(result.volume_mm3) + ' mm³（' + (result.volume_source ?? '') + '）' : '',
        result.body_count !== undefined ? '实体：' + result.body_count + '，面：' + (result.face_count ?? '?') + '，边：' + (result.edge_count ?? '?') : '',
        result.features ? '特征：' + result.features.length + ' 个' : '',
      ].filter(Boolean).join('\n')
      return result
    },
  }))

  // 5) 截图
  ctx.tools.register(defineTool({
    name: 'cadw_capture',
    description:
      '对指定文档截图：默认等轴测/前视/俯视/右视四视图（SolidWorks SaveBMP → PNG），可拼成 2×2 对照图。' +
      '每张图返回路径与像素统计（方差、唯一色数、全白占比），自动标记空白/全黑，避免"截图成功但屏幕全白"。' +
      '等轴测图与拼图会内联回传（最多 2 张），其余给路径。',
    parameters: {
      doc: { type: 'string', required: true, description: '文档令牌 doc_id' },
      views: { type: 'array', description: '视图列表，默认 ["iso","front","top","right"]；可选 iso/front/top/right/back/left/bottom' },
      width: { type: 'number', description: '像素宽（默认 1600）' },
      height: { type: 'number', description: '像素高（默认 1000）' },
      contact_sheet: { type: 'boolean', description: '是否生成 2×2 拼图（默认 true）' },
      out_dir: { type: 'string', description: '输出目录；缺省 <workspace>/cad-output' },
      basename: { type: 'string', description: '文件名前缀' },
      confirm: { type: 'boolean', description: '允许覆盖同名截图（默认拒绝）' },
      workspace: { type: 'string', description: '工作区根目录' },
    },
    timeoutMs: 300000,
    output: { schema: OUTPUT_SCHEMA, render: renderResult },
    async execute(args, exec) {
      const workspace = workspaceOf(args, exec, ctx)
      const outDir = resolveOutDir(workspace, args.out_dir, join(defaultOutputDir(workspace), 'captures'))
      const result = await invoke('sw_capture.py', {
        workspace,
        doc: args.doc,
        views: Array.isArray(args.views) && args.views.length ? args.views : ['iso', 'front', 'top', 'right'],
        width: Number(args.width) || 1600,
        height: Number(args.height) || 1000,
        contact_sheet: args.contact_sheet !== false,
        out_dir: outDir,
        basename: sanitizeBasename(args.basename, 'capture-' + stamp()),
        overwrite: args.confirm === true,
      }, { requirement: ['win32com', 'PIL'], timeoutMs: 280000 })
      if (result.ok === false) return result
      const images = Array.isArray(result.images) ? result.images : []
      const primary = images.find((item) => item.view === 'iso') ?? images[0]
      const sheet = result.contact_sheet
      const savedPrimary = primary ? await saveImageRef(ctx, primary.path) : { note: '没有可用视图' }
      const savedSheet = sheet ? await saveImageRef(ctx, sheet.path) : null
      result.image = savedPrimary.image
      result.imageNote = savedPrimary.note
      result.extraImage = savedSheet?.image
      result.extraImageNote = savedSheet?.note
      const blanks = [...images, ...(sheet ? [sheet] : [])].filter((item) => item.blank)
      result.summary = [
        '视图：' + images.map((item) => item.view + (item.blank ? '(空白!)' : '')).join('、'),
        sheet ? '拼图：' + displayPath(workspace, sheet.path) : '',
        blanks.length ? '警告：' + blanks.length + ' 张图疑似空白/全黑，请检查视图与模型是否可见' : '',
      ].filter(Boolean).join('\n')
      return result
    },
  }))

  // 6) 导出
  ctx.tools.register(defineTool({
    name: 'cadw_export',
    description:
      '把指定文档导出为 step / iges / stl / 3mf / pdf / png。每个格式都复核文件存在且非空，返回 bytes 与 sha256；' +
      '某个格式不可用（缺转换器/未授权）会单独报 UNSUPPORTED，不影响其他格式。覆盖已有文件需要 confirm=true。',
    parameters: {
      doc: { type: 'string', required: true, description: '文档令牌 doc_id' },
      formats: { type: 'array', description: '格式列表，默认 ["step"]；可选 step/iges/stl/3mf/pdf/png' },
      out_dir: { type: 'string', description: '输出目录；缺省 <workspace>/cad-output' },
      basename: { type: 'string', description: '文件名前缀（默认用文档名）' },
      confirm: { type: 'boolean', description: '允许覆盖已有文件' },
      job_id: { type: 'string', description: '可选：把产物记进作业台账' },
      workspace: { type: 'string', description: '工作区根目录' },
    },
    timeoutMs: 360000,
    output: { schema: OUTPUT_SCHEMA, render: renderResult },
    async execute(args, exec) {
      const workspace = workspaceOf(args, exec, ctx)
      const outDir = resolveOutDir(workspace, args.out_dir, defaultOutputDir(workspace))
      const formats = Array.isArray(args.formats) && args.formats.length ? args.formats : ['step']
      const result = await invoke('sw_export.py', {
        workspace,
        doc: args.doc,
        formats,
        out_dir: outDir,
        basename: args.basename ? sanitizeBasename(args.basename, 'export') : null,
        overwrite: args.confirm === true,
      }, { requirement: 'com', timeoutMs: 340000 })
      if (result.ok === false) return result
      for (const item of result.artifacts ?? []) {
        item.sha256 = fileSha256(item.path)
        item.bytes = item.bytes ?? null
      }
      if (!(result.artifacts ?? []).length) {
        return fail('EXPORT_FAILED', '所有格式都导出失败：' +
          (result.failures ?? []).map((item) => item.format + '=' + item.code).join('；'), {
          failures: result.failures ?? [],
        })
      }
      if (args.job_id) {
        const job = await getJob(workspace, args.job_id)
        if (!job) return fail('JOB_NOT_FOUND', '作业不存在：' + args.job_id)
        await recordArtifacts(workspace, job.job_id, result.artifacts ?? [])
        await recordEvent(workspace, job.job_id, { tool: 'cadw_export', ok: true, formats, artifacts: (result.artifacts ?? []).length })
      }
      result.summary = [
        '导出：' + (result.artifacts ?? []).map((item) => item.format + ' ' + displayPath(workspace, item.path) + ' (' + item.bytes + ' B)').join('；'),
        (result.failures ?? []).length ? '失败：' + result.failures.map((item) => item.format + '=' + item.code + ' ' + item.message).join('；') : '',
      ].filter(Boolean).join('\n')
      return result
    },
  }))

  // 7) 工程图
  ctx.tools.register(defineTool({
    name: 'cadw_drawing',
    description:
      '从零件/装配体生成工程图：标准视图（前/上/右/等轴测）、第一角法排列、可选模型尺寸导入，' +
      '保存 .SLDDRW 并按需导出 pdf/dxf/dwg。返回视图清单与尺寸计数；视图或导出不完整会写 warnings，不谎报成功。',
    parameters: {
      doc: { type: 'string', required: true, description: '文档令牌 doc_id（零件或装配体）' },
      sheet: { type: 'string', description: '图幅 A4/A3/A2/A1/A0（默认 A3）' },
      scale: { type: 'number', description: '视图比例（默认按图幅自适应）' },
      views: { type: 'array', description: '视图列表，默认 ["front","top","right","iso"]' },
      first_angle: { type: 'boolean', description: '第一角法布局（默认 true）' },
      insert_model_dims: { type: 'boolean', description: '导入模型尺寸（默认 true）' },
      export: { type: 'array', description: '导出格式，默认 ["pdf"]；可选 pdf/dxf/dwg' },
      out_dir: { type: 'string', description: '输出目录；缺省 <workspace>/cad-output' },
      basename: { type: 'string', description: '文件名前缀' },
      confirm: { type: 'boolean', description: '允许覆盖已有文件' },
      job_id: { type: 'string', description: '可选：把产物记进作业台账' },
      workspace: { type: 'string', description: '工作区根目录' },
    },
    timeoutMs: 600000,
    output: { schema: OUTPUT_SCHEMA, render: renderResult },
    async execute(args, exec) {
      const workspace = workspaceOf(args, exec, ctx)
      const outDir = resolveOutDir(workspace, args.out_dir, join(defaultOutputDir(workspace), 'drawings'))
      const result = await invoke('sw_drawing.py', {
        workspace,
        doc: args.doc,
        sheet: args.sheet ?? 'A3',
        scale: Number(args.scale) || null,
        views: Array.isArray(args.views) && args.views.length ? args.views : ['front', 'top', 'right', 'iso'],
        first_angle: args.first_angle !== false,
        insert_model_dims: args.insert_model_dims !== false,
        export: Array.isArray(args.export) && args.export.length ? args.export : ['pdf'],
        out_dir: outDir,
        basename: args.basename ? sanitizeBasename(args.basename, 'drawing') : null,
        overwrite: args.confirm === true,
      }, { requirement: 'com', timeoutMs: 580000 })
      if (result.ok === false) return result
      for (const item of result.artifacts ?? []) item.sha256 = fileSha256(item.path)
      if (args.job_id) {
        const job = await getJob(workspace, args.job_id)
        if (!job) return fail('JOB_NOT_FOUND', '作业不存在：' + args.job_id)
        await recordArtifacts(workspace, job.job_id, result.artifacts ?? [])
        await recordEvent(workspace, job.job_id, { tool: 'cadw_drawing', ok: true, views: (result.views ?? []).length })
      }
      result.summary = [
        '工程图：' + (result.drawing_path ? displayPath(workspace, result.drawing_path) : '(未保存)'),
        '视图：' + (result.views ?? []).map((view) => view.name).join('、'),
        '尺寸：' + (result.dimension_count ?? '?'),
        (result.warnings ?? []).length ? '警告：' + result.warnings.join('；') : '',
      ].filter(Boolean).join('\n')
      return result
    },
  }))

  // 8) 文件审查
  ctx.tools.register(defineTool({
    name: 'cadw_review',
    description:
      '只读审查 CAD 文件：DXF（7 项检查：单位、图层、线型、范围、零长/重复实体、尺寸、文字，规则数据驱动）、' +
      'STL（三角数、包围盒、退化面、水密启发式）、STEP（schema/单位/实体数/坐标包围盒，启发式会标注）。' +
      '每项检查给 status(PASS/WARN/FAIL) 与 hint；总体 verdict。',
    parameters: {
      paths: { type: 'array', description: '文件路径列表（.dxf/.stl/.step/.stp）' },
      dxf: { type: 'string', description: '单个 DXF 路径（与 paths 二选一）' },
      stl: { type: 'string', description: '单个 STL 路径' },
      step: { type: 'string', description: '单个 STEP 路径' },
      rules: { type: 'json', description: '覆盖默认 DXF 规则的部分字段' },
      workspace: { type: 'string', description: '工作区根目录（相对路径按它解析）' },
    },
    timeoutMs: 180000,
    output: { schema: OUTPUT_SCHEMA, render: (args, value) => contentFrom(value, errorSummary(value)) },
    async execute(args, exec) {
      const workspace = workspaceOf(args, exec, ctx)
      const paths = []
      for (const item of Array.isArray(args.paths) ? args.paths : []) paths.push(resolve(workspace, String(item)))
      for (const key of ['dxf', 'stl', 'step']) {
        if (args[key]) paths.push(resolve(workspace, String(args[key])))
      }
      if (!paths.length) return fail('BAD_ARGS', '至少要给一个文件：paths / dxf / stl / step')
      const needsDxf = paths.some((path) => path.toLowerCase().endsWith('.dxf'))
      let result
      try {
        result = await invoke('cad_review.py', { workspace, paths, rules: args.rules ?? null }, {
          requirement: needsDxf ? 'dxf' : 'base',
          timeoutMs: 160000,
          fallbackCode: 'REVIEW_FAILED',
        })
      } catch (error) {
        return asFail(error, 'REVIEW_FAILED')
      }
      if (result.ok === false && needsDxf && result.error?.code === 'PYTHON_DEPS') {
        result = await invoke('cad_review.py', { workspace, paths, rules: args.rules ?? null }, {
          requirement: 'base',
          timeoutMs: 160000,
          fallbackCode: 'REVIEW_FAILED',
        })
      }
      if (result.ok === false) return result
      result.summary = [
        '审查：' + (result.files ?? []).map((file) => file.kind + ' ' + displayPath(workspace, file.path) + ' → ' + file.verdict).join('；'),
        '总体：' + (result.verdict ?? 'UNKNOWN'),
      ].join('\n')
      return result
    },
  }))

  // 9) 装配体
  ctx.tools.register(defineTool({
    name: 'cadw_assembly',
    description:
      '装配体：new 新建；insert 插入零部件（mm 定位、可 fixed）；mate 在装配体上下文创建配合' +
      '（coincident/distance/parallel/perpendicular/tangent/angle；选择器用组件的 front/top/right 基准面）；' +
      'fix 固定/浮动；list 列出零部件与配合。组件路径只读；配合失败返回 AddMate5 的错误码。',
    parameters: {
      action: { type: 'string', required: true, description: 'new | insert | mate | fix | list' },
      doc: { type: 'string', description: '装配体文档令牌 doc_id（new 之外必填）' },
      components: { type: 'json', description: 'insert：[{path, x?, y?, z?, fixed?}]，位置单位 mm' },
      mates: { type: 'json', description: 'mate：[{type, a:{component,plane}, b:{component,plane}, distance?, angle?, align?, flip?, lock_rotation?}]' },
      component: { type: 'string', description: 'fix：组件名字或序号' },
      fixed: { type: 'boolean', description: 'fix：true 固定 / false 浮动（默认 true）' },
      workspace: { type: 'string', description: '工作区根目录（相对组件路径按它解析）' },
    },
    timeoutMs: 600000,
    output: { schema: OUTPUT_SCHEMA, render: renderResult },
    async execute(args, exec) {
      const workspace = workspaceOf(args, exec, ctx)
      const action = String(args.action ?? '')
      if (action === 'insert' && !Array.isArray(args.components)) {
        return fail('BAD_ARGS', 'insert 需要 components 数组')
      }
      if (action === 'mate' && !Array.isArray(args.mates)) {
        return fail('BAD_ARGS', 'mate 需要 mates 数组')
      }
      const spec = {
        workspace,
        action,
        doc: args.doc ?? null,
        components: args.components ?? null,
        mates: args.mates ?? null,
        component: args.component ?? null,
        fixed: args.fixed !== false,
      }
      const result = await invoke('sw_assembly.py', spec, { requirement: 'com', timeoutMs: 580000 })
      if (result.ok === false) return result
      const lines = []
      if (result.doc) lines.push('文档：' + result.doc.title + '（doc_id=' + result.doc.doc_id + '）')
      if (Array.isArray(result.components) && result.components.length) {
        lines.push('零部件：' + result.components.map((item) => item.name + (item.fixed ? '（固定）' : '')).join('、'))
      }
      if (Array.isArray(result.mates) && result.mates.length) {
        lines.push('配合：' + result.mates.map((item) => (item.name || item.type) + ' ' +
          (item.a || '') + '↔' + (item.b || '')).join('；'))
      }
      if (result.mate_count !== undefined) lines.push('配合总数：' + result.mate_count)
      if (result.component_count !== undefined) lines.push('组件总数：' + result.component_count)
      result.summary = lines.join('\n') || '装配体操作完成'
      return result
    },
  }))

  // 10) 作业台账
  ctx.tools.register(defineTool({
    name: 'cadw_job',
    description:
      '作业台账（纯本地 JSON，不碰 SolidWorks）：start 建作业并写批准方案，record 追加事件/产物，' +
      'verify 写实测校核结论，finish 按证据给交付结论（delivered/partial/failed + verified/measured-only/unverified），' +
      'get 读作业与不可变修订，list 列出近期作业。台账在 <workspace>/.cad-workbench/jobs/。',
    parameters: {
      action: { type: 'string', required: true, description: 'start | record | verify | finish | get | list' },
      job_id: { type: 'string', description: '作业 id（record/verify/finish/get 需要；start 可省略自动生成）' },
      name: { type: 'string', description: 'start：作业名/零件名' },
      plan: { type: 'json', description: 'start：批准方案（含 expect）' },
      doc: { type: 'json', description: 'start：文档身份 {doc_id,title,path,type}' },
      event: { type: 'json', description: 'record：事件对象' },
      artifacts: { type: 'json', description: 'record：产物数组 [{format,path,bytes,sha256}]' },
      verification: { type: 'json', description: 'verify：{status,checks,failures}' },
      accept_unverified: { type: 'boolean', description: 'finish：允许未 verified 的作业算 delivered（默认 false）' },
      limit: { type: 'number', description: 'list：条数（默认 20）' },
      workspace: { type: 'string', description: '工作区根目录' },
    },
    timeoutMs: 120000,
    output: { schema: OUTPUT_SCHEMA, render: renderResult },
    async execute(args, exec) {
      const workspace = workspaceOf(args, exec, ctx)
      const action = String(args.action ?? '')
      try {
        // 台账也必须过系统目录守卫：workspace 若指向 C:Windows 之类，不能在那里建 .cad-workbench。
        guardOutputPath(workspace, join(workspace, '.cad-workbench', 'jobs', '.probe'), { overwrite: true })
        if (action === 'start') {
          const job = await startJob(workspace, {
            job_id: args.job_id ? String(args.job_id) : newJobId(args.name ? 'cad' : 'job'),
            name: args.name ?? null,
            plan: args.plan ?? null,
            doc: args.doc ?? null,
          })
          return { ok: true, job, summary: '作业已建立：' + job.job_id + '（台账 .cad-workbench/jobs/' + job.job_id + '/）' }
        }
        if (action === 'record') {
          if (!args.job_id) return fail('BAD_ARGS', 'record 需要 job_id')
          if (args.artifacts !== undefined && !Array.isArray(args.artifacts)) {
            return fail('BAD_ARGS', 'record 的 artifacts 必须是数组')
          }
          let job
          if (args.artifacts) job = await recordArtifacts(workspace, String(args.job_id), args.artifacts)
          job = await recordEvent(workspace, String(args.job_id), args.event ?? { tool: 'cadw_job', ok: true })
          return { ok: true, job, summary: '已记录事件；当前事件数 ' + job.events.length }
        }
        if (action === 'verify') {
          if (!args.job_id) return fail('BAD_ARGS', 'verify 需要 job_id')
          const job = await recordVerification(workspace, String(args.job_id), args.verification ?? { status: 'unverified', checks: [] })
          return { ok: true, job, summary: '校核状态：' + (job.verification?.status ?? 'unknown') }
        }
        if (action === 'finish') {
          if (!args.job_id) return fail('BAD_ARGS', 'finish 需要 job_id')
          const job = await finishJob(workspace, String(args.job_id), { accept_unverified: args.accept_unverified === true })
          return {
            ok: true,
            job,
            summary: '交付：' + job.delivery.status + '（trust=' + job.delivery.trust + '）' +
              ((job.delivery.blockers ?? []).length ? '；阻塞：' + job.delivery.blockers.join('；') : ''),
          }
        }
        if (action === 'get') {
          if (!args.job_id) return fail('BAD_ARGS', 'get 需要 job_id')
          const job = await getJob(workspace, String(args.job_id))
          if (!job) return fail('JOB_NOT_FOUND', '作业不存在：' + args.job_id)
          return {
            ok: true,
            job,
            revisions: listRevisions(workspace, String(args.job_id)),
            summary: '作业 ' + job.job_id + '：' + job.delivery.status + '（trust=' + job.delivery.trust + '，产物 ' + (job.artifacts ?? []).length + '）',
          }
        }
        if (action === 'list') {
          const jobs = await listJobs(workspace, Number(args.limit) || 20)
          return { ok: true, jobs, summary: '近期作业 ' + jobs.length + ' 个' }
        }
        return fail('BAD_ARGS', '未知 action：' + action + '（可用 start/record/verify/finish/get/list）')
      } catch (error) {
        return asFail(error, 'JOB_FAILED')
      }
    },
  }))
}

// --------------------------------------------------------------------------
// 技能提供方：元数据直接读 SKILL.md 的 frontmatter，避免两处描述不一致
// --------------------------------------------------------------------------
function skillPath(name, ...parts) {
  return fileURLToPath(new URL('./skills/' + name + '/' + parts.join('/'), import.meta.url))
}

function frontmatter(raw) {
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(raw)
  if (!match) throw new Error('skill 缺少 YAML frontmatter')
  const text = match[1]
  const name = /^name:\s*(.+?)\s*$/m.exec(text)?.[1]
  const quoted = /^description:\s*"([^"]*)"\s*$/m.exec(text)?.[1]
  const plain = /^description:\s*(?!["'])(.+?)\s*$/m.exec(text)?.[1]
  const description = quoted ?? plain
  if (!name || !description) throw new Error('skill frontmatter 需要 name 与单行 description')
  return { name, description, bodyStart: match[0].length }
}

function createSkillProvider() {
  const candidates = []
  for (const name of SKILL_NAMES) {
    const directory = skillPath(name)
    const body = join(directory, 'SKILL.md')
    if (!existsSync(body)) continue
    const metadata = frontmatter(readFileSync(body, 'utf8'))
    if (metadata.name !== name) throw new Error(body + ' 声明的是 ' + metadata.name + '，期望 ' + name)
    candidates.push({
      path: body,
      name,
      description: metadata.description,
      invocation: { modelInvocable: true, userInvocable: true },
      provider: PROVIDER_NAME,
      source: 'bundled',
      resourceBase: { kind: 'directory', path: directory },
      rank: 600,
      locator: { body, bodyStart: metadata.bodyStart },
    })
  }
  return {
    name: PROVIDER_NAME,
    list: () => Promise.resolve(candidates),
    async get(candidate) {
      const selected = candidates.find(
        (item) => item.name === candidate?.name && item.provider === candidate?.provider,
      )
      if (!selected) return undefined
      const { rank: _rank, locator, ...summary } = selected
      return { ...summary, content: readFileSync(locator.body, 'utf8').slice(locator.bodyStart).trim() }
    },
  }
}

// --------------------------------------------------------------------------
// 插件入口
// --------------------------------------------------------------------------
export const name = PROVIDER_NAME
export const inject = ['tools']

export function apply(ctx) {
  const insideHarness = Boolean(process.env.DSH_INSTALL_DIR || process.env.DSH_PROFILE)
  const selftest = process.env.DSH_CAD_SELFTEST === '1'
  if (defineToolError && insideHarness && !selftest) {
    throw new Error(
      'dsh-cad-workbench: 无法解析 @deepseek-ai/dsh-tools（' + String(defineToolError?.message ?? defineToolError) + '）。' +
      '缺它时参数 schema 不会被编译，注册出的工具会把 {"type":"json"} 直接发给模型并让每次请求失败；' +
      '宁可整行激活失败，也不注册半残工具。修法：确保本包 package.json 的 peerDependencies 声明了 @deepseek-ai/dsh-tools，并重启 DSH。',
    )
  }
  registerTools(ctx)
  try {
    const skills = typeof ctx.get === 'function' ? ctx.get('skills') : ctx.skills
    if (skills && typeof skills.registerProvider === 'function') {
      skills.registerProvider(createSkillProvider)
    }
  } catch (error) {
    ctx.logger?.warn?.('dsh-cad-workbench: 技能注册失败：%s', error?.message ?? error)
  }
}
