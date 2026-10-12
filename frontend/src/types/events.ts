// FinRAG SSE 事件契约（与 app/api/main.py 的 docstring/产出逐字段对齐）。
// 注意：finrag 的帧只有 "data: {json}"（没有 event: 行），event 类型在 JSON 里。

export interface Citation {
  citation_id: string
  source: string
  section_path: string
  text: string
  score: number
}

export interface NodeEvent {
  event: 'node'
  node: string
  latency_ms?: number
  detail?: Record<string, unknown>
}

export interface StatusEvent {
  event: 'status'
  status: string
  retry_count: number
}

export interface AnswerDeltaEvent {
  event: 'answer'
  answer: string
  // 终态帧才带：
  citations?: Citation[]
  total_ms?: number
}

export interface RefuseEvent {
  event: 'refuse'
  reason: string
  // D13：确定性拒答归因码（retrieval_empty / year_not_in_evidence /
  // metric_not_in_evidence），空串=无归因（引用校验未通过）。可选字段，
  // 旧后端不发也有——前端展示以 reason 为准，此字段供未来做分因展示。
  refusal_reason?: string
  verdicts: Verdict[]
  answer?: string
  total_ms?: number
}

export interface ErrorEvent {
  event: 'error'
  detail: string
}

// verify.py 的 ClaimVerdict 扁平化：claim/citation_id/supported/missing
export interface Verdict {
  claim: string
  citation_id: string
  supported: boolean
  missing: string[]
}

export type RagEvent =
  | NodeEvent
  | StatusEvent
  | AnswerDeltaEvent
  | RefuseEvent
  | ErrorEvent
