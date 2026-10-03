<script setup lang="ts">
// 问答主页面：接线层（判断逻辑在 reducer/api/解析器中，均有单测覆盖）。
import AskForm from '@/components/AskForm.vue'
import MarkdownBlock from '@/components/MarkdownBlock.vue'
import CitationsPanel from '@/components/CitationsPanel.vue'
import VerdictsPanel from '@/components/VerdictsPanel.vue'
import { useAsk } from '@/composables/useAsk'

const {
  state, running, errorMessage, sessionId, setSessionId, ask, cancel, reset,
} = useAsk()

const PHASE_LABEL: Record<string, string> = {
  idle: '待命',
  running: '检索中',
  streaming: '生成中',
  done: '已完成',
  refused: '已拒答',
  error: '失败',
  cancelled: '已取消',
}
function phaseLabel(): string {
  return PHASE_LABEL[state.value.phase] ?? state.value.phase
}
</script>

<template>
  <div class="ask-view">
    <div class="session-bar">
      <label class="field">
        <span>会话 ID（多轮指代消解生效范围）</span>
        <input
          :value="sessionId"
          placeholder="同一 ID 才能追问"
          @input="setSessionId(($event.target as HTMLInputElement).value)"
        />
      </label>
      <button class="btn btn-ghost" :disabled="running" @click="reset">清空本轮</button>
    </div>

    <AskForm :disabled="running" @submit="ask" />

    <p v-if="errorMessage" class="error-banner rise">
      <b>出错了</b>{{ errorMessage }}
    </p>

    <template v-if="state.phase !== 'idle'">
      <div class="question-line rise">
        <span class="q-tag">问</span>
        <span class="q-text">{{ state.question }}</span>
        <span class="phase-tag" :class="state.phase">
          {{ phaseLabel() }}
        </span>
        <span v-if="running" class="running">
          <button class="btn btn-ghost" style="padding: 2px 10px; font-size: 12px;" @click="cancel">取消</button>
        </span>
      </div>

      <div v-if="state.phase === 'refused'" class="refuse-banner rise">
        <div class="stamp">拒答</div>
        <div class="refuse-body">
          <b>证据不足或引用校验未通过，本系统选择不猜。</b>
          <p v-if="state.refusalReason">{{ state.refusalReason }}</p>
          <span class="muted">——零幻觉原则：没有引用支撑的论断，宁可拒答。</span>
        </div>
      </div>

      <p v-if="state.phase === 'cancelled'" class="cancel-banner rise">本轮已取消。上方保留已流式产出的部分内容供参考。</p>

      <div v-if="state.streamed" class="card answer rise">
        <div class="answer-head">
          <span class="panel-title">答案</span>
          <span class="meta">
            <span v-if="state.totalMs != null">{{ state.totalMs }} ms</span>
            <span v-if="state.citations.length > 0">引用 × {{ state.citations.length }}</span>
            <span v-if="state.retryCount > 0">补偿检索 {{ state.retryCount }}</span>
          </span>
          <span v-if="state.phase === 'streaming'" class="pulse hint">生成中<span class="stream-caret" /></span>
        </div>
        <MarkdownBlock :content="state.streamed" />
      </div>

      <CitationsPanel :citations="state.citations" />
      <VerdictsPanel :verdicts="state.verdicts" />
    </template>

    <div v-else class="empty">
      <h3>两条内置路径</h3>
      <div class="paths">
        <div class="path ok">
          <div class="path-tag">有据可答</div>
          <p>“宁德润能的毛利率是多少？”</p>
          <span class="path-flow">混合检索（向量 + IDF 关键词）→ 引用校验 → 带引用生成</span>
        </div>
        <div class="path err">
          <div class="path-tag">零幻觉原则</div>
          <p>证据不足时选择拒答</p>
          <span class="path-flow">引用校验不通过的论断，系统明说“证据不足”而非硬答（当前 mock 演示环境只走作答路径，真实模式/缺证据时触发拒答）</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ask-view { display: flex; flex-direction: column; gap: 16px; }

