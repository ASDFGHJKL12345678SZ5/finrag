<script setup lang="ts">
// 答案纸面：流式生成时显示打字光标 + "生成中"，终态显示耗时/引用数/补偿检索次数。
// 复制按钮：答案可一键复制（金融场景要粘进报告里），剪贴板不可用时静默降级。
// 注意：streamed 是最新一份全量快照（reducer 的快照语义，见 services/reducer.ts 注释）——
// 流式中继帧到达即整体刷新，不是字符串拼接。
import { computed, ref } from 'vue'
import MarkdownBlock from './MarkdownBlock.vue'

const props = defineProps<{
  content: string
  streaming: boolean
  totalMs?: number
  citationCount: number
  retryCount: number
}>()
const copied = ref(false)

const meta = computed(() => {
  const items: string[] = []
  if (props.totalMs != null) items.push(props.totalMs + ' ms')
  if (props.citationCount > 0) items.push('引用 × ' + props.citationCount)
  if (props.retryCount > 0) items.push('补偿检索 ' + props.retryCount + ' 次')
  return items
})

async function copy() {
  try {
    await navigator.clipboard.writeText(props.content)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1500)
  } catch {
    /* 剪贴板不可用（非安全上下文）：静默 */
  }
}
</script>

<template>
  <div class="card answer rise">
    <div class="answer-head">
      <span class="panel-title">答案</span>
      <span class="meta">
        <span v-for="m in meta" :key="m">{{ m }}</span>
        <span v-if="streaming" class="hint">生成中<span class="stream-caret" /></span>
      </span>
      <button class="copy-btn" :disabled="streaming" @click="copy">{{ copied ? '已复制 ✓' : '复制' }}</button>
    </div>
    <MarkdownBlock :content="content" />
  </div>
</template>

<style scoped>
.answer {
  background: var(--sheet);
  box-shadow: 0 2px 6px rgba(90, 75, 55, 0.07), 0 16px 40px rgba(90, 75, 55, 0.1);
  border-color: var(--line-strong);
}
.answer-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; gap: 10px; flex-wrap: wrap; }
.panel-title {
  font-size: 12px; letter-spacing: 3px; color: var(--accent-deep);
  text-transform: uppercase; font-weight: 700; font-family: var(--serif);
}
.meta { display: flex; gap: 12px; align-items: center; font-size: 12px; color: var(--ink-dim); font-family: var(--mono); }
.hint { color: var(--gold); }
.copy-btn {
  border: 1px solid var(--line-strong); background: transparent; color: var(--ink-dim);
  border-radius: 6px; font-size: 12px; padding: 2px 10px; transition: all .15s;
}
.copy-btn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent-deep); }
.copy-btn:disabled { opacity: .4; cursor: not-allowed; }
</style>
