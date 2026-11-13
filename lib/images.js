/**
 * 把生成的 PNG 变成模型能看到的内容块。
 *
 * 沿用 DSH 第一方 read_image 的约定：execute 阶段经 ctx.get('attachments')
 * 的 saveImage 得到持久附件引用（纯 JSON），render 阶段同步拼装内容块。
 * 附件服务缺失或超限时降级为「仅路径 + 说明」，工具本身永不因此失败。
 *
 * @module dsh-cad-workbench/lib/images
 */

import { readFile } from 'node:fs/promises'
import { basename } from 'node:path'

/**
 * 把 PNG 存成持久附件引用。
 * @param {object} ctx 工具上下文
 * @param {string} path 已写好的 PNG 路径
 * @returns {Promise<{image?: object, note?: string}>}
 */
export async function saveImageRef(ctx, path) {
  let attachments
  try {
    attachments = typeof ctx?.get === 'function' ? ctx.get('attachments') : undefined
  } catch {
    attachments = undefined
  }
  if (!attachments || typeof attachments.saveImage !== 'function') {
    return { note: '当前部署没有附件服务，图片未内联；请按路径查看该文件。' }
  }
  try {
    const data = await readFile(path)
    const limits = attachments.imageLimits
    if (limits?.maxImageBytes && data.byteLength > limits.maxImageBytes) {
      return { note: `图片 ${data.byteLength} 字节超过部署单图上限 ${limits.maxImageBytes}，未内联；请按路径查看。` }
    }
    const ref = await attachments.saveImage({ data, mediaType: 'image/png', name: basename(path) })
    return {
      image: {
        attachmentId: ref.attachmentId,
        mediaType: ref.mediaType,
        bytes: ref.bytes,
        width: ref.width,
        height: ref.height,
        ...(ref.name === undefined ? {} : { name: ref.name }),
        ...(ref.originalDimensions === undefined
          ? {}
          : { originalDimensions: { ...ref.originalDimensions } }),
      },
    }
  } catch (error) {
    return { note: `图片未能附加到本次对话：${error?.message ?? error}。请按路径查看该文件。` }
  }
}

/**
 * 由工具结果值同步拼装内容块。
 * @param {object} value
 * @param {string} text
 * @returns {Array<object>}
 */
export function contentFrom(value, text) {
  const parts = [{ type: 'text', text: String(text ?? '') }]
  if (value?.imageNote) parts.push({ type: 'text', text: `（${value.imageNote}）` })
  if (value?.image) parts.push({ type: 'image', attachment: value.image })
  if (value?.extraImageNote) parts.push({ type: 'text', text: `（${value.extraImageNote}）` })
  if (value?.extraImage) parts.push({ type: 'image', attachment: value.extraImage })
  return parts
}
