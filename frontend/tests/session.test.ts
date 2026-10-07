import { afterEach, describe, expect, it } from 'vitest'
import { getSessionId, onSessionChange, setSessionId } from '@/services/session'

afterEach(() => setSessionId('demo-001'))

describe('finrag session 单一事实源', () => {
  it('默认 ID 与读写', () => {
    expect(getSessionId()).toBe('demo-001')
    setSessionId('conv-7')
    expect(getSessionId()).toBe('conv-7')
  })

  it('广播与退订', () => {
    const seen: string[] = []
    const off = onSessionChange((v) => seen.push(v))
    setSessionId('a')
    setSessionId('b')
    off()
    setSessionId('c')
    expect(seen).toEqual(['a', 'b'])
    expect(getSessionId()).toBe('c')
  })

  it('空白回退默认值', () => {
    setSessionId('x')
    setSessionId('  ')
    expect(getSessionId()).toBe('demo-001')
  })
})
