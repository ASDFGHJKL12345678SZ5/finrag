// SSE 帧解析器（纯函数）。finrag 的帧没有 event: 行——event 类型在
// data JSON 里（与 datacrew 不同），所以解析后统一返回 SseFrame，由上层取
// frame.data 的 JSON.event。tests/sse.test.ts 覆盖粘包/断帧/CRLF。
export interface SseFrame {
  event?: string
  data: string
}

export function parseFrame(raw: string): SseFrame | null {
  let event: string | undefined
  const dataLines: string[] = []
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''))
    else if (line.startsWith(':') || line === '') continue
  }
  if (event === undefined && dataLines.length === 0) return null
  return { event, data: dataLines.join('\n') }
}

export interface SseParser {
  feed(text: string): void
  reset(): void
}

export function createSseParser(onFrame: (frame: SseFrame) => void): SseParser {
  let buf = ''
  return {
    feed(text: string): void {
      buf += text.replace(/\r\n/g, '\n')
      let idx = buf.indexOf('\n\n')
      while (idx >= 0) {
        const raw = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        const frame = parseFrame(raw)
        if (frame) onFrame(frame)
        idx = buf.indexOf('\n\n')
      }
    },
    reset(): void { buf = '' },
  }
}
