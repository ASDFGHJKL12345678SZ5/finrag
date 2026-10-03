<script setup lang="ts">
// 应用外壳：阅读室式报头（品牌/连接/会话）+ 主阅读区。
import { onMounted, onUnmounted, ref } from 'vue'

const apiOk = ref<boolean | null>(null)
let timer: number | undefined

async function checkHealth() {
  try {
    const res = await fetch('/health')
    apiOk.value = res.ok
  } catch {
    apiOk.value = false
  }
}

onMounted(() => {
  checkHealth()
  timer = window.setInterval(checkHealth, 15_000)
})
onUnmounted(() => { if (timer) window.clearInterval(timer) })
</script>

<template>
  <div class="app-shell">
    <header class="masthead">
      <div class="brand">
        <span class="logo">研</span>
        <div class="brand-text">
          <h1>FinRAG 研报问答</h1>
          <p>带引用生成的金融知识库 · 引用校验不过则拒答</p>
        </div>
      </div>
      <div class="health" :class="apiOk === null ? 'unknown' : apiOk ? 'up' : 'down'">
        <span class="dot" />
        <span>{{ apiOk === null ? '检测中' : apiOk ? '知识库在线' : '后端不可达' }}</span>
      </div>
    </header>
    <main class="reading-room">
      <router-view />
    </main>
    <footer class="colophon">
      <span>数据来自合成研报语料（FactRegistry 同源登记）· mock / local 双模式</span>
      <span class="faint">零幻觉原则：没有引用支撑的论断，宁可拒答</span>
    </footer>
  </div>
</template>

<style scoped>
.app-shell { min-height: 100vh; display: flex; flex-direction: column; }
.masthead {
  display: flex; justify-content: space-between; align-items: center;
  padding: 18px 36px; border-bottom: 1px solid var(--line);
  background: var(--sheet);
}
.brand { display: flex; gap: 14px; align-items: center; }
.logo {
  width: 44px; height: 44px; border-radius: 10px; display: grid; place-items: center; flex: none;
  background: var(--accent); color: #fff; font-family: var(--serif); font-size: 22px; font-weight: 700;
  box-shadow: 0 3px 10px rgba(154, 107, 63, 0.35);
}
.brand-text h1 { margin: 0; font-family: var(--serif); font-size: 21px; letter-spacing: 1px; }
.brand-text p { margin: 1px 0 0; font-size: 12.5px; color: var(--ink-dim); }
.health { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--ink-dim); font-family: var(--mono); }
.health .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ink-faint); flex: none; }
.health.up .dot { background: var(--teal); box-shadow: 0 0 8px rgba(47, 125, 107, 0.6); }
.health.down .dot { background: var(--red); }
.reading-room { flex: 1; width: 100%; max-width: 1060px; margin: 0 auto; padding: 28px 32px 60px; }
.colophon {
  display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap;
  padding: 16px 36px; border-top: 1px solid var(--line);
  font-size: 12px; color: var(--ink-dim);
}
@media (max-width: 720px) {
  .masthead { padding: 14px 18px; }
  .reading-room { padding: 18px 16px 40px; }
  .colophon { padding: 12px 18px; }
}
</style>
