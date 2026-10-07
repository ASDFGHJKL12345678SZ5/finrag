<script setup lang="ts">
defineProps<{ size?: number }>()
</script>

<template>
  <span class="base-spinner" :style="{ width: (size ?? 14) + 'px', height: (size ?? 14) + 'px' }" aria-hidden="true" />
</template>

<style scoped>
.base-spinner {
  display: inline-block; flex: none;
  border: 2px solid var(--line-strong); border-top-color: var(--accent); border-radius: 50%;
  animation: spin 0.7s linear infinite;
}
</style>
