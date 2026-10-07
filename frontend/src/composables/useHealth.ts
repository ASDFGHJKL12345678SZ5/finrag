import { computed, reactive, type ComputedRef } from 'vue'
import { createHealthMonitor, type HealthMonitor, type HealthState } from '@/services/health'
import { API_BASE } from '@/services/api'

// ===== App 级健康探测：全局唯一实例 =====
// 与 datacrew 前端的同款教训：App 报头曾自带 setInterval 轮询，useAsk 又持
// 一套状态机探测——两套互不知情。侧栏/报头状态灯与提问门控现在共用这一个单例。
//
// 响应式桥接：纯模块（health.ts）按原始引用改对象，直接 reactive(state) 不触发
// （代理 setter 不拦原生赋值——实测踩坑，回归见 tests/health.reactive.test.ts）。
// 正解：单例创建时一次性挂 onStateChange → Object.assign 到 Vue 自持的镜像。

const HEALTH_INTERVAL_MS = 15_000
const HEALTH_TIMEOUT_MS = 6_000

interface Singleton {
  monitor: HealthMonitor
  mirror: HealthState
}

let singleton: Singleton | null = null

function probe(signal: AbortSignal): Promise<boolean> {
  return fetch(API_BASE + '/health', { signal })
    .then((r) => r.ok)
    .catch(() => false)
}

function ensureSingleton(): Singleton {
  if (singleton) return singleton
  // 必须先包 reactive 再让 onStateChange 闭包持有 proxy——
  // 若闭包持有原始对象，Object.assign 走原生赋值，代理 setter 不触发，
  // 界面永远停在"检测中"（health.reactive.test.ts 锁死的同款坑，别踩第二次）
  const mirror = reactive<HealthState>({ status: 'probing', since: Date.now(), fails: 0 })
  const monitor = createHealthMonitor({
    probe,
    intervalMs: HEALTH_INTERVAL_MS,
    probeTimeoutMs: HEALTH_TIMEOUT_MS,
    onStateChange: (s) => Object.assign(mirror, s), // 只桥接一次，别迟到
  })
  monitor.start()
  singleton = { monitor, mirror }
  return singleton
}

export interface UseHealth {
  state: Readonly<HealthState>
  /** computed Ref：解构传递的是 Ref 本体、模板自动解包——保持响应式。
   *  曾经的坑（datacrew 前端实机复现的同款）：普通 getter 被解构那一刻值即固化，
   *  status 从 probing→ok 后界面仍显示"后端不可达"、提问按钮永远禁用。 */
  isDown: ComputedRef<boolean>
  probeNow: () => Promise<void>
}

export function useHealth(): UseHealth {
  const state = ensureSingleton().mirror
  return {
    state,
    isDown: computed(() => state.status !== 'ok'),
    probeNow: async () => {
      await ensureSingleton().monitor.probeNow()
    },
  }
}
