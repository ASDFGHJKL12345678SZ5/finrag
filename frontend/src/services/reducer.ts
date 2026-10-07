// 一次问答轮次的状态机（纯函数，可单测）。
// 与 datacrew 的 reducer 同构，差异只有事件集合。
//
// ⚠️ answer 帧语义（2024 重构修复的真实 bug）：
// 后端 generate 节点一次性产出**全量答案**（graph.py：单次 LLM 调用、整体返回），
// 因此 API 发出的每种 answer 帧都是完整快照，不是 token 增量——
//   1. 中继帧：每次 generate 执行后发一份全量答案（无 citations 字段）；
//   2. 终态帧：流程结束后再发一份全量答案 + citations + total_ms；
//   3. 校验不过重试时，generate 会再跑，于是有 A1、A2 等多份快照。
// 旧版按"增量拼接"处理，结果 happy path 答案显示两遍、重试时三遍——
// 这是线上实际看到过满屏重复文案的根因。正确语义：**整体替换，以最新快照为准**。
// tests/reducer.test.ts 用真实帧序锁死这条行为。
import type { RagEvent, Citation, Verdict, NodeEvent, StatusEvent, AnswerDeltaEvent, RefuseEvent, ErrorEvent } from '@/types/events'

export type Phase = 'idle' | 'running' | 'streaming' | 'done' | 'refused' | 'error' | 'cancelled'

export interface TimelineEntry {
  node: string
  latency_ms?: number
  detail?: Record<string, unknown>
}

export interface AskState {
  phase: Phase
  question: string
  timeline: TimelineEntry[]
  /** 当前答案快照（最新一份全量，不是增量拼接结果） */
  streamed: string
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
  | { type: 'cancel' }

export function askReducer(state: AskState, action: AskAction): AskState {
  switch (action.type) {
    case 'start':
      return { ...initialState, phase: 'running', question: action.question }
    case 'reset':
      return initialState
    case 'cancel':
      // 主动取消：保留已产出的答案作证据，明确标记未完成
      return { ...state, phase: 'cancelled' }
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
        // 快照语义：整体替换。isFinal 只决定相态与 citations/total_ms 落定，
        // 不决定文本拼接方式——任何时候 streamed 都是"最新一份全量答案"。
        streamed: e.answer ?? '',
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
