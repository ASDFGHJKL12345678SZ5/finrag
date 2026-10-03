import { describe, expect, it } from 'vitest'
import { createRound, type Settlement } from '@/services/round'

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
    fireAll: () => {
      const fns = [...jobs.values()]
      jobs.clear()
      fns.forEach((f) => f())
    },
  }
}

describe('createRound', () => {
  it('cancel is synchronous', () => {
    const timers = fakeTimers()
    const seen: Settlement[] = []
    const r = createRound({
      inactivityMs: 30000,
      onInactivity: () => seen.push('failed'),
      onSettle: (k) => seen.push(k),
      ...timers,
    })
    r.arm()
    r.cancel()
    expect(r.settled).toBe('cancelled')
    expect(seen).toEqual(['cancelled'])
    expect(r.signal.aborted).toBe(true)
  })

  it('inactivity fires once then no timer survives', () => {
    const timers = fakeTimers()
    const events: string[] = []
    const r = createRound({
      inactivityMs: 30000,
      onInactivity: () => events.push('inactivity'),
      onSettle: (k) => events.push('settle:' + k),
      ...timers,
    })
    r.arm()
    timers.fireAll()
    timers.fireAll()
    expect(events).toEqual(['inactivity', 'settle:failed'])
    expect(timers.pending()).toBe(0)
  })

  it('settle is idempotent: cancel then timeout does not overwrite', () => {
    const timers = fakeTimers()
    const events: string[] = []
    const r = createRound({
      inactivityMs: 30000,
      onInactivity: () => events.push('inactivity'),
      onSettle: (k) => events.push(k),
      ...timers,
    })
    r.arm()
    r.cancel()
    timers.fireAll()
    expect(events).toEqual(['cancelled'])
    expect(r.settled).toBe('cancelled')
  })

  it('arm replaces rather than stacks; no revive after settle', () => {
    const timers = fakeTimers()
    const r = createRound({ inactivityMs: 1000, onInactivity: () => undefined, ...timers })
    r.arm()
    r.arm()
    expect(timers.pending()).toBe(1)
    r.cancel()
    r.arm()
    expect(timers.pending()).toBe(0)
  })

  it('disarm stops timer but does not abort', () => {
    const timers = fakeTimers()
    const r = createRound({ inactivityMs: 1000, onInactivity: () => undefined, ...timers })
    r.arm()
    r.disarm()
    expect(timers.pending()).toBe(0)
    expect(r.signal.aborted).toBe(false)
  })

  it('ids increase so rounds can be told apart', () => {
    const timers = fakeTimers()
    const a = createRound({ inactivityMs: 1000, onInactivity: () => undefined, ...timers })
    const b = createRound({ inactivityMs: 1000, onInactivity: () => undefined, ...timers })
    expect(b.id).toBeGreaterThan(a.id)
  })
})
