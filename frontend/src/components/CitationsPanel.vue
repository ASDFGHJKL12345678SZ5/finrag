<script setup lang="ts">
// 引用面板：每个论断的出处（来源报告 / 章节路径 / 相关度 / 原文片段）。
import type { Citation } from '@/types/events'

defineProps<{ citations: Citation[] }>()
</script>

<template>
  <div v-if="citations.length > 0" class="card citations">
    <h3>引用来源（{{ citations.length }}）</h3>
    <div v-for="c in citations" :key="c.citation_id" class="cite">
      <div class="cite-head">
        <span class="cid">{{ c.citation_id }}</span>
        <span class="src">{{ c.source }}</span>
        <span v-if="c.section_path" class="sec">{{ c.section_path }}</span>
        <span class="score">相关度 {{ c.score.toFixed(4) }}</span>
      </div>
      <p class="text">{{ c.text }}</p>
    </div>
  </div>
</template>

<style scoped>
h3 { margin: 0 0 10px; font-size: 14px; }
.cite { border: 1px solid var(--border); border-radius: 8px; padding: 9px 12px; margin-bottom: 8px; }
.cite-head { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; font-size: 12px; }
.cid { background: var(--accent); color: #fff; border-radius: 5px; padding: 0 7px; font-family: monospace; }
.src { color: var(--text); font-weight: 600; }
.sec { color: var(--text-muted); }
.score { color: var(--ok); font-family: monospace; margin-left: auto; }
.text { margin: 6px 0 0; font-size: 12.5px; color: var(--text-muted); }
</style>