/* 会话条 */
.session-bar {
  display: flex; gap: 14px; align-items: flex-end; flex-wrap: wrap;
  padding: 12px 14px; background: var(--sheet); border: 1px solid var(--line); border-radius: 12px;
}
.field { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 240px; }
.field span { font-size: 11.5px; color: var(--ink-faint); letter-spacing: 1px; }

/* 错误条 */
.error-banner {
  display: flex; gap: 10px; align-items: baseline; margin: 0;
  background: rgba(179, 65, 58, 0.07); border: 1px solid rgba(179, 65, 58, 0.4);
  color: var(--red); border-radius: 10px; padding: 11px 16px; font-size: 13.5px;
}
.error-banner b { flex: none; }

/* 问题行 */
.question-line {
  display: flex; gap: 10px; align-items: center; padding: 4px 2px;
}
.q-tag {
  flex: none; width: 26px; height: 26px; border-radius: 50%; display: inline-grid; place-items: center;
  background: var(--accent); color: #fff; font-family: var(--serif); font-weight: 700; font-size: 14px;
}
.q-text {
  font-family: var(--serif); font-size: 17px; font-weight: 700; letter-spacing: 0.5px;
}
.phase-tag {
  margin-left: auto; font-size: 12px; font-family: var(--mono); color: var(--ink-dim);
  border: 1px solid var(--line-strong); border-radius: 999px; padding: 0 10px; background: var(--paper-2);
}
.phase-tag.running, .phase-tag.streaming { color: var(--gold); border-color: rgba(176, 132, 48, 0.4); }
.phase-tag.done { color: var(--teal); border-color: rgba(47, 125, 107, 0.4); }
.phase-tag.refused { color: var(--red); border-color: rgba(179, 65, 58, 0.4); }
.phase-tag.error { color: var(--red); border-color: rgba(179, 65, 58, 0.4); }

/* 拒答 */
.refuse-banner {
  display: flex; gap: 16px; align-items: flex-start;
  background: rgba(176, 132, 48, 0.06); border: 1px solid rgba(176, 132, 48, 0.45);
  border-radius: 12px; padding: 16px 20px;
}
.stamp {
  flex: none; transform: rotate(-4deg);
  font-family: var(--serif); font-weight: 700; font-size: 20px; letter-spacing: 4px;
  color: var(--red); border: 2.5px solid var(--red); border-radius: 8px; padding: 4px 14px;
  opacity: 0.85;
}
.refuse-body p { margin: 6px 0 4px; font-size: 14px; }
.refuse-body b { font-size: 14.5px; }
.refuse-body .muted { font-size: 12.5px; }

/* 答案纸面 */
.answer {
  background: var(--sheet);
  box-shadow: 0 2px 6px rgba(90, 75, 55, 0.07), 0 16px 40px rgba(90, 75, 55, 0.1);
  border-color: var(--line-strong);
}
.answer-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.panel-title {
  font-size: 12px; letter-spacing: 3px; color: var(--accent-deep);
  text-transform: uppercase; font-weight: 700;
}
.hint { font-size: 12px; color: var(--gold); font-family: var(--mono); }

/* 空态 */
.empty h3 { margin: 10px 0 12px; font-size: 14px; color: var(--ink-dim); letter-spacing: 1px; }
.paths { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.path {
  background: var(--sheet); border: 1px solid var(--line); border-radius: 12px;
  padding: 18px; display: flex; flex-direction: column; gap: 8px;
  transition: border-color .15s, transform .15s; box-shadow: var(--shadow);
}
.path:hover { border-color: var(--line-strong); transform: translateY(-2px); }
.path p { margin: 0; font-family: var(--serif); font-size: 15px; font-weight: 700; }
.path-flow { font-size: 12px; color: var(--ink-dim); line-height: 1.6; }
.path-tag {
  align-self: flex-start; font-size: 11px; padding: 1px 10px; border-radius: 5px;
  background: var(--paper-2); color: var(--ink-dim); font-family: var(--mono);
}
.path.ok { border-top: 3px solid var(--teal); }
.path.ok .path-tag { color: var(--teal); }
.path.err { border-top: 3px solid var(--red); }
.path.err .path-tag { color: var(--red); }

@media (max-width: 720px) {
  .paths { grid-template-columns: 1fr; }
}
</style>
