// postSse 流式客户端集成测试（node 环境，与 datacrew 前端同款回归）。
// 背景：2026-10-03 线上事故——wake/notify 跨块信号量死锁，首块 yield 完后生成器
// 永不收敛，30s 看门狗把已显示的答案误报成"无新事件"错误。本项目同款代码同款修。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { askStream } from '@/services/api'

function stubChunked(chunks: string[], gapMs = 2) {
  vi.stubGlobal('fetch', vi.fn(async () => {
    const enc = new TextEncoder()
    let i = 0
    return {
      ok: true,
      body: {
        getReader: () => ({
          read: async () => {
            if (i < chunks.length) {
              const value = enc.encode(chunks[i++])
              if (gapMs > 0) await new Promise((r) => setTimeout(r, gapMs))
              return { done: false, value }
            }
            return { done: true, value: undefined }
          },
          cancel: async () => undefined,
        }),
      },
    } as unknown as Response
  }))
}

async function collect<T>(gen: AsyncGenerator<T>, timeoutMs = 2000): Promise<T[]> {
  const out: T[] = []
  await Promise.race([
    (async () => {
      for await (const ev of gen) out.push(ev)
    })(),
    new Promise((_, rej) => setTimeout(() => rej(new Error('生成器未收敛（死锁回归！）')), timeoutMs)),
  ])
  return out
}

// finrag 帧是 data-only：事件类型在 payload 里
function dframes(...types: string[]): string {
  return types.map((t) => `data: {"event":"${t}"}\n\n`).join('')
}

describe('finrag askStream 多分片流', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('回归：首块之后仍有数据（answer 中继 + status 终帧跨块）——必须全程收敛', async () => {
    stubChunked([
      dframes('node', 'answer'),   // 第 1 块：节点 + 首个 answer 中继快照
      dframes('answer'),           // 第 2 块：后续快照
      dframes('status'),           // 第 3 块：终态
    ])
    const evs = await collect(askStream('营业收入是多少', 's1'))
    expect(evs.map((e) => e.event)).toEqual(['node', 'answer', 'answer', 'status'])
  })

  it('一帧被拆在两个块中间（增量解析）', async () => {
    stubChunked(['data: {"event":"an', 'swer"}\n\n', 'data: {"event":"status"}\n\n'])
    const evs = await collect(askStream('q', 's1'))
    expect(evs.map((e) => e.event)).toEqual(['answer', 'status'])
  })

  it('心跳/注释帧与非 JSON 帧被忽略，不影响收敛', async () => {
    stubChunked([': ping\n\n', 'data: not-json\n\n', dframes('status')])
    const evs = await collect(askStream('q', 's1'))
    expect(evs.map((e) => e.event)).toEqual(['status'])
  })

  it('流以半帧结束：不产出假事件，生成器仍然收敛', async () => {
    stubChunked([dframes('status'), 'data: {"event":"ans'])
    const evs = await collect(askStream('q', 's1'))
    expect(evs.map((e) => e.event)).toEqual(['status'])
  })

  it('单块内含多帧：同轮全部 yield', async () => {
    stubChunked([dframes('node', 'node', 'answer', 'status')])
    const evs = await collect(askStream('q', 's1'))
    expect(evs.map((e) => e.event)).toEqual(['node', 'node', 'answer', 'status'])
  })
})
