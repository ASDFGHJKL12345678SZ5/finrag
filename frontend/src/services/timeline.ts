// ===== 执行时间线格式化（纯函数） =====
// 与服务 datacrew 同名模块同构：节点中文名/延迟人性化/detail 扁平化都是
// 可单测的展示规则，不放组件里。节点名按 finrag 问答图（app/rag/graph.py）登记。
import type { TimelineEntry } from '@/services/reducer'

export const NODE_LABELS: Record<string, string> = {
  understand_query: '查询理解',
  retrieve: '混合检索',
  generate: '带引用生成',
  verify: '引用校验',
  rewrite_query: '查询改写',
}

export const NODE_TONES: Record<string, string> = {
  understand_query: 'teal',
  retrieve: 'accent',
  generate: 'violet',
  verify: 'gold',
  rewrite_query: 'warn',
}

export interface TimelineRow {
  key: string
  node: string
  label: string
  tone: string
  attempt: number
  latency_ms?: number
  detail: [string, string][]
}

export function formatLatency(ms?: number): string {
  if (ms == null || Number.isNaN(ms)) return '—'
  if (ms < 1000) return Math.round(ms) + 'ms'
  return (ms / 1000).toFixed(1) + 's'
}

export function flattenDetail(detail?: Record<string, unknown>, maxLen = 48): [string, string][] {
  if (!detail) return []
  const out: [string, string][] = []
  for (const [k, v] of Object.entries(detail)) {
    if (v === null || v === undefined) continue
    let s: string
    if (typeof v === 'object') s = JSON.stringify(v)
    else s = String(v)
    if (s.length > maxLen) s = s.slice(0, maxLen - 1) + '…'
    out.push([k, s])
  }
  return out
}

export function totalLatency(rows: TimelineRow[]): number | undefined {
  if (rows.length === 0) return undefined
  const nums = rows.map((r) => r.latency_ms).filter((n): n is number => typeof n === 'number')
  if (nums.length === 0) return undefined
  return nums.reduce((a, b) => a + b, 0)
}

export function buildTimelineRows(entries: TimelineEntry[]): TimelineRow[] {
  const seen = new Map<string, number>()
  return entries.map((e) => {
    const n = (seen.get(e.node) ?? 0) + 1
    seen.set(e.node, n)
    return {
      key: e.node + '#' + n,
      node: e.node,
      label: NODE_LABELS[e.node] ?? e.node,
      tone: NODE_TONES[e.node] ?? 'accent',
      attempt: n,
      latency_ms: e.latency_ms,
      detail: flattenDetail(e.detail),
    }
  })
}
