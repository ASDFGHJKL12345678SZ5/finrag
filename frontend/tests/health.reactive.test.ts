import { describe, expect, it } from 'vitest'
import { effect, reactive } from 'vue'
import { createHealthMonitor } from '@/services/health'

// 回归测试（真实事故，两连踩）：
// ① useAsk 曾把 health.state 裸接给 Vue——computed 缓存首值，永远“探测中”；
// ② 改 reactive(health.state) 仍无效——纯模块按原始引用改对象，代理 setter 不触发。
// 正解：纯模块发 onStateChange 回调，Vue 层自持 reactive 状态。本测试锁死这条接法。

function fakeTimers() {
  const jobs = new Map<number, () => void>()
  let seq = 0
  return {
    setTimeout: (fn: () => void, _ms: number) => {
      jobs.set(++seq, fn)
      return seq
    },
    clearTimeout: (h: unknown) => {
      jobs.delete(h as number)
    },
    pending: () => jobs.size,
    fireAll: async () => {
      const fns = [...jobs.values()]
      jobs.clear()
      for (const f of fns) await f()
    },
  }
}

const flush = () => new Promise((r) => setTimeout(r, 0))

describe('health 经 onStateChange 桥接后可被 Vue 观测', () => {
  it('探测结果变化会触发 effect（与 useAsk 接法一致）', async () => {
    const timers = fakeTimers()
    let ok = false
    const state = reactive({ status: 'probing', since: 0, fails: 0 })
    const m = createHealthMonitor({
      probe: () => Promise.resolve(ok),
      intervalMs: 15000,
      probeTimeoutMs: 6000,
      onStateChange: (s) => Object.assign(state, s),
      ...timers,
    })
    // 与 useAsk 相同的接法：Vue 层自持 reactive，纯模块回调通知
    const seen: string[] = []
    effect(() => {
      seen.push(state.status)
    })
    m.start()
    await flush()
    expect(state.status).toBe('unreachable')
    ok = true
    await timers.fireAll()
    await flush()
    expect(state.status).toBe('ok')
    expect(seen).toEqual(['probing', 'unreachable', 'ok'])
  })
})
