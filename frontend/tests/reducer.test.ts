import { describe, expect, it } from 'vitest'
import { askReducer, initialState } from '@/services/reducer'

// 帧序按后端 app/api/main.py + app/rag/graph.py 的真实产出构造：
// answer 中继帧（每次 generate 后一份全量快照）与终态帧（全量 + citations）
// 都不是 token 增量——reducer 必须整体替换，否则答案重复上屏（已修复的 bug）。
describe('finrag askReducer', () => {
  it('节点事件进时间线', () => {
    let s = askReducer(initialState, { type: 'start', question: '毛利率是多少' })
    s = askReducer(s, { type: 'event', ev: { event: 'node', node: 'retrieve', latency_ms: 12.5 } })
    expect(s.timeline[0]).toMatchObject({ node: 'retrieve', latency_ms: 12.5 })
    expect(s.phase).toBe('running')
  })

  it('answer 帧是快照：中继帧整体替换，终态帧带 citations 后落定 done（不重复拼接）', () => {
    let s = askReducer(initialState, { type: 'start', question: 'q' })
    // 真实后端 happy path 帧序：中继（全量答案）→ 终态（同一份全量答案 + citations）
    s = askReducer(s, { type: 'event', ev: { event: 'answer', answer: '宁德润能的毛利率为 32.5%。[c1]' } })
    expect(s.phase).toBe('streaming')
    expect(s.streamed).toBe('宁德润能的毛利率为 32.5%。[c1]')
    s = askReducer(s, {
      type: 'event',
      ev: {
        event: 'answer', answer: '宁德润能的毛利率为 32.5%。[c1]', total_ms: 90.2,
        citations: [{ citation_id: 'c1', source: '2025年报.pdf', section_path: '第三节', text: '毛利率 32.5%', score: 0.83 }],
      },
    })
    expect(s.phase).toBe('done')
    // 关键断言：答案出现且仅出现一次（旧版拼接语义这里会是两遍）
    expect(s.streamed).toBe('宁德润能的毛利率为 32.5%。[c1]')
    expect(s.citations[0].score).toBe(0.83)
    expect(s.totalMs).toBe(90.2)
  })

  it('补偿检索重试：多份中继快照以最新一份为准', () => {
    let s = askReducer(initialState, { type: 'start', question: 'q' })
    s = askReducer(s, { type: 'event', ev: { event: 'answer', answer: '第一版答案（依据不足）' } })
    s = askReducer(s, { type: 'event', ev: { event: 'status', status: 'insufficient', retry_count: 1 } })
    s = askReducer(s, { type: 'event', ev: { event: 'answer', answer: '第二版答案（改写查询后）' } })
    expect(s.retryCount).toBe(1)
    expect(s.streamed).toBe('第二版答案（改写查询后）') // 不是两版缝在一起
    s = askReducer(s, {
      type: 'event',
      ev: { event: 'answer', answer: '第二版答案（改写查询后）', citations: [], total_ms: 210.5 },
    })
    expect(s.phase).toBe('done')
    expect(s.streamed).toBe('第二版答案（改写查询后）')
  })

  it('refuse 事件落定 refused 并保留 verdicts（answer 快照同步替换）', () => {
    let s = askReducer(initialState, { type: 'start', question: 'q' })
    s = askReducer(s, { type: 'event', ev: { event: 'answer', answer: '带病答案草稿' } })
    s = askReducer(s, {
      type: 'event',
      ev: {
        event: 'refuse', reason: '证据不足或引用校验未通过',
        verdicts: [{ claim: '毛利率 99%', citation_id: 'c1', supported: false, missing: ['99%'] }],
        answer: '带病答案草稿', total_ms: 40,
      },
    })
    expect(s.phase).toBe('refused')
    expect(s.verdicts[0].supported).toBe(false)
    expect(s.verdicts[0].missing).toEqual(['99%'])
    expect(s.streamed).toBe('带病答案草稿')
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

  it('start 清空上一轮（轨迹/答案/引用全重置）', () => {
    let s = askReducer(initialState, { type: 'start', question: 'q1' })
    s = askReducer(s, { type: 'event', ev: { event: 'answer', answer: '某答案', total_ms: 10, citations: [] } })
    expect(s.phase).toBe('done')
    s = askReducer(s, { type: 'start', question: 'q2' })
    expect(s.phase).toBe('running')
    expect(s.streamed).toBe('')
    expect(s.timeline).toHaveLength(0)
    expect(s.citations).toHaveLength(0)
  })
})
