<script setup lang="ts">
// 节点时间线：node 事件 + 状态事件。retry_count>0 = 触发过改写补偿检索。
import type { TimelineEntry } from '@/services/reducer'

defineProps<{
  timeline: TimelineEntry[]
  status: string
  retryCount: number
  totalMs?: number
}>()

const NODE_LABEL: Record<string, string> = {
  rewrite_query: '查询改写（多轮指代消解）',
  retrieve: '混合检索（向量 + IDF 关键词）',
  verify: '引用校验（数字论断词面核验）',
  generate: '答案生成',
}

function labelOf(node: string): string {
  return NODE_LABEL[node] ?? node
}
</script>

<template>
  <div v-if="timeline.length > 0" class="card timeline">
    <h3>节点时间线</h3>
    <div v-for="(t, i) in timeline" :key="i" class="row">
      <span class="step">{{ i + 1 }}</span>
      <span class="node">{{ labelOf(t.node) }}</span>
      <span v-if="t.latency_ms != null" class="latency">{{ t.latency_ms }} ms</span>
    </div>
    <div class="summary">
      <span v-if="status" class="tag">状态: {{ status }}</span>
      <span v-if="retryCount > 0" class="tag warn">补偿检索 {{ retryCount }} 次</span>
      <span v-if="totalMs != null" class="tag ok">总耗时 {{ totalMs }} ms</span>
    </div>
  </div>
</template>

<style scoped>
h3 { margin: 0 0 10px; font-size: 14px; }
.row { display: flex; gap: 12px; align-items: baseline; padding: 5px 0; border-bottom: 1px dashed #ffffff10; }
.row:last-child { border-bottom: none; }
.step { width: 20px; height: 20px; border-radius: 50%; background: var(--accent); color: #fff; font-size: 11px; display: grid; place-items: center; flex: none; }
.node { font-weight: 600; }
.latency { color: var(--ok); font-size: 12px; font-family: monospace; }
.summary { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.tag.warn { color: var(--warn); border-color: var(--warn); }
.tag.ok { color: var(--ok); border-color: var(--ok); }
</style>
