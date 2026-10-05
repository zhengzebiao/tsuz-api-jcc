# AI Agent / RAG 聊天功能：第五阶段“质量和运行完善”执行记录

> 状态：部分完成
>
> 执行日期：2026-10-05
>
> 总实施方案：[jcc-ai-agent-rag-implementation-plan.md](jcc-ai-agent-rag-implementation-plan.md)
>
> 阶段实现计划：[JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_5_PLAN.md](JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_5_PLAN.md)

## 1. 执行范围与结论

本次开始实施阶段五，完成了 LLM token usage 基础链路、单进程消息限流、readiness 检查基础接口和 SSE event id 数据结构兼容改动。阶段仍为部分完成，不能进入阶段完成验收。

已完成：

1. provider-neutral `LLMUsage`，OpenAI-compatible 非流式及流式 usage 解析；
2. orchestrator 对文本流和 tool loop usage 累计，并在 AgentRun 终态保存已有 token 字段；
3. 单进程按用户消息窗口限流，超限返回 429 和 Retry-After；
4. 增加 `/readyz`，区分数据库、runtime/LLM 和 RAG 基础状态；
5. AgentEvent 增加可选 event_id 字段，为后续回放兼容做准备；
6. 阶段五计划文档和本执行记录。

明确未实现：

- 持久化 SSE event log、Last-Event-ID 回放和 retention；
- 上下文摘要；
- 完整成本统计 API；
- 完整的限流/SSE 连接并发限制；
- readiness 的 active RAG pointer 深度检查；
- 生产 provider、共享数据库和部署验证。

## 2. 实际代码与配置变更

### 2.1 Usage 与运行持久化

- `app/agent/llm/base.py`：新增 `LLMUsage`、`TextDelta.usage` 和 `LLMResponse.usage`。
- `app/agent/llm/openai_compatible_client.py`：解析 `input_tokens/output_tokens` 及 `prompt_tokens/completion_tokens`；流式 usage chunk 即使没有 choices 也可被读取。
- `app/agent/orchestrator.py`：累计纯文本流和多轮 tool response 的 usage，并在 completed/failed/cancelled/timeout 的 run 更新中写入已有 `AgentRun.input_tokens/output_tokens`。
- `app/conversations/repository.py`：扩展 `update_run_status` 的 token 更新参数。

### 2.2 限流与 readiness

- `app/core/rate_limit.py`：新增有界、带锁的进程内滑动窗口 limiter。
- `app/api/agent.py`：消息提交按 user id 限流，超限返回稳定错误码和 `Retry-After`；测试中无完整 app lifespan 的路由仍保持兼容。
- `app/core/config.py`：增加限流、SSE replay 上限、摘要开关/阈值和价格配置占位项；限流默认关闭以保持既有测试及未配置环境兼容，部署可显式开启。
- `app/api/health.py`：新增 `/readyz`，执行短数据库 `SELECT 1`，报告 runtime/LLM/RAG 状态；`/health` 保持轻量存活语义。

### 2.3 SSE 兼容基础

- `app/agent/events.py`：`AgentEvent` 增加可选 `event_id`，有值时输出 SSE `id:` 行；现有事件无 id 时格式保持兼容。

## 3. 数据、迁移和状态

新增 0008 migration，为 `agent_runs` 增加 nullable `estimated_cost` 和 `pricing_key`；token 统计继续复用 0004 migration 的字段。SSE 事件和摘要尚未持久化。

## 4. 测试与验证结果

| 检查 | 命令或方法 | 结果 | 证据/说明 |
|---|---|---|---|
| 定向测试 | `./.venv/bin/pytest tests/test_agent_llm.py tests/test_agent_runtime.py tests/test_health.py -q` | 通过 | 7 passed |
| 回归修复测试 | `./.venv/bin/pytest tests/test_agent_conversations.py::test_message_mode_sequence_pagination_and_idempotency tests/test_agent_runtime.py::test_cancel_only_cancels_matching_current_message -q` | 通过 | 2 passed |
| 全量测试 | `./.venv/bin/pytest -q` | 通过 | 155 passed，2 条既有 deprecation warnings |
| 定向 Ruff | `./.venv/bin/ruff check` 修改 Python 文件 | 通过 | All checks passed |
| 真实 provider usage | 外部 OpenAI-compatible 服务 | 未执行 | 本阶段未获得生产/外部服务验收授权，使用现有 Fake/Mock 回归 |
| 迁移/生产验证 | Alembic 本地测试库、生产部署 | 部分执行/未执行 | 本地测试库已执行 0008 upgrade；未连接共享长期库或生产环境 |

## 5. 阶段验收结果

| 编号 | 验收标准 | 结果 | 验证证据 |
|---|---|---|---|
| AC-5-01 | Agent 提交在单进程范围内限流，超限返回 429/Retry-After | 部分通过 | `app/core/rate_limit.py`、Agent API；SSE 建连/运行并发限制尚未实现 |
| AC-5-02 | provider usage 被解析、tool loop 累计并保存到 AgentRun | 部分通过 | LLM/orchestrator 代码、0008 migration 和回归测试；尚无新增 usage 专项测试及真实 provider 验证 |
| AC-5-03 | `/health` 轻量，`/readyz` 区分依赖状态，敏感值不进日志 | 部分通过 | `/readyz` 和既有日志脱敏测试；LLM/RAG 深度检查及新增敏感字段测试尚未完成 |
| AC-5-04 | 长上下文摘要失败安全回退 | 未通过 | 尚未实现摘要模块/持久状态 |
| AC-5-05 | SSE event id 与 Last-Event-ID 回放，断线不取消 | 未通过 | 仅完成可选 event_id 序列化，尚无持久 event log/replay |
| AC-5-06 | 离线 eval 不访问真实 provider | 未通过 | 尚未新增 eval 命令和数据集 |

## 6. 安全、兼容性与遗留问题

- 限流状态仅存在于进程内，不能宣称多 worker 或多容器安全；当前默认关闭，启用前必须继续遵守 `WEB_CONCURRENCY=1`。
- token 字段只记录 provider 明确返回的非负整数；provider 未返回时保持 NULL，不估算未知 token。
- readiness 不调用真实 LLM，不把配置完整误称为 provider 已连通。
- 当前阶段还没有新增统一成本价格版本、事件 payload 脱敏/保留策略、摘要内容安全策略和离线评测边界。

## 7. 下一步入口

优先补齐：

1. usage 专项测试与可选成本字段/价格配置；
2. durable event log、事件序号和 `Last-Event-ID` replay；
3. fallback-safe conversation summary；
4. offline eval 命令与 JSONL rubric；
5. 完善 readiness RAG active pointer、SSE 连接限流和新增日志脱敏测试；
6. 再次执行完整阶段验收并据真实结果更新总方案状态。

## 8. 文档同步记录

- 总方案：应标记阶段五为“部分完成”，并链接本阶段计划与执行记录；
- 阶段五计划：已创建并标记“实施中”，范围包含未完成的回放、摘要和 eval；
- 本执行记录：记录本次实际代码、测试结果、未执行环境和剩余入口。

## 9. 阶段结论

第五阶段当前为部分完成：usage 基础链路、单进程消息限流、readiness 基础接口和 event id 兼容字段已落地，155 项全量测试通过；摘要、离线评测和 SSE 可靠回放等必需能力尚未实现，因此不能标记阶段完成。
