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
.markdown :deep(table) { border-collapse: collapse; margin: 10px 0; }
.markdown :deep(th), .markdown :deep(td) { border: 1px solid var(--border); padding: 5px 12px; }
.markdown :deep(th) { background: var(--bg-elevated); }
.markdown :deep(h1), .markdown :deep(h2), .markdown :deep(h3) { margin: 14px 0 6px; }
.markdown :deep(code) { background: #0b0f18; padding: 1px 6px; border-radius: 5px; }
.markdown :deep(pre code) { background: none; padding: 0; }
.markdown :deep(ul) { margin: 6px 0; padding-left: 22px; }
</style>
