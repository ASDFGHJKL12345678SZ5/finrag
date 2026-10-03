import { reactive, ref, onScopeDispose } from 'vue'
import { askReducer, initialState, type AskState } from '@/services/reducer'
import { askStream, ApiError } from '@/services/api'
import { createRound, type Round } from '@/services/round'
import { createHealthMonitor, type HealthDeps, type HealthMonitor, type HealthState } from '@/services/health'

// Vue 薄接线层（与 datacrew 同构）：轮次/健康在纯模块（有单测），这里只接线。

const INACTIVITY_MS = 30_000
const HEALTH_INTERVAL_MS = 15_000
const HEALTH_TIMEOUT_MS = 6_000
const LIVE_PHASES = ['running', 'streaming']

function probeHealth(signal: AbortSignal): Promise<boolean> {
  return fetch('/health', { signal })
    .then((r) => r.ok)
    .catch(() => false)
}

export function useAsk() {
  const state = ref<AskState>({ ...initialState })
  const running = ref(false)
  const errorMessage = ref('')
  const sessionId = ref(localStorage.getItem('finrag.sessionId') || 'demo-001')
  let round: Round | null = null

  const healthOptions: HealthDeps = {
    probe: probeHealth,
    intervalMs: HEALTH_INTERVAL_MS,
    probeTimeoutMs: HEALTH_TIMEOUT_MS,
  }
  const health: HealthMonitor = createHealthMonitor(healthOptions)
  // Vue 层自持响应式状态，纯模块经回调通知（不能 reactive(health.state)：
  // 纯模块按原始引用改对象，代理 setter 不触发——界面永远停在“探测中”，
  // 见 tests/health.reactive.test.ts 的回归用例）。
  const healthState = reactive<HealthState>({ ...health.state })
  healthOptions.onStateChange = (s: HealthState) => Object.assign(healthState, s)
  health.start()
  onScopeDispose(() => health.stop())

  function setSessionId(v: string) {
    sessionId.value = v
    localStorage.setItem('finrag.sessionId', v)
  }

  function fail(msg: string) {
    errorMessage.value = msg
    state.value = askReducer(state.value, { type: 'event', ev: { event: 'error', detail: msg } as never })
  }

  function onSettle(kind: string) {
    if (kind === 'cancelled' && LIVE_PHASES.includes(state.value.phase)) {
      state.value = askReducer(state.value, { type: 'cancel' })
    }
    running.value = false
    round = null
  }

  async function run(gen: AsyncGenerator<never>): Promise<void> {
    running.value = true
    errorMessage.value = ''
    const r = round
    if (!r) return
    r.arm()
    try {
      for await (const ev of gen) {
        if (r.settled) return
        state.value = askReducer(state.value, { type: 'event', ev })
        r.arm()
      }
      if (r.settled !== 'cancelled' && state.value.phase === 'running') {
        fail('连接中断：流已结束但未收到终态事件（answer / refuse / error）。请重试或检查后端')
      }
    } catch (e) {
      if (r.settled) return
      const msg = e instanceof ApiError ? `HTTP ${e.status}: ${e.message}` : String(e)
      fail(msg)
      r.settle('failed')
    } finally {
      if (r.settled === null) r.settle('done')
    }
  }

  function newRound(): Round {
    round?.disarm()
    const r = createRound({
      inactivityMs: INACTIVITY_MS,
      onInactivity: () => fail(`连接 ${INACTIVITY_MS / 1000} 秒无新事件，已自动断开（后端无响应或连接被中断）`),
      onSettle,
    })
    round = r
    return r
  }

  function ask(question: string) {
    state.value = askReducer(state.value, { type: 'start', question })
    const r = newRound()
    return run(askStream(question, sessionId.value, r.signal) as never)
  }

  function cancel() {
    round?.cancel()
  }

  function reset() {
    round?.settle('failed')
    state.value = askReducer(state.value, { type: 'reset' })
    errorMessage.value = ''
    running.value = false
  }

  return { state, running, errorMessage, sessionId, setSessionId, healthState, ask, cancel, reset }
}
