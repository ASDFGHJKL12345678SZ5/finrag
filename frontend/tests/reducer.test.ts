import { describe, expect, it } from 'vitest'
import { askReducer, initialState } from '@/services/reducer'

describe('finrag askReducer', () => {
  it('节点事件进时间线', () => {
    let s = askReducer(initialState, { type: 'start', question: '毛利率是多少' })
    s = askReducer(s, { type: 'event', ev: { event: 'node', node: 'retrieve', latency_ms: 12.5 } })
    expect(s.timeline[0]).toMatchObject({ node: 'retrieve', latency_ms: 12.5 })
    expect(s.phase).toBe('running')
  })

  it('answer 中继帧拼接、终态帧带 citations 后落定 done', () => {
    let s = askReducer(initialState, { type: 'start', question: 'q' })
    s = askReducer(s, { type: 'event', ev: { event: 'answer', answer: '宁德' } })
    expect(s.phase).toBe('streaming')
    expect(s.streamed).toBe('宁德')
    s = askReducer(s, { type: 'event', ev: { event: 'answer', answer: '润能的毛利率为 32.5%。[c1]' } })
    expect(s.phase).toBe('streaming')
    s = askReducer(s, {
      type: 'event',
      ev: {
        event: 'answer', answer: '', total_ms: 90.2,
        citations: [{ citation_id: 'c1', source: '2025年报.pdf', section_path: '第三节', text: '毛利率 32.5%', score: 0.83 }],
      },
    })
    expect(s.phase).toBe('done')
    expect(s.streamed).toContain('宁德')
    expect(s.citations[0].score).toBe(0.83)
    expect(s.totalMs).toBe(90.2)
  })

  it('refuse 事件落定 refused 并保留 verdicts', () => {
    let s = askReducer(initialState, { type: 'start', question: 'q' })
    s = askReducer(s, {
      type: 'event',
      ev: {
        event: 'refuse', reason: '证据不足或引用校验未通过',
        verdicts: [{ claim: '毛利率 99%', citation_id: 'c1', supported: false, missing: ['99%'] }],
        total_ms: 40,
      },
    })
    expect(s.phase).toBe('refused')
    expect(s.verdicts[0].supported).toBe(false)
    expect(s.verdicts[0].missing).toEqual(['99%'])
  })

  it('status 事件更新 retry_count', () => {
    let s = askReducer(initialState, { type: 'start', question: 'q' })
    s = askReducer(s, { type: 'event', ev: { event: 'status', status: 'done', retry_count: 1 } })
    expect(s.retryCount).toBe(1)
    expect(s.status).toBe('done')
  })

  it('error 事件（后端 detail 字段）', () => {
    let s = askReducer(initialState, { type: 'start', question: 'q' })
    s = askReducer(s, { type: 'event', ev: { event: 'error', detail: '连接池耗尽' } })
    expect(s.phase).toBe('error')
    expect(s.error).toBe('连接池耗尽')
  })
})
