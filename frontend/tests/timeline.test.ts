import { describe, expect, it } from 'vitest'
import { buildTimelineRows, flattenDetail, formatLatency, totalLatency } from '@/services/timeline'

describe('finrag formatLatency', () => {
  it('毫秒/秒人性化 + 缺失占位', () => {
    expect(formatLatency(12.5)).toBe('13ms')
    expect(formatLatency(2340)).toBe('2.3s')
    expect(formatLatency(undefined)).toBe('—')
  })
})

describe('finrag flattenDetail', () => {
  it('标量保留、null 跳过、嵌套转 JSON、超长截断', () => {
    expect(flattenDetail({ n: 5, x: null })).toEqual([['n', '5']])
    const rows = flattenDetail({ meta: { a: 1 }, long: 'y'.repeat(60) })
    expect(rows[0]).toEqual(['meta', '{"a":1}'])
    expect(rows[1][1].endsWith('…')).toBe(true)
  })
})

describe('finrag buildTimelineRows', () => {
  it('节点中文名按问答图登记', () => {
    const rows = buildTimelineRows([
      { node: 'understand_query' },
      { node: 'retrieve' },
      { node: 'generate' },
      { node: 'verify' },
    ])
    expect(rows.map((r) => r.label)).toEqual(['查询理解', '混合检索', '带引用生成', '引用校验'])
  })

  it('retrieve 两次出现 = 补偿检索，attempt 序号可见', () => {
    const rows = buildTimelineRows([
      { node: 'retrieve', latency_ms: 30 },
      { node: 'retrieve', latency_ms: 28 },
    ])
    expect(rows.map((r) => r.attempt)).toEqual([1, 2])
    expect(rows[1].key).toBe('retrieve#2')
  })

  it('未登记节点原样透传', () => {
    expect(buildTimelineRows([{ node: 'future_node' }])[0].label).toBe('future_node')
  })

  it('totalLatency 汇总', () => {
    const rows = buildTimelineRows([
      { node: 'retrieve', latency_ms: 30 },
      { node: 'generate', latency_ms: 70 },
    ])
    expect(totalLatency(rows)).toBe(100)
    expect(totalLatency([])).toBeUndefined()
  })
})
