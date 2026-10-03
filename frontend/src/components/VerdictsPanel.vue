<script setup lang="ts">
// 校验明细：verify.py 的逐论断裁定——金融场景"带病输出比拒答危险"，
// 所以每个含数字的论断都必须被证据词面支持，一个不过就整篇拒答。
import type { Verdict } from '@/types/events'

defineProps<{ verdicts: Verdict[] }>()

</script>

<template>
  <div v-if="verdicts.length > 0" class="card verdicts">
    <h3>
      引用校验明细
      <span class="muted">（{{ verdicts.filter(v => v.supported).length }}/{{ verdicts.length }} 通过）</span>
    </h3>
    <div v-for="(v, i) in verdicts" :key="i" class="verdict" :class="v.supported ? 'ok' : 'bad'">
      <span class="mark">{{ v.supported ? '✓' : '✗' }}</span>
      <div class="body">
        <p class="claim">{{ v.claim }}</p>
        <p class="meta">
          <span class="cid">{{ v.citation_id || '无引用' }}</span>
          <span v-if="v.missing.length > 0" class="missing">缺失证据: {{ v.missing.join('、') }}</span>
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
h3 { margin: 0 0 10px; font-size: 14px; }
.verdict { display: flex; gap: 10px; padding: 8px 0; border-bottom: 1px dashed #ffffff10; }
.verdict:last-child { border-bottom: none; }
.mark { font-weight: 700; flex: none; }
.verdict.ok .mark { color: var(--ok); }
.verdict.bad .mark { color: var(--err); }
.claim { margin: 0; font-size: 13px; }
.meta { margin: 3px 0 0; font-size: 12px; color: var(--text-muted); display: flex; gap: 8px; flex-wrap: wrap; }
.cid { font-family: monospace; background: var(--bg-elevated); padding: 0 6px; border-radius: 4px; }
.missing { color: var(--err); }
</style>
