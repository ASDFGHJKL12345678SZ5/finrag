<script setup lang="ts">
// 问答主页面：接线层（判断逻辑在 reducer/api/解析器中，均有单测覆盖）。
import AskForm from '@/components/AskForm.vue'
import EventTimeline from '@/components/EventTimeline.vue'
import MarkdownBlock from '@/components/MarkdownBlock.vue'
import CitationsPanel from '@/components/CitationsPanel.vue'
import VerdictsPanel from '@/components/VerdictsPanel.vue'
import { useAsk } from '@/composables/useAsk'

const {
  state, running, errorMessage, sessionId, setSessionId, ask, cancel, reset,
} = useAsk()
</script>

<template>
  <div class="ask-view">
    <div class="session-bar">
      <label>
        会话 ID（多轮指代消解生效范围）
        <input
          :value="sessionId"
          placeholder="同一 ID 才能追问"
          @input="setSessionId(($event.target as HTMLInputElement).value)"
        />
      </label>
      <button class="btn" :disabled="running" @click="reset">清空本轮</button>
    </div>

    <AskForm :disabled="running" @submit="ask" />

    <p v-if="errorMessage" class="error-banner">{{ errorMessage }}</p>

    <template v-if="state.phase !== 'idle'">
      <div class="question-line">
        <span class="tag">{{ state.question }}</span>
        <span v-if="running" class="running">运行中… <button class="link" @click="cancel">取消</button></span>
      </div>

      <div v-if="state.phase === 'refused'" class="refuse-banner">
        <b>拒答</b>：{{ state.refusalReason }}
        <span class="muted">——证据不足或引用校验未通过，本系统选择不猜</span>
      </div>

      <div v-if="state.streamed" class="card answer">
        <MarkdownBlock :content="state.streamed" />
      </div>

      <CitationsPanel :citations="state.citations" />
      <VerdictsPanel :verdicts="state.verdicts" />
      <EventTimeline
        :timeline="state.timeline"
        :status="state.status"
        :retry-count="state.retryCount"
        :total-ms="state.totalMs"
      />
    </template>

    <div v-else class="empty">
      <p>输入金融研报问题开始。两条内置路径：</p>
      <ul>
        <li><b>有据可答</b>：混合检索（向量 + IDF 关键词）→ 引用校验 → 带引用生成</li>
        <li><b>拒答</b>：语料里没有的证据，系统明说"证据不足"——金融场景不带病输出</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.ask-view { display: flex; flex-direction: column; gap: 4px; }
.session-bar { display: flex; gap: 18px; align-items: flex-end; background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 12px 16px; margin-bottom: 14px; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-muted); flex: 1; }
input { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; color: var(--text); padding: 7px 11px; font: inherit; font-size: 13px; }
input:focus { outline: none; border-color: var(--accent); }
.question-line { display: flex; gap: 10px; align-items: center; margin: 10px 0 4px; }
.running { color: var(--warn); font-size: 12px; }
.link { background: none; border: none; color: var(--accent); font-size: 12px; }
.error-banner { background: #f8717115; border: 1px solid var(--err); color: var(--err); border-radius: 10px; padding: 10px 14px; margin: 10px 0; }
.refuse-banner { background: #fbbf2415; border: 1px solid var(--warn); color: var(--warn); border-radius: 10px; padding: 10px 14px; margin: 8px 0; font-size: 13px; }
.answer { border-color: #22c55e40; }
.empty { color: var(--text-muted); padding: 30px 6px; }
.empty ul { padding-left: 20px; }
.empty li { margin: 6px 0; }
</style>
