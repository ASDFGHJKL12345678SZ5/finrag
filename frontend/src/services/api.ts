// FinRAG 后端客户端（无鉴权；CORS 后端已放开，dev 仍走 proxy）。
import { createSseParser, type SseFrame } from './sse'
import type { RagEvent } from '@/types/events'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

const BASE: string = import.meta.env.VITE_API_BASE ?? ''

export async function* askStream(
  question: string,
  sessionId: string,
  signal?: AbortSignal,
): AsyncGenerator<RagEvent> {
  const res = await fetch(BASE + '/ask', {
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
  let done = false
  let wake: (() => void) | null = null
  const notify = () => { if (wake) { const w2 = wake; wake = null; w2() } }

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
    notify()
  })

  try {
    while (!done) {
      const { value, done: streamDone } = await reader.read()
      done = streamDone
      if (value) parser.feed(decoder.decode(value, { stream: true }))
      while (queue.length > 0) yield queue.shift() as RagEvent
      if (!done) {
        await new Promise<void>((resolve) => { wake = resolve })
      }
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
