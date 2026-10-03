# FinRAG 前端（Vue 3 研报问答）

独立部署的 SPA，**对后端零侵入**：经 `POST /ask` 的 SSE 事件流完成问答展示
（后端 CORS 已放开；dev 模式仍走 Vite proxy，与 DataCrew 前端保持一致形态）。

## 运行

```powershell
# 前置：后端在 :8001（python -m app.main），库已入库
npm install
npm run dev        # http://localhost:5174
npm run build      # vue-tsc 类型检查 + vite build
npm test           # vitest：11 项单测
```

## 它把后端契约用全了

| 后端能力 | 前端落点 |
|---|---|
| SSE 帧（仅 `data:` 行，无 `event:` 头） | `src/services/sse.ts` 解析器（与 datacrew 帧格式不同，各自适配） |
| 流式答案（answer 中继帧） | reducer 拼接，逐段上屏 |
| 终态 citations | `CitationsPanel`：来源/章节/相关度/原文片段 |
| 拒答（refuse + verdicts） | `VerdictsPanel`：逐论断 ✓/✗ + 缺失证据词 |
| node/status 事件 | `EventTimeline`：节点耗时 + 补偿检索次数 + 总耗时 |

## 结构

```
src/
  types/events.ts    SSE 事件类型（与 app/api/main.py 契约逐字段对齐）
  services/sse.ts    帧解析器（纯函数，data-only 帧）
  services/reducer.ts 问答轮次状态机（纯函数：流式拼接/终态/拒答/错误）
  services/api.ts     /ask SSE 客户端
  composables/useAsk.ts 接线层
  views/AskView.vue  提问 + 答案 + 引用 + 校验明细 + 拒答横幅
  components/         CitationsPanel / VerdictsPanel / EventTimeline / MarkdownBlock
tests/                sse.test.ts（6）+ reducer.test.ts（5）
```

**设计纪律**：与 DataCrew 前端同款——判断逻辑在纯函数（解析器/状态机），组件只渲染。
