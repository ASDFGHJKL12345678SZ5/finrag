<script setup lang="ts">
// 设计系统·按钮（纸感主题）：variant 决定语义色，loading 自转并禁用。
defineProps<{
  variant?: 'default' | 'primary' | 'ok' | 'err' | 'ghost'
  size?: 'sm' | 'md'
  loading?: boolean
  disabled?: boolean
  type?: 'button' | 'submit'
}>()
const emit = defineEmits<{ (e: 'click', ev: MouseEvent): void }>()
</script>

<template>
  <button
    class="base-btn"
    :class="[variant ?? 'default', size ?? 'md']"
    :disabled="disabled || loading"
    :type="type ?? 'button'"
    @click="emit('click', $event)"
  >
    <span v-if="loading" class="base-btn-spin" aria-hidden="true" />
    <slot />
  </button>
</template>

<style scoped>
.base-btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 7px;
  border: 1px solid var(--line-strong); background: var(--sheet); color: var(--ink);
  border-radius: var(--radius-s); padding: 8px 18px; font-size: 14px;
  transition: border-color .15s, box-shadow .15s, transform .05s, background .15s;
  box-shadow: 0 1px 2px rgba(90, 75, 55, 0.06); white-space: nowrap;
}
.base-btn:hover:not(:disabled) { border-color: var(--accent); box-shadow: 0 2px 8px rgba(90, 75, 55, 0.12); }
.base-btn:active:not(:disabled) { transform: translateY(1px); }
.base-btn:disabled { opacity: .5; cursor: not-allowed; }
.base-btn.sm { padding: 4px 12px; font-size: 12px; }
.base-btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
.base-btn.primary:hover:not(:disabled) { background: var(--accent-deep); }
.base-btn.ok { background: var(--teal); border-color: var(--teal); color: #fff; }
.base-btn.err { background: transparent; border-color: var(--red); color: var(--red); }
.base-btn.err:hover:not(:disabled) { background: var(--red-soft); }
.base-btn.ghost { background: transparent; border-color: var(--line-strong); color: var(--ink-dim); }
.base-btn-spin {
  width: 12px; height: 12px; flex: none;
  border: 2px solid rgba(255,255,255,0.4); border-top-color: #fff; border-radius: 50%;
  animation: spin 0.7s linear infinite;
}
</style>
