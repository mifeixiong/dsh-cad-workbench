/**
 * Python 解释器解析与脚本调用。
 *
 * 两条刻意的选择（来自 dsh-mech-workbench 的实战教训）：
 *  1. spec 走临时文件而不是命令行，避免 Windows 命令行长度上限截断大图/大图数据；
 *  2. 子进程 stdout/stderr **写文件而不是管道** —— 受限沙箱里 Node 用管道 stdio
 *     捕获子进程输出会 spawn EPERM，文件通道在受限与非受限环境下都可用。
 *
 * 依赖分档（requirement）：
 *   base  —— 纯标准库（图校验、台账）
 *   image —— 加 Pillow（截图拼图、像素统计）
 *   com   —— 加 pywin32（SolidWorks COM）
 *   dxf   —— 加 ezdxf（DXF 审查）
 *   full  —— com + image + dxf
 *
 * @module dsh-cad-workbench/lib/python
 */

import { spawn } from 'node:child_process'
import { existsSync, readdirSync } from 'node:fs'
import { mkdtemp, open, readFile, rm, writeFile } from 'node:fs/promises'
import { homedir, tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const SCRIPTS_DIR = fileURLToPath(new URL('../scripts', import.meta.url))
const PLUGIN_ROOT = fileURLToPath(new URL('..', import.meta.url))

const REQUIREMENTS = {
  base: [],
  image: ['PIL'],
  com: ['win32com'],
  dxf: ['ezdxf'],
  full: ['win32com', 'PIL', 'ezdxf'],
}

/** 依赖档位 → 模块清单。允许直接传模块名数组。 */
export function requiredModules(requirement = 'base') {
  if (Array.isArray(requirement)) {
    const list = requirement.map((item) => String(item).trim()).filter(Boolean)
    return list.length ? list : []
  }
  const key = String(requirement ?? 'base').trim()
  if (REQUIREMENTS[key]) return REQUIREMENTS[key]
  return key ? key.split(',').map((item) => item.trim()).filter(Boolean) : []
}

/**
 * 候选解释器（按优先级）。
 * 环境变量 CADW_PYTHON 永远第一优先；随后是 PATH 上的 python/python3。
 */
export function pythonCandidates() {
  const list = []
  if (process.env.CADW_PYTHON) list.push({ cmd: process.env.CADW_PYTHON, args: [] })
  list.push({ cmd: 'python', args: [] })
  list.push({ cmd: 'python3', args: [] })
  list.push({ cmd: 'py', args: ['-3'] })

  // Windows 用户级安装
  try {
    const programs = join(homedir(), 'AppData', 'Local', 'Programs', 'Python')
    if (existsSync(programs)) {
      for (const name of readdirSync(programs)) {
        const exe = join(programs, name, 'python.exe')
        if (existsSync(exe)) list.push({ cmd: exe, args: [] })
      }
    }
  } catch { /* 忽略 */ }

  // DSH Desktop 的运行时（通常带 numpy/Pillow，不一定带 pywin32）
  const exe = process.execPath
  for (const candidate of [
    join(dirname(exe), 'resources', 'office-runtime', 'primary-runtime', 'dependencies', 'python', 'python.exe'),
    join(homedir(), '.dsh', 'dsh-runtimes', 'dsh-primary-runtime', 'dependencies', 'python', 'python.exe'),
  ]) {
    if (existsSync(candidate)) list.push({ cmd: candidate, args: [] })
  }
  return list
}

const cache = new Map()

function probeCode(modules) {
  const wanted = JSON.stringify(modules)
  return [
    'import importlib.util as u, json, sys',
    `mods = ${wanted}`,
    'missing = [m for m in mods if u.find_spec(m) is None]',
    'print(json.dumps({"ok": not missing, "missing": missing, "version": sys.version.split()[0]}))',
  ].join('\n')
}

async function runProcess(command, args, options) {
  const ownsDir = !options.dir
  const dir = options.dir ?? (await mkdtemp(join(tmpdir(), 'dsh-cad-')))
  const stamp = `${Date.now()}-${Math.random().toString(36).slice(2)}`
  const outPath = join(dir, `out-${stamp}.log`)
  const errPath = join(dir, `err-${stamp}.log`)
  const outHandle = await open(outPath, 'w')
  const errHandle = await open(errPath, 'w')
  let child
  try {
    child = spawn(command, args, {
      stdio: ['ignore', outHandle.fd, errHandle.fd],
      windowsHide: true,
      cwd: options.cwd,
      env: {
        ...process.env,
        PYTHONUTF8: '1',
        PYTHONIOENCODING: 'utf-8',
        PYTHONUNBUFFERED: '1',
        ...(options.env ?? {}),
      },
    })
  } catch (error) {
    await outHandle.close().catch(() => {})
    await errHandle.close().catch(() => {})
    return { code: null, stdout: '', stderr: '', timedOut: false, spawnError: error }
  }
  const outcome = await new Promise((resolve) => {
    let settled = false
    const finish = (value) => {
      if (!settled) {
        settled = true
        resolve(value)
      }
    }
    const timer = setTimeout(() => {
      try { child.kill() } catch { /* 已退出 */ }
      finish({ code: null, timedOut: true, spawnError: undefined })
    }, options.timeoutMs ?? 180000)
    child.once('error', (error) => {
      clearTimeout(timer)
      finish({ code: null, timedOut: false, spawnError: error })
    })
    child.once('close', (code) => {
      clearTimeout(timer)
      finish({ code, timedOut: false, spawnError: undefined })
    })
  })
  await outHandle.close().catch(() => {})
  await errHandle.close().catch(() => {})
  const stdout = await readFile(outPath, 'utf8').catch(() => '')
  const stderr = await readFile(errPath, 'utf8').catch(() => '')
  await rm(outPath, { force: true }).catch(() => {})
  await rm(errPath, { force: true }).catch(() => {})
  if (ownsDir) await rm(dir, { recursive: true, force: true }).catch(() => {})
  return { ...outcome, stdout, stderr }
}

/**
 * 解析满足依赖要求的解释器。结果按模块组合缓存。
 * @param {string|string[]} requirement
 * @returns {Promise<string>}
 */
export async function resolvePython(requirement = 'base') {
  const modules = requiredModules(requirement)
  const key = modules.join(',')
  if (cache.has(key)) return cache.get(key)
  const problems = []
  for (const candidate of pythonCandidates()) {
    if ((candidate.cmd.includes('\\') || candidate.cmd.includes('/')) && !existsSync(candidate.cmd)) {
      problems.push(`${candidate.cmd}: 不存在`)
      continue
    }
    const probe = await runProcess(candidate.cmd, [...candidate.args, '-c', probeCode(modules)], {
      timeoutMs: 30000,
    })
    if (probe.spawnError) {
      problems.push(`${candidate.cmd}: ${probe.spawnError.message ?? probe.spawnError}`)
      continue
    }
    if (probe.timedOut) {
      problems.push(`${candidate.cmd}: 探测超时`)
      continue
    }
    if (probe.code === 0) {
      try {
        const info = JSON.parse(probe.stdout.trim())
        if (info.ok) {
          cache.set(key, candidate.cmd)
          cache.set(`${key}::version`, info.version)
          cache.set(`${key}::args`, candidate.args)
          return candidate.cmd
        }
        problems.push(`${candidate.cmd}: 缺 ${info.missing.join(' + ')}`)
      } catch {
        problems.push(`${candidate.cmd}: 探测输出无法解析`)
      }
    } else {
      problems.push(`${candidate.cmd}: 退出码 ${probe.code}`)
    }
  }
  const need = modules.length ? modules.join(' + ') : '标准库'
  throw new Error(
    `找不到满足依赖的 Python 解释器（需要 ${need}）。已尝试：${problems.join('；')}。` +
      '可用 CADW_PYTHON 指定解释器（COM 建模需要带 pywin32 的 Python；截图拼图需要 Pillow；DXF 审查需要 ezdxf）。',
  )
}

/** 已解析过的解释器信息（doctor 用；未解析则空）。 */
export function pythonCacheInfo() {
  const out = {}
  for (const [key, value] of cache.entries()) {
    if (key.endsWith('::version') || key.endsWith('::args')) continue
    out[key] = {
      path: value,
      version: cache.get(`${key}::version`) ?? null,
      args: cache.get(`${key}::args`) ?? [],
    }
  }
  return out
}

/**
 * 从混合输出里取出**最后一个**可解析的 JSON 对象。
 *
 * 现实场景：脚本前面可能混有 Set-Content/警告/花括号片段，或者调试输出；
 * 只有最后一个完整 JSON 才是工具结果。非 JSON 的 {…} 片段要跳过继续找。
 */
export function parseJsonObject(text) {
  if (!text) return undefined
  const trimmed = String(text).trim()
  try {
    return JSON.parse(trimmed)
  } catch { /* 继续扫描 */ }
  const candidates = []
  for (let start = 0; start < trimmed.length; start += 1) {
    if (trimmed[start] !== '{') continue
    let depth = 0
    let inString = false
    let escaped = false
    let end = -1
    for (let index = start; index < trimmed.length; index += 1) {
      const ch = trimmed[index]
      if (inString) {
        if (escaped) escaped = false
        else if (ch === '\\') escaped = true
        else if (ch === '"') inString = false
        continue
      }
      if (ch === '"') inString = true
      else if (ch === '{') depth += 1
      else if (ch === '}') {
        depth -= 1
        if (depth === 0) {
          end = index
          break
        }
      }
    }
    if (end < 0) continue
    try {
      candidates.push(JSON.parse(trimmed.slice(start, end + 1)))
      start = end
    } catch {
      /* 不是合法 JSON，继续从下一个 { 找 */
    }
  }
  return candidates.length ? candidates[candidates.length - 1] : undefined
}

/**
 * 运行 scripts/ 下的脚本并取回 JSON。
 *
 * 与 mech-workbench 的差异：脚本的 `ok:false` **不抛异常**，原样返回，
 * 让工具把结构化错误（code/details）交给模型；只有进程级失败（起不来、
 * 超时、输出不是 JSON）才抛 Error。
 *
 * @param {string} script scripts 下的文件名
 * @param {object} spec 传给脚本的 JSON
 * @param {object} [options] timeoutMs / requirement / python / cwd / env
 * @returns {Promise<object>}
 */
export async function runScript(script, spec, options = {}) {
  const timeoutMs = options.timeoutMs ?? 180000
  const requirement = options.requirement ?? 'base'
  const python = options.python ?? (await resolvePython(requirement))
  const args = cache.get(`${requiredModules(requirement).join(',')}::args`) ?? []
  const dir = await mkdtemp(join(tmpdir(), 'dsh-cad-'))
  const specPath = join(dir, 'spec.json')
  try {
    await writeFile(specPath, JSON.stringify(spec ?? {}), 'utf8')
    const run = await runProcess(python, [...args, join(SCRIPTS_DIR, script), '--spec', specPath], {
      dir,
      timeoutMs,
      cwd: options.cwd,
      env: options.env,
    })
    if (run.spawnError) throw scriptError('PYTHON_UNAVAILABLE', `无法启动 ${script}：${run.spawnError.message ?? run.spawnError}`)
    if (run.timedOut) throw scriptError('TIMEOUT', `${script} 执行超过 ${Math.round(timeoutMs / 1000)} 秒，已终止`)
    const parsed = parseJsonObject(run.stdout)
    if (!parsed) {
      const tail = String(run.stderr ?? '').trim().slice(-1200)
      throw scriptError('BAD_SCRIPT_OUTPUT', `${script} 没有输出可解析的 JSON（退出码 ${run.code}）。stderr：${tail || '(空)'}`)
    }
    if (parsed.ok === false && run.code && run.code !== 0) {
      parsed._exit_code = run.code
    }
    if (run.stderr && String(run.stderr).trim()) {
      parsed._stderr_tail = String(run.stderr).trim().slice(-1500)
    }
    return parsed
  } finally {
    await rm(dir, { recursive: true, force: true }).catch(() => {})
  }
}

/** 带错误码的脚本调用异常（工具层据此返回 TIMEOUT/PYTHON_UNAVAILABLE 等）。 */
function scriptError(code, message) {
  const error = new Error(message)
  error.code = code
  return error
}

/** 插件根目录（脚本定位 rules/skills 用）。 */
export const pluginRoot = PLUGIN_ROOT
/** scripts 目录。 */
export const scriptsDir = SCRIPTS_DIR
