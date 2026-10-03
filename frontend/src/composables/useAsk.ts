// useAsk：reducer + api 客户端接到 Vue 响应式（只做接线）。
// 健壮性：硬超时看门狗（30s 无事件，UI 必须收口，不依赖 abort 是否传播）+
// EOF 兜底（流结束但还在 running = 终态丢失）+ 取消 = 明确的“已取消”状态。
import { ref } from 'vue'
import { askReducer, initialState, type AskState } from '@/services/reducer'
import { askStream, ApiError } from '@/services/api'

const INACTIVITY_MS = 30_000

export function useAsk() {
  const state = ref<AskState>({ ...initialState })
  const running = ref(false)
  const errorMessage = ref('')
  const sessionId = ref(localStorage.getItem('finrag.sessionId') || 'demo-001')
  let controller: AbortController | null = null

  function setSessionId(v: string) {
    sessionId.value = v
    localStorage.setItem('finrag.sessionId', v)
  }

  function fail(msg: string) {
    errorMessage.value = msg
    state.value = askReducer(state.value, { type: 'event', ev: { event: 'error', detail: msg } as never })
  }

  async function run(gen: AsyncGenerator<never>, own: AbortController): Promise<void> {
    running.value = true
    errorMessage.value = ''
    let timedOut = false
    let timer: number | undefined
    const arm = () => {
      timer = window.setTimeout(() => {
        timedOut = true
        own.abort()
        // 代理层可能吞掉 abort 的传播——UI 在这里无条件收口
        fail(`连接 ${INACTIVITY_MS / 1000} 秒无新事件，已自动断开（后端无响应或连接被中断）`)
      }, INACTIVITY_MS)
    }
    const disarm = () => { if (timer) { window.clearTimeout(timer); timer = undefined } }
    try {
      arm()
      for await (const ev of gen) {
        state.value = askReducer(state.value, { type: 'event', ev })
        disarm(); arm()
      }
      if (state.value.phase === 'running') {
        fail('连接中断：流已结束但未收到终态事件（answer / refuse / error）。请重试或检查后端')
      }
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') {
        if (timedOut) return
        state.value = askReducer(state.value, { type: 'cancel' })
        return
      }
      const msg = e instanceof ApiError ? `HTTP ${e.status}: ${e.message}` : String(e)
      fail(msg)
    } finally {
      disarm()
      running.value = false
      controller = null
    }
  }

  function ask(question: string) {
    state.value = askReducer(state.value, { type: 'start', question })
    controller = new AbortController()
    return run(askStream(question, sessionId.value, controller.signal) as never, controller)
  }

  function cancel() { controller?.abort() }
  function reset() {
    controller?.abort()
    state.value = askReducer(state.value, { type: 'reset' })
    errorMessage.value = ''
    running.value = false
  }

  return { state, running, errorMessage, sessionId, setSessionId, ask, cancel, reset }
}
