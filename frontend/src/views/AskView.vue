<script setup lang="ts">
// 问答主页面：接线层（判断逻辑在 reducer/api/解析器中，均有单测覆盖）。
// 重构点：
//   1. 答案抽出 AnswerCard（流式光标/耗时/复制按钮）；
//   2. 执行轨迹 EventTimeline 上屏（节点耗时 + 补偿检索 ×N，之前只存不显）；
//   3. 健康门控与报头灯共用 useHealth 单例，附"立即重试"。
import { computed, onUnmounted, ref } from 'vue'
import AskForm from '@/components/AskForm.vue'
import AnswerCard from '@/components/AnswerCard.vue'
import EventTimeline from '@/components/EventTimeline.vue'
import CitationsPanel from '@/components/CitationsPanel.vue'
import VerdictsPanel from '@/components/VerdictsPanel.vue'
import { useAsk } from '@/composables/useAsk'

const {
  state, running, errorMessage, sessionId, setSessionId, healthDown, healthState, probeNow,
  ask, cancel, reset,
} = useAsk()

// 不可达持续多久：给用户"等了多久"的实感
const downSeconds = ref(0)
const timer = window.setInterval(() => {
  downSeconds.value = Math.max(0, Math.round((Date.now() - healthState.since) / 1000))
}, 1000)
onUnmounted(() => window.clearInterval(timer))

const PHASE_LABEL: Record<string, string> = {
  idle: '待命',
  running: '检索中',
  streaming: '生成中',
  done: '已完成',
  refused: '已拒答',
  error: '失败',
  cancelled: '已取消',
}
const phaseLabel = computed(() => PHASE_LABEL[state.value.phase] ?? state.value.phase)
</script>

<template>
  <div class="ask-view">
    <div class="session-bar">
      <label class="field">
        <span>会话 ID</span>
        <input
          :value="sessionId"
          placeholder="local-001"
          @input="setSessionId(($event.target as HTMLInputElement).value)"
        />
      </label>
      <button class="btn btn-ghost" :disabled="running" @click="reset">清空本轮</button>
    </div>

    <p class="session-hint">
      单轮问答：本系统不做服务端会话持久化，<b>每轮请把限定条件说全</b>（不继承上一轮的口径与条件）。
    </p>

    <div v-if="healthDown" class="banner health-banner rise">
      <b>后端不可达{{ healthState.status === 'probing' ? '（探测中）' : '已 ' + downSeconds + 's' }}</b>
      <span>提问已临时禁用。检查后端进程（<code>python -m app.main</code> :8001）或容器是否被暂停。</span>
      <button class="btn btn-ghost btn-sm" @click="probeNow">立即重试</button>
    </div>

    <AskForm :disabled="running || healthDown" @submit="ask" />

    <div v-if="errorMessage" class="banner error-banner rise">
      <b>出错了</b><span>{{ errorMessage }}</span>
    </div>

    <template v-if="state.phase !== 'idle'">
      <div class="question-line rise">
        <span class="q-tag">问</span>
        <span class="q-text">{{ state.question }}</span>
        <span class="phase-tag" :class="state.phase">
          {{ phaseLabel }}
          <span v-if="running" class="pulse-dot">●</span>
        </span>
        <span v-if="running" class="running">
          <button class="btn btn-ghost btn-sm" @click="cancel">取消</button>
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

      <p v-if="state.phase === 'cancelled'" class="banner cancel-banner rise">本轮已取消。上方保留已产出的部分内容供参考。</p>

      <AnswerCard
        v-if="state.streamed"
        :content="state.streamed"
        :streaming="running && state.phase === 'streaming'"
        :total-ms="state.totalMs"
        :citation-count="state.citations.length"
        :retry-count="state.retryCount"
      />

      <CitationsPanel :citations="state.citations" />
      <VerdictsPanel :verdicts="state.verdicts" />

      <EventTimeline :entries="state.timeline" />
    </template>

    <div v-else class="empty">
      <h3>两条内置路径</h3>
      <div class="paths">
        <div class="path ok">
          <div class="path-tag">有据可答</div>
          <p>"宁德润能的毛利率是多少？"</p>
          <span class="path-flow">混合检索（向量 + IDF 关键词）→ 引用校验 → 带引用生成</span>
        </div>
        <div class="path err">
          <div class="path-tag">零幻觉原则</div>
          <p>证据不足时选择拒答</p>
          <span class="path-flow">引用校验不通过的论断，系统明说"证据不足"而非硬答（当前 mock 演示环境只走作答路径，真实模式/缺证据时触发拒答）</span>
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
  padding: 12px 14px; background: var(--sheet); border: 1px solid var(--line); border-radius: var(--radius-l);
}
.session-hint { margin: -6px 0 0; font-size: 11.5px; color: var(--ink-faint); line-height: 1.6; }
.session-hint b { color: var(--ink-dim, var(--ink-faint)); font-weight: 600; }
.field { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 240px; }
.field span { font-size: 11.5px; color: var(--ink-faint); letter-spacing: 1px; }

/* 横幅 */
.banner {
  display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; margin: 0;
  border-radius: var(--radius-m); padding: 11px 16px; font-size: 13.5px;
}
.banner b { flex: none; }
.error-banner { background: var(--red-soft); border: 1px solid rgba(179, 65, 58, 0.4); color: var(--red); }
.health-banner {
  background: var(--gold-soft); border: 1px solid rgba(176, 132, 48, 0.45); color: #8a6116;
  align-items: center;
}
.health-banner code { font-family: var(--mono, monospace); font-size: 12px; }
.health-banner .btn { margin-left: auto; }
.cancel-banner { background: var(--paper-2); border: 1px solid var(--line-strong); color: var(--ink-dim); }

/* 问题行 */
.question-line { display: flex; gap: 10px; align-items: center; padding: 4px 2px; flex-wrap: wrap; }
.q-tag {
  flex: none; width: 26px; height: 26px; border-radius: 50%; display: inline-grid; place-items: center;
  background: var(--accent); color: #fff; font-family: var(--serif); font-weight: 700; font-size: 14px;
}
.q-text { font-family: var(--serif); font-size: 17px; font-weight: 700; letter-spacing: 0.5px; }
.phase-tag {
  margin-left: auto; font-size: 12px; font-family: var(--mono); color: var(--ink-dim);
  border: 1px solid var(--line-strong); border-radius: 999px; padding: 0 10px; background: var(--paper-2);
  display: inline-flex; align-items: center; gap: 6px;
}
.pulse-dot { color: var(--gold); animation: pulse 1.4s ease-in-out infinite; font-size: 9px; }
.phase-tag.running, .phase-tag.streaming { color: var(--gold); border-color: rgba(176, 132, 48, 0.4); }
.phase-tag.done { color: var(--teal); border-color: rgba(47, 125, 107, 0.4); }
.phase-tag.refused { color: var(--red); border-color: rgba(179, 65, 58, 0.4); }
.phase-tag.error { color: var(--red); border-color: rgba(179, 65, 58, 0.4); }

/* 拒答 */
.refuse-banner {
  display: flex; gap: 16px; align-items: flex-start;
  background: var(--gold-soft); border: 1px solid rgba(176, 132, 48, 0.45);
  border-radius: var(--radius-l); padding: 16px 20px;
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

/* 空态 */
.empty h3 { margin: 10px 0 12px; font-size: 14px; color: var(--ink-dim); letter-spacing: 1px; }
.paths { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.path {
  background: var(--sheet); border: 1px solid var(--line); border-radius: var(--radius-l);
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
