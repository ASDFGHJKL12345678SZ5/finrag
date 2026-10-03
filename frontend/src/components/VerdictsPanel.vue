<script setup lang="ts">
// 校验明细：verify.py 的逐论断裁定——金融场景"带病输出比拒答危险"，
// 所以每个含数字的论断都必须被证据词面支持，一个不过就整篇拒答。
import { computed } from 'vue'
import type { Verdict } from '@/types/events'

const props = defineProps<{ verdicts: Verdict[] }>()

const passed = computed(() => props.verdicts.filter((v) => v.supported).length)
const allPassed = computed(() => passed.value === props.verdicts.length)
</script>

<template>
  <div v-if="verdicts.length > 0" class="card verdicts">
    <div class="panel-head">
      <h3>引用校验明细</h3>
      <span class="tag" :class="allPassed ? 'ok' : 'err'">
        {{ passed }}/{{ verdicts.length }} 通过
      </span>
    </div>
    <div v-for="(v, i) in verdicts" :key="i" class="verdict" :class="v.supported ? 'ok' : 'bad'">
      <span class="mark">{{ v.supported ? '✓' : '✗' }}</span>
      <div class="body">
        <p class="claim">{{ v.claim }}</p>
        <p class="meta">
          <span class="cid">{{ v.citation_id || '无引用' }}</span>
          <span v-if="v.missing.length > 0" class="missing">缺失证据：{{ v.missing.join('、') }}</span>
          <span v-else class="en">证据词面支持</span>
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.panel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.panel-head h3 {
  margin: 0; font-size: 13px; letter-spacing: 2px; color: var(--accent-deep);
  text-transform: uppercase; font-weight: 700;
}
.verdict {
  display: flex; gap: 12px; padding: 10px 12px; border-radius: 8px; margin-bottom: 8px;
  border: 1px solid var(--line); background: var(--paper-2);
}
.verdict:last-child { margin-bottom: 0; }
.mark { font-weight: 700; flex: none; font-family: var(--mono); }
.verdict.ok { border-left: 3px solid var(--teal); }
.verdict.ok .mark { color: var(--teal); }
.verdict.bad { border-left: 3px solid var(--red); }
.verdict.bad .mark { color: var(--red); }
.claim { margin: 0; font-size: 13.5px; }
.meta { margin: 3px 0 0; font-size: 12px; color: var(--ink-dim); display: flex; gap: 8px; flex-wrap: wrap; }
.cid { font-family: var(--mono); background: var(--sheet); border: 1px solid var(--line); padding: 0 6px; border-radius: 4px; }
.missing { color: var(--red); }
.en { color: var(--teal); }
</style>
