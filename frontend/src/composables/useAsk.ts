// useAsk：reducer + api 客户端接到 Vue 响应式（只做接线）。
import { ref } from 'vue'
import { askReducer, initialState, type AskState } from '@/services/reducer'
import { askStream, ApiError } from '@/services/api'

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

  async function run(question: string): Promise<void> {
    controller = new AbortController()
    running.value = true
    errorMessage.value = ''
    try {
      for await (const ev of askStream(question, sessionId.value, controller.signal)) {
        state.value = askReducer(state.value, { type: 'event', ev })
      }
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') return
      const msg = e instanceof ApiError ? `HTTP ${e.status}: ${e.message}` : String(e)
      errorMessage.value = msg
      state.value = askReducer(state.value, { type: 'event', ev: { event: 'error', detail: msg } })
    } finally {
      running.value = false
      controller = null
    }
  }

  function ask(question: string) {
    state.value = askReducer(state.value, { type: 'start', question })
    return run(question)
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
