/**
 * 工具注册与返回值基础设施。
 *
 * DSH 对工具返回值做「无损 JSON」校验：undefined 值的键、NaN/±Infinity/-0、
 * 类实例、稀疏数组、环——任意一条都会让整次调用失败，模型连产物路径都拿不到。
 * 所有工具在 index.js 的注册处统一过 toLossless，新增工具自动受保护。
 *
 * @module dsh-cad-workbench/lib/tooling
 */

/**
 * 把任意返回值净化成可无损 JSON 往返的纯对象。
 * @param {unknown} value
 * @returns {unknown}
 */
export function toLossless(value) {
  return sanitize(value, new WeakSet())
}

function sanitize(value, seen) {
  if (value === null || value === undefined) return null
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null
    return Object.is(value, -0) ? 0 : value
  }
  if (typeof value === 'string' || typeof value === 'boolean') return value
  if (typeof value === 'bigint') return String(value)
  if (typeof value !== 'object') return null
  if (seen.has(value)) return null
  seen.add(value)
  try {
    if (Array.isArray(value)) return value.map((item) => sanitize(item, seen))
    const out = {}
    for (const key of Object.keys(value)) {
      if (value[key] === undefined) continue
      out[key] = sanitize(value[key], seen)
    }
    return out
  } finally {
    seen.delete(value)
  }
}

/**
 * 统一的失败信封：{ ok:false, error:{ code, message, details? } }。
 * @param {string} code
 * @param {string} message
 * @param {object} [details]
 */
export function fail(code, message, details) {
  const error = { code: String(code || 'INTERNAL'), message: String(message || '未知错误') }
  if (details !== undefined && details !== null) error.details = details
  return { ok: false, error }
}

/** 把异常转成失败信封，不把 Python traceback 直接甩给模型。 */
export function asFail(error, fallbackCode = 'INTERNAL') {
  const message = String(error?.message ?? error ?? '未知错误')
  const code = typeof error?.code === 'string' && error.code ? error.code : fallbackCode
  return fail(code, message)
}

/** 结果里是否有内容块所需的图片引用。 */
export function hasImages(value) {
  return Boolean(value && (value.image || value.extraImage))
}

/** Python 输出的退出码 → 错误码映射（脚本自己也会给出 code，这里只兜底）。 */
export const EXIT_CODES = Object.freeze({
  2: 'GRAPH_INVALID',
  3: 'DOC_NOT_FOUND',
  4: 'DOC_BUSY',
  5: 'COM_UNAVAILABLE',
  6: 'BUILD_FAILED',
  7: 'EXPORT_FAILED',
  8: 'UNSUPPORTED',
  9: 'TIMEOUT',
})

/**
 * 由脚本结果构造工具返回值：
 * 脚本自己的 ok:false 原样返回；成功则并入 extra 字段。
 * @param {object} result 脚本打印的 JSON
 * @param {object} [extra]
 */
export function fromScript(result, extra) {
  if (!result || typeof result !== 'object') {
    return fail('BAD_SCRIPT_OUTPUT', '脚本没有返回 JSON 对象')
  }
  if (result.ok === false) return result
  return { ...result, ...(extra ?? {}) }
}
