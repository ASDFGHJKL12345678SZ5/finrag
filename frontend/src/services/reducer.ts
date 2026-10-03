// 一次问答轮次的状态机（纯函数，可单测）。
// 与 datacrew 的 reducer 同构，差异只有事件集合。
import type { RagEvent, Citation, Verdict, NodeEvent, StatusEvent, AnswerDeltaEvent, RefuseEvent, ErrorEvent } from '@/types/events'

export type Phase = 'idle' | 'running' | 'streaming' | 'done' | 'refused' | 'error'

export interface TimelineEntry {
  node: string
  latency_ms?: number
  detail?: Record<string, unknown>
}

export interface AskState {
  phase: Phase
  question: string
  timeline: TimelineEntry[]
  streamed: string      // 流式增量拼接（answer 中继帧）
  citations: Citation[] // 终态才有
  verdicts: Verdict[]
  refusalReason: string
  status: string
  retryCount: number
  totalMs?: number
  error?: string
}

export const initialState: AskState = {
  phase: 'idle',
  question: '',
  timeline: [],
  streamed: '',
  citations: [],
  verdicts: [],
  refusalReason: '',
  status: '',
  retryCount: 0,
}

export type AskAction =
  | { type: 'start'; question: string }
  | { type: 'event'; ev: RagEvent }
  | { type: 'reset' }

export function askReducer(state: AskState, action: AskAction): AskState {
  switch (action.type) {
    case 'start':
      return { ...initialState, phase: 'running', question: action.question }
    case 'reset':
      return initialState
    case 'event':
      return applyEvent(state, action.ev)
  }
}

function applyEvent(state: AskState, ev: RagEvent): AskState {
  switch (ev.event) {
    case 'node': {
      const e = ev as NodeEvent
      return {
        ...state,
        timeline: [...state.timeline, { node: e.node, latency_ms: e.latency_ms, detail: e.detail }],
      }
    }
    case 'status': {
      const e = ev as StatusEvent
      return { ...state, status: e.status, retryCount: e.retry_count ?? 0 }
    }
    case 'answer': {
      const e = ev as AnswerDeltaEvent
      const isFinal = e.citations !== undefined || e.total_ms !== undefined
      return {
        ...state,
        streamed: state.streamed + (e.answer ?? ''),
        phase: isFinal ? 'done' : 'streaming',
        citations: e.citations ?? state.citations,
        totalMs: e.total_ms ?? state.totalMs,
      }
    }
    case 'refuse': {
      const e = ev as RefuseEvent
      return {
        ...state,
        phase: 'refused',
        refusalReason: e.reason,
        verdicts: e.verdicts ?? [],
        streamed: e.answer ?? state.streamed,
        totalMs: e.total_ms ?? state.totalMs,
      }
    }
    case 'error': {
      const e = ev as ErrorEvent
      return { ...state, phase: 'error', error: e.detail }
    }
    default:
      return state
  }
}
