// @vitest-environment jsdom
// App 外壳冒烟（与 datacrew 前端同款）：真实挂载一次，拦运行时初始化错。
// 健康探测是异步状态机，用 waitFor 等状态落定。
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '@/App.vue'

const router = createRouter({
  history: createMemoryHistory(),
  routes: [{ path: '/', component: { template: '<div class="stub-view" />' } }],
})

describe('FinRAG App 外壳', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true })))
  })

  it('挂载即显示报头与知识库在线灯', async () => {
    await router.push('/')
    const w = mount(App, { global: { plugins: [router] } })
    await router.isReady()
    await vi.waitFor(() => expect(w.text()).toContain('知识库在线'))
    expect(w.text()).toContain('FinRAG 研报问答')
    expect(w.text()).toContain('零幻觉原则')
    expect(w.find('.stub-view').exists()).toBe(true)
  })

  it('探测失败（点灯重探）→ 显示后端不可达', async () => {
    await router.push('/')
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('down'))))
    const w = mount(App, { global: { plugins: [router] } })
    await router.isReady()
    await w.find('.health').trigger('click')
    await vi.waitFor(() => expect(w.text()).toContain('后端不可达'))
  })
})
