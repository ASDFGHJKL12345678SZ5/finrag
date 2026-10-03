import { describe, expect, it } from 'vitest'
import { createSseParser, parseFrame, type SseFrame } from '@/services/sse'

function collect(chunks: string[]): SseFrame[] {
  const frames: SseFrame[] = []
  const parser = createSseParser((f) => frames.push(f))
  for (const c of chunks) parser.feed(c)
  return frames
}

describe('finrag parseFrame（无 event: 行）', () => {
  it('解析纯 data 帧', () => {
    expect(parseFrame('data: {"event":"node","node":"retrieve"}')).toEqual({
      event: undefined,
      data: '{"event":"node","node":"retrieve"}',
    })
  })

  it('逐字模拟 finrag 后端的帧格式（data + 两个 \n）', () => {
    const frames = collect(['data: {"event":"answer","answer":"毛利率"}\n\n'])
    expect(frames).toHaveLength(1)
    expect((JSON.parse(frames[0].data) as { event: string }).event).toBe('answer')
  })
})

describe('finrag createSseParser', () => {
  it('帧从中间切断也能正确重组', () => {
    const frames = collect(['data: {"event":"no', 'de","node":"veri', 'fy"}\n\n'])
    expect(frames).toHaveLength(1)
    expect(JSON.parse(frames[0].data)).toEqual({ event: 'node', node: 'verify' })
  })

  it('一个 chunk 粘多帧', () => {
    const frames = collect([
      'data: {"event":"node","node":"retrieve"}\n\ndata: {"event":"status","status":"done"}\n\ndata: {"event":"answer","answer":"答"}\n\n',
    ])
    expect(frames.map((f) => JSON.parse(f.data).event)).toEqual(['node', 'status', 'answer'])
  })

  it('CRLF 兼容', () => {
    const frames = collect(['data: {"ok":1}\r\n\r\n'])
    expect(frames).toHaveLength(1)
    expect(JSON.parse(frames[0].data)).toEqual({ ok: 1 })
  })

  it('不完整尾巴不出帧', () => {
    expect(collect(['data: {"a":'])).toHaveLength(0)
    const frames: SseFrame[] = []
    const p = createSseParser((f) => frames.push(f))
    p.feed('data: {"a":')
    p.feed('1}\n\n')
    expect(frames).toHaveLength(1)
    expect(JSON.parse(frames[0].data)).toEqual({ a: 1 })
  })
})
