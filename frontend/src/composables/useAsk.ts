import { ref, onScopeDispose } from 'vue'
import { askReducer, initialState, type AskState } from '@/services/reducer'
import { askStream, ApiError } from '@/services/api'
import { createRound, type Round, type Settlement } from '@/services/round'
import { getSessionId, setSessionId, onSessionChange } from '@/services/session'
import { useHealth } from '@/composables/useHealth'
import type { RagEvent } from '@/types/events'

// Vue 薄接线层（与 datacrew 同构）：轮次/健康在纯模块（有单测），这里只接线。
// 重构点：
//   1. 修掉 run(gen: AsyncGenerator<never>) + 满屏 as never 的类型涂脂荡粉——
//      事件类型本来就是 RagEvent，直抒胸臆；
//   2. 健康探测改用全局单例 useHealth()（报头灯与提问门控同源）；
//   3. 会话 ID 走 services/session.ts 单一事实源。

const INACTIVITY_MS = 30_000
const LIVE_PHASES = ['running', 'streaming']

export function useAsk() {
  const state = ref<AskState>({ ...initialState })
  const running = ref(false)
  const errorMessage = ref('')
  const sessionId = ref(getSessionId())
  const offSession = onSessionChange((v) => { sessionId.value = v })
  onScopeDispose(offSession)

  // 健康：全局单例（报头状态灯与提问门控共用同一个探测实程）
  const { state: healthState, isDown: healthDown, probeNow } = useHealth()

  function setSessionIdValue(v: string) {
    setSessionId(v)
  }

  function fail(msg: string) {
    errorMessage.value = msg
    state.value = askReducer(state.value, { type: 'event', ev: { event: 'error', detail: msg } })
  }

  function onSettle(kind: Settlement) {
    if (kind === 'cancelled' && LIVE_PHASES.includes(state.value.phase)) {
      state.value = askReducer(state.value, { type: 'cancel' })
    }
    running.value = false
    round = null
  }

  async function run(gen: AsyncGenerator<RagEvent>): Promise<void> {
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
      if (r.settled !== 'cancelled' && (state.value.phase === 'running' || state.value.phase === 'streaming')) {
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

  let round: Round | null = null

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
    return run(askStream(question, sessionId.value, r.signal))
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

  return { state, running, errorMessage, sessionId, setSessionId: setSessionIdValue, healthState, healthDown, probeNow, ask, cancel, reset }
}
