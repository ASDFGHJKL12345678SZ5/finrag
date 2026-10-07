// ===== 会话 ID：单一事实源（localStorage 持久化 + 订阅通知） =====
// 与 datacrew 同构：侧栏/输入框/useAsk 三方共用一份，不会"显示一个用一个"。
// finrag 的 session_id 用于多轮指代消解范围（同一 ID 才能追问）。
const STORAGE_KEY = 'finrag.sessionId'
const DEFAULT_ID = 'demo-001'

function store(): Storage | null {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null
  } catch {
    return null
  }
}

let current: string = store()?.getItem(STORAGE_KEY) || DEFAULT_ID
const listeners = new Set<(v: string) => void>()

export function getSessionId(): string {
  return current
}

export function setSessionId(v: string): void {
  const next = v.trim() || DEFAULT_ID
  if (next === current) return
  current = next
  store()?.setItem(STORAGE_KEY, next)
  listeners.forEach((fn) => fn(next))
}

export function onSessionChange(fn: (v: string) => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
