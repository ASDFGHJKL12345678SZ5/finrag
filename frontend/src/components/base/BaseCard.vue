<script setup lang="ts">
// 设计系统·卡片（纸感）：统一纸面容器，title 命名 slot + 操作位。
defineProps<{ title?: string; tone?: 'default' | 'ok' | 'warn' | 'err' | 'accent' }>()
</script>

<template>
  <section class="base-card" :class="tone ?? 'default'">
    <header v-if="title || $slots.actions" class="base-card-head">
      <div class="base-card-title">
        <span v-if="tone && tone !== 'default'" class="title-bar" aria-hidden="true" />
        <slot name="title">{{ title }}</slot>
      </div>
      <div v-if="$slots.actions" class="base-card-actions"><slot name="actions" /></div>
    </header>
    <slot />
  </section>
</template>

<style scoped>
.base-card {
  background: var(--sheet); border: 1px solid var(--line); border-radius: var(--radius-l);
  padding: 20px 24px; box-shadow: var(--shadow);
}
.base-card.ok { border-left: 3px solid var(--teal); }
.base-card.warn { border-left: 3px solid var(--gold); }
.base-card.err { border-left: 3px solid var(--red); }
.base-card.accent { border-left: 3px solid var(--accent); }
.base-card-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-bottom: 12px; }
.base-card-title {
  display: flex; align-items: center; gap: 8px;
  font-size: 13px; letter-spacing: 2px; color: var(--accent-deep);
  text-transform: uppercase; font-weight: 700; font-family: var(--serif);
}
.title-bar { width: 3px; height: 13px; border-radius: 2px; background: currentColor; }
.base-card-actions { display: flex; gap: 8px; align-items: center; }
</style>
