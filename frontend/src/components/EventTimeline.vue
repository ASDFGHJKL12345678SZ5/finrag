<script setup lang="ts">
// 节点流水线：node 事件 + 状态事件。retry_count>0 = 触发过改写补偿检索。
import { computed } from 'vue'
import type { TimelineEntry } from '@/services/reducer'

const props = defineProps<{
  timeline: TimelineEntry[]
  status: string
  retryCount: number
  totalMs?: number
}>()

const NODE_META: Record<string, { label: string; icon: string }> = {
  rewrite_query: { label: '查询改写（多轮指代消解）', icon: '↻' },
  understand_query: { label: '意图理解', icon: '◎' },
  retrieve: { label: '混合检索（向量 + IDF 关键词）', icon: '⌕' },
  verify: { label: '引用校验（数字论断词面核验）', icon: '✓' },
  generate: { label: '答案生成', icon: '✎' },
}

const rows = computed(() =>
  props.timeline.map((t, i) => ({
    ...t,
    i,
    label: NODE_META[t.node]?.label ?? t.node,
    icon: NODE_META[t.node]?.icon ?? '•',
  })),
)
</script>

<template>
  <div v-if="timeline.length > 0" class="card timeline">
    <div class="panel-head">
      <h3>检索流水线</h3>
      <div class="summary">
        <span v-if="status" class="tag">{{ status }}</span>
        <span v-if="retryCount > 0" class="tag warn">补偿检索 {{ retryCount }} 次</span>
        <span v-if="totalMs != null" class="tag ok">总耗时 {{ totalMs }} ms</span>
      </div>
    </div>
    <ol class="pipe">
      <li v-for="t in rows" :key="t.i" class="rise">
        <span class="step">{{ t.icon }}</span>
        <span class="node">{{ t.label }}</span>
        <span v-if="t.latency_ms != null" class="latency">{{ t.latency_ms }} ms</span>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.panel-head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.panel-head h3 {
  margin: 0; font-size: 13px; letter-spacing: 2px; color: var(--accent-deep);
  text-transform: uppercase; font-weight: 700;
}
.summary { display: flex; gap: 8px; flex-wrap: wrap; }
.pipe { list-style: none; margin: 0; padding: 0; }
.pipe li {
  display: flex; gap: 12px; align-items: baseline; padding: 7px 0;
  border-bottom: 1px dashed var(--line-strong);
}
.pipe li:last-child { border-bottom: none; }
.step {
  width: 22px; height: 22px; border-radius: 50%; background: var(--accent-soft); color: var(--accent-deep);
  display: inline-grid; place-items: center; font-family: var(--mono); font-size: 12px; flex: none;
  border: 1px solid rgba(154, 107, 63, 0.35);
}
.node { font-weight: 600; font-size: 13.5px; }
.latency { color: var(--teal); font-size: 12px; font-family: var(--mono); margin-left: auto; }
</style>
