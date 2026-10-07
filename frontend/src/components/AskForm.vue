<script setup lang="ts">
// 提问表单：示例问题覆盖"有据可答 / 跨文档综合"两条路径。
// textarea 随内容长高（4 行封顶）。
import { nextTick, ref, watch } from 'vue'
import BaseButton from './base/BaseButton.vue'

const props = defineProps<{ disabled: boolean }>()
const emit = defineEmits<{ (e: 'submit', v: string): void }>()
const question = ref('')
const ta = ref<HTMLTextAreaElement | null>(null)

const EXAMPLES = [
  { q: '宁德润能的毛利率是多少？', kind: 'ok', label: '有据' },
  { q: '方衡半导体2025年的净利润是多少？', kind: 'accent', label: '跨文档' },
]

async function autosize() {
  await nextTick()
  const el = ta.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 108) + 'px'
}
watch(question, autosize)

function submit() {
  const q = question.value.trim()
  if (q && !props.disabled) emit('submit', q)
}
</script>

<template>
  <div class="ask-form">
    <div class="reader-input">
      <span class="quote-mark">"</span>
      <textarea
        ref="ta"
        v-model="question"
        rows="1"
        placeholder="问一个关于研报的问题，例如：宁德润能的毛利率是多少？"
        @keydown.enter.exact.prevent="submit"
      />
      <BaseButton variant="primary" :disabled="disabled || !question.trim()" :loading="disabled" @click="submit">
        {{ disabled ? '检索中' : '提问' }}
      </BaseButton>
    </div>
    <div class="examples">
      <span class="faint">试试：</span>
      <button v-for="e in EXAMPLES" :key="e.q" class="chip" :class="e.kind" :disabled="disabled" @click="question = e.q">
        <b>{{ e.label }}</b>{{ e.q }}
      </button>
      <span class="faint hint"><span class="kbd">Enter</span> 提交 · <span class="kbd">Shift+Enter</span> 换行</span>
    </div>
  </div>
</template>

<style scoped>
.ask-form { display: flex; flex-direction: column; gap: 10px; }
.reader-input {
  display: flex; align-items: flex-end; gap: 8px;
  background: var(--sheet); border: 1px solid var(--line-strong); border-radius: var(--radius-l);
  padding: 8px 8px 8px 16px; box-shadow: var(--shadow); transition: border-color .15s;
}
.reader-input:focus-within { border-color: var(--accent); }
.quote-mark { font-family: var(--serif); font-size: 34px; color: var(--line-strong); line-height: 1; flex: none; }
.reader-input textarea {
  border: none; background: transparent; box-shadow: none; resize: none;
  font-size: 15px; padding: 8px 0; min-height: 20px; line-height: 1.6; max-height: 108px;
}
.reader-input textarea:focus { box-shadow: none; }
.reader-input :deep(.base-btn) { padding: 9px 24px; flex: none; }
.examples { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 12.5px; }
.chip {
  display: inline-flex; align-items: center; gap: 7px;
  background: var(--sheet); border: 1px solid var(--line-strong); color: var(--ink-dim);
  border-radius: 999px; padding: 3px 13px; font-size: 12.5px; transition: border-color .15s, color .15s;
}
.chip b {
  font-family: var(--mono); font-size: 10px; padding: 0 6px; border-radius: 4px;
  background: var(--paper-2); color: var(--ink-dim);
}
.chip:hover:not(:disabled) { color: var(--ink); border-color: var(--accent); }
.chip.ok b { color: var(--teal); }
.chip.accent b { color: var(--accent-deep); }
.chip.err b { color: var(--red); }
.chip:disabled { opacity: .5; cursor: not-allowed; }
.hint { margin-left: auto; }
</style>
