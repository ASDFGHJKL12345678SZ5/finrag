import { describe, expect, it } from 'vitest'
import { createHealthMonitor } from '@/services/health'

// 手工假定时器 + 可控探针：不依赖真实时钟/网络。
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

// 每次探测让出一次微任务，模拟异步完成
const flush = () => new Promise((r) => setTimeout(r, 0))

describe('createHealthMonitor', () => {
  it('探测成功 → ok；探测失败 → unreachable 且持续计数', async () => {
    const timers = fakeTimers()
    let ok = true
    const m = createHealthMonitor({ probe: () => Promise.resolve(ok), intervalMs: 15000, probeTimeoutMs: 6000, ...timers })
    m.start()
    await flush()
    expect(m.state.status).toBe('ok')
    ok = false
    await timers.fireAll()
    await flush()
    expect(m.state.status).toBe('unreachable')
    expect(m.state.fails).toBe(1)
  })

  it('probe 抛错/超时都算 unreachable（不会假装绿）', async () => {
    const timers = fakeTimers()
    const m = createHealthMonitor({
      probe: () => Promise.reject(new Error('boom')),
      intervalMs: 15000,
      probeTimeoutMs: 6000,
      ...timers,
    })
    m.start()
    await flush()
    expect(m.state.status).toBe('unreachable')
  })

  it('unreachable 恢复后翻回 ok 且 fails 归零', async () => {
    const timers = fakeTimers()
    let ok = false
    const m = createHealthMonitor({ probe: () => Promise.resolve(ok), intervalMs: 15000, probeTimeoutMs: 6000, ...timers })
    m.start()
    await flush()
    expect(m.state.status).toBe('unreachable')
    ok = true
    await timers.fireAll()
    await flush()
    expect(m.state.status).toBe('ok')
    expect(m.state.fails).toBe(0)
  })

  it('stop 之后不再探测（无泄漏）', async () => {
    const timers = fakeTimers()
    let calls = 0
    const m = createHealthMonitor({
      probe: () => {
        calls += 1
        return Promise.resolve(true)
      },
      intervalMs: 15000,
      probeTimeoutMs: 6000,
      ...timers,
    })
    m.start()
    await flush()
    m.stop()
    await timers.fireAll()
    await flush()
    expect(calls).toBe(1)
    expect(timers.pending()).toBe(0)
  })
})
