/**
 * Feature Graph 的 JS 侧轻量归一化。
 *
 * 真正的校验（字段、范围、闭环、引用）只有一份实现：scripts/graph.py。
 * 这里只做「能不能交给 Python」的形状检查，避免把垃圾 JSON 传过去。
 *
 * @module dsh-cad-workbench/lib/graph
 */

import { createHash } from 'node:crypto'

export const GRAPH_UNITS = 'mm'
export const SUPPORTED_OPS = Object.freeze([
  'sketch', 'extrude', 'cut', 'revolve', 'hole', 'fillet', 'chamfer',
])

/**
 * 轻量归一化：检查顶层形状与节点数组，补默认 units。
 * @param {unknown} raw
 * @returns {{units: string, nodes: object[]}}
 */
export function normalizeGraph(raw) {
  let graph = raw
  if (typeof graph === 'string') {
    try {
      graph = JSON.parse(graph)
    } catch {
      throw graphError('graph 不是合法 JSON 字符串')
    }
  }
  if (!graph || typeof graph !== 'object' || Array.isArray(graph)) {
    throw graphError('graph 必须是对象：{ units:"mm", nodes:[...] }')
  }
  const units = graph.units ?? GRAPH_UNITS
  if (units !== GRAPH_UNITS) {
    throw graphError(`graph.units 只支持 "mm"，收到 ${JSON.stringify(units)}`)
  }
  if (!Array.isArray(graph.nodes) || graph.nodes.length === 0) {
    throw graphError('graph.nodes 必须是非空数组')
  }
  if (graph.nodes.length > 200) throw graphError('graph.nodes 最多 200 个节点')
  for (const [index, node] of graph.nodes.entries()) {
    if (!node || typeof node !== 'object' || Array.isArray(node)) {
      throw graphError(`graph.nodes[${index}] 必须是对象`)
    }
    if (typeof node.op !== 'string') {
      throw graphError(`graph.nodes[${index}].op 缺失或不是字符串`)
    }
    if (!SUPPORTED_OPS.includes(node.op)) {
      throw graphError(
        `graph.nodes[${index}].op="${node.op}" 不支持；可用 ${SUPPORTED_OPS.join(' / ')}（shell/pattern 计划在 v0.2）`,
      )
    }
  }
  return { units, nodes: graph.nodes }
}

/** 规范 JSON（键排序）→ sha256，用于台账比对同一张图。 */
export function graphHash(graph) {
  return createHash('sha256').update(stableStringify(graph), 'utf8').digest('hex')
}

/** 键排序的 JSON。 */
export function stableStringify(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null'
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`
  const keys = Object.keys(value).sort()
  return `{${keys.map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(',')}}`
}

function graphError(message) {
  const error = new Error(message)
  error.code = 'GRAPH_INVALID'
  return error
}
