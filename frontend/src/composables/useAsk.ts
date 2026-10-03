// useAsk：reducer + api 客户端接到 Vue 响应式（只做接线）。
// 健壮性三件事（与 datacrew 前端同款，真实事故驱动）：
//  1. signal 发给 fetch——取消按钮必须真的能掐断请求；
//  2. 僵死看门狗：连接被静默掐断（容器重启/代理断）时流既不报错也不结束，
//     N 秒无字节主动 abort 并明确报错；
//  3. EOF 兜底：流正常结束但状态机还停在 running = 终态事件没来，按错误收尾。
import { ref } from 'vue'
import { askReducer, initialState, type AskState } from '@/services/reducer'
import { askStream, ApiError } from '@/services/api'

const INACTIVITY_MS = 60_000

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
      timer = window.setTimeout(() => { timedOut = true; own.abort() }, INACTIVITY_MS)
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
        if (timedOut) {
          fail(`连接 ${INACTIVITY_MS / 1000} 秒无响应，已自动断开（后端不可达或被中断）`)
        }
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
