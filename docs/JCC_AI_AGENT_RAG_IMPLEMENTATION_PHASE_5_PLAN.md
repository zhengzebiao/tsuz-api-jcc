# AI Agent / RAG 聊天功能：第五阶段“质量和运行完善”实现计划

> 状态：实施中
>
> 总实施方案：[jcc-ai-agent-rag-implementation-plan.md](jcc-ai-agent-rag-implementation-plan.md)
>
> 阶段执行记录：[JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_5_EXECUTION.md](JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_5_EXECUTION.md)

## 1. 阶段基准与目标

阶段二至四已提供单进程 latest-wins runtime、LLM/工具调用、来源记录和 RAG generation。当前缺少运行限流、provider usage 持久化、readiness、长上下文降级、离线评测和 SSE 重连游标。本阶段在既有单 worker 边界内补齐这些能力。

目标：

1. 在单进程范围内对 Agent 提交和 SSE 建连限流；
2. 解析并累计 OpenAI-compatible usage，保存已有 AgentRun token 字段并计算可选估算成本；
3. 增加 `/readyz`，与轻量 `/health` 分离，并扩展日志脱敏；
4. 用有界、失败可回退的上下文摘要辅助长对话；
5. 为 SSE 事件提供有限事件 ID、重连回放和 Last-Event-ID；
6. 提供完全离线、确定性的 eval 命令和测试。

## 2. 范围与非目标

实现：配置、单进程 limiter、usage/cost contract、readiness、summary fallback、SSE replay、offline eval、测试和本文档同步。

不实现：Redis 持久队列或分布式锁、多 worker/多容器扩容、长期记忆、多 Agent、人工审批，以及阶段四未完成的 PostgreSQL 原生 FTS/vector 混合 SQL。

约束：SSE 回放仅保证已持久化/当前进程保留的事件；断线不取消 Agent。未配置 LLM 时应用仍可启动，但 readiness 不通过 Agent 检查。

## 3. 设计

- `LLMUsage` 扩展 provider-neutral LLM contract；tool loop 各轮 usage 累加，未知 token 保持 NULL。
- 使用配置化 input/output 每百万 token 价格；价格未知时 estimated cost 为 NULL，不猜测。
- `RateLimiter` 为有界进程内滑动窗口，key 使用已认证 user/conversation；超限返回 429 和 Retry-After，明确不跨 worker。
- `/readyz` 检查数据库、runtime/LLM 配置和 RAG active pointer 的可用性，不发送真实 LLM 请求。
- 摘要只作为上下文前缀，超过阈值时生成；失败、超时、数据库不可用均回退既有最近消息，不删除原始消息。
- `AgentEvent` 增加 event_id；runtime 为每个 run 保留有界事件历史，SSE 支持 `Last-Event-ID`，过期游标返回明确错误。该回放不替代持久任务队列。
- `eval-agent --offline` 使用固定 JSONL cases 和 Fake LLM/规则评分，不访问外部服务。

## 4. 关键文件

- 配置/API/runtime：`app/core/config.py`、`app/core/rate_limit.py`、`app/api/health.py`、`app/api/agent.py`、`app/agent/events.py`、`app/agent/runtime.py`。
- LLM/统计：`app/agent/llm/base.py`、`app/agent/llm/openai_compatible_client.py`、`app/agent/orchestrator.py`、`app/conversations/repository.py`。
- 摘要/eval：`app/agent/summary.py`、`scripts/eval_agent.py`、`evals/cases.jsonl`。
- 配置样例、测试及本阶段执行记录。

## 5. 验收标准

| 编号 | 标准 | 验证 |
|---|---|---|
| AC-5-01 | Agent 提交/SSE 在单进程范围限流，超限返回 429/Retry-After | limiter/API tests |
| AC-5-02 | provider usage 被解析、tool loop 累计并保存到 AgentRun；价格未知不猜测 | LLM/orchestrator tests |
| AC-5-03 | `/health` 轻量，`/readyz` 区分 DB/runtime/LLM/RAG 状态；敏感值不进日志 | health/logging tests |
| AC-5-04 | 长上下文摘要失败安全回退且不删除原消息 | summary/orchestrator tests |
| AC-5-05 | SSE 事件带稳定 ID，Last-Event-ID 可回放未丢事件，断线不取消运行 | SSE tests |
| AC-5-06 | 离线 eval 不访问真实 provider，失败返回非零并输出可读结果 | eval tests/command |

## 6. 验证计划

实现后执行定向 pytest、修改文件 Ruff、compileall、全量 pytest、Alembic heads/check、离线 eval 和 git diff --check。真实 provider、共享数据库、生产部署和多 worker 验证不在本阶段默认执行，未执行项写入执行记录。
