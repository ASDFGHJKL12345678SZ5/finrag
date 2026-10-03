// ===== 连接健康状态机（不是一个布尔值） =====
// probing → ok | unreachable。unreachable 带 since（界面显示持续多久）。
// 为什么需要超时：容器被 docker pause 时 TCP 握手成功但永不响应，
// 没有超时的探测会永远挂起，健康灯假装还是绿的——用户于是朝黑洞提问。
// 纯模块：定时器与探测函数可注入 → 可单测。

export type HealthStatus = "probing" | "ok" | "unreachable"

export interface HealthState {
  status: HealthStatus
  /** 进入当前状态的时刻（now 注入） */
  since: number
  /** 连续不可达次数 */
  fails: number
}

export interface HealthDeps {
  /** 探测结果变化通知：Vue 层据此更新自己的响应式状态。
   *  为什么用回调而不是让调用方 reactive(state)：纯模块按原始引用改对象，
   *  reactive 代理的 setter 不会触发（实测踩坑），界面会永远停在“探测中”。 */
  onStateChange?: (s: HealthState) => void
  probe: (signal: AbortSignal) => Promise<boolean>
  intervalMs: number
  probeTimeoutMs: number
  setTimeout?: (fn: () => void, ms: number) => unknown
  clearTimeout?: (h: unknown) => void
  now?: () => number
}

export interface HealthMonitor {
  readonly state: HealthState
  start(): void
  stop(): void
  probeNow(): Promise<void>
}

export function createHealthMonitor(deps: HealthDeps): HealthMonitor {
  const setT = deps.setTimeout ?? ((fn, ms) => setTimeout(fn, ms))
  const clearT = deps.clearTimeout ?? ((h) => clearTimeout(h as never))
  const now = deps.now ?? (() => Date.now())
  const state: HealthState = { status: "probing", since: now(), fails: 0 }
  let timer: unknown
  let stopped = true

  async function probeNow(): Promise<void> {
    let ok = false
    try {
      // 探测自带超时：不可达时 6s 内必须给出结论（暂停的容器握手成功但不响应）
      const ctrl = new AbortController()
      const t = setT(() => ctrl.abort(), deps.probeTimeoutMs)
      try {
        ok = await deps.probe(ctrl.signal)
      } finally {
        clearT(t)
      }
    } catch {
      ok = false
    }
    if (stopped) return
    if (ok) {
      state.fails = 0
      if (state.status !== 'ok') {
        state.status = 'ok'
        state.since = now()
      }
    } else {
      state.fails += 1
      if (state.status !== 'unreachable') {
        state.status = 'unreachable'
        state.since = now()
      }
    }
    deps.onStateChange?.(state)
  }

  function schedule(): void {
    if (stopped) return
    timer = setT(() => {
      void probeNow().then(schedule)
    }, deps.intervalMs)
  }

  return {
    state,
    start() {
      if (!stopped) return
      stopped = false
      void probeNow().then(schedule)
    },
    stop() {
      stopped = true
      if (timer !== undefined) clearT(timer)
      timer = undefined
    },
    probeNow,
  }
}
