// ===== 轮次生命周期：一轮问答的唯一所有者 =====
// 为什么存在：取消/超时/终态曾经散落在 composable 闭包里（controller/timer/flags），
// 计时器能活得比轮次更久、取消要赌 abort 传播——三次"修好的取消"都没锁死行为。
// 现在：一个 Round 独占 AbortController 与 deadline，只有三个出口：
//   settle('done' | 'cancelled' | 'failed') —— 幂等，settle 后计时器必须死。
// 纯模块：不 import vue、不碰 DOM；定时器可注入 → 单测锁死每个行为。

export type Settlement = "done" | "cancelled" | "failed"

type TimerHandle = unknown

export interface RoundDeps {
  setTimeout?: (fn: () => void, ms: number) => TimerHandle
  clearTimeout?: (h: TimerHandle) => void
}

export interface RoundOptions extends RoundDeps {
  /** 多久没有新事件就判死（mock 全链路 <2s，真实 LLM 一轮也远小于此） */
  inactivityMs: number
  /** deadline 到：调用方负责把 UI 收口（Round 随后自动 settle('failed')） */
  onInactivity: () => void
  /** 终态回调（含 cancelled/failed/done），只触发一次 */
  onSettle?: (kind: Settlement) => void
}

export interface Round {
  readonly id: number
  readonly signal: AbortSignal
  readonly settled: Settlement | null
  /** 开始/重置 deadline（每个事件都是一次 touch） */
  arm(): void
  /** 停表但不 abort——供"被新一轮取代"使用 */
  disarm(): void
  /** 幂等终态：停表 + abort + 回调 */
  settle(kind: Settlement): void
  /** 取消 = 同步收口，不依赖 abort 是否传播 */
  cancel(): void
}

let roundSeq = 0

export function createRound(opts: RoundOptions): Round {
  const setT = opts.setTimeout ?? ((fn, ms) => setTimeout(fn, ms))
  const clearT = opts.clearTimeout ?? ((h) => clearTimeout(h as never))
  const controller = new AbortController()
  let handle: TimerHandle | undefined
  let settled: Settlement | null = null
  const id = ++roundSeq

  function disarm(): void {
    if (handle !== undefined) {
      clearT(handle)
      handle = undefined
    }
  }

  function arm(): void {
    if (settled) return // 终态后不再武装
    disarm()
    handle = setT(() => {
      // deadline 到：先让调用方收口 UI，再自行终态。
      // 注意这里不能只 abort——黑对端场景 abort 可能永不兑现。
      opts.onInactivity()
      settle("failed")
    }, opts.inactivityMs)
  }

  function settle(kind: Settlement): void {
    if (settled) return // 幂等：取消/超时/正常完成只认第一个
    settled = kind
    disarm()
    controller.abort()
    opts.onSettle?.(kind)
  }

  return {
    id,
    signal: controller.signal,
    get settled() {
      return settled
    },
    arm,
    disarm,
    settle,
    cancel: () => settle("cancelled"),
  }
}
