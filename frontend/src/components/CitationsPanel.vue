<script setup lang="ts">
// 引用面板：每个论断的出处（来源报告 / 章节路径 / 相关度 / 原文片段）。
import type { Citation } from '@/types/events'

defineProps<{ citations: Citation[] }>()
</script>

<template>
  <div v-if="citations.length > 0" class="card citations">
    <div class="panel-head">
      <h3>引用来源</h3>
      <span class="tag accent">共 {{ citations.length }} 条</span>
    </div>
    <div v-for="(c, i) in citations" :key="c.citation_id" class="cite rise">
      <div class="cite-head">
        <span class="ord">{{ i + 1 }}</span>
        <span class="cid">[{{ c.citation_id }}]</span>
        <span class="src">{{ c.source }}</span>
        <span v-if="c.section_path" class="sec">§ {{ c.section_path }}</span>
        <span class="score">相关度 {{ c.score.toFixed(4) }}</span>
      </div>
      <blockquote class="text">{{ c.text }}</blockquote>
    </div>
  </div>
</template>

<style scoped>
.panel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.panel-head h3 {
  margin: 0; font-size: 13px; letter-spacing: 2px; color: var(--accent-deep);
  text-transform: uppercase; font-weight: 700;
}
.cite {
  border: 1px solid var(--line); border-left: 3px solid var(--accent);
  border-radius: 8px; padding: 10px 14px; margin-bottom: 10px; background: var(--paper-2);
}
.cite-head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; font-size: 12.5px; }
.ord {
  width: 18px; height: 18px; border-radius: 50%; background: var(--accent); color: #fff;
  display: inline-grid; place-items: center; font-size: 11px; flex: none;
}
.cid { color: var(--accent-deep); font-family: var(--mono); font-weight: 700; }
.src { color: var(--ink); font-weight: 600; }
.sec { color: var(--ink-dim); font-family: var(--mono); font-size: 12px; }
.score { color: var(--teal); font-family: var(--mono); font-size: 12px; margin-left: auto; }
.text {
  margin: 7px 0 0; font-size: 13px; color: var(--ink-dim); line-height: 1.7;
  border-left: 2px solid var(--line-strong); padding-left: 12px;
  font-family: var(--serif);
}
</style>
