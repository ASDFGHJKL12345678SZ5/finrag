<script setup lang="ts">
import { onMounted, ref } from 'vue'

const apiOk = ref<boolean | null>(null)

onMounted(async () => {
  try {
    const res = await fetch('/health')
    apiOk.value = res.ok
  } catch {
    apiOk.value = false
  }
})
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <div class="brand">
        <span class="logo">FR</span>
        <div>
          <h1>FinRAG 研报问答</h1>
          <p class="subtitle">混合检索 · 引用校验 · 证据不足就拒答</p>
        </div>
      </div>
      <div class="health" :class="apiOk === null ? 'unknown' : apiOk ? 'up' : 'down'">
        <span class="dot" />{{ apiOk === null ? '检测中' : apiOk ? 'API 正常' : 'API 不可达' }}
      </div>
    </header>
    <main class="app-main">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app-shell { min-height: 100vh; display: flex; flex-direction: column; }
.app-header { display: flex; justify-content: space-between; align-items: center; padding: 14px 28px; border-bottom: 1px solid var(--border); background: var(--bg-elevated); }
.brand { display: flex; gap: 14px; align-items: center; }
.logo { width: 40px; height: 40px; border-radius: 10px; display: grid; place-items: center; background: linear-gradient(135deg, #10b981, #0ea5e9); color: #fff; font-weight: 700; }
h1 { font-size: 17px; margin: 0; }
.subtitle { margin: 2px 0 0; font-size: 12px; color: var(--text-muted); }
.health { display: flex; align-items: center; gap: 7px; font-size: 13px; color: var(--text-muted); }
.dot { width: 9px; height: 9px; border-radius: 50%; background: #94a3b8; }
.health.up .dot { background: #22c55e; box-shadow: 0 0 8px #22c55e88; }
.health.down .dot { background: #ef4444; }
.app-main { flex: 1; max-width: 1120px; width: 100%; margin: 0 auto; padding: 24px 28px 60px; }
</style>
