<script setup lang="ts">
// 安全渲染 Markdown（LLM 输出按不可信内容处理：marked 转 HTML → DOMPurify 消毒）。
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps<{ content: string }>()
const html = computed(() =>
  DOMPurify.sanitize(marked.parse(props.content || '', { async: false }) as string),
)
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html -->
  <div class="markdown" v-html="html" />
</template>

<style scoped>
.markdown :deep(table) { border-collapse: collapse; margin: 12px 0; display: block; overflow: auto; }
.markdown :deep(th), .markdown :deep(td) { border: 1px solid var(--line-strong); padding: 6px 14px; }
.markdown :deep(th) { background: var(--paper-2); color: var(--ink-dim); font-family: var(--mono); font-size: 12.5px; }
.markdown :deep(h1), .markdown :deep(h2), .markdown :deep(h3) {
  font-family: var(--serif); margin: 16px 0 6px; color: var(--ink);
}
.markdown :deep(p) { margin: 9px 0; line-height: 1.9; }
.markdown :deep(code) { background: var(--paper-2); padding: 1px 6px; border-radius: 5px; color: var(--accent-deep); font-family: var(--mono); font-size: 12.5px; }
.markdown :deep(pre) { background: var(--paper-2); }
.markdown :deep(pre code) { background: none; padding: 0; }
.markdown :deep(ul) { margin: 6px 0; padding-left: 22px; }
.markdown :deep(li) { margin: 3px 0; line-height: 1.8; }
.markdown :deep(strong) { color: var(--ink); }
</style>
