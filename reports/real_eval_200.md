# FinRAG 真实模型 200 题代表性抽样（FR-9 中间标定）

> 全量 800 题见 [`real_eval_800.md`](real_eval_800.md)；本文件是**修正抽样偏差后**
> 的 200 题样本，用于迭代期快速标定，不作为最终数字。

## 设置

- 语料/题库同 FR-1（1000 篇 / 8000 块 / 800 题），seed=42
- `EMBEDDING_MODE=local` + `LLM_MODE=real`（DeepSeek），hybrid 检索
- 端到端抽样：按题档分层后**先打散再取**（每个可答档 66 题 + 100 道拒答），
  共 298 次 LLM 调用
- 命令：

```bash
LLM_MODE=real EMBEDDING_MODE=local python -m app.eval.runner \
  --companies 200 --strategies hybrid --end2end 200
```

## 结果

| 档 | 通过 | 通过率 |
|---|---|---|
| exact | 66/66 | 100% |
| abbr | 66/66 | 100% |
| calculation | 51/66 | 77.3% |
| refusal | 100/100 | 100% |
| answer（可答档） | 183/198 | 92.4% |

检索 hybrid：exact/abbr recall@5 100%、calculation 81%、全档 MRR 0.908；
表格题（segment_share）recall 100%。守卫误拒 0%，引用忠实度 89.5%，
P95 993ms。

## 为什么 overall 95.0% 低于全量 800 的 97.1%

这是**抽样配比**造成的，不是系统退化：

- 200 题样本里 calculation 占 66/198 = 33.3%，全量 800 里只占 100/700 = 14.3%；
- calculation 是当前最弱档（77%），权重高就把总通过率拉低；
- 分档看与全量一致：exact/abbr 100%、calculation 77%、refusal 100%。

这也解释了第一次 200 题样本（未打散，只抽到 yoy 子类）为何虚高——那个数字
已作废，不写进任何总结。
