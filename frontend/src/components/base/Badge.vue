<script setup lang="ts">
// 设计系统·徽章（纸感主题）：与 style.css 的 .tag.* 一一对应。
defineProps<{
  tone?: 'neutral' | 'accent' | 'ok' | 'warn' | 'err'
  mono?: boolean
}>()
</script>

<template>
  <span class="badge" :class="[tone ?? 'neutral', { mono: mono ?? true }]"><slot /></span>
</template>

<style scoped>
.badge {
  display: inline-flex; align-items: center; gap: 5px; padding: 1px 10px; border-radius: 999px;
  font-size: 12px; line-height: 1.6;
  border: 1px solid var(--line-strong); color: var(--ink-dim); background: var(--paper-2);
}
.badge.mono { font-family: var(--mono); }
.badge.accent { color: var(--accent-deep); border-color: rgba(154, 107, 63, 0.4); background: var(--accent-soft); }
.badge.ok { color: var(--teal); border-color: rgba(47, 125, 107, 0.35); background: var(--teal-soft); }
.badge.warn { color: var(--gold); border-color: rgba(176, 132, 48, 0.35); background: var(--gold-soft); }
.badge.err { color: var(--red); border-color: rgba(179, 65, 58, 0.35); background: var(--red-soft); }
</style>
