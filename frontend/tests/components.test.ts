// @vitest-environment jsdom
// 组件测试：展示契约即产品行为——时间线补偿检索徽标、答案卡流光、Markdown 消毒。
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import EventTimeline from '@/components/EventTimeline.vue'
import AnswerCard from '@/components/AnswerCard.vue'
import MarkdownBlock from '@/components/MarkdownBlock.vue'
import CitationsPanel from '@/components/CitationsPanel.vue'
import type { Citation } from '@/types/events'

describe('EventTimeline', () => {
  it('空轨迹不渲染', () => {
    expect(mount(EventTimeline, { props: { entries: [] } }).find('.timeline').exists()).toBe(false)
  })

  it('节点中文名 + 延迟 + 合计', () => {
    const w = mount(EventTimeline, {
      props: { entries: [{ node: 'retrieve', latency_ms: 30 }, { node: 'verify', latency_ms: 12 }] },
    })
    expect(w.text()).toContain('混合检索')
    expect(w.text()).toContain('引用校验')
    expect(w.text()).toContain('30ms')
    expect(w.text()).toContain('合计 42ms')
  })

  it('retrieve 第二次出现打 ×2 补偿徽标', () => {
    const w = mount(EventTimeline, {
      props: {
        entries: [
          { node: 'retrieve', latency_ms: 30 },
          { node: 'retrieve', latency_ms: 28 },
        ],
      },
    })
    const retry = w.findAll('.tl-retry')
    expect(retry).toHaveLength(1)
    expect(retry[0].text()).toBe('×2')
  })
})

describe('AnswerCard', () => {
  const base = { content: '毛利率 32.5% [c1]', streaming: false, citationCount: 1, retryCount: 0 }

  it('非流式：无光标，显示耗时与引用数', () => {
    const w = mount(AnswerCard, { props: { ...base, totalMs: 90.5 } })
    expect(w.find('.stream-caret').exists()).toBe(false)
    expect(w.text()).toContain('90.5 ms')
    expect(w.text()).toContain('引用 × 1')
  })

  it('流式：显示"生成中"与打字光标', () => {
    const w = mount(AnswerCard, { props: { ...base, streaming: true } })
    expect(w.find('.stream-caret').exists()).toBe(true)
    expect(w.text()).toContain('生成中')
  })

  it('补偿检索次数 >0 时展示', () => {
    const w = mount(AnswerCard, { props: { ...base, retryCount: 2 } })
    expect(w.text()).toContain('补偿检索 2 次')
  })
})

describe('MarkdownBlock（LLM 输出消毒）', () => {
  it('渲染 Markdown 加粗与表格', () => {
    const w = mount(MarkdownBlock, { props: { content: '**加粗**\n\n| a | b |\n|---|---|\n| 1 | 2 |' } })
    expect(w.find('strong').text()).toBe('加粗')
    expect(w.find('table').exists()).toBe(true)
  })

  it('剥掉 script 与 onerror（XSS 不执行）', () => {
    const w = mount(MarkdownBlock, {
      props: { content: '<img src=x onerror="window.__xss=1">' },
    })
    expect(w.html()).not.toContain('onerror')
    expect((window as unknown as { __xss?: number }).__xss).toBeUndefined()
  })
})

describe('CitationsPanel', () => {
  const cite: Citation = {
    citation_id: 'c1', source: '2025年报.pdf', section_path: '第三节',
    text: '毛利率 32.5%', score: 0.83,
  }

  it('空引用不渲染面板', () => {
    expect(mount(CitationsPanel, { props: { citations: [] } }).find('.citations').exists()).toBe(false)
  })

  it('渲染序号/来源/章节/分数与占比条', () => {
    const w = mount(CitationsPanel, { props: { citations: [cite, { ...cite, citation_id: 'c2', score: 0.4 }] } })
    expect(w.text()).toContain('2025年报.pdf')
    expect(w.text()).toContain('第三节')
    expect(w.text()).toContain('0.83')
    expect(w.text()).toContain('0.40')
    const bars = w.findAll('.score-bar')
    expect(bars).toHaveLength(2)
    // 最高分（0.83）= 100% 基准，0.4 ≈ 48%
    expect(bars[1].attributes('style')).toContain('48%')
  })
})
