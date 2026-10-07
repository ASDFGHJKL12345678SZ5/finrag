<script setup lang="ts">
// 执行时间线：问答图的节点耗时与补偿检索次数一目了然。
// retrieve/generate 出现多次 = 引用校验不过后的补偿检索环（有上限 2 次）。
// 展示规则在 services/timeline.ts（纯函数，有单测），组件只渲染。
import { computed } from 'vue'
import { buildTimelineRows, formatLatency, totalLatency } from '@/services/timeline'
import type { TimelineEntry } from '@/services/reducer'

const props = defineProps<{ entries: TimelineEntry[] }>()
const rows = computed(() => buildTimelineRows(props.entries))
const total = computed(() => totalLatency(rows.value))
</script>

<template>
  <div v-if="rows.length > 0" class="timeline">
    <div class="tl-head">
      <span class="tl-title">执行轨迹</span>
      <span class="tl-meta">{{ rows.length }} 节点 · 合计 {{ formatLatency(total) }}</span>
    </div>
    <ol class="tl-list">
      <li v-for="row in rows" :key="row.key" class="tl-row">
        <span class="tl-dot" :class="row.tone" :title="row.node" />
        <div class="tl-body">
          <div class="tl-line">
            <span class="tl-label">{{ row.label }}</span>
            <span v-if="row.attempt > 1" class="tl-retry" title="补偿检索：引用校验未通过，改写查询后重跑">×{{ row.attempt }}</span>
            <span class="tl-node mono">{{ row.node }}</span>
            <span class="tl-latency mono">{{ formatLatency(row.latency_ms) }}</span>
          </div>
          <div v-if="row.detail.length > 0" class="tl-detail">
            <span v-for="[k, v] in row.detail" :key="k" class="tl-chip"><b>{{ k }}</b>{{ v }}</span>
          </div>
        </div>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.timeline {
  background: var(--sheet); border: 1px solid var(--line); border-radius: var(--radius-l);
  padding: 14px 18px; box-shadow: var(--shadow);
}
.tl-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; }
.tl-title {
  font-size: 12px; letter-spacing: 3px; color: var(--accent-deep);
  text-transform: uppercase; font-weight: 700; font-family: var(--serif);
}
.tl-meta { font-size: 11.5px; color: var(--ink-faint); font-family: var(--mono); }
.tl-list { list-style: none; margin: 0; padding: 0; }
.tl-row { display: flex; gap: 12px; padding: 7px 0; position: relative; }
.tl-row:not(:last-child)::before {
  content: ''; position: absolute; left: 4px; top: 23px; bottom: -7px;
  width: 1px; background: var(--line-strong);
}
.tl-dot {
  width: 9px; height: 9px; border-radius: 50%; flex: none; margin-top: 7px;
  background: var(--accent);
}
.tl-dot.teal { background: var(--teal); }
.tl-dot.accent { background: var(--accent); }
.tl-dot.violet { background: #7c6cff; }
.tl-dot.gold { background: var(--gold); }
.tl-dot.warn { background: var(--gold); }
.tl-body { flex: 1; min-width: 0; }
.tl-line { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.tl-label { font-size: 13.5px; font-weight: 600; font-family: var(--serif); }
.tl-retry {
  font-family: var(--mono); font-size: 10.5px; color: var(--gold);
  border: 1px solid rgba(176, 132, 48, 0.35); background: var(--gold-soft);
  border-radius: 5px; padding: 0 6px; cursor: help;
}
.tl-node { font-size: 11px; color: var(--ink-faint); }
.tl-latency { margin-left: auto; font-size: 12px; color: var(--ink-dim); }
.tl-detail { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 3px; }
.tl-chip {
  display: inline-flex; gap: 5px; font-size: 11px; font-family: var(--mono);
  color: var(--ink-dim); background: var(--paper-2); border-radius: 5px; padding: 0 7px;
}
.tl-chip b { color: var(--ink-faint); font-weight: 600; }
</style>
