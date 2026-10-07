// FinRAG 后端客户端（无鉴权；CORS 后端已放开，dev 仍走 proxy）。
import { createSseParser, type SseFrame } from './sse'
import type { RagEvent } from '@/types/events'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

/** API 基址：dev 留空走 Vite proxy；生产由 VITE_API_BASE 指向网关。
 *  导出它是为了让健康探测（useHealth）打到同一个后端。 */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? ''

export async function* askStream(
  question: string,
  sessionId: string,
  signal?: AbortSignal,
): AsyncGenerator<RagEvent> {
  const res = await fetch(API_BASE + '/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, session_id: sessionId }),
    signal,
  })
  if (!res.ok || !res.body) {
    throw new ApiError(res.status, await safeText(res))
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  const queue: RagEvent[] = []

  const parser = createSseParser((frame: SseFrame) => {
    if (!frame.data) return
    let payload: Record<string, unknown>
    try {
      payload = JSON.parse(frame.data)
    } catch {
      return
    }
    // finrag：event 类型在 payload 里（帧无 event: 行）
    queue.push(payload as unknown as RagEvent)
  })

  // 朴素 read 循环：解析器同步，块内多帧同轮 yield 完，块间靠 read() 天然同步。
  // 曾经的坑（与 datacrew 前端同款事故）：wake/notify 跨块信号量会死锁——notify 只在
  // feed 时触发，feed 只在 read 之后，循环却在等 wake 才 read：首个块 yield 完生成器
  // 永不收敛，30s 看门狗把已显示的答案误报成错误。回归见 tests/api.stream.test.ts。
  try {
    while (true) {
      const { value, done: streamDone } = await reader.read()
      if (value) parser.feed(decoder.decode(value, { stream: true }))
      while (queue.length > 0) yield queue.shift() as RagEvent
      if (streamDone) break
    }
    const tail = decoder.decode()
    if (tail) parser.feed(tail)
    while (queue.length > 0) yield queue.shift() as RagEvent
  } finally {
    reader.cancel().catch(() => undefined)
  }
}

async function safeText(res: Response): Promise<string> {
  try {
    return await res.text()
  } catch {
    return res.statusText
  }
}
