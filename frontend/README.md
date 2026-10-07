# FinRAG 前端（Vue 3 研报问答）

独立部署的 SPA，**对后端零侵入**：经 `POST /ask` 的 SSE 事件流完成问答展示
（后端 CORS 已放开；dev 模式仍走 Vite proxy，与 DataCrew 前端保持一致形态）。

## 运行

```powershell
# 前置：后端在 :8001（python -m app.main），库已入库
npm install
npm run dev        # http://localhost:5174
npm run build      # vue-tsc 类型检查 + vite build
npm test           # vitest：50 项单测（含 12 项组件/外壳测试，jsdom）
```

## 它把后端契约用全了

| 后端能力 | 前端落点 |
|---|---|
| SSE 帧（仅 `data:` 行，无 `event:` 头） | `src/services/sse.ts` 解析器（与 datacrew 帧格式不同，各自适配） |
| 流式答案（answer 中继帧） | reducer **快照语义**：中继帧与终态帧都是全量答案，整体替换不拼接（见下方"已修复的 bug"） |
| 终态 citations | `CitationsPanel`：来源/章节/相关度（数字+占比条）/原文片段 |
| 拒答（refuse + verdicts） | `VerdictsPanel`：逐论断 ✓/✗ + 缺失证据词；拒答横幅 |
| node/status 事件 | `EventTimeline`：节点耗时 + 补偿检索次数（retrieve ×N）+ 总耗时 |
| 健康检查 | `useHealth()` 全局单例：报头灯与提问门控同源，附立即重试 |

## 已修复的 bug：答案重复上屏

后端 `generate` 节点**一次性产出全量答案**（graph.py：单次 LLM 调用整体返回），
API 因此发出两种 answer 帧，且都是完整快照而非 token 增量：

1. 中继帧——每次 generate 执行后一份全量答案（无 citations）；
2. 终态帧——流程结束后同一份全量答案 + citations + total_ms；
3. 校验不过重试时 generate 再跑，于是有 A1、A2 多份快照。

旧版 reducer 按"增量拼接"处理 → happy path 答案显示两遍、重试时三遍。
现改为**整体替换，以最新快照为准**（`streamed: e.answer ?? ''`），
`tests/reducer.test.ts` 用真实帧序（happy path / 补偿检索）锁死这条行为。

## 结构

```
src/
  types/events.ts     SSE 事件类型（与 app/api/main.py 契约逐字段对齐）
  services/
    sse.ts            帧解析器（纯函数，data-only 帧）
    reducer.ts        问答轮次状态机（纯函数：快照替换/终态/拒答/错误）
    timeline.ts       执行轨迹展示规则：中文名/attempt/延迟/detail 扁平化（纯函数）
    round.ts          轮次生命周期（与 datacrew 同款，可注入定时器）
    health.ts         连接健康状态机
    session.ts        会话 ID 单一事实源（localStorage + 订阅广播）
    api.ts            /ask SSE 客户端 + API_BASE 导出
  composables/
    useHealth.ts      App 级健康探测单例（报头灯与提问门控同源）
    useAsk.ts         接线层（reducer × api，泛型严实：AsyncGenerator<RagEvent>）
  views/AskView.vue   提问 + 答案 + 引用 + 校验明细 + 拒答横幅 + 执行轨迹
  components/
    base/             设计系统基件（纸感主题）：BaseButton / BaseCard / Badge / Spinner / EmptyState
    AnswerCard.vue    答案纸面（流式光标/耗时/引用数/复制）
    EventTimeline.vue 执行轨迹
    CitationsPanel    引用出处 / VerdictsPanel 逐论断裁定 / MarkdownBlock 安全渲染
tests/                sse(6) reducer(7) round(6) health(4) health.reactive(1)
                      timeline(6) session(3) api.stream(5) components(10) app(2)（后两项 jsdom）
```

**设计纪律**：与 DataCrew 前端同款——判断逻辑在纯函数（解析器/状态机/展示规则），
组件只渲染；纯函数有单测，UI 改动不碰协议。

## 同样修过的两个引擎盖下 bug（2026-10-03，与 datacrew 前端同源同修）

1. **SSE 流式管道死锁**：`askStream` 曾用 wake/notify 跨块信号量，首块 yield 完
   生成器永不收敛 → 30s 看门狗把已显示的答案误报成"无新事件"。修复与回归测试
   （`tests/api.stream.test.ts`，data-only 帧多分片场景）同 datacrew。
2. **健康横幅冻结**：`useHealth().isDown` 解构即固化，后端恢复后提问按钮永远
   禁用。修复：`isDown` 改 computed。

## 健壮性约定

- `strictPort: true`——端口被占时启动直接失败，绝不静默换端口。
- 流僵死看门狗——SSE 连接 30s 无字节即主动断开并给出明确错误；流正常结束但
  状态机仍停在 running/streaming 时按错误收尾，不会永远转圈。
- mock 演示环境（LLM_MODE=mock）只会走作答路径；拒答（证据不足/引用校验失败）
  需要在真实模式或证据缺失场景触发。
