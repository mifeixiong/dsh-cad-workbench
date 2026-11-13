/**
 * 会话工作目录、路径守卫、状态目录。
 *
 * Desktop 里进程 cwd 是空的 launch-root（不是会话工作目录），所以不能拿
 * process.cwd() 当工作区；Session 的当前目录由宿主 workingDirectory 服务持有，
 * 能读到就优先用它，读不到才退回 process.cwd()。
 *
 * @module dsh-cad-workbench/lib/session
 */

import { createHash } from 'node:crypto'
import { existsSync, mkdirSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { isAbsolute, join, normalize, resolve, sep } from 'node:path'

/** 会话工作目录（宿主服务优先）。 */
export function sessionDirectory(ctx, exec) {
  const session = exec?.agent?.session
  if (!session) return null
  try {
    const service = typeof ctx?.get === 'function' ? ctx.get('workingDirectory') : ctx?.workingDirectory
    const directory = service?.get?.(session)
    return typeof directory === 'string' && directory.length ? directory : null
  } catch {
    return null
  }
}

/** 工具工作区：显式参数 > 会话目录 > 进程 cwd。 */
export function workspaceOf(args, exec, ctx) {
  if (args?.workspace) return resolve(String(args.workspace))
  return resolve(sessionDirectory(ctx, exec) ?? process.cwd())
}

/** 插件状态目录（锁、解释器缓存）。 */
export function stateDir() {
  const base =
    process.env.CADW_STATE_DIR
    ?? (process.platform === 'win32'
      ? join(process.env.LOCALAPPDATA ?? join(homedir(), 'AppData', 'Local'), 'dsh-cad-workbench')
      : join(process.env.XDG_STATE_HOME ?? join(homedir(), '.local', 'state'), 'dsh-cad-workbench'))
  return base
}

/** 确保目录存在。 */
export function ensureDir(path) {
  mkdirSync(path, { recursive: true })
  return path
}

/** 时间戳（文件名安全）。 */
export function stamp() {
  return new Date().toISOString().replace(/[-:]/g, '').replace(/\..*$/, '')
}

/** 短哈希。 */
export function shortHash(text, length = 12) {
  return createHash('sha256').update(String(text), 'utf8').digest('hex').slice(0, length)
}

/** 供显示用：把绝对路径缩成工作区相对路径。 */
export function displayPath(workspace, path) {
  try {
    const base = resolve(workspace)
    const target = resolve(path)
    if (target === base) return '.'
    if (target.startsWith(base + sep)) return target.slice(base.length + 1)
    return target
  } catch {
    return String(path)
  }
}

/** 禁止写入的系统/安装目录前缀（按平台）。 */
const FORBIDDEN_PREFIXES = process.platform === 'win32'
  ? [
      'c:\\windows',
      'c:\\program files',
      'c:\\program files (x86)',
      'c:\\programdata',
    ]
  : ['/etc', '/usr', '/bin', '/sbin', '/boot', '/sys', '/proc', '/dev', '/var/lib']

/** Windows 风格绝对路径（在 POSIX 上会被当成普通相对路径，必须显式拒绝）。 */
const WINDOWS_ABSOLUTE = /^[A-Za-z]:[\\/]/

/**
 * 路径守卫：默认只允许写工作区内；越界必须显式 allowOutside。
 * 另禁止写 SolidWorks 安装目录与系统目录。
 *
 * @param {string} workspace
 * @param {string} target
 * @param {{allowOutside?: boolean, overwrite?: boolean}} [options]
 * @returns {string} 绝对路径
 * @throws {Error & {code?: string}} 拒绝时抛 PATH_DENIED
 */
export function guardOutputPath(workspace, target, options = {}) {
  const base = resolve(workspace)
  const raw = String(target ?? '')
  if (!raw.trim()) throw pathDenied('输出路径为空')
  if (process.platform !== 'win32' && WINDOWS_ABSOLUTE.test(raw.trim())) {
    throw pathDenied(`当前平台不接受 Windows 绝对路径：${raw}`)
  }
  const absolute = isAbsolute(raw) ? normalize(raw) : resolve(base, raw)
  const lower = absolute.toLowerCase()
  for (const prefix of FORBIDDEN_PREFIXES) {
    if (lower === prefix || lower.startsWith(prefix + sep)) {
      throw pathDenied(`拒绝写入系统/安装目录：${absolute}`)
    }
  }
  const caseFold = process.platform === 'win32' ? (value) => value.toLowerCase() : (value) => value
  const inside = caseFold(absolute) === caseFold(base) || caseFold(absolute).startsWith(caseFold(base) + sep)
  if (!inside && !options.allowOutside) {
    throw pathDenied(
      `输出路径不在工作区内：${absolute}。请使用工作区内的路径，或显式 allow_outside=true。`,
    )
  }
  if (existsSync(absolute) && !options.overwrite) {
    throw pathDenied(`目标已存在，拒绝覆盖：${absolute}。确认覆盖请传 overwrite=true。`)
  }
  return absolute
}

function pathDenied(message) {
  const error = new Error(message)
  error.code = 'PATH_DENIED'
  return error
}

/**
 * 文件名前缀消毒：去掉路径分隔符、.. 与 Windows 非法字符，保留中文。
 * 防止 basename 携带 ../ 逃出 out_dir。
 */
export function sanitizeBasename(value, fallback = 'output') {
  const raw = String(value ?? '').trim()
  if (!raw) return fallback
  const cleaned = raw
    .replace(/[\\/]+/g, '-')
    .replace(/\.\.+/g, '-')
    .replace(/[:*?"<>|]/g, '-')
    .replace(/^[\s.-]+|[\s.-]+$/g, '')
  return cleaned.slice(0, 120) || fallback
}

/** 默认输出目录 <workspace>/cad-output。 */
export function defaultOutputDir(workspace) {
  return join(workspace, 'cad-output')
}

/** 临时目录（测试用）。 */
export function tempDir() {
  return tmpdir()
}
